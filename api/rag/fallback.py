"""LLM 없이 그래프 수치만으로 브리핑을 만드는 결정론적 규칙 생성기.

PoC 설계 §6 "데모 안정성" 요구사항의 구현: 발표 중 LLM API가 죽어도 데모가
죽지 않도록, generate_briefing이 실패하면 이 함수로 전환한다(호출부는
api/routes/whatif.py, Task 8). 같은 입력에는 항상 같은 문장을 낸다 --
발표 중 재현성도 이 함수의 요구사항이다.

K5: 문맥의 근거가 근거 색인 형식(source_id·kind·text)이면, 새로 들어올 사람의 최신 근거를 좋은 점·
아쉬운 점에서 하나씩 골라 출처와 함께 evidence에 담는다. 종류(quote/summary/label)는 색인이 정한
그대로 둔다 -- 요약을 인용이라고 부르지 않는다."""


def rule_based_briefing(ctx: dict, out_id: str, in_id: str) -> dict:
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
    rationale = " ".join(rationale_parts)

    risks = []
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
