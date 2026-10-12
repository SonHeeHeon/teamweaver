"""LLM 없이 그래프 수치만으로 브리핑을 만드는 결정론적 규칙 생성기.

PoC 설계 §6 "데모 안정성" 요구사항의 구현: 발표 중 LLM API가 죽어도 데모가
죽지 않도록, generate_briefing이 실패하면 이 함수로 전환한다(호출부는
api/routes/whatif.py, Task 8). 같은 입력에는 항상 같은 문장을 낸다 --
발표 중 재현성도 이 함수의 요구사항이다.

K5: 문맥의 근거가 근거 색인 형식(source_id·kind·text)이면, 새로 들어올 사람의 최신 근거를 좋은 점·
아쉬운 점에서 하나씩 골라 출처와 함께 evidence에 담는다. 종류(quote/summary/label)는 색인이 정한
그대로 둔다 -- 요약을 인용이라고 부르지 않는다."""


def rule_based_briefing(ctx: dict, out_id: str, in_id: str, score_change: dict | None = None) -> dict:
    """score_change(교체 라우트가 LLM에 넘기는 것과 같은 값)를 주면 이 교체가 새로 만드는 제약 위반을 결론·위험 첫 줄에 쓴다 --
    없으면 위반 교체에도 "뚜렷한 리스크가 관측되지 않는다"가 나와 화면의 위반 경고와 어긋났다(폴백 리뷰 2026-10-12)."""
    out_skills = {s["key"]: s["value"] for s in ctx.get(out_id, {}).get("skills", [])}
    in_skills = {s["key"]: s["value"] for s in ctx.get(in_id, {}).get("skills", [])}
    out_coworks = len(ctx.get(out_id, {}).get("coworks", []))
    in_coworks = len(ctx.get(in_id, {}).get("coworks", []))
    in_evidence_rows = ctx.get(in_id, {}).get("evidence", [])
    in_evidence = len(in_evidence_rows)
    sourced = [e for e in in_evidence_rows if isinstance(e, dict) and "source_id" in e]

    shared = sorted(set(out_skills) & set(in_skills))
    only_in = sorted(set(in_skills) - set(out_skills))
    rationale_parts = [f"{in_id}(으)로 교체 검토: 스킬 {len(in_skills)}종 보유"
                       f"({out_id}는 {len(out_skills)}종)."]
    if only_in:
        rationale_parts.append(f"{in_id}만 보유한 스킬: {', '.join(only_in)}.")
    if shared:
        rationale_parts.append(f"공통 보유 스킬: {', '.join(shared)}.")
    # 인정 경력(credited_from)은 본인 경력과 구분해 밝힌다 -- LLM이 없을 때도(사용자 결정 2026-10-11, Codex 리뷰 P1 2026-10-12)
    for pid in (in_id, out_id):
        # 본인 경력이 있어도 인정분이 더 길어 레벨을 정한 기술이다(SkillCredit) -- "본인 경력이 아니다"라고 쓰지 않고 본인 개월을 같이 적는다(Codex 리뷰 P2)
        credited = [f"{s['key']}({s['credited_from']} 경력에서 인정"
                    + (f", 본인 경력 {s['own_months']}개월" if s.get("own_months") else "") + ")"
                    for s in ctx.get(pid, {}).get("skills", []) if s.get("credited_from")]
        if credited:
            rationale_parts.append(f"{pid}의 다음 스킬 레벨은 하위 기술 경력에서 일부 인정된 개월로 정해졌다: {', '.join(credited)}.")
    new_violations = list((score_change or {}).get("new_violations") or [])
    if new_violations:
        # 라우트가 위반 문구를 5개까지만 넘긴다 -- 5개면 "5건 이상"(달마다 하나씩 생겨 6건을 넘기도 한다, 폴백 리뷰 2차)
        count = f"{len(new_violations)}건" if len(new_violations) < 5 else f"{len(new_violations)}건 이상"
        rationale_parts.insert(0, f"보류: 이 교체는 제약 위반 {count}을 새로 만든다.")
    rationale = " ".join(rationale_parts)

    risks = [f"새 제약 위반: {v}" for v in new_violations]
    if in_coworks < out_coworks:
        risks.append(f"{in_id}의 팀 내 협업 이력({in_coworks}건)이 {out_id}({out_coworks}건)보다 적다.")
    if in_evidence == 0:
        risks.append(f"{in_id}에 대한 리뷰 근거가 아직 없다.")
    picked = _pick_evidence(sourced)
    if sourced:
        quotes = sum(e["kind"] == "quote" for e in sourced)
        detail = "원문 비공개" if all(e["kind"] == "label" for e in sourced) else f"직접 인용 {quotes}건"
        rationale += f" {in_id}의 최근 리뷰 근거 {len(sourced)}건({detail})."
    if not risks:
        risks.append("그래프 수치상 뚜렷한 리스크가 관측되지 않는다.")

    alternatives = [f"{out_id}를 유지하고 다른 프로젝트에서 {in_id}를 투입"]

    return {"rationale": rationale, "risks": risks, "alternatives": alternatives, "evidence": picked}


def _pick_evidence(sourced: list[dict]) -> list[dict]:
    """좋은 점·아쉬운 점에서 최신 근거 하나씩(문맥은 최신 회차가 앞이다)."""
    out = []
    for suffix in (":pos", ":neg"):
        e = next((e for e in sourced if e["source_id"].endswith(suffix)), None)
        if e is not None:
            out.append({"source_id": e["source_id"], "reviewer_id": e["reviewer_id"],
                        "kind": e["kind"], "text": e["text"]})
    return out
