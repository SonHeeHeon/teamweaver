"""실험 5: 영속성 (SQLite).

인메모리 계층에는 영속성이 없다 — 재시작하면 저장소에서 전량 다시 읽어야 한다.
그래서 저장소의 가치는 탐색 속도가 아니라 (1) 적재 (2) 공간 (3) 증분 갱신
(4) 재수화 에서 나온다. 실험 1·4가 재지 않은 축이다.

Plan 3에서는 이 4지표로 두 저장소를 비교했고(의사결정 규칙 2: 4지표 중 3개
이상 우위면 keep), 판정 결과가 experiments/results/exp5_persistence.json /
exp5_persistence_primed.json에 고정돼 있다. Plan 4에서 그 드라이버 의존을
제거하며 이 러너도 비교 기능 없이 SQLite 단독 측정으로 축소한다 — 커밋된
JSON은 판정의 근거이므로 이 축소와 무관하게 그대로 둔다. 이 러너를 다시
돌려도 그 파일들은 바뀌지 않는다(harness.save_result가 이전 실행을
`.superseded-N.json`으로 보존한 뒤에만 새로 쓰고, main()도 다른 이름
(exp5_persistence_sqlite_only)으로 저장한다).

타이밍 대상 레코드에 관한 공정성 장치 하나는 여전히 유효하다 — 매 반복마다
**새 쌍**을 쓴다. 같은 (a, b)를 재사용하면 SQLite는 review_item을 계속
누적해 ReviewSection(max_length=5) 위반으로 from_sqlite가 죽고, csr_matrix가
중복 좌표를 합산해 cowork_months가 조용히 불어난다(§_new_pairs 주석 참고).
_new_pairs가 데이터셋에 방향 무관으로 없는 쌍을 결정적 순서로 뽑는다.

적재·재수화는 반복 비용이 크므로 repeats를 줄인다 — 방법론 일관성보다 실행
가능성을 택한 결정이며 NOTE에 명시한다. repeats=3에서 p95_ms는 사실상
최댓값이다(백분위수가 아니다).
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from core.domain.models import CoworkRecord
from core.graph import rehydrate
from core.graph.sqlite_store import append_cowork, append_review, build_sqlite
from experiments.bench import datasets, harness

RAG_SCALES = [(100, 20), (300, 60), (500, 100), (1000, 200)]

# 적재·재수화: 회당 비용이 커 반복을 줄인다(다른 실험은 20/3). repeats=3에서
# median은 3표본의 가운데 값이고 p95_ms는 최댓값과 같다.
HEAVY_REPEATS = 3
# 웜업 부족이 초기 측정을 크게 부풀리는 전례가 이 프로젝트에 있었다(실측 곡선
# 근거는 experiments/optimization_report.md 참고). 20이면 곡선이 평탄해진
# 뒤다.
HEAVY_WARMUP = 20

# 증분 갱신: 다른 실험과 같은 repeats=20, warmup=20.
APPEND_REPEATS = 20
APPEND_WARMUP = 20
APPEND_CO_MONTHS = 3
APPEND_PROJECT_COUNT = 1

# 첫 스윕은 exp5_persistence.json에 있고 **덮어쓰지 않는다**(태스크 4에서 스윕
# 재실행이 첫 실행을 통째로 파괴한 전례가 있다). 이 축소된 러너는 다른 이름으로
# 나간다. 이름을 재사용하더라도 harness.save_result가 이전 실행의 파일을
# <name>.superseded-N.json으로 보존한다(최종 리뷰 T4-c).
RESULT_NAME = "exp5_persistence_sqlite_only"

NOTE = ("Plan 4에서 저장소 드라이버 의존을 제거하며 이 러너를 SQLite 단독 "
        "측정으로 축소했다. 의사결정 규칙 2(영속성 4지표 비교)의 판정 근거는 "
        "Plan 3이 이미 커밋한 exp5_persistence.json / "
        "exp5_persistence_primed.json에 고정돼 있고 이 축소와 무관하게 그대로 "
        f"남는다. 적재(load_ms)·재수화(rehydrate_ms)는 repeats={HEAVY_REPEATS}로 "
        "축소 측정했다 — 다른 실험의 20/3과 다르며, 회당 비용이 커 전체 실행 "
        f"시간을 감당할 수 없기 때문이다. warmup은 {HEAVY_WARMUP}을 쓴다(웜업 "
        "부족이 초기 측정을 부풀리는 전례가 있다). "
        f"repeats={HEAVY_REPEATS}에서 p95_ms는 백분위수가 아니라 최댓값이다. "
        f"증분 갱신은 repeats={APPEND_REPEATS}, warmup={APPEND_WARMUP}이다.")


# --------------------------------------------------------------------------
# 크기 측정
# --------------------------------------------------------------------------

def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


# --------------------------------------------------------------------------
# 타이밍 대상 레코드 — 매 반복 새 쌍
# --------------------------------------------------------------------------

def _new_pairs(ds, count: int, used: set) -> list[tuple[str, str]]:
    """데이터셋에 (방향 무관) 없고 서로도 겹치지 않는 (a, b) 쌍 count개.

    왜 필요한가: harness.measure는 warmup + repeats회 fn을 호출한다. 같은
    쌍을 그 횟수만큼 재사용하면
      - SQLite append_review는 review_item을 계속 쌓고, from_sqlite가 한 쌍의
        항목을 한 리스트로 누적하므로 6번째부터 ReviewSection(max_length=5)를
        위반해 **재수화 측정 자체가 크래시**한다,
      - append_cowork는 collaboration에 행을 더 쌓고 csr_matrix가 중복 좌표를
        **합산**해 cowork_months가 조용히 불어난다.
    tests/test_rehydrate.py의 _assert_pair_is_new와 같은 기준으로 거른다 —
    pair_review_score의 키가 방향 무관이므로(core/graph/memory_graph.py:47)
    협업·리뷰 양쪽 다 frozenset으로 본다.

    쌍은 실재하는 Person id로만 만든다 — person 테이블에 없는 id로 협업·리뷰를
    추가하면 이 실험이 재려는 "실제 존재하는 두 사람 사이의 갱신" 워크로드가
    아니게 되고, 재수화 시 person 테이블 밖을 가리키는 고아 레코드가 생긴다.

    순서는 결정적이다(간격 내림차순 → 인덱스 오름차순). 재현 가능해야 한다.
    used를 호출 간에 넘겨 협업용 쌍과 리뷰용 쌍이 서로 겹치지 않게 한다.
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

    항목 수·극성이 실제 워크로드와 같아야 한다. 원본은 건드리지 않는다
    (model_copy는 새 객체를 만든다) — datasets.build_scale은 lru_cache 공유다.
    """
    src_review, src_parsed = ds.reviews[0], parsed[0]
    return [(src_review.model_copy(update={"reviewer_id": a, "reviewee_id": b}),
             src_parsed.model_copy(update={"reviewer_id": a, "reviewee_id": b}))
            for a, b in pairs]


def _feeder(items):
    """호출마다 다음 원소를 주는 함수. 타이밍 구간 안에서 도는 유일한 추가
    비용이다(리스트 이터레이터의 __next__, 수십 나노초)."""
    return iter(items).__next__


# --------------------------------------------------------------------------
# 보정
# --------------------------------------------------------------------------

def _measure_sqlite_unlink(db: Path, tmp: Path) -> dict:
    """build_sqlite의 철거 비용(path.unlink)을 잰다. 사본을 미리 만들어
    두고(타이밍 밖) 삭제만 잰다."""
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

def _fresh_scale(n_people: int, n_projects: int) -> list[dict]:
    """오염 걱정이 없는 한 규모의 disk_bytes.

    SQLite는 build_sqlite가 매번 파일을 지우고 새로 만들므로 원래 클린이다
    (이전 규모의 잔여가 섞일 수 없다) — 별도 인스턴스 초기화가 필요 없다.
    그래도 metric 이름(disk_bytes_clean)은 Plan 3이 커밋한 JSON
    (exp5_persistence*.json)과 계속 맞춰 둔다.
    """
    ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / f"clean_{n_people}.db"
        build_sqlite(ds, parsed, db)
        sqlite_bytes = _dir_bytes(db)
    return [{"backend": "sqlite", "n_people": n_people, "metric": "disk_bytes_clean",
             "value": sqlite_bytes, "unit": "bytes"}]


# --------------------------------------------------------------------------
# 부분 결과 저장
# --------------------------------------------------------------------------

def _save_partial(payload: dict, path: Path) -> None:
    """규모 하나가 끝날 때마다 부분 결과를 남기는 데 쓰던 유틸리티.

    Plan 4에서 run()의 시그니처가 `run(scales=None) -> dict`로 축소되며 이
    파일 안에서는 더 이상 호출되지 않는다(스윕이 순수 SQLite/Python이 되어
    도커 컨테이너 재기동에 기인하던 중단 위험이 사라졌다). 그래도 브리핑의
    "SQLite 전용으로 남는 것" 목록이 이 함수를 명시적으로 지정하므로 삭제하지
    않는다 — 재개 가능한 스윕이 다시 필요해지면 재사용할 수 있는 일반 유틸
    리티다.

    태스크 4에서 스윕을 두 번 돌렸다가 harness.save_result가 고정 경로에
    쓰는 바람에 첫 실행이 통째로 사라진 전례가 있다. 이 함수가 쓰는 경로는
    완성 아티팩트(exp5_persistence.json)와 다른 경로라 완성본을 덮어쓰지
    않는다. 커밋 대상이 아니다(재개·사후 확인용).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"partial": True,
           "note": "중단 대비 부분 결과. 완성 아티팩트는 별도 파일이다.",
           "data": payload}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")


# --------------------------------------------------------------------------
# 스윕
# --------------------------------------------------------------------------

def run(scales=None) -> dict:
    """규모별 4지표(load_ms / disk_bytes / append_*_ms / rehydrate_ms) 측정.

    scales를 부분집합으로 줄 수 있다.
    """
    scales = scales or RAG_SCALES
    rows: list[dict] = []

    def record(measured: dict, n_people: int, metric: str) -> None:
        """harness.measure의 기록을 통째로 남긴다 — median/p95만 남기면
        min/max 스프레드(웜업이 실제로 됐는지 볼 수 있는 유일한 신호)와
        repeats/warmup 감사 근거가 사라진다."""
        rows.append({"backend": "sqlite", "n_people": n_people, "metric": metric,
                     "value": measured["median_ms"], "unit": "ms", **measured})

    for n_people, n_projects in scales:
        ds, parsed, _ = datasets.build_scale(n_people, n_projects, 42)

        # 앞 APPEND_WARMUP개는 웜업, 뒤 APPEND_REPEATS개가 타이밍 대상.
        used: set = set()
        n_appends = APPEND_WARMUP + APPEND_REPEATS
        cowork_recs = _cowork_records(_new_pairs(ds, n_appends, used))
        review_payloads = _review_payloads(ds, parsed, _new_pairs(ds, n_appends, used))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db = tmp / f"p_{n_people}.db"
            m = harness.measure(lambda: build_sqlite(ds, parsed, db),
                                repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP)
            record(m, n_people, "load_ms")
            rows.append({"backend": "sqlite", "n_people": n_people,
                         "metric": "disk_bytes", "value": _dir_bytes(db),
                         "unit": "bytes"})

            record(_measure_sqlite_unlink(db, tmp), n_people, "sqlite_unlink_ms")
            record(_measure_assemble(ds, parsed), n_people, "assemble_ms")

            conn = sqlite3.connect(db)
            try:
                noop = harness.measure(lambda: conn.execute("SELECT 1").fetchall())
                record(noop, n_people, "sqlite_noop_ms")

                nxt = _feeder(cowork_recs)
                record(harness.measure(lambda: append_cowork(conn, nxt()),
                                       repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                       n_people, "append_cowork_ms")
                nxt = _feeder(review_payloads)
                record(harness.measure(lambda: append_review(conn, *nxt()),
                                       repeats=APPEND_REPEATS, warmup=APPEND_WARMUP),
                       n_people, "append_review_ms")
                record(harness.measure(lambda: rehydrate.from_sqlite(conn),
                                       repeats=HEAVY_REPEATS, warmup=HEAVY_WARMUP),
                       n_people, "rehydrate_ms")
            finally:
                conn.close()        # 디렉터리 삭제 전에 핸들부터 닫는다

        rows += _fresh_scale(n_people, n_projects)

    return {"rows": rows, "note": NOTE}


# --------------------------------------------------------------------------
# 출력
# --------------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if v >= 1000 else f"{v:.3f}"


def _print_metric_table(rows) -> None:
    scales = sorted({r["n_people"] for r in rows})
    vals = {(r["metric"], r["n_people"]): r["value"] for r in rows}
    metrics = sorted({r["metric"] for r in rows})
    print("지표별 값 (sqlite):")
    for m in metrics:
        for n in scales:
            v = vals.get((m, n))
            if v is None:
                continue
            print(f"  {m:<20} n={n:<5} sqlite={_fmt(v):>14}")


def main(name: str = RESULT_NAME):
    """스윕 1회를 돌려 experiments/results/<name>.json에 남긴다.

    **이전 실행의 원자료는 파괴되지 않는다.** 태스크 4에서 harness.save_result가
    고정 경로에 쓰는 바람에 재실행이 첫 스윕을 통째로 파괴한 전례가 있어 이 러너가
    자기 main()에 로컬 가드(거부)를 달고 있었는데, 최종 리뷰 T4-c에서 그 보호를
    `harness.save_result` 자체로 올렸다 — 이제 같은 이름으로 다시 돌리면 이전
    파일이 `<name>.superseded-N.json`으로 보존된다. 여기서 같은 검사를 한 번 더
    하지 않는다(두 겹이면 어느 쪽이 실제로 지키는지가 흐려진다).
    """
    out = run()
    path = harness.save_result(name, out)
    print(f"saved: {path}  rows={len(out['rows'])}")
    _print_metric_table(out["rows"])
    return out


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(_args[0] if _args else RESULT_NAME)
