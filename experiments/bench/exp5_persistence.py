"""실험 5: 영속성 (SQLite vs Neo4j).

인메모리 계층에는 영속성이 없다 — 재시작하면 저장소에서 전량 다시 읽어야 한다.
그래서 저장소의 가치는 탐색 속도가 아니라 (1) 적재 (2) 공간 (3) 증분 갱신
(4) 재수화 에서 나온다. 실험 1·4가 재지 않은 축이다.

이 러너가 내는 4지표(load_ms / disk_bytes / append_*_ms / rehydrate_ms)가
플랜 3 의사결정 규칙 2를 그대로 먹인다(4지표 중 3개 이상 Neo4j 우위면 keep).
규칙 1(RAG 20셀)과 규칙 3(SQL 복잡도)은 이미 실패했으므로 **여기가 마지막
관문**이다. 그래서 공정성 장치가 이 파일의 본체다.

공정성 장치
  1) 매 타이밍 반복마다 **새 쌍** — 같은 (a, b)를 재사용하면 SQLite는
     review_item을 누적해 ReviewSection(max_length=5) 위반으로 from_sqlite가
     죽고 csr_matrix가 중복 좌표를 합산하는 반면, Neo4j의 MERGE는 멱등이라
     멀쩡하다. 계측기가 한쪽에서만 깨진다(태스크 5 리포트 §6(1)).
     _new_pairs가 데이터셋에 방향 무관으로 없는 쌍을 결정적 순서로 뽑고,
     **두 백엔드에 같은 시퀀스**를 준다.
  2) JVM 예열 — 웜업 부족으로 Neo4j 수치가 최대 8배 부풀려진 전례가 있다
     (플랜 2). 스케일 루프 **밖에서** _prime_neo4j로 한 번에 데우고(웜업
     상태가 규모 순서와 교락되지 않게), 무거운 지표의 warmup을 HEAVY_WARMUP
     으로 올린다. 실측 근거는 리포트의 웜업 곡선 참고.
  3) 철거 비대칭 공개 — build_sqlite의 철거는 path.unlink()(O(1))인데
     load_neo4j의 철거는 MATCH (n) DETACH DELETE n(O(n))이고 둘 다 타이밍
     구간 안에 있다. 양쪽 철거 비용을 따로 재서 calibration으로 공개하되
     **원 수치에서 빼지 않는다** — 규칙은 원 수치를 센다.
  4) append의 엔진 외 비용 공개 — Neo4j의 append_*는 호출마다 세션을 열고
     두 Person을 MATCH한 뒤 MERGE한다. SQLite의 INSERT는 외래키가 없어 읽기가
     0회이고 conn은 타이밍 밖에서 열린다. 맨 session 획득/반납, no-op MATCH,
     두 엔드포인트 MATCH를 따로 재서 엔진 외 몫을 바운드한다
     (태스크 5 리포트 §6(1b)).
  5) 공유 파이썬 비용 공개 — rehydrate_ms에는 두 백엔드가 똑같이 내는
     MemoryGraph.build 비용이 들어 있다. rehydrate._assemble을 따로 재서
     그 몫을 밝힌다(태스크 5 리포트 §6(3)).
  6) disk_bytes 오염 분리 — Neo4j는 컨테이너 볼륨 전체를 재므로 이전 규모의
     잔여가 섞인다. 오염된 값을 그대로 남기되(metric=disk_bytes) 볼륨을
     지우고 다시 띄워 규모 하나만 적재한 값(metric=disk_bytes_clean)을 따로
     측정한다. 판정은 clean 쪽으로 한다.

남아 있는(고칠 수 없고 공개만 하는) 비대칭 — 전부 SQLite에 유리한 방향이다:
  - 새 쌍을 써도 Neo4j의 MERGE는 찾아서-만들기이고 SQLite의 INSERT는 맹목적
    덧붙이기다.
  - Neo4j의 append_review만 evidence를 쓴다(SQLite review 테이블에는 컬럼
    자체가 없다) — disk_bytes뿐 아니라 append_review_ms도 부풀린다.
  - rehydrate_ms는 "엔진 대 엔진"이 아니라 "이 스키마 × 이 엔진"이다. SQLite의
    정규화 스키마는 스킬·리뷰 항목을 파이썬에서 dict로 조립하게 만들고, Neo4j는
    서버가 collect()로 묶어 준다. 그 서버 작업은 왕복 안에 있으므로 계상된다.
  - 여기서 "내구성"은 커밋되어 새 클라이언트에서 보인다는 뜻이다(양쪽 대칭).
    크래시 내구성은 어느 쪽도 시험하지 않았다.
  - availability / projects / evidence 는 양쪽 다 복원하지 않는다. 따라서
    rehydrate_ms는 "완전한 상태 복원"이 아니라 "핵심 그래프 복원" 비용이다.

적재·재수화는 반복 비용이 크므로 repeats를 줄인다 — 방법론 일관성보다 실행
가능성을 택한 결정이며 리포트에 명시한다. repeats=3에서 p95_ms는 사실상
최댓값이다(백분위수가 아니다).
"""
import json
import shutil
import sqlite3
import statistics as st
import subprocess
import tempfile
import time
from pathlib import Path

from core.config import REPO_ROOT
from core.domain.models import CoworkRecord
from core.graph import rehydrate
from core.graph.sqlite_store import append_cowork, append_review, build_sqlite
from experiments.bench import datasets, harness

RAG_SCALES = [(100, 20), (300, 60), (500, 100), (1000, 200)]

# 적재·재수화: 회당 비용이 커 반복을 줄인다(다른 실험은 20/3). repeats=3에서
# median은 3표본의 가운데 값이고 p95_ms는 최댓값과 같다.
HEAVY_REPEATS = 3
# 브리프의 warmup=1은 이 프로젝트 자신의 전례(웜업 부족 → 8배 부풀림)와
# 정면으로 어긋난다. 실측 웜업 곡선(리포트 §웜업 근거)에서 n=100 load_neo4j는
# 1회차 1669ms → 2회차 203ms → 15회차 이후 60ms대로, 10회차까지도 계속
# 내려간다. 20이면 곡선이 평탄해진 뒤이고, 양쪽 백엔드에 같은 값을 쓴다
# (SQLite 웜업은 값이 싸고, 같게 두면 append 후 저장소 상태까지 동일해져
# rehydrate_ms가 같은 상태를 재게 된다).
HEAVY_WARMUP = 20

# 증분 갱신: 다른 실험과 같은 repeats=20. warmup은 양쪽 20으로 맞춘다
# (brief는 neo4j만 20이었다) — 두 백엔드가 같은 횟수를 써야 rehydrate_ms가
# 같은 상태를 재고, 타이밍 대상 쌍도 양쪽이 동일해진다.
APPEND_REPEATS = 20
APPEND_WARMUP = 20
APPEND_CO_MONTHS = 3
APPEND_PROJECT_COUNT = 1

# 스케일 루프 전 1회 실행하는 예열. 규모는 작게(비용) 횟수는 곡선이 평탄해질
# 때까지(효과). 예열을 루프 밖에 두어야 웜업 상태가 규모 순서와 교락되지 않는다.
PRIMING_SCALE = (100, 20)
PRIMING_ROUNDS = 30

NEO4J_DATA_DIR = REPO_ROOT / ".neo4j" / "data"
PARTIAL_PATH = harness.RESULTS_DIR / "exp5_persistence.partial.json"

NOTE = ("적재(load_ms)·재수화(rehydrate_ms)는 repeats=3으로 축소 측정했다 — "
        "다른 실험의 20/3과 다르며, 회당 비용이 커 전체 실행 시간을 감당할 수 "
        "없기 때문이다. 대신 warmup은 브리프의 1이 아니라 20을 썼다(웜업 부족이 "
        "Neo4j를 최대 8배 부풀린 전례가 있다). repeats=3에서 p95_ms는 백분위수가 "
        "아니라 최댓값이다. 증분 갱신은 repeats=20, warmup=20(양쪽 동일)이다. "
        "neo4j의 disk_bytes는 컨테이너 볼륨 전체라 이전 규모가 섞인 오염된 값이고, "
        "판정에는 볼륨을 지우고 규모 하나만 적재해 잰 disk_bytes_clean을 쓴다.")


# --------------------------------------------------------------------------
# 크기 측정
# --------------------------------------------------------------------------

def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _neo4j_disk_detail(reading: str, n_people: int) -> dict:
    """Neo4j 볼륨을 구성 요소별로 나눠 남긴다.

    전체 크기만 남기면 "무엇이 그 용량을 쓰는가"를 리포트가 말할 수 없다.
    Neo4j 5는 데이터베이스마다 트랜잭션 로그를 미리 잡아 두므로(기본 회전 크기
    256MiB), 빈 데이터베이스도 수백 MB로 잡힌다 — 데이터 크기와 무관한 고정
    선불이다. 이 분해가 있어야 "전체 volume 기준"과 "스토어 파일 기준" 두
    읽기를 모두 정직하게 보고할 수 있다.
    """
    base = NEO4J_DATA_DIR
    return {"backend": "neo4j", "n_people": n_people, "reading": reading,
            "total_bytes": _dir_bytes(base),
            "store_bytes": _dir_bytes(base / "databases" / "neo4j"),
            "txlog_bytes": _dir_bytes(base / "transactions" / "neo4j"),
            "system_store_bytes": _dir_bytes(base / "databases" / "system"),
            "system_txlog_bytes": _dir_bytes(base / "transactions" / "system")}


# --------------------------------------------------------------------------
# 타이밍 대상 레코드 — 매 반복 새 쌍
# --------------------------------------------------------------------------

def _new_pairs(ds, count: int, used: set) -> list[tuple[str, str]]:
    """데이터셋에 (방향 무관) 없고 서로도 겹치지 않는 (a, b) 쌍 count개.

    왜 필요한가: harness.measure는 warmup + repeats회 fn을 호출한다. 브리프
    참조 코드처럼 같은 쌍을 그 횟수만큼 재사용하면
      - SQLite append_review는 review_item을 계속 쌓고, from_sqlite가 한 쌍의
        항목을 한 리스트로 누적하므로 6번째부터 ReviewSection(max_length=5)를
        위반해 **재수화 측정 자체가 크래시**한다,
      - append_cowork는 collaboration에 행을 더 쌓고 csr_matrix가 중복 좌표를
        **합산**해 cowork_months가 조용히 불어난다,
      - Neo4j는 MERGE가 멱등이라 둘 다 겪지 않는다(대신 첫 회만 생성이고
        나머지는 갱신이라 같은 일을 재는 것도 아니게 된다).
    tests/test_rehydrate.py의 _assert_pair_is_new와 같은 기준으로 거른다 —
    pair_review_score의 키가 방향 무관이므로(core/graph/memory_graph.py:47)
    협업·리뷰 양쪽 다 frozenset으로 본다.

    쌍은 실재하는 Person id로만 만든다. Neo4j의 append_*는 두 Person을
    MATCH한 뒤 MERGE하므로, 없는 id를 쓰면 MATCH가 0행이라 아무것도 쓰지 않는
    "공짜" 측정이 된다.

    순서는 결정적이다(간격 내림차순 → 인덱스 오름차순). 재현 가능해야 하고,
    두 백엔드에 같은 시퀀스를 줘야 한다. used를 호출 간에 넘겨 협업용 쌍과
    리뷰용 쌍이 서로 겹치지 않게 한다.
    """
    ids = [p.id for p in ds.people]
    n = len(ids)
    occupied = {frozenset((c.a_id, c.b_id)) for c in ds.coworks}
    occupied |= {frozenset((r.reviewer_id, r.reviewee_id)) for r in ds.reviews}
    out: list[tuple[str, str]] = []
    for gap in range(n - 1, 0, -1):
        for i in range(n - gap):
            a, b = ids[i], ids[i + gap]
            key = frozenset((a, b))
            if key in occupied or key in used:
                continue
            used.add(key)
            out.append((a, b))
            if len(out) == count:
                return out
    raise RuntimeError(f"새 쌍이 부족하다: {len(out)}/{count} (n={n})")


def _cowork_records(pairs) -> list[CoworkRecord]:
    """타이밍 구간 밖에서 미리 만들어 둔다 — pydantic 검증 비용이 타이밍에
    들어가면 SQLite의 INSERT(수십 마이크로초)와 같은 자릿수라 측정이 흐려진다."""
    return [CoworkRecord(a_id=a, b_id=b, co_months=APPEND_CO_MONTHS,
                         project_count=APPEND_PROJECT_COUNT) for a, b in pairs]


def _review_payloads(ds, parsed, pairs) -> list[tuple]:
    """데이터셋의 실제 리뷰를 복사해 대상 쌍만 바꾼다.

    항목 수·극성·evidence 길이가 실제 워크로드와 같아야 한다. 특히 evidence는
    Neo4j만 저장하는 필드라(태스크 5 리포트 §6(1b)-2) 인위적으로 비우면 그
    비대칭을 측정에서 지워 Neo4j에 **유리하게** 왜곡된다. 원본은 건드리지 않는다
    (model_copy는 새 객체를 만든다) — datasets.build_scale은 lru_cache 공유다.
    """
    src_review, src_parsed = ds.reviews[0], parsed[0]
    return [(src_review.model_copy(update={"reviewer_id": a, "reviewee_id": b}),
             src_parsed.model_copy(update={"reviewer_id": a, "reviewee_id": b}))
            for a, b in pairs]


def _feeder(items):
    """호출마다 다음 원소를 주는 함수. 타이밍 구간 안에서 도는 유일한 추가
    비용이고(리스트 이터레이터의 __next__, 수십 나노초) 양쪽 백엔드에 동일하게
    붙는다."""
    return iter(items).__next__


# --------------------------------------------------------------------------
# 예열 / 보정
# --------------------------------------------------------------------------

def _prime_neo4j(driver, load_fn) -> None:
    """스케일 루프 전에 JVM을 한 번에 예열한다 — 웜업 상태가 규모 순서와
    교락되면 '규모가 커질수록 빨라지는' 가짜 추세가 만들어진다(플랜 2 전례).
    재는 두 무거운 경로(적재·재수화)를 그대로 돌리고 결과는 버린다."""
    ds, parsed, _ = datasets.build_scale(*PRIMING_SCALE, 42)
    for _ in range(PRIMING_ROUNDS):
        load_fn(driver, ds, parsed)
        rehydrate.from_neo4j(driver)


def _measure_detach_delete(driver, load_fn, ds, parsed) -> dict:
    """load_neo4j의 타이밍 구간 안에 들어 있는 철거 비용만 따로 잰다.

    build_sqlite는 path.unlink()(파일 하나 삭제, O(1))로 시작하고 load_neo4j는
    MATCH (n) DETACH DELETE n(현재 그래프 전체, O(n))으로 시작한다. 둘 다
    "지우고 적재한다"지만 지우는 양이 다르다. 적재를 타이밍 밖에 두고 삭제만
    잰다. **원 수치에서 빼지 않는다** — 사전 고정된 규칙은 원 수치를 센다.
    """
    samples = []
    for k in range(HEAVY_WARMUP + HEAVY_REPEATS):
        load_fn(driver, ds, parsed)                 # 적재는 타이밍 밖
        with driver.session() as s:                 # 세션 획득도 타이밍 밖
            t0 = time.perf_counter()
            s.run("MATCH (n) DETACH DELETE n").consume()
            samples.append((time.perf_counter() - t0) * 1000.0)
        if k + 1 == HEAVY_WARMUP:
            samples.clear()
    samples.sort()
    return {"median_ms": st.median(samples), "p95_ms": samples[-1],
            "min_ms": samples[0], "max_ms": samples[-1],
            "repeats": HEAVY_REPEATS, "warmup": HEAVY_WARMUP}


def _calibrate_neo4j(driver, ds) -> dict:
    """append_*_ms 중 그래프 갱신이 아닌 몫을 바운드한다(보정이 아니라 공개용).

    Neo4j의 append_*는 (1) 호출마다 driver.session()을 열고 (2) 두 Person을
    id로 MATCH한 뒤에야 (3) 관계를 MERGE한다. SQLite의 INSERT는 collaboration에
    외래키가 없어 읽기가 0회이고, conn은 호출자가 타이밍 밖에서 열어 넘긴다.
    (1)(2)를 각각 재서 Neo4j 표본에만 붙는 고정비의 상한을 남긴다.
    """
    a_id, b_id = ds.people[0].id, ds.people[-1].id

    def _open_close():
        with driver.session():
            pass

    def _noop_match():
        with driver.session() as s:
            s.run("MATCH (a:Person {id:$a}) RETURN a", a=a_id).consume()

    def _two_endpoint_match():
        with driver.session() as s:
            s.run("MATCH (a:Person {id:$a}), (b:Person {id:$b}) RETURN a, b",
                  a=a_id, b=b_id).consume()

    return {"neo4j_session_open": harness.measure(_open_close, warmup=APPEND_WARMUP),
            "neo4j_noop_match": harness.measure(_noop_match, warmup=APPEND_WARMUP),
            "neo4j_two_endpoint_match": harness.measure(_two_endpoint_match,
                                                        warmup=APPEND_WARMUP)}


def _measure_sqlite_unlink(db: Path, tmp: Path) -> dict:
    """build_sqlite의 철거 비용(path.unlink). Neo4j의 DETACH DELETE와 같은
    자리에 놓고 볼 수 있게 잰다. 사본을 미리 만들어 두고(타이밍 밖) 삭제만 잰다."""
    repeats, warmup = 10, 3
    copies = []
    for k in range(repeats + warmup):
        p = tmp / f"unlink_{k}.db"
        shutil.copyfile(db, p)
        copies.append(p)
    nxt = _feeder(copies)
    return harness.measure(lambda: nxt().unlink(), repeats=repeats, warmup=warmup)


def _measure_assemble(ds, parsed) -> dict:
    """rehydrate_ms 안의 저장소와 무관한 공유 파이썬 비용(MemoryGraph.build).

    이 몫이 지배적이면 rehydrate_ms는 저장소 성능에 둔감해진다 — 리포트가 그
    사실을 말할 수 있어야 한다(태스크 5 리포트 §6(3)). rehydrate._assemble은
    바로 이 목적으로 모듈 수준에 남겨 둔 함수다.
    """
    people, coworks = list(ds.people), list(ds.coworks)
    reviews = list(ds.reviews)
    return harness.measure(
        lambda: rehydrate._assemble(people, coworks, reviews, parsed),
        repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP)


# --------------------------------------------------------------------------
# 클린 슬레이트 disk_bytes
# --------------------------------------------------------------------------

def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO_ROOT, check=True,
                   capture_output=True, text=True, timeout=300)


def _wait_for_neo4j(timeout_s: float = 180.0):
    """컨테이너가 볼트 접속을 받을 때까지 기다린 뒤 driver를 돌려준다."""
    from core.graph.neo4j_store import get_driver
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        driver = get_driver()
        try:
            driver.verify_connectivity()
            return driver
        except Exception as exc:                    # noqa: BLE001 -- 기동 대기
            last = exc
            driver.close()
            time.sleep(1.0)
    raise RuntimeError(f"Neo4j 기동 대기 시간 초과: {type(last).__name__}: {last}")


def _reset_neo4j_store():
    """볼륨을 비우고 새 인스턴스를 띄운다.

    docker-compose.yml의 볼륨은 바인드 마운트(./.neo4j/data:/data)라
    `down -v`만으로는 지워지지 않는다 — 컨테이너를 내린 뒤 디렉터리를 직접
    비운다. .neo4j/는 gitignore 대상이고 모든 실험이 실행 시점에 적재하므로
    잃는 것은 없다.
    """
    _compose("down", "-v")
    assert NEO4J_DATA_DIR == REPO_ROOT / ".neo4j" / "data", "안전장치: 경로가 예상과 다르다"
    shutil.rmtree(NEO4J_DATA_DIR, ignore_errors=True)
    NEO4J_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _compose("up", "-d")
    return _wait_for_neo4j()


def _clean_slate_scale(n_people: int, n_projects: int) -> tuple[list, list]:
    """오염되지 않은 disk_bytes 한 규모.

    Neo4j는 DETACH DELETE로 지워도 페이지 파일이 줄지 않고 트랜잭션 로그는
    계속 쌓인다. 한 인스턴스에 여러 규모를 연달아 적재한 뒤 잰 값은 규모별
    비교에 쓸 수 없다(단조 증가만 한다). 볼륨을 지우고 새로 띄워 해당 규모만
    적재한 뒤 잰다. 읽기 전에 컨테이너를 정상 종료해 체크포인트를 흘려보낸다
    (실측: 종료 전후 스토어 크기 차 2.6% — 종료 쪽이 실제 반영 값이다).
    SQLite 쪽은 build_sqlite가 매번 파일을 지우고 새로 만들므로 원래 클린이다.
    """
    from core.graph.neo4j_store import load_neo4j
    ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)

    driver = _reset_neo4j_store()
    try:
        load_neo4j(driver, ds, parsed)
    finally:
        driver.close()
    _compose("stop")
    detail = _neo4j_disk_detail("clean", n_people)
    _compose("up", "-d")
    _wait_for_neo4j().close()

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / f"clean_{n_people}.db"
        build_sqlite(ds, parsed, db)
        sqlite_bytes = _dir_bytes(db)

    rows = [{"backend": "neo4j", "n_people": n_people, "metric": "disk_bytes_clean",
             "value": detail["total_bytes"], "unit": "bytes"},
            {"backend": "sqlite", "n_people": n_people, "metric": "disk_bytes_clean",
             "value": sqlite_bytes, "unit": "bytes"}]
    details = [detail,
               {"backend": "sqlite", "n_people": n_people, "reading": "clean",
                "total_bytes": sqlite_bytes, "store_bytes": sqlite_bytes,
                "txlog_bytes": 0, "system_store_bytes": 0, "system_txlog_bytes": 0}]
    return rows, details


# --------------------------------------------------------------------------
# 부분 결과 저장
# --------------------------------------------------------------------------

def _save_partial(payload: dict, path: Path) -> None:
    """규모 하나가 끝날 때마다 부분 결과를 남긴다.

    태스크 4에서 스윕을 두 번 돌렸다가 harness.save_result가 고정 경로에 쓰는
    바람에 첫 실행이 통째로 사라진 전례가 있다. 이 파일은 완성 아티팩트
    (exp5_persistence.json)와 **다른 경로**라 완성본을 덮어쓰지 않는다.
    커밋 대상이 아니다(재개·사후 확인용).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"partial": True,
           "note": ("중단 대비 부분 결과. 완성 아티팩트는 exp5_persistence.json이다. "
                    "environment()는 완성본에만 기록한다(규모마다 재조회하지 않는다)."),
           "data": payload}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")


# --------------------------------------------------------------------------
# 스윕
# --------------------------------------------------------------------------

def run(scales=None, neo4j: bool = True, clean_disk: bool = False,
        partial_path=None) -> dict:
    """규모별 4지표 측정.

    scales를 부분집합으로 줄 수 있다 — 스윕이 끊겼을 때 남은 규모만 다시
    돌려 이어붙이기 위해서다. partial_path를 주면 규모 하나가 끝날 때마다
    그 경로에 부분 결과를 쓴다(main()은 항상 켠다). clean_disk는 도커
    컨테이너를 내렸다 올리므로 기본값은 꺼짐이고 main()에서만 켠다.
    """
    scales = scales or RAG_SCALES
    rows, skipped, disk_detail, completed = [], [], [], []
    calibration = {"per_scale": []}

    driver, load_neo4j = None, None
    if neo4j:
        try:
            from core.graph.neo4j_store import get_driver, load_neo4j
            driver = get_driver()
            driver.verify_connectivity()
        except Exception as exc:                    # 미기동/연결 실패를 조용히 넘기지 않는다
            skipped.append({"backend": "neo4j", "n_people": "all",
                            "reason": f"{type(exc).__name__}: {exc}"})
            driver = None
    else:
        skipped.append({"backend": "neo4j", "n_people": "all",
                        "reason": "neo4j=False 로 명시적으로 제외"})
    neo4j_ok = driver is not None

    def payload() -> dict:
        return {"rows": rows, "skipped": skipped, "calibration": calibration,
                "disk_detail": disk_detail, "completed_scales": completed,
                "note": NOTE}

    def record(measured: dict, backend: str, n_people: int, metric: str) -> None:
        """harness.measure의 기록을 통째로 남긴다 — median/p95만 남기면
        min/max 스프레드(웜업이 실제로 됐는지 볼 수 있는 유일한 신호)와
        repeats/warmup 감사 근거가 사라진다."""
        rows.append({"backend": backend, "n_people": n_people, "metric": metric,
                     "value": measured["median_ms"], "unit": "ms", **measured})

    try:
        if driver is not None:
            _prime_neo4j(driver, load_neo4j)

        for n_people, n_projects in scales:
            ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)
            cal = {"n_people": n_people, "records": {}}

            # 두 백엔드에 **같은** 시퀀스를 준다. 앞 APPEND_WARMUP개는 웜업,
            # 뒤 APPEND_REPEATS개가 타이밍 대상 — warmup을 양쪽 같게 뒀으므로
            # 실제로 시간을 잰 쌍도 양쪽이 동일하다.
            used: set = set()
            n_appends = APPEND_WARMUP + APPEND_REPEATS
            cowork_recs = _cowork_records(_new_pairs(ds, n_appends, used))
            review_payloads = _review_payloads(ds, parsed,
                                               _new_pairs(ds, n_appends, used))

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # ---------------- SQLite ----------------
                db = tmp / f"p_{n_people}.db"
                m = harness.measure(lambda: build_sqlite(ds, parsed, db),
                                    repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP)
                record(m, "sqlite", n_people, "load_ms")
                rows.append({"backend": "sqlite", "n_people": n_people,
                             "metric": "disk_bytes", "value": _dir_bytes(db),
                             "unit": "bytes"})
                disk_detail.append({"backend": "sqlite", "n_people": n_people,
                                    "reading": "contaminated(해당 없음 — build_sqlite는 "
                                               "매번 파일을 지우고 새로 만든다)",
                                    "total_bytes": _dir_bytes(db),
                                    "store_bytes": _dir_bytes(db), "txlog_bytes": 0,
                                    "system_store_bytes": 0, "system_txlog_bytes": 0})

                u = _measure_sqlite_unlink(db, tmp)
                cal["sqlite_unlink_ms"] = u["median_ms"]
                cal["records"]["sqlite_unlink"] = u
                a = _measure_assemble(ds, parsed)
                cal["assemble_ms"] = a["median_ms"]
                cal["records"]["assemble"] = a

                conn = sqlite3.connect(db)
                try:
                    noop = harness.measure(lambda: conn.execute("SELECT 1").fetchall())
                    cal["sqlite_noop_ms"] = noop["median_ms"]
                    cal["records"]["sqlite_noop"] = noop

                    nxt = _feeder(cowork_recs)
                    record(harness.measure(lambda: append_cowork(conn, nxt()),
                                           repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                           "sqlite", n_people, "append_cowork_ms")
                    nxt = _feeder(review_payloads)
                    record(harness.measure(lambda: append_review(conn, *nxt()),
                                           repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                           "sqlite", n_people, "append_review_ms")
                    record(harness.measure(lambda: rehydrate.from_sqlite(conn),
                                           repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP),
                           "sqlite", n_people, "rehydrate_ms")
                finally:
                    conn.close()

                # ---------------- Neo4j ----------------
                if driver is not None:
                    from core.graph.neo4j_store import append_cowork as n_append_cowork
                    from core.graph.neo4j_store import append_review as n_append_review

                    record(harness.measure(lambda: load_neo4j(driver, ds, parsed),
                                           repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP),
                           "neo4j", n_people, "load_ms")
                    detail = _neo4j_disk_detail("contaminated", n_people)
                    rows.append({"backend": "neo4j", "n_people": n_people,
                                 "metric": "disk_bytes",
                                 "value": detail["total_bytes"], "unit": "bytes"})
                    disk_detail.append(detail)

                    d = _measure_detach_delete(driver, load_neo4j, ds, parsed)
                    cal["neo4j_detach_delete_ms"] = d["median_ms"]
                    cal["records"]["neo4j_detach_delete"] = d
                    load_neo4j(driver, ds, parsed)      # 철거 측정이 비워 둔 그래프 복구

                    for name, rec in _calibrate_neo4j(driver, ds).items():
                        cal[f"{name}_ms"] = rec["median_ms"]
                        cal["records"][name] = rec

                    nxt = _feeder(cowork_recs)
                    record(harness.measure(lambda: n_append_cowork(driver, nxt()),
                                           repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                           "neo4j", n_people, "append_cowork_ms")
                    nxt = _feeder(review_payloads)
                    record(harness.measure(lambda: n_append_review(driver, *nxt()),
                                           repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                           "neo4j", n_people, "append_review_ms")
                    record(harness.measure(lambda: rehydrate.from_neo4j(driver),
                                           repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP),
                           "neo4j", n_people, "rehydrate_ms")

            calibration["per_scale"].append(cal)
            completed.append([n_people, n_projects])
            if partial_path is not None:
                _save_partial(payload(), partial_path)
    finally:
        if driver is not None:
            driver.close()

    if clean_disk:
        if not neo4j_ok:
            skipped.append({"backend": "neo4j", "n_people": "all",
                            "reason": "clean_disk: neo4j를 쓸 수 없어 건너뛴다"})
        else:
            for n_people, n_projects in scales:
                # 규모 단위로 감싼다. 여기서 터지면(도커 재기동 실패 등) 이미 끝난
                # 본 스윕 결과까지 잃는다 — 조용히 넘기지 않고 skipped에 남겨
                # 출력·JSON 양쪽에서 보이게 한 뒤 계속한다.
                try:
                    c_rows, c_detail = _clean_slate_scale(n_people, n_projects)
                except Exception as exc:            # noqa: BLE001 -- 부분 실패 보존
                    skipped.append({"backend": "neo4j", "n_people": n_people,
                                    "reason": f"clean_disk 실패: {type(exc).__name__}: {exc}"})
                    continue
                rows += c_rows
                disk_detail += c_detail
                if partial_path is not None:
                    _save_partial(payload(), partial_path)

    return payload()


# --------------------------------------------------------------------------
# 출력
# --------------------------------------------------------------------------

_RULE2_GROUPS = (("load_ms", ["load_ms"]),
                 ("disk_bytes", None),               # 호출 시점에 어느 읽기인지 정한다
                 ("append_*_ms", ["append_cowork_ms", "append_review_ms"]),
                 ("rehydrate_ms", ["rehydrate_ms"]))


def _fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if v >= 1000 else f"{v:.3f}"


def _print_metric_table(rows) -> None:
    scales = sorted({r["n_people"] for r in rows})
    vals = {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}
    print("지표별 값 (sqlite / neo4j / 배수):")
    for m in ("load_ms", "disk_bytes", "disk_bytes_clean",
              "append_cowork_ms", "append_review_ms", "rehydrate_ms"):
        for n in scales:
            s, g = vals.get(("sqlite", m, n)), vals.get(("neo4j", m, n))
            if s is None and g is None:
                continue
            ratio = f"{g / s:.1f}x" if s and g else "-"
            print(f"  {m:<18} n={n:<5} sqlite={_fmt(s):>14}  neo4j={_fmt(g):>14}  {ratio}")


def _print_rule2_tally(rows, disk_metric: str = "disk_bytes_clean") -> int:
    """의사결정 규칙 2가 소비하는 수치: 4지표 중 Neo4j가 우위인 것이 몇 개인가.

    지표당 규모별 셀을 세고 과반이면 그 지표를 Neo4j 우위로 본다(집계 방식은
    사전 고정 규칙에 없어 여기서 정하고 리포트에 명시한다). append_*_ms는
    두 연산 × 규모 셀을 합쳐 한 지표로 센다.
    """
    vals = {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}
    scales = sorted({r["n_people"] for r in rows})
    won = 0
    print(f"규칙 2 집계 (disk 지표 = {disk_metric}):")
    for label, metrics in _RULE2_GROUPS:
        metrics = [disk_metric] if metrics is None else metrics
        cells = wins = 0
        for m in metrics:
            for n in scales:
                s, g = vals.get(("sqlite", m, n)), vals.get(("neo4j", m, n))
                if s is None or g is None:
                    continue
                cells += 1
                wins += int(g < s)
        ok = cells > 0 and wins * 2 > cells
        won += int(ok)
        print(f"  {label:<14} neo4j {wins}/{cells} 셀 우위 → {'우위' if ok else '열세'}"
              + ("" if cells else "  (측정 없음)"))
    print(f"neo4j wins: {won}/4 지표 — 규칙 2는 3 이상이면 keep")
    return won


def _print_calibration(calibration) -> None:
    print("calibration (원 수치에서 빼지 않음 — 공개용):")
    for cell in calibration["per_scale"]:
        n = cell["n_people"]
        print(f"  n={n:<5} " + "  ".join(
            f"{k}={cell[k]:.3f}" for k in sorted(cell)
            if k not in ("n_people", "records")))


def main():
    out = run(clean_disk=True, partial_path=PARTIAL_PATH)
    path = harness.save_result("exp5_persistence", out)
    print(f"saved: {path}  rows={len(out['rows'])}")
    print(f"partial: {PARTIAL_PATH} (별도 파일 — 완성본을 덮어쓰지 않는다, 커밋 대상 아님)")
    _print_metric_table(out["rows"])
    _print_rule2_tally(out["rows"], "disk_bytes")
    _print_rule2_tally(out["rows"], "disk_bytes_clean")
    _print_calibration(out["calibration"])
    if out["skipped"]:
        print("skipped:", out["skipped"])


if __name__ == "__main__":
    main()
