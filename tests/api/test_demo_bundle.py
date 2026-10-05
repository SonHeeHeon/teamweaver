"""The server boots from a CSV demo bundle when TEAMWEAVER_DEMO_BUNDLE is set (demo/org-n100 in run_poc.sh)."""
from fastapi.testclient import TestClient

from core.ingest.org_profile import generate_org_bundle


def test_server_boots_from_the_demo_bundle(tmp_path, monkeypatch):
    root = generate_org_bundle(tmp_path / "org-n100", 100, seed=11)
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(root))
    from api.main import app
    with TestClient(app) as c:
        info = c.get("/api/datasets/active").json()
        assert info["source"] == "demo-bundle" and info["synthetic"] is True
        assert info["dataset_id"] == "org-n100-s11" and info["people"] == 100 and info["projects"] == 20
        meta = c.get("/api/meta").json()
        assert all(p["id"].startswith("DP") for p in meta["people"])


def test_without_the_variable_the_fixture_is_used(monkeypatch):
    monkeypatch.delenv("TEAMWEAVER_DEMO_BUNDLE", raising=False)
    from api.main import app
    with TestClient(app) as c:
        assert c.get("/api/datasets/active").json()["source"] == "fixture"


def test_a_broken_demo_bundle_falls_back_to_the_fixture_with_a_reason(tmp_path, monkeypatch):
    root = generate_org_bundle(tmp_path / "org-n100", 100, seed=11)
    (root / "people.csv").write_text("person_id\n", encoding="utf-8")       # breaks the contract
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(root))
    from api.main import app
    with TestClient(app) as c:
        info = c.get("/api/datasets/active").json()
        assert info["source"] == "fixture"
        assert "시연 데이터 묶음" in (info.get("restore_error") or "")
