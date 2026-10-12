"""PDF 계산 신뢰도 배지(2026-10-12 사용자 요청 "PDF에도 표기"): 서버가 서명한 값만 찍는다."""
import json

import pytest
from fastapi import HTTPException

from api.plan_token import EVIDENCE_FIELDS, sign_evidence, sign_plan, verify_evidence
from api.routes.report import _solve_evidence
from api.schemas import ReportRequest
from core.optimize.milp import MilpParams

ENTRIES = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}]
EVIDENCE = {"termination": "time_limit_incumbent", "objective": 57.59, "best_bound": 59.1, "gap_allowed": 0.05,
            "time_limited": True, "carried_from_k": None}


def _req(label="A", evidence=EVIDENCE, token="sign", swaps=()):
    plan_token = sign_plan("v" * 64, label, ENTRIES, {}, MilpParams())
    ev_token = sign_evidence(plan_token, evidence) if token == "sign" else token
    return ReportRequest(plan_label=label, entries=ENTRIES, objective=1.0, fulfillment=1.0, optimization_ratio=1.0,
                         dataset_version="v" * 64, plan_token=plan_token, solve_evidence=evidence,
                         evidence_token=ev_token, applied_swaps=list(swaps),
                         base_entries=ENTRIES if swaps else None)


def test_signed_evidence_goes_to_the_pdf_with_context():
    out = _solve_evidence(_req(), "verified")
    assert {k: out[k] for k in EVIDENCE_FIELDS} == EVIDENCE and out["within"] is None and out["changed"] == 0
    assert _solve_evidence(_req(label="C"), "verified")["within"] == "다양성 조건"      # 대안은 다양성 조건 안에서의 증명
    swapped = _req(swaps=[{"out_person_id": "p0", "in_person_id": "p1", "project_id": "j0"}])
    assert _solve_evidence(swapped, "verified")["changed"] == 1                       # 변경한 명단 -- 원안 기준이라고 밝힌다


@pytest.mark.parametrize("change", [{"termination": "Optimal"}, {"objective": 60.0}, {"best_bound": 57.6},
                                    {"gap_allowed": 0.0}, {"time_limited": False}, {"carried_from_k": 1}])
def test_forged_evidence_is_refused(change):
    plan_token = sign_plan("v" * 64, "A", ENTRIES, {}, MilpParams())
    token = sign_evidence(plan_token, EVIDENCE)
    forged = {**EVIDENCE, **change}
    assert verify_evidence(token, plan_token, EVIDENCE) and not verify_evidence(token, plan_token, forged)
    with pytest.raises(HTTPException) as err:
        _solve_evidence(_req(evidence=forged, token=token), "verified")
    assert err.value.status_code == 422


def test_no_badge_without_a_verified_plan_or_a_token():
    assert _solve_evidence(_req(), "unverified") is None                               # 원 플랜 서명이 없으면 찍지 않는다
    with pytest.raises(HTTPException):
        _solve_evidence(_req(token=None), "verified")                                  # 신뢰도만 보내고 서명이 없다
    assert verify_evidence("가" * 64, "x", EVIDENCE) is False                         # ASCII 아닌 서명도 그냥 틀림


def test_optimize_plans_carry_an_evidence_token_that_the_pdf_accepts(small_graph_client):
    with small_graph_client.stream("POST", "/api/optimize", json={"weights": {}, "n_alternatives": 0, "fresh": True}) as res:
        plans = [json.loads(l[5:]) for l in res.iter_lines() if l.startswith("data:") and '"label"' in l]
    plan = plans[0]
    evidence = plan["evidence"]                                                  # 화면은 이 묶음을 그대로 돌려보낸다
    assert set(evidence) == set(EVIDENCE_FIELDS) and evidence["termination"] == plan["termination"]
    assert plan["evidence_token"] and verify_evidence(plan["evidence_token"], plan["plan_token"], evidence)
    # JSON을 한 번 거쳐도(화면이 받아 다시 보냄) 같은 값으로 검증된다
    again = json.loads(json.dumps(evidence))
    assert verify_evidence(plan["evidence_token"], plan["plan_token"], again)


def test_operating_k_rows_are_signed_for_the_k_pdf():
    from types import SimpleNamespace
    from api.routes.operating import _signed
    from api.schemas import MilpParamsIn
    row = {"k": 1, "accepted": True, "entries": ENTRIES, "termination": "Optimal", "objective": 68.59, "best_bound": 68.59}
    req = SimpleNamespace(weights={}, milp_params=MilpParamsIn())
    out = _signed(row, req, "v" * 64)
    evidence = {"termination": "Optimal", "objective": 68.59, "best_bound": 68.59, "gap_allowed": 0.0, "time_limited": False,
                "carried_from_k": None}
    assert out["evidence"] == evidence and out["gap_allowed"] == 0.0
    assert verify_evidence(out["evidence_token"], out["plan_token"], evidence)


def test_a_carried_k_row_does_not_borrow_the_earlier_k_proof():
    """K=3에서 더 나은 해가 없어 K=2의 해를 유지한 행 -- K=2의 Optimal·상한을 K=3의 증명으로 서명하지 않는다(Codex 리뷰)."""
    from types import SimpleNamespace
    from api.routes.operating import _signed
    from api.schemas import MilpParamsIn
    row = {"k": 3, "accepted": True, "entries": ENTRIES, "termination": "Optimal", "objective": 69.13, "best_bound": 69.13,
           "carried_from_k": 2, "own": {"termination": "time_limit_incumbent", "objective": 68.0}}
    out = _signed(row, SimpleNamespace(weights={}, milp_params=MilpParamsIn()), "v" * 64)
    assert out["evidence"]["termination"] is None and out["evidence"]["best_bound"] is None
    assert out["evidence"]["carried_from_k"] == 2
    borrowed = {**out["evidence"], "termination": "Optimal", "best_bound": 69.13}
    assert not verify_evidence(out["evidence_token"], out["plan_token"], borrowed)       # 증명을 붙여 보내면 거절
