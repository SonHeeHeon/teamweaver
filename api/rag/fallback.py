"""LLM 없이 그래프 수치만으로 브리핑을 만드는 결정론적 규칙 생성기.

PoC 설계 §6 "데모 안정성" 요구사항의 구현: 발표 중 LLM API가 죽어도 데모가
죽지 않도록, generate_briefing이 실패하면 이 함수로 전환한다(호출부는
api/routes/whatif.py, Task 8). 같은 입력에는 항상 같은 문장을 낸다 --
발표 중 재현성도 이 함수의 요구사항이다."""


def rule_based_briefing(ctx: dict, out_id: str, in_id: str) -> dict:
    out_skills = {s["key"]: s["value"] for s in ctx.get(out_id, {}).get("skills", [])}
    in_skills = {s["key"]: s["value"] for s in ctx.get(in_id, {}).get("skills", [])}
    out_coworks = len(ctx.get(out_id, {}).get("coworks", []))
    in_coworks = len(ctx.get(in_id, {}).get("coworks", []))
    in_evidence = len(ctx.get(in_id, {}).get("evidence", []))

    shared = sorted(set(out_skills) & set(in_skills))
    only_in = sorted(set(in_skills) - set(out_skills))
    rationale_parts = [f"{in_id}(으)로 교체 검토: 스킬 {len(in_skills)}종 보유"
                       f"({out_id}는 {len(out_skills)}종)."]
    if only_in:
        rationale_parts.append(f"{in_id}만 보유한 스킬: {', '.join(only_in)}.")
    if shared:
        rationale_parts.append(f"공통 보유 스킬: {', '.join(shared)}.")
    rationale = " ".join(rationale_parts)

    risks = []
    if in_coworks < out_coworks:
        risks.append(f"{in_id}의 팀 내 협업 이력({in_coworks}건)이 {out_id}({out_coworks}건)보다 적다.")
    if in_evidence == 0:
        risks.append(f"{in_id}에 대한 리뷰 근거가 아직 없다.")
    if not risks:
        risks.append("그래프 수치상 뚜렷한 리스크가 관측되지 않는다.")

    alternatives = [f"{out_id}를 유지하고 다른 프로젝트에서 {in_id}를 투입"]

    return {"rationale": rationale, "risks": risks, "alternatives": alternatives}
