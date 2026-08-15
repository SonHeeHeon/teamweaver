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
     **무거운 경로만 예열하면 부족하다** — 첫 스윕에서 append_*_ms /
     neo4j_noop_match_ms / neo4j_session_open_ms 네 계열이 규모가 커질수록
     단조 감소했다(n=100 append_cowork 1.044 → n=1000 0.705). 같은 구간에서
     SQLite는 평탄하다. 이것이 이 플랜이 사전에 지목한 웜업 교락 signature다.
     그래서 _prime_neo4j는 세션 획득·두 엔드포인트 MATCH·실제 append_* 까지
     LIGHT_PRIMING_ROUNDS회 돌린다(콜드 JVM 실측 곡선으로 횟수를 정했다).
  3) 철거 비대칭 공개 — build_sqlite의 철거는 path.unlink()(O(1))인데
     load_neo4j의 철거는 MATCH (n) DETACH DELETE n(O(n))이고 둘 다 타이밍
     구간 안에 있다. 양쪽 철거 비용을 따로 재서 calibration으로 공개하되
     **원 수치에서 빼지 않는다** — 규칙은 원 수치를 센다.
  4) append 고정비 공개 — Neo4j의 append_*는 호출마다 세션을 열고 두 Person을
     MATCH한 뒤 MERGE한다. SQLite의 INSERT는 외래키가 없어 읽기가 0회이고
     conn은 타이밍 밖에서 열린다. 맨 session 획득/반납, no-op MATCH, 두
     엔드포인트 MATCH를 따로 재서 그 몫을 남긴다(태스크 5 리포트 §6(1b)).
     **이 값들은 원 수치에서 빼지 않으며, 뺄 수 있다고 주장하지도 않는다.**
     엔드포인트 MATCH는 "엔진 외 오버헤드"가 아니라 엔진·스키마의 진짜 비용
     이다 — Neo4j는 두 Person 사이의 참조 무결성을 지키고, SQLite의
     collaboration 테이블은 외래키가 없어 지키지 않는다. 같은 값을 두 배로
     쓰지 않도록 리포트도 같은 입장을 쓴다(§3.5 반사실 항목 참조).
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

append_*_ms를 "할인"할 수 있는가 — 결론: 뺄셈으로는 판정되지 않는다
  (i)  원 수치: 두 스윕 모두 SQLite가 8셀 전부 우위다.
       첫 스윕(exp5_persistence.json, n=100/300/500/1000):
         cowork  sqlite 0.255/0.244/0.228/0.233  neo4j 1.044/0.796/0.791/0.705
         review  sqlite 0.286/0.268/0.233/0.251  neo4j 2.256/0.942/0.780/0.778
       재측정(exp5_persistence_primed.json, 가벼운 경로까지 예열):
         cowork  sqlite 0.231/0.255/0.244/0.230  neo4j 0.550/0.489/0.702/0.545
         review  sqlite 0.260/0.277/0.241/0.344  neo4j 0.735/0.455/0.538/0.860
  (ii) 엔드포인트 MATCH는 빼지 않는다(위 4번). 엔진·스키마의 진짜 비용이다.
  (iii)반사실: 그래도 빼면 append_*는 뒤집힌다.
       첫 스윕: cowork 잔차 +0.087/+0.211/+0.066/+0.159로 4셀 전부 SQLite보다
       작고 review는 n=500/1000이 작다 → 8셀 중 6셀.
       재측정: cowork 잔차 +0.031/+0.086/+0.124/+0.047, review
       +0.216/+0.053/**−0.040**/+0.361 → 8셀 중 7셀. 지표가 통째로 뒤집힌다.
  (iv) 그래도 규칙 2는 어느 읽기로도 실패한다: 원 수치 0/4, 반사실 1/4
       (keep은 3 이상). 두 스윕 모두 같다. 판정은 이 뺄셈에 의존하지 않는다.
  (v)  뺄셈 자체가 못 미덥다. (a) 보정 질의 MATCH (a),(b) RETURN a, b는 노드
       레코드 2개를 실제로 반환하는데 append의 MATCH는 아무것도 반환하지
       않는다 — 빼는 값(그리고 "73~92%" 몫)이 과대다. (b) 보정이 자체 모순인
       셀이 두 스윕 모두에 있다. 첫 스윕 n=100: noop_match 1.103 >
       two_endpoint_match 0.956 > append_cowork 1.044 (MATCH 1회가 2회보다,
       또 MATCH+MERGE보다 클 수 없다). 재측정 n=500: append_review 0.538 <
       two_endpoint_match 0.578 이라 잔차가 **음수**다. 잔차(±0.04~0.36ms)가
       보정 자신의 드리프트 안에 있다 — 즉 append_*에 대해 뺄셈에 기반한
       주장은 **어느 방향으로도** 성립하지 않는다. 판정은 원 수치로 한다.

적재·재수화는 반복 비용이 크므로 repeats를 줄인다 — 방법론 일관성보다 실행
가능성을 택한 결정이며 리포트에 명시한다. repeats=3에서 p95_ms는 사실상
최댓값이다(백분위수가 아니다).
"""
import json
import shutil
import sqlite3
import statistics as st
import subprocess
import sys
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

# 가벼운 경로(세션 획득 · 두 엔드포인트 MATCH · append_*) 예열 횟수.
# 첫 스윕(exp5_persistence.json)에서 이 경로만 규모에 따라 단조 감소했다
# (append_cowork 1.044 → 0.796 → 0.791 → 0.705, noop_match 1.103 → 0.499,
# session_open 0.011 → 0.005). session_open이 절반이 되는 것은 JVM이 아니라
# 파이썬/드라이버 쪽이라, 규모당 warmup 20으로는 이 경로의 평탄 구간에 닿지
# 못했다는 뜻이다. 콜드 JVM(컨테이너 재시작 직후 + 무거운 예열 30라운드 뒤)에서
# 라운드당 1회씩 1500라운드를 개별 계측한 곡선(100라운드 블록 중앙값, ms):
#   two_endpoint_match 1.067 0.851 0.724 0.781 0.650 0.556 0.520 0.563 ... 0.551
#   append_cowork      1.214 1.014 0.857 0.928 0.855 0.699 0.632 0.730 ... 0.684
#   append_review      1.290 1.068 0.882 0.952 0.866 0.716 0.664 0.773 ... 0.746
#   session_open       0.018 0.015 0.015 0.015 0.015 0.015 0.014 0.015 ... 0.016
# 500라운드 부근에서 평탄해진다(그 전 200라운드까지도 꼬리값의 1.6~1.7배다).
# 600을 쓴다 — 평탄 구간을 넘긴 값이고 비용은 약 2초다.
LIGHT_PRIMING_ROUNDS = 600

NEO4J_DATA_DIR = REPO_ROOT / ".neo4j" / "data"

# 첫 스윕은 exp5_persistence.json에 있고 **덮어쓰지 않는다**(태스크 4에서 스윕
# 재실행이 첫 실행을 통째로 파괴한 전례가 있다). 가벼운 경로까지 예열한 재측정은
# 다른 이름으로 나간다. 이름을 재사용하더라도 harness.save_result가 이전 실행의
# 파일을 <name>.superseded-N.json으로 보존한다(최종 리뷰 T4-c).
RESULT_NAME = "exp5_persistence_primed"

# 규칙 2 판정에 쓰는 disk 읽기. 오염된 disk_bytes는 이전 규모의 잔여를 포함해
# 규모별 비교에 쓸 수 없다(§disk_bytes 오염 분리). 두 읽기 모두로 집계해 JSON에
# 남기되, 판정 기준은 이쪽이다.
RULE2_DISK_METRIC = "disk_bytes_clean"

NOTE = ("적재(load_ms)·재수화(rehydrate_ms)는 repeats=3으로 축소 측정했다 — "
        "다른 실험의 20/3과 다르며, 회당 비용이 커 전체 실행 시간을 감당할 수 "
        "없기 때문이다. 대신 warmup은 브리프의 1이 아니라 20을 썼다(웜업 부족이 "
        "Neo4j를 최대 8배 부풀린 전례가 있다). repeats=3에서 p95_ms는 백분위수가 "
        "아니라 최댓값이다. 증분 갱신은 repeats=20, warmup=20(양쪽 동일)이다. "
        "neo4j의 disk_bytes는 컨테이너 볼륨 전체라 이전 규모가 섞인 오염된 값이고, "
        "판정에는 볼륨을 지우고 규모 하나만 적재해 잰 disk_bytes_clean을 쓴다. "
        "예열은 스케일 루프 밖에서 무거운 경로(적재·재수화 30라운드)와 가벼운 경로"
        "(세션 획득·두 엔드포인트 MATCH·실제 append_* 600라운드)를 모두 돌린다 — "
        "첫 스윕(exp5_persistence.json)은 무거운 경로만 예열해 가벼운 경로에 "
        "규모 순서와 교락된 하강 램프가 남았다.")


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
    """스케일 루프 전에 JVM·드라이버를 한 번에 예열한다 — 웜업 상태가 규모
    순서와 교락되면 '규모가 커질수록 빨라지는' 가짜 추세가 만들어진다
    (플랜 2 전례). 재는 경로를 그대로 돌리고 결과는 버린다.

    **무거운 경로(적재·재수화)만으로는 부족하다.** 첫 스윕에서 가벼운 경로
    (append_*, noop MATCH, 세션 획득)만 규모에 따라 단조 감소했다 — 규모당
    warmup 20으로는 단문 왕복 경로의 평탄 구간에 닿지 못한다는 뜻이다.
    그래서 여기서 세션 획득 · 두 엔드포인트 MATCH · **실제 append_* 호출**을
    LIGHT_PRIMING_ROUNDS회 돌린다(횟수 근거는 상수 주석의 실측 곡선).

    예열이 쓰는 쓰기는 안전하다: 각 규모는 load_neo4j로 시작하고 load_neo4j는
    MATCH (n) DETACH DELETE n으로 그래프를 통째로 비운다. 예열이 만든 관계는
    측정이 시작되기 전에 사라진다. 쌍도 데이터셋에 없는 새 쌍만 쓴다.

    SQLite 쪽은 예열하지 않는다 — 첫 스윕에서 SQLite의 append 계열은 평탄했고
    (0.255/0.244/0.228/0.233), 연결 객체는 규모마다 새로 만들어 스케일 간
    예열이 넘어가지도 않는다(규모당 APPEND_WARMUP=20이 그 자리를 맡는다).
    비대칭 예열이지만 방향은 **Neo4j에 유리**하다.
    """
    from core.graph.neo4j_store import append_cowork as n_append_cowork
    from core.graph.neo4j_store import append_review as n_append_review

    ds, parsed, _ = datasets.build_scale(*PRIMING_SCALE, 42)
    for _ in range(PRIMING_ROUNDS):
        load_fn(driver, ds, parsed)
        rehydrate.from_neo4j(driver)

    used: set = set()
    recs = _cowork_records(_new_pairs(ds, LIGHT_PRIMING_ROUNDS, used))
    payloads = _review_payloads(ds, parsed, _new_pairs(ds, LIGHT_PRIMING_ROUNDS, used))
    a_id, b_id = ds.people[0].id, ds.people[-1].id
    for rec, (review, pr) in zip(recs, payloads):
        with driver.session():                      # 세션 획득/반납
            pass
        with driver.session() as s:                 # 두 엔드포인트 MATCH
            s.run("MATCH (a:Person {id:$a}), (b:Person {id:$b}) RETURN a, b",
                  a=a_id, b=b_id).consume()
        n_append_cowork(driver, rec)                # 실제 append 경로
        n_append_review(driver, review, pr)

    # 보정 경로도 나중에 시간을 재는 경로다 — 예열하지 않으면 첫 규모의
    # calibration만 부풀어 규모 순서와 교락된다(첫 스윕: session_open 0.011 →
    # 0.005). 위 루프의 인라인 세션/MATCH와 코드 객체가 달라 따로 돌려야 한다.
    # 결과는 버린다.
    _calibrate_neo4j(driver, ds)


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

    **이 값은 공개용이고 원 수치에서 빼지 않는다.** 뺄셈이 못 미더운 이유가
    코드에 그대로 보인다: _two_endpoint_match는 RETURN a, b로 **노드 레코드
    2개를 실제로 반환**하는데 append_*의 MATCH는 아무것도 반환하지 않는다
    (뒤에 MERGE가 이어진다). 즉 이 보정값은 append 안의 MATCH 비용보다
    **과대**하고, 그것으로 계산한 "몫(%)"도 과대다. 실제로 n=100에서는
    noop_match > two_endpoint_match > append_cowork가 나왔다 — MATCH 1회가
    2회보다, 또 MATCH+MERGE보다 클 수는 없으므로 보정 자체의 드리프트가
    잔차보다 크다는 뜻이다(모듈 docstring "append_*_ms를 할인할 수 있는가").
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


def _check_wipe_target(path: Path) -> Path:
    """rm -rf 대상이 정말 그 디렉터리인지 확인한다(아니면 raise).

    `assert NEO4J_DATA_DIR == REPO_ROOT / ".neo4j" / "data"`는 상수를 정의한
    바로 그 식을 다시 쓴 것이라 절대 실패하지 않고, python -O에서는 사라진다.
    재귀 삭제의 전제를 실제로 검사한다: 심링크가 아니고, 이름이 data이고,
    부모가 .neo4j이고, 그 부모가 리포 루트여야 한다. 위반이면 지우지 않는다.
    """
    root = REPO_ROOT.resolve()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError(f"삭제 거부: 심링크다 — {path}")
    resolved = path.resolve()
    if resolved.name != "data" or resolved.parent.name != ".neo4j":
        raise RuntimeError(f"삭제 거부: .neo4j/data 형태가 아니다 — {resolved}")
    if resolved.parent.parent != root:
        raise RuntimeError(f"삭제 거부: 리포 루트({root}) 밖이다 — {resolved}")
    return resolved


def _reset_neo4j_store():
    """볼륨을 비우고 새 인스턴스를 띄운다.

    docker-compose.yml의 볼륨은 바인드 마운트(./.neo4j/data:/data)라
    `down -v`만으로는 지워지지 않는다 — 컨테이너를 내린 뒤 디렉터리를 직접
    비운다. .neo4j/는 gitignore 대상이고 모든 실험이 실행 시점에 적재하므로
    잃는 것은 없다.

    비우기가 부분 실패하면 그 뒤의 disk_bytes_clean은 조용히 오염된 값이 된다
    — 규칙 2가 판정에 쓰는 바로 그 수치다. 그래서 실패를 삼키지 않고(과거
    ignore_errors=True), 비운 **뒤에 실제로 비었는지** 확인해 아니면 raise한다.
    """
    _compose("down", "-v")
    target = _check_wipe_target(NEO4J_DATA_DIR)
    if target.exists():
        shutil.rmtree(target)                       # 실패는 삼키지 않는다
    target.mkdir(parents=True, exist_ok=True)
    leftover = sorted(p.name for p in target.iterdir())
    if leftover:
        raise RuntimeError(
            f"클린 슬레이트 실패: {target}가 비워지지 않았다 — {leftover[:5]}"
            f"{'...' if len(leftover) > 5 else ''}. 이대로 재면 disk_bytes_clean이 "
            "조용히 오염된다.")
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
        # 규칙 2 집계를 **JSON에 넣는다** — stdout에만 찍으면 태스크 7이
        # 인용할 숫자에 아티팩트가 없다. 두 disk 읽기로 모두 세고 어느 쪽이
        # 판정 기준인지도 같이 남긴다.
        return {"rows": rows, "skipped": skipped, "calibration": calibration,
                "disk_detail": disk_detail, "completed_scales": completed,
                "rule2_tally": {"adjudicated_on": RULE2_DISK_METRIC,
                                "by_disk_reading": {m: _rule2_tally(rows, m)
                                                    for m in _DISK_READINGS}},
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
_DISK_READINGS = ("disk_bytes", "disk_bytes_clean")


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


def _rule2_tally(rows, disk_metric: str = RULE2_DISK_METRIC) -> dict:
    """의사결정 규칙 2가 소비하는 수치: 4지표 중 Neo4j가 우위인 것이 몇 개인가.

    지표당 규모별 셀을 세고 과반이면 그 지표를 Neo4j 우위로 본다(집계 방식은
    사전 고정 규칙에 없어 여기서 정하고 리포트에 명시한다). append_*_ms는
    두 연산 × 규모 셀을 합쳐 한 지표로 센다.

    **결과를 dict로 돌려주고 JSON에 넣는다.** 예전에는 stdout에만 찍혀서
    태스크 7이 인용할 숫자에 아티팩트가 없었다 — 어느 disk 읽기로 셌는지까지
    같이 남긴다.
    """
    vals = {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}
    scales = sorted({r["n_people"] for r in rows})
    per_metric, won = [], 0
    for label, metrics in _RULE2_GROUPS:
        counted = [disk_metric] if metrics is None else metrics
        cells = wins = 0
        for m in counted:
            for n in scales:
                s, g = vals.get(("sqlite", m, n)), vals.get(("neo4j", m, n))
                if s is None or g is None:
                    continue
                cells += 1
                wins += int(g < s)
        ok = cells > 0 and wins * 2 > cells
        won += int(ok)
        per_metric.append({"metric": label, "counted_metrics": counted,
                           "cells": cells, "neo4j_wins": wins, "neo4j_ahead": ok})
    return {"disk_metric": disk_metric, "per_metric": per_metric,
            "neo4j_metric_wins": won, "metrics": len(_RULE2_GROUPS),
            "keep_threshold": 3, "keep": won >= 3}


def _print_rule2_tally(tally: dict) -> int:
    print(f"규칙 2 집계 (disk 지표 = {tally['disk_metric']}):")
    for m in tally["per_metric"]:
        print(f"  {m['metric']:<14} neo4j {m['neo4j_wins']}/{m['cells']} 셀 우위 → "
              f"{'우위' if m['neo4j_ahead'] else '열세'}"
              + ("" if m["cells"] else "  (측정 없음)"))
    print(f"neo4j wins: {tally['neo4j_metric_wins']}/{tally['metrics']} 지표 — "
          f"규칙 2는 {tally['keep_threshold']} 이상이면 keep "
          f"→ {'keep' if tally['keep'] else '실패'}")
    return tally["neo4j_metric_wins"]


def _print_calibration(calibration) -> None:
    print("calibration (원 수치에서 빼지 않음 — 공개용):")
    for cell in calibration["per_scale"]:
        n = cell["n_people"]
        print(f"  n={n:<5} " + "  ".join(
            f"{k}={cell[k]:.3f}" for k in sorted(cell)
            if k not in ("n_people", "records")))


def partial_path_for(name: str) -> Path:
    return harness.RESULTS_DIR / f"{name}.partial.json"


def main(name: str = RESULT_NAME):
    """스윕 1회를 돌려 experiments/results/<name>.json에 남긴다.

    **이전 실행의 원자료는 파괴되지 않는다.** 태스크 4에서 harness.save_result가
    고정 경로에 쓰는 바람에 재실행이 첫 스윕을 통째로 파괴한 전례가 있어 이 러너가
    자기 main()에 로컬 가드(거부)를 달고 있었는데, 최종 리뷰 T4-c에서 그 보호를
    `harness.save_result` 자체로 올렸다 — 이제 같은 이름으로 다시 돌리면 이전
    파일이 `<name>.superseded-N.json`으로 보존된다. 여기서 같은 검사를 한 번 더
    하지 않는다(두 겹이면 어느 쪽이 실제로 지키는지가 흐려진다).
    """
    partial = partial_path_for(name)
    out = run(clean_disk=True, partial_path=partial)
    path = harness.save_result(name, out)
    print(f"saved: {path}  rows={len(out['rows'])}")
    print(f"partial: {partial} (별도 파일 — 완성본을 덮어쓰지 않는다, 커밋 대상 아님)")
    _print_metric_table(out["rows"])
    for reading in _DISK_READINGS:
        _print_rule2_tally(out["rule2_tally"]["by_disk_reading"][reading])
    print(f"판정 기준 읽기: {out['rule2_tally']['adjudicated_on']} (JSON에 그대로 저장됨)")
    _print_calibration(out["calibration"])
    if out["skipped"]:
        print("skipped:", out["skipped"])
    return out


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(_args[0] if _args else RESULT_NAME)
