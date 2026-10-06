"""Demo bundle list and switching (2026-10-06): the demo shows the planning scene (everyone placed from scratch) and the
operating scene (most people already on running projects, a few freed people staffed onto new proposals)."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from core.ingest.org_profile import generate_org_bundle

JSON = {"Content-Type": "application/json"}


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    generate_org_bundle(root / "org-n100", 100, seed=11)
    op = generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:                 # same operating bundle as a zip (200/300 ship as zips)
        for f in sorted(op.iterdir()):
            zf.writestr(f.name, f.read_bytes())
    (root / "org-z100-operating.zip").write_bytes(buf.getvalue())
    broken = generate_org_bundle(root / "org-n100-broken", 100, seed=11)
    (broken / "people.csv").write_text("person_id\n", encoding="utf-8")
    bad = generate_org_bundle(root / "org-bad", 100, seed=11)          # listed (no "-broken") but fails validation
    (bad / "people.csv").write_text("person_id\n", encoding="utf-8")
    (root / ".tmp-n200").mkdir()
    return root


@pytest.fixture
def demo_client(demo_root, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(demo_root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(demo_root / "org-n100"))
    from api.main import app
    with TestClient(app) as c:
        yield c


def _switch(c, preset_id, **headers):
    return c.post("/api/datasets/demo", json={"id": preset_id}, headers={**JSON, **headers})


def test_lists_both_scenes_and_skips_the_upload_check_bundle(demo_client):
    body = demo_client.get("/api/datasets/demos").json()
    ids = [p["id"] for p in body["presets"]]
    assert ids == ["org-bad", "org-n100", "org-n100-operating", "org-z100-operating"]
    assert body["active"] == body["default"] == "org-n100"
    by = {p["id"]: p for p in body["presets"]}
    assert by["org-n100"]["scenario"] == "planning" and by["org-n100"]["title"].startswith("연초 계획 · 100명")
    op = by["org-n100-operating"]
    assert op["scenario"] == "operating" and (op["current"], op["bench"], op["proposals"]) == (90, 10, 2)
    assert op["title"] == "운영 중 · 90명 배치 중, 대기 10명"


def test_switching_to_the_operating_scene_and_back(demo_client):
    res = _switch(demo_client, "org-n100-operating")
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["source"] == "demo-bundle" and info["dataset_id"].endswith("-operating") and info["demo_preset"] == "org-n100-operating"
    assert demo_client.get("/api/datasets/active").json()["demo_preset"] == "org-n100-operating"
    assert demo_client.get("/api/datasets/demos").json()["active"] == "org-n100-operating"
    assert demo_client.get("/api/meta").json()["dataset_version"] == info["version"]
    back = demo_client.post("/api/datasets/reset", headers=JSON).json()
    assert back["demo_preset"] == "org-n100" and back["dataset_id"].endswith("-s11")
    assert demo_client.get("/api/datasets/active").json()["demo_preset"] == "org-n100"


def test_a_zip_preset_switches_like_a_folder(demo_client):
    res = _switch(demo_client, "org-z100-operating")
    assert res.status_code == 200 and res.json()["dataset_id"].endswith("-operating")


def test_rejudge_rebuilds_the_chosen_preset_not_the_default(demo_client):
    _switch(demo_client, "org-n100-operating")
    res = demo_client.post("/api/datasets/rejudge", headers=JSON)
    assert res.status_code == 200 and res.json()["dataset_id"].endswith("-operating")


def test_errors_keep_the_current_data(demo_client):
    before = demo_client.get("/api/datasets/active").json()["version"]
    assert _switch(demo_client, "nope").status_code == 404
    assert _switch(demo_client, "org-bad").status_code == 422
    assert demo_client.post("/api/datasets/demo", content=b'{"id":"org-n100-operating"}',
                            headers={"Content-Type": "text/plain"}).status_code == 415
    assert demo_client.post("/api/datasets/demo", content=b'{"id":', headers=JSON).status_code == 422
    demo_client.app.state.dataset_switching = True
    try:
        assert _switch(demo_client, "org-n100-operating").status_code == 409
    finally:
        demo_client.app.state.dataset_switching = False
    assert demo_client.get("/api/datasets/active").json()["version"] == before


def test_switching_drops_a_stored_upload(demo_client, demo_root):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted((demo_root / "org-n100").iterdir()):
            zf.writestr(f.name, f.read_bytes())
    up = demo_client.post("/api/datasets", content=buf.getvalue(), headers={"Content-Type": "application/zip"})
    assert up.status_code == 200 and up.json()["persisted"]
    assert demo_client.app.state.dataset_store.load() is not None
    assert _switch(demo_client, "org-n100-operating").status_code == 200
    assert demo_client.app.state.dataset_store.load() is None


def test_switching_needs_the_admin_when_protected(demo_client, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "s3cret")
    assert _switch(demo_client, "org-n100-operating").status_code == 401
    assert demo_client.get("/api/datasets/demos").status_code == 200          # reading the list is open
    assert _switch(demo_client, "org-n100-operating", **{"X-Admin-Token": "s3cret"}).status_code == 200


def test_no_demo_dir_means_an_empty_list(monkeypatch):
    monkeypatch.delenv("TEAMWEAVER_DEMO_DIR", raising=False)
    monkeypatch.delenv("TEAMWEAVER_DEMO_BUNDLE", raising=False)
    from api.main import app
    with TestClient(app) as c:
        body = c.get("/api/datasets/demos").json()
        assert body == {"presets": [], "active": None, "default": None}
        assert c.get("/api/datasets/active").json()["demo_preset"] is None
