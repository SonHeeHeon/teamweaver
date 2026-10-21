"""인사팀 소명 "근거 한눈에"의 화면 재료(2026-10-21 사용자 요청 -- 소명 글의 사실 수십 개를 표·그림으로).

소명 재료의 지식 그래프 근거(`core.kg.views.project_evidence`, `JustificationInput.evidence`)에서 숫자·판정만 추린다 --
글과 같은 재료라 표와 글이 어긋나지 않는다. AI 글을 만드는 코드가 아니라서 저장된 AI 글의 코드 지문
(`api.justification_cache._CODE_FILES`) 밖에 둔다: 화면 모양을 고칠 때마다 밤 배치가 미리 쓴 글이 버려지지 않게(폴백 리뷰 NIT).
"""
from __future__ import annotations

ALTERNATIVES = 1          # 팀원마다 보이는 대안 수(첫째 = 위반 없는·점수 높은 순으로 가장 나은 것, core.kg.views._alternatives 정렬)


def evidence_view(ev: dict | None, names: dict[str, str]) -> dict | None:
    """이름은 가상 데이터만(`names` -- 실데이터는 빈 사전이라 None → 화면이 ID). 동료 평가 극성·인용·고객사 이름은 싣지 않는다
    (협업 지도 색은 화면 meta `coworks.review` -- 가상 데이터만 실린다)."""
    if not ev:
        return None
    from core.ingest.convert import level_from_months

    def name(pid: str) -> str | None:
        return names.get(pid)

    def req(c: dict) -> dict:
        out = {k: c.get(k) for k in ("skill", "months", "level", "min_level", "status")}
        imp = c.get("implied_from")
        if isinstance(imp, dict) and imp.get("skill"):
            # 하위 기술 경력에서 일부 인정: 실제 비율은 credit^깊이(Spring Boot→Java 깊이 2면 0.7²=49%, core.ingest.skills) --
            # credit만 보내면 깊이 2 이상이 70%로 보였다(폴백 리뷰 SHOULD)
            credit, depth = imp.get("credit"), imp.get("depth") or 1
            out["credited_from"] = {"skill": imp["skill"], "months": imp.get("months"),
                                    "ratio": round(credit ** depth, 4) if isinstance(credit, (int, float)) else None}
        return out
    members = [{"person_id": m["person_id"], "name": name(m["person_id"]), "grade": m.get("grade"),
                "skill_fit": m.get("skill_fit"), "requirements": [req(c) for c in m.get("requirements", [])],
                "same_industry_projects": m.get("same_industry_projects"),
                "same_industry_months": m.get("same_industry_months")}
               for m in ev.get("members", [])]
    alts = {pid: [{"person_id": r["person_id"], "name": name(r["person_id"]), "skill_fit": r.get("skill_fit"),
                   "delta_total": r.get("delta_total"), "delta_skill": r.get("delta_skill"),
                   "delta_synergy": r.get("delta_synergy"), "new_violations": len(r.get("new_violations") or [])}
                  for r in rows[:ALTERNATIVES]]
            for pid, rows in (ev.get("alternatives") or {}).items()}
    return {"project_id": ev.get("project_id"), "industry": ev.get("industry"),
            "requirements": [{"skill": r["skill"], "headcount": r["headcount"], "min_level": level_from_months(r.get("min_months") or 0),
                              "met_by": r["met_by"], "status": r["status"]} for r in ev.get("requirements", [])],
            "members": members, "alternatives": alts}
