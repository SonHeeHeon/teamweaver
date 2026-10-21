"""교체 검토에서 다른 사업 사람 데려오기·맞바꾸기(사용자 2026-10-20 "교체 투입에 왜 기존 플젝 인력은 안돼?"): 원래 사업에서 빠지고(빈자리),
맞바꾸기면 빠지는 사람이 그 자리로 간다. 적용·저장·되풀이(PDF)가 같은 함수를 쓴다. 기본값 칸은 저장 모양에 나오지 않는다."""
from api.schemas import SwapIn
from tests.ai_override import override_ai


def _setup(client):
    meta = client.get("/api/meta").json()
    j1, j2 = meta["projects"][0]["id"], meta["projects"][1]["id"]
    a, b, c = (p["id"] for p in meta["people"][:3])
    entries = [{"person_id": a, "project_id": j1, "alloc": 1.0}, {"person_id": b, "project_id": j2, "alloc": 1.0},
               {"person_id": c, "project_id": j2, "alloc": 1.0}]
    return entries, a, b, j1, j2


def _post(client, entries, swap):
    with override_ai(None):
        return client.post("/api/whatif", json={"entries": entries, "swap": swap, "weights": {}})


def test_bringing_someone_from_another_project_frees_their_seat(client):
    entries, a, b, j1, j2 = _setup(client)
    r = _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1, "in_from_project_id": j2})
    assert r.status_code == 200, r.text
    risks = r.json()["briefing"]["risks"]
    assert "데려오면" in risks[0] and j2 in risks[0]
    # 예전처럼 데려올 사업을 안 주면 그 사람은 두 사업에 걸린다 -- 그대로(위반이면 평가기가 알린다)
    assert _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1}).status_code == 200


def test_exchange_puts_the_outgoing_person_into_the_freed_seat(client):
    entries, a, b, j1, j2 = _setup(client)
    r = _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1, "in_from_project_id": j2,
                                "exchange": True})
    assert r.status_code == 200, r.text
    assert r.json()["briefing"]["risks"][0].startswith("맞바꾸기:")


def test_bad_move_requests_are_rejected(client):
    entries, a, b, j1, j2 = _setup(client)
    assert _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1, "in_from_project_id": j1}).status_code == 422
    assert _post(client, entries, {"out_person_id": a, "in_person_id": a, "project_id": j1, "in_from_project_id": j2}).status_code == 422
    assert _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1, "exchange": True}).status_code == 422
    assert _post(client, entries, {"out_person_id": a, "in_person_id": b, "project_id": j1, "in_from_project_id": "NOPE"}).status_code == 404


def test_saved_shape_of_a_plain_swap_is_unchanged():
    assert SwapIn(out_person_id="a", in_person_id="b", project_id="J1").model_dump() == \
        {"out_person_id": "a", "in_person_id": "b", "project_id": "J1"}
    assert SwapIn(out_person_id="a", in_person_id="b", project_id="J1", in_from_project_id="J2", exchange=True).model_dump() == \
        {"out_person_id": "a", "in_person_id": "b", "project_id": "J1", "in_from_project_id": "J2", "exchange": True}


def test_apply_and_replay_carry_the_move(client):
    """적용(apply-swap)도 같은 규칙: 데려온 사람은 원래 사업에서 빠지고 맞바꾸기면 나간 사람이 그 자리에."""
    entries, a, b, j1, j2 = _setup(client)
    with override_ai(None):
        r = client.post("/api/plans/apply-swap", json={"entries": entries, "weights": {}, "swap": {
            "out_person_id": a, "in_person_id": b, "project_id": j1, "in_from_project_id": j2, "exchange": True}})
    assert r.status_code == 200, r.text
    got = {(e["person_id"], e["project_id"]) for e in r.json()["entries"]}
    assert (b, j1) in got and (a, j2) in got and (b, j2) not in got and (a, j1) not in got
