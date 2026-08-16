"""스왑 대상 두 사람의 그래프 문맥 추출 -- XAI 브리핑 프롬프트의 원재료.

core.rag.sqlite_rag.swap_diff가 이미 PoC 설계 §6이 요구하는 3축(스킬 커버리지,
협업/시너지 엣지, 리뷰 근거 evidence)을 한 번의 질의로 반환한다(Plan 3에서
parity 20/20로 고정됨). 이 모듈은 그 flat 행 리스트를 사람별·종류별로 묶어
LLM 프롬프트가 바로 쓸 수 있는 구조로 바꿀 뿐 -- 새 질의를 추가하지 않는다."""
from core.rag import sqlite_rag

_KIND_KEY = {"skill": "skills", "cowork": "coworks", "evidence": "evidence"}


def swap_context(conn, out_id: str, in_id: str) -> dict:
    rows = sqlite_rag.swap_diff(conn, out_id, in_id)
    ctx: dict[str, dict[str, list]] = {out_id: {"skills": [], "coworks": [], "evidence": []},
                                       in_id: {"skills": [], "coworks": [], "evidence": []}}
    for r in rows:
        bucket = ctx.setdefault(r["person_id"], {"skills": [], "coworks": [], "evidence": []})
        bucket[_KIND_KEY[r["kind"]]].append({"key": r["key"], "value": r["value"]})
    return ctx
