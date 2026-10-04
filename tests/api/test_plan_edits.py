"""K13: 적용한 교체의 저장·복원과 플랜 서명키 고정."""
import itertools
import json
import stat

import pytest
from fastapi.testclient import TestClient

from api.main import app
from tests.api.test_apply_swap import _apply, tiny  # noqa: F401  (픽스처 재사용)


def _plan(client, weights=None):
    with client.stream("POST", "/api/optimize",
                       json={"weights": weights or {}, "n_alternatives": 0}) as res:
        for line in res.iter_lines():
            if line.startswith("data:") and '"label"' in line:
                return json.loads(line[5:])
    raise AssertionError("plan 이벤트가 없다")


_REV = itertools.count(1)


def _body(plan, swaps, **extra):
    return {"plan_label": plan["label"], "base_entries": plan["entries"], "weights": {},
            "dataset_version": plan["dataset_version"], "swaps": swaps,
            "revision": next(_REV), **extra}


def _first_swap(client, plan):
    placed = {e["person_id"] for e in plan["entries"]}
    e0 = plan["entries"][0]
    bench = next(p for p in ("p0", "p1", "p2", "p3", "p4") if p not in placed)
    return {"out_person_id": e0["person_id"], "in_person_id": bench, "project_id": e0["project_id"]}


def test_saved_edits_are_replayed_by_the_server(tiny):
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    res = tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(plan, [swap]))
    assert res.status_code == 200, res.text
    loaded = tiny.get(f"/api/plans/edits/{plan['plan_token']}").json()
    assert loaded["swaps"] == [swap]
    direct = tiny.post("/api/plans/apply-swap", json={
        "entries": plan["entries"], "swap": swap, "weights": {}}).json()
    assert loaded["steps"][0]["entries"] == direct["entries"]
    assert loaded["steps"][0]["objective_delta"] == pytest.approx(direct["objective_delta"])


def test_edits_survive_restart_with_the_same_plan_token(tiny):
    """서명키가 고정돼 재기동 후 같은 계산의 plan_token이 같고, 저장분을 찾는다."""
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(plan, [swap]))
    import api.plan_token as pt
    pt._cache.clear()                                    # 프로세스 재시작 흉내(메모리 캐시 비움)
    with TestClient(app) as again:
        replan = _plan(again)
        assert replan["plan_token"] == plan["plan_token"]
        assert again.get(f"/api/plans/edits/{replan['plan_token']}").json()["swaps"] == [swap]


def test_plan_secret_is_a_private_file(tiny, data_dir):
    _plan(tiny)
    secret = data_dir / "plan_secret"
    assert secret.exists()
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert len(bytes.fromhex(secret.read_text())) == 32


def test_env_secret_overrides_file(tiny, data_dir, monkeypatch):
    a = _plan(tiny)["plan_token"]
    monkeypatch.setenv("TEAMWEAVER_PLAN_SECRET", "fixed-by-operator")
    assert _plan(tiny)["plan_token"] != a


def test_saving_requires_a_valid_plan_signature(tiny):
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    forged = dict(plan, entries=[dict(e, alloc=0.2) for e in plan["entries"]])
    res = tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(forged, [swap]))
    assert res.status_code == 422
    assert tiny.get(f"/api/plans/edits/{plan['plan_token']}").json()["swaps"] == []


def test_invalid_swap_is_not_saved(tiny):
    plan = _plan(tiny)
    bad = {"out_person_id": "p4", "in_person_id": "p3", "project_id": "j9"}
    assert tiny.put(f"/api/plans/edits/{plan['plan_token']}",
                    json=_body(plan, [bad])).status_code == 404
    assert tiny.get(f"/api/plans/edits/{plan['plan_token']}").json()["swaps"] == []


def test_empty_swap_list_clears(tiny):
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(plan, [swap]))
    assert tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(plan, [])).json()["saved"] == 0
    assert tiny.get(f"/api/plans/edits/{plan['plan_token']}").json()["swaps"] == []


def test_stale_dataset_version_is_409(tiny):
    plan = _plan(tiny)
    res = tiny.put(f"/api/plans/edits/{plan['plan_token']}",
                   json=_body(plan, [], dataset_version="0" * 64))
    assert res.status_code == 409


def test_store_keeps_at_most_the_newest_records(tmp_path, monkeypatch):
    import os
    import api.plan_edits as pe
    monkeypatch.setattr(pe, "MAX_SAVED_EDITS", 3)
    store = pe.PlanEditStore(tmp_path)
    for k in range(5):
        tok = f"{k:064x}"
        store.put(tok, {"swaps": [], "n": k}, revision=1)
        os.utime(store._path(tok), (1000 + k, 1000 + k))          # 생성 순서를 mtime으로 고정
    names = sorted(p.stem for p in (tmp_path / "plan_edits").glob("*.json"))
    assert len(names) == 3 and f"{0:064x}" not in names


def test_late_older_save_cannot_overwrite_a_newer_cancel(tiny):
    """적용(rev 1) 직후 원래대로(rev 2)를 보냈는데 rev 1 요청이 늦게 도착해도 취소가 이긴다(Opus 리뷰 M1)."""
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    url = f"/api/plans/edits/{plan['plan_token']}"
    cancel = tiny.put(url, json=_body(plan, [], revision=2)).json()
    assert cancel["applied"] is True
    late = tiny.put(url, json=_body(plan, [swap], revision=1)).json()
    assert late["applied"] is False
    assert tiny.get(url).json()["swaps"] == []


def test_token_path_param_must_be_hex(tiny):
    plan = _plan(tiny)
    assert tiny.get("/api/plans/edits/..%2F..%2Fsecret").status_code in (404, 422)
    assert tiny.put("/api/plans/edits/not-a-token", json=_body(plan, [])).status_code == 422


def test_reset_and_new_upload_prune_other_datasets_edits(tiny, data_dir):
    """되돌리기·새 업로드 때 다른 데이터셋의 교체 기록(사번·명단 포함)을 지운다(Opus 리뷰 S2)."""
    plan = _plan(tiny)
    swap = _first_swap(tiny, plan)
    tiny.put(f"/api/plans/edits/{plan['plan_token']}", json=_body(plan, [swap]))
    store = tiny.app.state.plan_edit_store
    stale = {"dataset_version": "f" * 64, "swaps": [swap], "base_entries": []}
    store.put("e" * 64, stale, revision=1)
    assert tiny.post("/api/datasets/reset", json={}).status_code == 200
    left = {p.stem for p in (data_dir / "plan_edits").glob("*.json")}
    assert "e" * 64 not in left and plan["plan_token"] in left      # 현재(fixture) 것은 남는다


def test_store_itself_rejects_non_token_names(tmp_path):
    """라우트 검사와 별개로 저장소도 경로 조작을 막는다(이중 방어)."""
    import api.plan_edits as pe
    store = pe.PlanEditStore(tmp_path)
    for bad in ("../../plan_secret", "a" * 63, "A" * 64, "x/" + "a" * 62):
        with pytest.raises(ValueError):
            store.get(bad)


def test_corrupt_revision_in_saved_record_can_be_overwritten(tmp_path):
    import api.plan_edits as pe
    store = pe.PlanEditStore(tmp_path)
    tok = "c" * 64
    (tmp_path / "plan_edits").mkdir(parents=True)
    (tmp_path / "plan_edits" / f"{tok}.json").write_text('{"revision": "abc", "swaps": []}')
    assert store.put(tok, {"swaps": [1]}, revision=5) is True
    assert store.get(tok)["revision"] == 5
