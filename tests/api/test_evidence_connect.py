"""K5 연결(claude-b 쪽): 활성 데이터셋의 근거 색인을 whatif 두 곳에 넘기고, 브리핑 근거를 응답에 싣는다."""
import io
import zipfile

import pytest

from api.deps import get_openai_client_or_none
from api.main import app
from core.ingest.synthetic import generate_bundle

ZIP = {"Content-Type": "application/zip"}
SWAP = {"entries": [{"person_id": "p000", "project_id": "j00", "alloc": 0.5}],
        "swap": {"out_person_id": "p000", "in_person_id": "p001", "project_id": "j00"}}


def test_fixture_dataset_has_a_text_revealing_index(client):
    ev = client.app.state.dataset.evidence
    assert ev is not None and ev.reveal_text is True          # 가상 데이터는 원문 공개


def _zip(root) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(root.iterdir()):
            z.write(f, f.name)
    return buf.getvalue()


@pytest.mark.parametrize("synthetic, reveal", [(True, True), (False, False)])
def test_uploaded_dataset_reveals_text_only_when_synthetic(client, tmp_path, synthetic, reveal):
    import json
    root = generate_bundle(tmp_path / "b", 12, 3, 5)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m["synthetic"] = synthetic
    m.pop("files", None)                                      # 해시 목록은 manifest 수정 뒤 맞지 않는다
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    assert client.post("/api/datasets", content=_zip(root), headers=ZIP).status_code == 200
    ev = client.app.state.dataset.evidence
    assert ev.reveal_text is reveal
    person = client.get("/api/meta").json()["people"][0]["id"]
    kinds = {e.kind for e in ev.for_person(person, limit=None)}
    assert kinds and (kinds <= {"quote", "summary"} if reveal else kinds == {"label"})


def test_whatif_passes_the_same_index_to_context_and_briefing(client, monkeypatch):
    """swap_context에만 넘기면 인용 검증이 꺼진다(K5 리뷰 MUST) -- 둘 다 같은 색인을 받아야 한다."""
    import api.routes.whatif as w
    seen = {}
    real_ctx = w.swap_context

    def ctx(conn, out_id, in_id, evidence=None, project_id=None):
        seen["ctx"] = evidence
        seen["project_id"] = project_id
        return real_ctx(conn, out_id, in_id, evidence=evidence, project_id=project_id)

    def brief(client_, model, c, out_id, in_id, evidence=None, score_change=None):
        seen["brief"] = evidence
        seen["score_change"] = score_change
        return {"rationale": "r", "risks": [], "alternatives": [], "evidence": []}

    monkeypatch.setattr(w, "swap_context", ctx)
    monkeypatch.setattr(w, "generate_briefing", brief)
    app.dependency_overrides[get_openai_client_or_none] = lambda: object()
    try:
        res = client.post("/api/whatif", json=SWAP)
        assert res.status_code == 200
        body = res.json()
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
    assert seen["ctx"] is client.app.state.dataset.evidence
    assert seen["brief"] is seen["ctx"]
    # claude-a 요청: 교체 대상 프로젝트와 점수 변화(전체 = objective_delta)를 설명 재료로 넘긴다.
    assert seen["project_id"] == SWAP["swap"]["project_id"]
    assert set(seen["score_change"]) == {"skill", "synergy", "overfamiliarity", "unfilled", "total"}
    assert seen["score_change"]["total"] == pytest.approx(body["objective_delta"])


def test_llm_quote_round_trips_from_whatif_to_report(client, monkeypatch, tmp_path):
    """LLM이 원문의 일부를 인용하면 whatif가 "직접 인용"으로 싣고, 그 브리핑을 PDF로 다시 보내도 받는다."""
    import json
    from unittest.mock import MagicMock
    index = client.app.state.dataset.evidence
    src = next(e for e in index.for_person("p001") if e.kind == "quote")
    part = src.text[:8]
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({
        "rationale": f"근거 [{src.source_id}]", "risks": [], "alternatives": [],
        "citations": [{"source_id": src.source_id, "quote": part}]})))])
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake
    try:
        body = client.post("/api/whatif", json=SWAP).json()
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
    assert body["fallback_used"] is False, body
    assert body["briefing"]["evidence"] == [{"source_id": src.source_id, "reviewer_id": src.reviewer_id,
                                             "kind": "quote", "text": part}]
    _report_client(monkeypatch, tmp_path)
    assert _report(client, body["briefing"]["evidence"]).status_code == 200


def test_rule_based_briefing_carries_sourced_evidence(client):
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        body = client.post("/api/whatif", json=SWAP).json()
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
    ev = body["briefing"]["evidence"]
    assert ev, "규칙 기반 브리핑도 출처 붙은 근거를 낸다"
    assert all(set(e) == {"source_id", "reviewer_id", "kind", "text"} for e in ev)
    assert all(e["source_id"].startswith("rv:") and e["kind"] in ("quote", "summary") for e in ev)


def _report_client(monkeypatch, tmp_path):
    import api.routes.report as report_route
    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, *a, **k):
        seen.append(payload)
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    return seen


def _report(client, evidence):
    return client.post("/api/report", json={
        "plan_label": "A", "entries": [{"person_id": "p000", "project_id": "j00", "alloc": 1.0}],
        "objective": 1.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
        "briefing": {"rationale": "r", "risks": [], "alternatives": [], "evidence": evidence}})


def _index_evidence(client, kind):
    index = client.app.state.dataset.evidence
    e = next(e for p in client.get("/api/meta").json()["people"]
             for e in index.for_person(p["id"], limit=None) if e.kind == kind)
    return {"source_id": e.source_id, "reviewer_id": e.reviewer_id, "kind": e.kind, "text": e.text}


def test_report_accepts_evidence_that_matches_the_server_index(client, monkeypatch, tmp_path):
    seen = _report_client(monkeypatch, tmp_path)
    ev = [_index_evidence(client, "quote"), _index_evidence(client, "summary")]
    res = _report(client, ev)
    assert res.status_code == 200, res.text
    assert seen[0]["briefing"]["evidence"] == ev
    # 원문의 일부(5자 이상)도 인용으로 받는다 -- LLM 인용·잘린 근거가 그렇다.
    part = {**ev[0], "text": ev[0]["text"][:6]}
    assert _report(client, [part]).status_code == 200


@pytest.mark.parametrize("change", [
    {"text": "근거 마커 XYZZY-QUOTE"},                    # 지어낸 인용
    {"source_id": "rv:nobody>p000#1:pos"},               # 없는 출처
    {"reviewer_id": "p099"},                             # 출처와 다른 리뷰어
    {"kind": "made-up"},                                 # 스키마 밖 종류
])
def test_report_rejects_forged_quote(client, monkeypatch, tmp_path, change):
    """화면이 보낸 아무 문장이 PDF에 "직접 인용"으로 찍히면 안 된다(K5 연결 리뷰 S1)."""
    seen = _report_client(monkeypatch, tmp_path)
    assert _report(client, [{**_index_evidence(client, "quote"), **change}]).status_code == 422
    assert seen == []


def test_report_rejects_summary_relabelled_or_rewritten(client, monkeypatch, tmp_path):
    _report_client(monkeypatch, tmp_path)
    summary = _index_evidence(client, "summary")
    assert _report(client, [{**summary, "text": summary["text"] + " 덧붙임"}]).status_code == 422
    # 요약 문장을 인용으로 바꿔 달아도 원문에 그 글자가 그대로 없으면 거절한다.
    quote = _index_evidence(client, "quote")
    assert _report(client, [{**quote, "kind": "label"}]).status_code == 422


def _upload(client, tmp_path, synthetic, long_item=False):
    import csv
    import json
    root = generate_bundle(tmp_path / "b", 12, 3, 5)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m["synthetic"] = synthetic
    m.pop("files", None)
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    if long_item:
        path = root / "review_items.csv"
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        for r in rows:
            r["item"] = r["item"] + "가" * 2100
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["review_id", "polarity", "item"])
            w.writeheader()
            w.writerows(rows)
    assert client.post("/api/datasets", content=_zip(root), headers=ZIP).status_code == 200


def test_whatif_survives_review_items_longer_than_the_response_limit(client, monkeypatch, tmp_path):
    """실데이터의 긴 리뷰 항목이 응답 스키마 상한(2000자)을 넘어도 500이 아니라 잘린 근거로 답한다
    (K5 연결 리뷰 M1). 잘린 근거는 PDF로 다시 보내도 받는다."""
    _upload(client, tmp_path, synthetic=False, long_item=True)
    people = [p["id"] for p in client.get("/api/meta").json()["people"]]
    project = client.get("/api/meta").json()["projects"][0]["id"]
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        res = client.post("/api/whatif", json={
            "entries": [{"person_id": people[0], "project_id": project, "alloc": 0.5}],
            "swap": {"out_person_id": people[0], "in_person_id": people[1], "project_id": project}})
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
    assert res.status_code == 200, res.text
    ev = res.json()["briefing"]["evidence"]
    assert ev and all(e["kind"] == "label" and len(e["text"]) == 2000 for e in ev)
    _report_client(monkeypatch, tmp_path)
    back = client.post("/api/report", json={
        "plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
        "dataset_version": client.get("/api/meta").json()["dataset_version"],
        "briefing": res.json()["briefing"]})
    assert back.status_code == 200, back.text


def test_report_rejects_quotes_on_a_hidden_text_dataset(client, monkeypatch, tmp_path):
    """실데이터(원문 비공개)에는 인용이 있을 수 없다 -- 라벨 문장을 인용으로 바꿔 달면 거절."""
    _upload(client, tmp_path, synthetic=False)
    _report_client(monkeypatch, tmp_path)
    label = _index_evidence(client, "label")
    version = client.get("/api/meta").json()["dataset_version"]

    def post(evidence):
        return client.post("/api/report", json={
            "plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0,
            "optimization_ratio": 1.0, "dataset_version": version,
            "briefing": {"rationale": "r", "risks": [], "alternatives": [], "evidence": evidence}})

    assert post([label]).status_code == 200
    assert post([{**label, "kind": "quote"}]).status_code == 422
    assert post([{**label, "kind": "summary"}]).status_code == 422


def test_clamp_briefing_fits_the_response_schema():
    from api.briefing_evidence import clamp_briefing
    from api.schemas import BriefingOut
    ev = [{"source_id": f"rv:{i}", "reviewer_id": "p", "kind": "quote", "text": "가" * 3000} for i in range(60)]
    ev.append({"source_id": "rv:" + "x" * 300, "reviewer_id": "p", "kind": "label", "text": "t"})
    out = clamp_briefing({"rationale": "r", "risks": [], "alternatives": [], "evidence": ev[::-1]})
    BriefingOut(**out)                                     # 상한 안에 들어간다
    assert len(out["evidence"]) == 50 and all(len(e["source_id"]) <= 200 for e in out["evidence"])
