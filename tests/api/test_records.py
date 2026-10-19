"""편성 기록 내보내기·불러오기(2026-10-18): 시연 기록을 불러와 열면 그때의 편성안이 바로 뜨고, 내보낸 기록은 다시 불러와도
같다. 고친 파일·위반 명단·서명이 맞지 않는 안은 받지 않는다. 열어 둔 기록은 재기동해도 다시 열린다."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.config import REPO_ROOT

JSON = {"Content-Type": "application/json"}
RECORD = REPO_ROOT / "demo" / "records" / "org-n100.json"
OPERATING = REPO_ROOT / "demo" / "records" / "org-n100-operating.json"


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _import(client, raw: bytes):
    return client.post("/api/records", content=raw, headers=JSON)


def _open(client, rid):
    return client.post(f"/api/records/{rid}/open", json={})


def _run(client, request: dict, version: str):
    res = client.post("/api/optimize", json={**request, "dataset_version": version})
    assert res.status_code == 200
    return [d for e, d in _sse(res.text) if e == "plan"], [d for e, d in _sse(res.text) if e == "done"][0]


@pytest.fixture(scope="module")
def raw():
    return RECORD.read_bytes()


def test_demo_record_imports_opens_and_shows_its_plans_at_once(client, raw):
    rec = json.loads(raw)
    res = _import(client, raw)
    assert res.status_code == 200, res.text
    item = res.json()["record"]
    assert item["title"] == rec["title"] and item["people"] == 100 and item["plans"] == 4
    assert res.json()["check"]["score_changed"] == []
    assert client.get("/api/datasets/active").json()["source"] != "record"      # 불러오기만으로는 바뀌지 않는다
    assert [i["id"] for i in client.get("/api/records").json()] == [item["id"]]

    opened = _open(client, item["id"])
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["dataset"]["source"] == "record" and body["request"] == rec["request"]   # 시연 기록은 이미 화면 모양
    active = client.get("/api/datasets/active").json()
    assert active["record_id"] == item["id"] and active["record_title"] == rec["title"]
    assert client.get("/api/records").json()[0]["open"] is True

    plans, done = _run(client, body["request"], body["dataset"]["version"])
    assert [p["label"] for p in plans] == ["A", "B", "C", "D"]
    assert all(p["cached"] and p["precomputed_label"] == rec["title"] for p in plans)
    assert done["precomputed_label"] == rec["title"]
    key = lambda es: sorted((e["person_id"], e["project_id"], e["alloc"]) for e in es)   # noqa: E731
    assert [key(p["entries"]) for p in plans] == [key(r["entries"]) for r in rec["optimize"]["plans"]]
    assert all(p["plan_token"] and p["evidence_token"] for p in plans)


def test_export_then_import_gives_the_same_plans(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    body = _open(client, rid).json()
    plans, _ = _run(client, body["request"], body["dataset"]["version"])
    res = client.post("/api/records/export", json={**body["request"], "title": "2024년 상반기 편성",
                                                  "dataset_version": body["dataset"]["version"], "plans": plans})
    assert res.status_code == 200, res.text
    assert "attachment" in res.headers["content-disposition"]
    exported = res.json()
    assert exported["title"] == "2024년 상반기 편성"
    assert exported["dataset"]["files"] == json.loads(raw)["dataset"]["files"]
    again = _import(client, res.content)
    assert again.status_code == 200 and again.json()["check"]["score_changed"] == []
    rid2 = again.json()["record"]["id"]
    body2 = _open(client, rid2).json()
    plans2, _ = _run(client, body2["request"], body2["dataset"]["version"])
    assert [p["entries"] for p in plans2] == [p["entries"] for p in plans]


def test_export_refuses_plans_this_server_did_not_sign(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    body = _open(client, rid).json()
    plans, _ = _run(client, body["request"], body["dataset"]["version"])
    forged = copy.deepcopy(plans)
    forged[0]["entries"][0]["alloc"] = round(forged[0]["entries"][0]["alloc"] + 0.1, 2)
    res = client.post("/api/records/export", json={**body["request"], "title": "x", "plans": forged})
    assert res.status_code == 409 and "서명" in res.json()["detail"]


def test_fixture_data_cannot_be_exported(client):
    """고정 fixture는 CSV 원천이 없다 -- 계산은 되지만 기록으로 내보낼 수 없다."""
    meta = client.get("/api/meta").json()
    res = client.post("/api/optimize", json={"dataset_version": meta["dataset_version"], "n_alternatives": 0,
                                             "milp_params": {"time_limit": 5}})
    plans = [d for e, d in _sse(res.text) if e == "plan"]
    out = client.post("/api/records/export", json={"title": "x", "milp_params": {"time_limit": 5},
                                                  "n_alternatives": 0, "plans": plans})
    assert out.status_code == 409 and "원천" in out.json()["detail"]


@pytest.mark.parametrize("tamper,word", [
    (lambda r: r["dataset"]["files"].__setitem__("people.csv", r["dataset"]["files"]["people.csv"] + "\n"), "고쳐졌다"),
    (lambda r: r.__setitem__("format", "something-else"), "편성 기록 파일이 아니다"),
    (lambda r: r["dataset"]["files"].__setitem__("../evil.csv", "x"), "허용하지 않는 파일"),
    (lambda r: r["review_judgments"]["polarity"].pop(), "판정값"),
])
def test_tampered_records_are_refused(client, raw, tamper, word):
    rec = json.loads(raw)
    tamper(rec)
    res = _import(client, json.dumps(rec).encode())
    assert res.status_code == 422 and word in res.json()["detail"]
    assert client.get("/api/records").json() == []


def test_a_plan_that_breaks_a_rule_is_refused(client, raw):
    rec = json.loads(raw)
    # 규칙을 깨는 명단: 전일 투입(2026-10-19 기본)에서 투입률 0.5는 허용 범위 밖
    rec["optimize"]["plans"][0]["entries"][0]["alloc"] = 0.5
    res = _import(client, json.dumps(rec).encode())
    assert res.status_code == 422 and "위반" in res.json()["detail"]


def test_a_changed_score_is_accepted_and_reported(client, raw):
    rec = json.loads(raw)
    rec["optimize"]["plans"][1]["eval_objective"] += 3.0             # 기록 당시 규칙이 달랐던 옛 기록
    res = _import(client, json.dumps(rec).encode())
    assert res.status_code == 200
    changed = res.json()["check"]["score_changed"]
    assert [c["label"] for c in changed] == ["B"]


def test_operating_record_serves_its_k_rows(client):
    raw = OPERATING.read_bytes()
    rid = _import(client, raw).json()["record"]["id"]
    body = _open(client, rid).json()
    assert body["operating_params"] and body["check"]["operating"] is True
    rec = json.loads(raw)
    res = client.post("/api/operating/compare", json={"ks": rec["operating"]["ks"],
                                                      "milp_params": body["operating_params"],
                                                      "dataset_version": body["dataset"]["version"]})
    events = _sse(res.text)
    assert events[0][1]["precomputed_at"]
    assert len([d for e, d in events if e == "row"]) == len(rec["operating"]["ks"])


def test_open_record_survives_restart_and_upload_closes_it(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    _open(client, rid)
    with TestClient(app) as again:
        active = again.get("/api/datasets/active").json()
        assert active["source"] == "record" and active["record_id"] == rid
        assert again.post("/api/datasets/reset", json={}).status_code == 200
        assert again.get("/api/datasets/active").json()["source"] != "record"
        assert all(not i["open"] for i in again.get("/api/records").json())


def test_delete_and_download(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    dl = client.get(f"/api/records/{rid}/file")
    assert dl.status_code == 200 and dl.content == raw
    assert client.delete(f"/api/records/{rid}").status_code == 200
    assert _open(client, rid).status_code == 404
    assert client.get("/api/records/zzz/file").status_code == 404


def test_size_limit_and_content_type(client):
    assert client.post("/api/records", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    big = client.post("/api/records", content=b"x", headers={**JSON, "Content-Length": str(21 * 1024 * 1024)})
    assert big.status_code == 413


def test_rejudging_a_record_dataset_is_refused(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    _open(client, rid)
    res = client.post("/api/datasets/rejudge", json={})
    assert res.status_code == 409 and "기록" in res.json()["detail"]


# --- 폴백 리뷰(2026-10-18) 반영 ------------------------------------------------------------

def test_recorded_proof_claims_are_not_re_signed(client, raw):
    """기록에 적힌 목적값·종료 사유·증명 상한은 이 서버가 확인할 수 없다 -- 평가기 점수로 바꾸고 증명 값은 버린다(M3)."""
    rec = json.loads(raw)
    for p in rec["optimize"]["plans"]:
        p.update({"objective": 99999.0, "termination": "Optimal", "best_bound": 99999.0, "gap_used": 0.0,
                  "unfilled": [], "time_limited": False})
    rid = _import(client, json.dumps(rec).encode()).json()["record"]["id"]
    body = _open(client, rid).json()
    plans, _ = _run(client, body["request"], body["dataset"]["version"])
    for p, r in zip(plans, json.loads(raw)["optimize"]["plans"]):
        assert p["objective"] == pytest.approx(r["eval_objective"], abs=1e-6)
        assert p["evidence"]["termination"] == "record" and p["evidence"]["best_bound"] is None
        assert p["evidence"]["objective"] != 99999.0


def test_operating_rows_lose_proof_and_respect_k(client):
    rec = json.loads(OPERATING.read_bytes())
    rid = _import(client, json.dumps(rec).encode()).json()["record"]["id"]
    body = _open(client, rid).json()
    res = client.post("/api/operating/compare", json={"ks": rec["operating"]["ks"], "milp_params": body["operating_params"],
                                                      "dataset_version": body["dataset"]["version"]})
    rows = [d for e, d in _sse(res.text) if e == "row"]
    assert rows and all(r["termination"] == "record" and r["best_bound"] is None and not r["proven_optimal"] for r in rows)
    k1 = next(r for r in rec["operating"]["rows"] if r["k"] == 1 and r.get("accepted"))
    k0 = next(r for r in rec["operating"]["rows"] if r["k"] == 0 and r.get("accepted"))
    k0_entries = copy.deepcopy(k0["entries"])
    bad = copy.deepcopy(rec)
    for r in bad["operating"]["rows"]:
        if r["k"] == 0:                                   # K=0 행에 K=1의 배치(한 명 옮김)를 넣는다
            r["entries"], r["objective"], r["violations"] = k1["entries"], k1["objective"], k1["violations"]
    out = _import(client, json.dumps(bad).encode())
    assert out.status_code == 200 and out.json()["check"]["operating"] is False
    assert any("보다 많다" in m for m in out.json()["check"]["skipped"])
    assert k0_entries != k1["entries"]


@pytest.mark.parametrize("tamper", [
    lambda r: r["request"].__setitem__("weights", {"x": "abc"}),
    lambda r: r["request"].__setitem__("n_alternatives", None),
    lambda r: r["request"].__setitem__("weights", {"x": 9}),
    lambda r: r["optimize"]["plans"][0].__setitem__("label", "../A"),
])
def test_malformed_values_are_422_not_500(client, raw, tamper):
    rec = json.loads(raw)
    tamper(rec)
    res = _import(client, json.dumps(rec).encode())
    assert res.status_code == 422


def test_deeply_nested_json_is_422(client):
    res = _import(client, b"[" * 50_000 + b"]" * 50_000)
    assert res.status_code == 422


def test_open_record_cannot_be_deleted_and_list_uses_rebuilt_facts(client, raw):
    rec = json.loads(raw)
    rec["dataset"]["people"] = {"nested": [1]}
    rec["dataset"]["synthetic"] = "FAKE"
    item = _import(client, json.dumps(rec).encode()).json()["record"]
    assert item["people"] == 100 and item["synthetic"] is True        # 다시 만든 데이터의 값
    _open(client, item["id"])
    assert client.delete(f"/api/records/{item['id']}").status_code == 409


def test_reset_evicts_the_record_plans_from_the_cache(client, raw):
    rid = _import(client, raw).json()["record"]["id"]
    body = _open(client, rid).json()
    key_count = len(client.app.state.precomputed_keys)
    assert key_count == 1
    key = next(iter(client.app.state.precomputed_keys))
    assert client.app.state.cache.get(key) is not None
    client.post("/api/datasets/reset", json={})
    assert client.app.state.cache.get(key) is None
    assert body["request"]


def test_operating_row_figures_come_from_the_server_not_the_file(client):
    """기록의 빈자리·품질·개선량은 화면에 그대로 나가지 않는다 -- 명단으로 다시 계산한다(Codex 리뷰 P2)."""
    rec = json.loads(OPERATING.read_bytes())
    true_k0 = dict(next(r for r in rec["operating"]["rows"] if r["k"] == 0))
    for r in rec["operating"]["rows"]:
        if r["k"] == 0:
            r.update({"unfilled_seats": 0, "quality": 999999.0, "gain_vs_k0": 123.0})
    rid = _import(client, json.dumps(rec).encode()).json()["record"]["id"]
    body = _open(client, rid).json()
    res = client.post("/api/operating/compare", json={"ks": rec["operating"]["ks"], "milp_params": body["operating_params"],
                                                      "dataset_version": body["dataset"]["version"]})
    k0 = next(d for e, d in _sse(res.text) if e == "row" and d["k"] == 0)
    assert k0["unfilled_seats"] == true_k0["unfilled_seats"] and k0["quality"] == pytest.approx(true_k0["quality"])
    assert k0["gain_vs_k0"] == 0.0


def test_operating_rows_follow_the_k_move_rules(client):
    """옮긴 사람은 새 사업에 들어가야 한다 -- 빼서 대기시키는 행은 받지 않는다(Codex 리뷰 2라운드 P2)."""
    rec = json.loads(OPERATING.read_bytes())
    k1 = next(r for r in rec["operating"]["rows"] if r["k"] == 1)
    mover = k1["diff"]["moved"][0]["person_id"]
    k1["entries"] = [e for e in k1["entries"] if e["person_id"] != mover or e["project_id"] in {"J005"}]
    out = _import(client, json.dumps(rec).encode())
    assert out.status_code == 200 and out.json()["check"]["operating"] is False


def test_full_history_window_survives_the_record_request():
    """clique_window_months=None(전체 이력)은 설정 기본값(36)으로 바뀌지 않는다(Codex 리뷰 2라운드 P2)."""
    from api.records import request_fields
    from core.optimize.milp import MilpParams
    assert request_fields(MilpParams(clique_window_months=None))["clique_window_months"] is None
