"""E8 판정: 지식 그래프 구현 기술(RDF 트리플 저장소 / 그래프 DB / 메모리 networkx) -- **측정 전에 고정한 규칙**을 코드로 옮긴 것.

계획: .omc/plan/2026-10-09-kg-backend-compare.md "사전 판정 규칙". 이 파일은 측정(rehearsal/kg_backends.py)보다 먼저 커밋한다 -- 측정을 보고
기준을 바꾸지 않았다는 증적(8월 저장 계층 결정 experiments/decision.py와 같은 방식). 하드코딩된 측정값은 없다. 결과 JSON만 읽는다.

규칙(측정 전 고정, 사후 변경 금지):
  1. 정확성: 질문 4종 중 하나라도 기준 구현과 답이 다르면 탈락.
  2. 화면 응답성: 300명에서 질문 4종 중 3종 이상의 지연 중앙값이 200 ms를 넘으면 탈락.
  3. 운영 정책 적합: 별도 서버 프로세스가 필요하거나 기본 설정에서 데이터가 디스크에 남는 후보는, 이를 만족하는 후보가 하나라도 있으면 후순위.
     (근거: PoC 배포 = 소스 그대로 -- 2026-10-05 사용자 결정, 실데이터는 서버 디스크에 남기지 않음 -- K9·K13)
  4. 성능: 같은 순위 안에서 질의 셀(4 질문 × 3 규모) 과반(7셀 이상)에서 가장 빠른 후보. 과반이 없으면 셀 승수가 많은 후보.
     동률이거나 셀 평균 지연 차이가 10% 이내면 코드 줄 수가 적은 후보.
  5. 예외: 표준 연동(RDF/SKOS 교환)이나 규칙 추론이 **현재 요구사항**이면 1~4보다 우선해 RDF 후보를 다시 본다. 요구가 있어도
     "JSON 보관 + 필요할 때 RDF(Turtle) 내보내기"가 시험으로 확인되면 발동하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

QUESTIONS = ("q1_skill_map", "q2_project_evidence", "q3_three_hop", "q4_ego")
SIZES = (100, 200, 300)
LATENCY_LIMIT_MS = 200.0
MAJORITY = 7
NEAR_TIE = 0.10


def decide(result: dict) -> dict:
    """result: rehearsal/results/kg-backends.json. 반환: 판정과 규칙별 근거."""
    cands = result["candidates"]                       # 이름 -> {correct: {q: bool}, latency: {size: {q: {p50_ms}}}, server, disk_persist, loc}
    trail = []
    alive = []
    for name, c in cands.items():
        if name == result.get("baseline"):
            continue
        wrong = [q for q in QUESTIONS if not c["correct"].get(q, False)]
        if wrong:
            trail.append({"rule": 1, "candidate": name, "result": "탈락", "why": f"기준과 다른 답: {wrong}"})
            continue
        slow = [q for q in QUESTIONS if c["latency"][str(300)][q]["p50_ms"] > LATENCY_LIMIT_MS]
        if len(slow) >= 3:
            trail.append({"rule": 2, "candidate": name, "result": "탈락", "why": f"300명 중앙값 200 ms 초과 {slow}"})
            continue
        trail.append({"rule": "1-2", "candidate": name, "result": "통과"})
        alive.append(name)
    if not alive:
        return {"decision": None, "trail": trail}
    fits = [n for n in alive if not cands[n]["server"] and not cands[n]["disk_persist"]]
    tier = fits or alive
    for n in alive:
        if n not in tier:
            trail.append({"rule": 3, "candidate": n, "result": "후순위",
                          "why": "별도 서버 필요" if cands[n]["server"] else "기본 설정에서 디스크 잔류"})
    wins = {n: 0 for n in tier}
    for size in SIZES:
        for q in QUESTIONS:
            best = min(tier, key=lambda n: cands[n]["latency"][str(size)][q]["p50_ms"])
            wins[best] += 1
    leader = max(tier, key=lambda n: wins[n])
    rule4 = {"cell_wins": wins, "majority": wins[leader] >= MAJORITY}
    mean = {n: sum(cands[n]["latency"][str(s)][q]["p50_ms"] for s in SIZES for q in QUESTIONS) / (len(SIZES) * len(QUESTIONS))
            for n in tier}
    close = [n for n in tier if n != leader and abs(mean[n] - mean[leader]) <= NEAR_TIE * mean[leader]]
    if close:
        pick = min([leader] + close, key=lambda n: cands[n]["loc"])
        rule4["near_tie"] = {"with": close, "mean_ms": mean, "picked_by_loc": pick}
        leader = pick
    trail.append({"rule": 4, "result": leader, **rule4, "mean_ms": mean})
    req = result.get("standards_requirement", {})
    export_ok = result.get("rdf_export", {}).get("ok", False)
    if req.get("required_now") and not export_ok:
        trail.append({"rule": 5, "result": "발동", "why": "표준 연동이 지금 요구되는데 JSON+내보내기로 충족 못 함 -- RDF 후보 재검토"})
        return {"decision": "RDF 재검토", "trail": trail}
    trail.append({"rule": 5, "result": "미발동", "why": ("표준 연동이 지금 요구사항이 아님" if not req.get("required_now")
                                                       else "JSON 보관 + RDF(Turtle) 내보내기 시험 통과")})
    return {"decision": leader, "trail": trail}


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results" / "kg-backends.json")
    print(json.dumps(decide(json.loads(path.read_text("utf-8"))), ensure_ascii=False, indent=1))
