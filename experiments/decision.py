"""저장 계층 의사결정 — 플랜에 사전 고정된 규칙을 코드로 옮긴 것.

측정 후에 기준을 만들면 어떤 숫자든 정당화할 수 있다. 그래서 규칙을 먼저 쓰고
(플랜 §"의사결정 규칙"), 데이터를 넣으면 판정이 나오게 한다. 이 모듈에는
하드코딩된 실험 수치가 없다 — 전부 커밋된 `experiments/results/*.json`과
커밋된 소스(`core/rag/`)에서 읽는다.

규칙 (측정 전에 문서로 고정, 사후 변경 금지):
  1. RAG 20개 셀 중 neo4j가 과반(11+) 빠르면          -> keep (성능)
  2. 아니면, 영속성 4지표 중 3+에서 우위면             -> keep (영속성)
  3. 아니면, SQL이 재귀 CTE/다중 서브쿼리를 요구하는
     질의가 5종 중 3+ 이면                              -> keep_expressiveness
  4. 그 외                                              -> drop
     (Neo4j·docker-compose·드라이버 의존 **제거 계획 동반 제시**)

구현이 브리프 참조 코드와 다른 네 곳 — 전부 규칙을 사후 변경하지 않기 위한 것:

(a) **방향**. 참조 코드는 lower-is-better 지표를 손으로 나열한 집합으로 관리했고
    거기서 `disk_bytes_clean`이 빠져 있었다. 실제 exp5 rows에 있는 이름이라
    "크면 좋다" 분기로 떨어져 Neo4j의 541 MB가 SQLite의 147 KB를 이겼다 —
    규칙 2를 판정하는 바로 그 지표에서. 이 실험의 모든 지표는 작을수록 좋다.
    모르는 지표는 방향을 추측하지 않고 raise한다(조용히 틀리는 대신 크게 깨진다).

(b) **분모**. exp5 rows에는 지표 이름이 여섯 개 있다(load_ms / disk_bytes /
    disk_bytes_clean / append_cowork_ms / append_review_ms / rehydrate_ms).
    이름마다 하나씩 세면 분모가 조용히 6이 되어 사전 고정된 "4지표 중 3+"가
    "6지표 중 3+"로 바뀐다. append_*는 "증분 갱신 비용" 한 지표로 합치고,
    disk는 한 판독만 쓴다 — 정확히 4지표여야 하며 아니면 raise한다.

(c) **집계**. 참조 코드는 규모별 값을 평균낸 뒤 비교했다. Task 6이 JSON에 남긴
    집계(`rule2_tally`)는 **규모 셀의 과반**으로 센다. 한쪽 규모의 이상치가
    평균을 뒤집을 수 있어 두 방식은 갈릴 수 있다. 여기서는 Task 6과 같은 방식을
    쓰고, tests/test_decision.py가 커밋된 JSON의 tally와 지표별로 대조한다.

(d) **규칙 3의 두 해석**. enum 라벨(`SPECS[q].sql_complexity`) 기준과, 규칙 3의
    문자 그대로의 프로즈("재귀 CTE나 3중 이상 서브쿼리") 기준을 **둘 다** 계산해
    근거에 남긴다. 판정에는 둘 중 Neo4j에 유리한(큰) 쪽을 쓴다 — 불리한 해석을
    골라 drop을 만들었다는 의심을 남기지 않기 위해서다.
"""
import inspect
import re

from core.rag import sqlite_rag
from core.rag.queries import RAG_QUERIES, SPECS

MAJORITY_CELLS = 11
TOTAL_CELLS = 20
PERSISTENCE_THRESHOLD = 3
PERSISTENCE_METRICS = 4
COMPLEXITY_THRESHOLD = 3

# 이 실험의 모든 지표는 **작을수록 좋다** — 지연(ms)과 디스크 사용량(bytes).
# 멤버십 집합으로 방향을 고르지 않고, 알려진 이름 전체를 여기 두고 모르는 이름은
# 거부한다(브리프 구현은 빠진 이름을 조용히 "크면 좋다"로 처리했다).
LOWER_IS_BETTER = frozenset({
    "load_ms", "disk_bytes", "disk_bytes_clean",
    "append_cowork_ms", "append_review_ms", "rehydrate_ms",
})

# 규칙 2가 세는 **4지표**. disk는 판독(오염 누적 vs 클린 슬레이트)이 두 가지라
# 실행 시점에 고른다. append_*는 사전 고정 규칙의 "증분 갱신 비용" 한 지표다.
_RULE2_GROUPS = (("load_ms", ("load_ms",)),
                 ("disk_bytes", None),
                 ("append_*_ms", ("append_cowork_ms", "append_review_ms")),
                 ("rehydrate_ms", ("rehydrate_ms",)))
# 우선순위 순서. 클린 판독이 있으면 그쪽으로 판정한다(A-11) — 누적 판독은 단조
# 증가해 규모별 비교에 쓸 수 없다. 다만 지표를 통째로 빼지는 않는다. 빼면 규칙 2가
# 몰래 "3 of 3"이 되어 사전 고정 임계를 사후 변경하는 셈이 된다.
_DISK_READINGS = ("disk_bytes_clean", "disk_bytes")


# ---------------------------------------------------------------------------
# 입력 경계 — 커밋된 아티팩트의 래퍼를 여기서 한 번만 벗긴다
# ---------------------------------------------------------------------------

def payload(doc: dict) -> dict:
    """`harness.save_result`가 씌운 래퍼를 벗겨 안쪽 payload를 돌려준다.

    커밋된 파일은 `{"environment", "generated_at_note", "data"}` 형태이고
    러너가 방금 돌려준 값과 테스트 픽스처는 안쪽 payload 그대로다. evaluate는
    안쪽만 보게 하고, 두 형태의 차이는 이 경계 함수 하나가 흡수한다 — 안 그러면
    `exp4["rows"]`가 실제 파일에서 KeyError로 터진다.
    """
    inner = doc.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("rows"), list):
        return inner
    return doc


# ---------------------------------------------------------------------------
# 규칙 1 — RAG 성능
# ---------------------------------------------------------------------------

def _neo4j_faster_cells(exp4: dict) -> tuple[int, int]:
    """(질의, 규모) 셀마다 median_ms를 맞대어 neo4j가 빠른 셀 수를 센다."""
    by_cell: dict[tuple, dict[str, float]] = {}
    for r in exp4["rows"]:
        by_cell.setdefault((r["query"], r["n_people"]), {})[r["backend"]] = r["median_ms"]
    wins = total = 0
    for cell in by_cell.values():
        if "neo4j" in cell and "sqlite" in cell:
            total += 1
            if cell["neo4j"] < cell["sqlite"]:
                wins += 1
    if total != TOTAL_CELLS:
        raise ValueError(
            f"규칙 1은 '{TOTAL_CELLS}개 셀 중 {MAJORITY_CELLS}+'라는 절대 임계로 "
            f"사전 고정돼 있는데 비교 가능한 셀이 {total}개다 — 부분 스윕에 이 "
            "임계를 그대로 적용하면 규칙의 의미가 달라진다.")
    return wins, total


# ---------------------------------------------------------------------------
# 규칙 2 — 영속성
# ---------------------------------------------------------------------------

def _neo4j_better(metric: str, neo: float, sql: float) -> bool:
    if metric not in LOWER_IS_BETTER:
        raise ValueError(
            f"방향을 모르는 지표: {metric!r}. 이 실험의 지표는 전부 작을수록 "
            f"좋다고 선언돼 있다(LOWER_IS_BETTER). 새 지표를 추가했다면 방향을 "
            "명시할 것 — 추측하면 규칙 2가 조용히 뒤집힌다.")
    return neo < sql


def _disk_reading(rows: list[dict]) -> str:
    """규칙 2가 쓸 disk 판독을 고른다. 클린 슬레이트 판독이 있으면 그쪽."""
    present = {r["metric"] for r in rows}
    for name in _DISK_READINGS:
        if name in present:
            return name
    return _DISK_READINGS[-1]


def _rule2_tally(exp5: dict) -> dict:
    """4지표 각각에 대해 규모 셀의 과반으로 Neo4j 우위 여부를 판정한다.

    Task 6의 `exp5_persistence._rule2_tally`와 같은 방식이며, 두 계산이 커밋된
    JSON에서 지표별로 일치하는지 tests/test_decision.py가 대조한다.
    """
    rows = exp5["rows"]
    disk_metric = _disk_reading(rows)
    vals = {(r["backend"], r["metric"], r["n_people"]): r["value"] for r in rows}
    scales = sorted({r["n_people"] for r in rows})

    per_metric, won = [], 0
    for label, metrics in _RULE2_GROUPS:
        counted = [disk_metric] if metrics is None else list(metrics)
        cells = wins = 0
        for m in counted:
            for n in scales:
                s, g = vals.get(("sqlite", m, n)), vals.get(("neo4j", m, n))
                if s is None or g is None:
                    continue
                cells += 1
                wins += int(_neo4j_better(m, g, s))
        ok = cells > 0 and wins * 2 > cells
        won += int(ok)
        per_metric.append({"metric": label, "counted_metrics": counted,
                           "cells": cells, "neo4j_wins": wins, "neo4j_ahead": ok})

    # 방향을 모르는 지표가 rows에 섞여 있으면 위 루프가 지나칠 수 있다(그룹에
    # 속하지 않는 이름). 규칙이 못 보는 지표가 조용히 있는 상태로 판정하지 않는다.
    unknown = {r["metric"] for r in rows} - LOWER_IS_BETTER
    if unknown:
        raise ValueError(
            f"방향을 모르는 지표: {sorted(unknown)!r}. 이 실험의 지표는 전부 "
            "작을수록 좋다고 선언돼 있다(LOWER_IS_BETTER).")

    measured = [m for m in per_metric if m["cells"]]
    if len(measured) != PERSISTENCE_METRICS:
        raise ValueError(
            f"규칙 2는 '영속성 {PERSISTENCE_METRICS}지표 중 "
            f"{PERSISTENCE_THRESHOLD}+'로 사전 고정돼 있는데 측정된 지표가 "
            f"{len(measured)}개다({[m['metric'] for m in per_metric]}) — 분모가 "
            "바뀌면 사전 고정 임계를 사후 변경하는 셈이다.")
    return {"disk_reading": disk_metric, "per_metric": per_metric,
            "neo4j_metric_wins": won, "metrics": len(per_metric)}


# ---------------------------------------------------------------------------
# 규칙 3 — 표현력 (두 해석)
# ---------------------------------------------------------------------------

_SELECT_IN_PARENS = re.compile(r"\(\s*SELECT\b", re.IGNORECASE)
_CTE_BODY = re.compile(r"\bAS\s*\(\s*SELECT\b", re.IGNORECASE)
_RECURSIVE = re.compile(r"\bWITH\s+RECURSIVE\b", re.IGNORECASE)


def sql_structure() -> dict[str, dict]:
    """커밋된 SQLite 구현의 **실제 SQL**에서 구조를 센다(라벨을 다시 읽지 않는다).

    규칙 3의 문언은 "SQL이 재귀 CTE나 3중 이상 서브쿼리를 요구하는 질의"다.
    `SPECS[q].sql_complexity`는 세 값짜리 enum이라 서브쿼리 **개수**를 담지
    못한다 — `team_cohesion`은 남는 값인 `multi_subquery`로 분류돼 있지만 실제
    상관 서브쿼리는 **1개**다(core/rag/queries.py의 주석이 그 사실을 이미
    밝혀 두었다). 그래서 프로즈 해석은 소스에서 직접 센다.

    세는 방법: 괄호 뒤에 오는 SELECT를 서브쿼리로 보되, CTE 정의(`... AS (SELECT`)
    는 서브쿼리가 아니므로 뺀다. UNION ALL로 이어붙인 최상위 SELECT는 괄호가
    없으므로 애초에 세지 않는다. 형식이 바뀌면 tests/test_decision.py가
    깨진다(라벨과의 교차 검증 포함).
    """
    out = {}
    for q in RAG_QUERIES:
        src = inspect.getsource(sqlite_rag.ALL[q])
        subqueries = len(_SELECT_IN_PARENS.findall(src)) - len(_CTE_BODY.findall(src))
        out[q] = {"subqueries": subqueries,
                  "recursive_cte": bool(_RECURSIVE.search(src)),
                  "label": SPECS[q].sql_complexity}
    return out


def _complex_sql_queries() -> dict:
    """규칙 3의 두 해석을 모두 계산한다(A-6).

    - enum 해석: `sql_complexity in ("recursive_cte", "multi_subquery")` -> 2/5
    - 프로즈 해석: 재귀 CTE **또는** 서브쿼리 3개 이상 -> 1/5
    둘 다 임계(3) 미달이라 판정은 바뀌지 않지만, 판정에 쓰는 값은 Neo4j에
    **유리한**(큰) 쪽으로 둔다.
    """
    structure = sql_structure()
    enum_hits = [q for q in RAG_QUERIES
                 if SPECS[q].sql_complexity in ("recursive_cte", "multi_subquery")]
    literal_hits = [q for q in RAG_QUERIES
                    if structure[q]["recursive_cte"] or structure[q]["subqueries"] >= 3]
    return {"enum": len(enum_hits), "enum_queries": enum_hits,
            "literal": len(literal_hits), "literal_queries": literal_hits,
            "adjudicated": max(len(enum_hits), len(literal_hits)),
            "structure": structure}


# ---------------------------------------------------------------------------
# 규칙 4의 문언 — 제거 계획 동반 제시
# ---------------------------------------------------------------------------

REMOVAL_PLAN = {
    "trigger": "규칙 4(drop)의 문언: 'Neo4j·docker-compose·드라이버 의존 제거 계획 동반 제시'",
    "remove": [
        {"path": "core/graph/neo4j_store.py",
         "note": "적재·증분 갱신·드라이버 획득. SQLite 대응물이 이미 전부 있다."},
        {"path": "core/rag/neo4j_rag.py",
         "note": "RAG 질의 5종의 Cypher 구현. sqlite_rag가 같은 계약(RETURN_KEYS)을 이미 만족한다."},
        {"path": "core/graph/rehydrate.py::from_neo4j",
         "note": "재수화의 Neo4j 경로. from_sqlite가 같은 MemoryGraph를 만든다."},
        {"path": "docker-compose.yml",
         "note": "neo4j 서비스와 ./.neo4j/data 바인드 마운트. 이 저장소의 유일한 컨테이너 의존이다."},
        {"path": "pyproject.toml::dependencies[neo4j]",
         "note": "파이썬 드라이버. 제거하면 uv.lock도 재생성한다."},
        {"path": "tests/test_neo4j_store.py, tests/test_neo4j_rag.py",
         "note": "neo4j 마커가 붙은 테스트 전부. pytest 마커 'neo4j'와 addopts의 제외 규칙도 함께 정리."},
        {"path": "experiments/bench/exp4_rag.py, experiments/bench/exp5_persistence.py 의 neo4j 분기",
         "note": "**러너 코드만** 정리하고 커밋된 결과 JSON은 판정 근거이므로 보존한다."},
        {"path": "core/config.py 의 NEO4J_* 설정",
         "note": "URI·인증 환경변수."},
    ],
    "keep": [
        {"path": "core/rag/sqlite_rag.py",
         "note": "RAG 질의 5종을 전부 커버한다(parity 20/20 일치). 대체 구현을 새로 쓸 필요가 없다."},
        {"path": "core/graph/rehydrate.py::from_sqlite",
         "note": "콜드 스타트(저장소 -> MemoryGraph)를 이미 커버한다."},
        {"path": "core/graph/sqlite_store.py",
         "note": "적재·증분 갱신(append_cowork/append_review)."},
        {"path": "core/graph/memory_graph.py",
         "note": "연산 계층은 바뀌지 않는다 — 저장 계층만 SQLite 단일화된다."},
        {"path": "experiments/results/exp4_rag.json, exp5_persistence*.json",
         "note": "판정의 근거 아티팩트. 코드가 사라져도 결론의 재검증 가능성은 남겨야 한다."},
    ],
    "lost": [
        "가변 길이 경로 탐색의 표현력 — `skill_within_hops`는 Cypher에서 `*1..N` 한 줄이지만 "
        "SQL에서는 재귀 CTE가 필요하다. 5종 중 유일하게 Neo4j의 표현력 이점이 실체가 있는 질의다.",
        "임의 형태의 애드혹 그래프 질의 — 새 질의를 붙일 때 Cypher 쪽이 짧다. 다만 이 플랜이 "
        "실제로 측정한 5종에서는 SQL도 전부 구현 가능했고 20/20 셀에서 더 빨랐다.",
        "그래프 전용 엔진의 운영 기능(브라우저 UI, APOC, 클러스터). 이 PoC 규모(n<=1000)에서는 "
        "쓰지 않았지만, 훨씬 큰 규모나 다중 사용자 운영으로 가면 다시 평가할 항목이다.",
        "외래키 수준의 참조 무결성 — Neo4j의 관계는 두 끝점 노드의 존재를 보장한다. SQLite의 "
        "`collaboration` 테이블에는 외래키가 없어 append 시 끝점을 확인하지 않는다. "
        "제거 시 이 검증을 애플리케이션 계층이나 스키마에서 보완해야 한다.",
    ],
    "scope": ("이 계획은 Plan 3의 **산출물**이고 실행은 Plan 4의 범위다 — 이 태스크는 "
              "제거를 수행하지 않는다."),
}


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------

def evaluate(exp4: dict, exp5: dict) -> dict:
    """사전 고정된 규칙에 실측 데이터를 먹여 판정한다.

    exp4/exp5는 **안쪽 payload**를 받는다(래퍼는 `payload()`로 벗긴다).
    """
    wins, cells = _neo4j_faster_cells(exp4)
    tally = _rule2_tally(exp5)
    better, pmetrics = tally["neo4j_metric_wins"], tally["metrics"]
    complexity = _complex_sql_queries()
    complex_q = complexity["adjudicated"]

    evidence = {"neo4j_faster_cells": wins, "total_cells": cells,
                "neo4j_better_persistence_metrics": better,
                "total_persistence_metrics": pmetrics,
                "disk_reading": tally["disk_reading"],
                "rule2_per_metric": tally["per_metric"],
                "complex_sql_queries": complexity["enum"],
                "complex_sql_queries_queries": complexity["enum_queries"],
                "complex_sql_queries_literal": complexity["literal"],
                "complex_sql_queries_literal_queries": complexity["literal_queries"],
                "complex_sql_queries_adjudicated": complex_q,
                "sql_structure": complexity["structure"],
                "total_queries": len(RAG_QUERIES),
                "thresholds": {"majority_cells": MAJORITY_CELLS,
                               "persistence": PERSISTENCE_THRESHOLD,
                               "complexity": COMPLEXITY_THRESHOLD}}

    if wins >= MAJORITY_CELLS:
        return {"rule": 1, "verdict": "keep", "evidence": evidence,
                "rationale": (f"RAG 워크로드 {cells}개 셀 중 {wins}개에서 Neo4j가 "
                              "더 빨랐다 — 과반 기준을 충족하므로 성능 근거로 존치한다.")}
    if better >= PERSISTENCE_THRESHOLD:
        return {"rule": 2, "verdict": "keep", "evidence": evidence,
                "rationale": (f"RAG 성능은 열세지만({wins}/{cells}), 영속성 "
                              f"{pmetrics}개 지표 중 {better}개에서 우위다 — "
                              "영속성 근거로 존치한다.")}
    if complex_q >= COMPLEXITY_THRESHOLD:
        return {"rule": 3, "verdict": "keep_expressiveness", "evidence": evidence,
                "rationale": (f"성능({wins}/{cells})과 영속성({better}/{pmetrics}) 모두 "
                              f"열세다. 다만 5종 중 {complex_q}종이 SQL에서 재귀 CTE나 "
                              "다중 서브쿼리를 요구한다 — 표현력 근거로만 존치하며, "
                              "성능·영속성 열세를 리포트에 함께 명시해야 한다.")}
    return {"rule": 4, "verdict": "drop", "evidence": evidence,
            "removal_plan": REMOVAL_PLAN,
            "rationale": (
                f"성능({wins}/{cells} 셀), 영속성({better}/{pmetrics} 지표), "
                f"표현력(enum {complexity['enum']}/{len(RAG_QUERIES)} · 프로즈 "
                f"{complexity['literal']}/{len(RAG_QUERIES)}, 판정에는 Neo4j에 유리한 "
                f"{complex_q} 사용) 어느 축에서도 사전 고정된 존치 근거가 없다 — "
                "SQLite로 영속·탐색·RAG를 모두 처리하고 Neo4j 의존을 제거할 것을 "
                "권고한다(규칙 4의 문언에 따라 제거 계획을 함께 제시한다).")}
