"""스왑 대상 두 사람의 그래프 문맥 추출 -- XAI 브리핑 프롬프트의 원재료.

core.rag.sqlite_rag.swap_diff가 이미 PoC 설계 §6이 요구하는 3축(스킬 커버리지,
협업/시너지 엣지, 리뷰 근거 evidence)을 한 번의 질의로 반환한다(Plan 3에서
parity 20/20로 고정됨). 이 모듈은 그 flat 행 리스트를 사람별·종류별로 묶어
LLM 프롬프트가 바로 쓸 수 있는 구조로 바꿀 뿐 -- 새 질의를 추가하지 않는다.

K5: swap_diff의 evidence 행은 리뷰 극성 숫자뿐이다(SQLite에 리뷰 문장이 없다). 근거 색인
(api/rag/evidence.py)을 넘기면 evidence를 출처 ID가 붙은 근거 문장으로 바꾼다. 넘기지 않으면
예전과 똑같다. project_id를 넘기면 ctx["project"]에 그 프로젝트의 요구 기술을 더한다."""
from dataclasses import asdict

from api.rag.evidence import EvidenceIndex
from core.rag import sqlite_rag

_KIND_KEY = {"skill": "skills", "cowork": "coworks", "evidence": "evidence"}


def swap_context(conn, out_id: str, in_id: str, evidence: EvidenceIndex | None = None,
                 project_id: str | None = None) -> dict:
    rows = sqlite_rag.swap_diff(conn, out_id, in_id)
    ctx: dict[str, dict[str, list]] = {out_id: {"skills": [], "coworks": [], "evidence": []},
                                       in_id: {"skills": [], "coworks": [], "evidence": []}}
    for r in rows:
        bucket = ctx.setdefault(r["person_id"], {"skills": [], "coworks": [], "evidence": []})
        bucket[_KIND_KEY[r["kind"]]].append({"key": r["key"], "value": r["value"]})
    if evidence is not None:
        for pid in (out_id, in_id):
            ctx[pid]["evidence"] = [asdict(e) for e in evidence.for_person(pid)]
    if project_id is not None:
        project = _project(conn, project_id)
        if project is not None:          # 없는 프로젝트면 빈 정보를 넣지 않는다(LLM이 "요구 없음"으로 읽지 않게)
            ctx["project"] = project
    return ctx


def _project(conn, project_id: str) -> dict | None:
    """교체 대상 프로젝트의 요구 기술(2026-10-05 튜닝: 이게 없어 LLM이 매번 결론을 못 냈다).
    기존 SQLite project·requirement 표를 읽기만 한다 -- 공유 스키마 무변경."""
    row = conn.execute("SELECT id, name FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        return None
    reqs = conn.execute("SELECT skill, min_level, headcount FROM requirement WHERE project_id = ? "
                        "ORDER BY skill", (project_id,)).fetchall()
    return {"id": project_id, "name": row[1],
            "requirements": [{"skill": s, "min_level": lv, "headcount": n} for s, lv, n in reqs]}
