"""Demo bundle picker additions (2026-10-07, claude-a on top of claude-b's api/demos.py -- user decision to keep
claude-b's contract): zip bundles (200/300 ship as zips), the upload-check bundle stays out of the list, a title and a
one-line description per scene, and the chosen bundle is what rejudge and the precompute lookup use."""
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
    with zipfile.ZipFile(buf, "w") as zf:                 # the operating bundle again, as a zip
        for f in sorted(op.iterdir()):
            zf.writestr(f.name, f.read_bytes())
    (root / "org-z100-operating.zip").write_bytes(buf.getvalue())
    (root / "org-n100-broken.zip").write_bytes(buf.getvalue())         # upload-check file: never listed
    (root / ".tmp-n200").mkdir()
    return root


@pytest.fixture
def demo_client(demo_root, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(demo_root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(demo_root / "org-n100"))
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_list_has_zips_titles_and_order(demo_client):
    demos = demo_client.get("/api/datasets/demos").json()
    assert [d["name"] for d in demos] == ["org-n100", "org-n100-operating", "org-z100-operating"]
    by = {d["name"]: d for d in demos}
    assert {"name", "dataset_id", "people", "projects", "scenario", "synthetic"} <= set(by["org-n100"])   # claude-b's fields
    assert by["org-n100"]["title"].startswith("연초 계획 · 100명")
    op = by["org-z100-operating"]
    assert op["scenario"] == "operating" and (op["people"], op["current"], op["bench"], op["proposals"]) == (100, 90, 10, 2)
    assert op["title"] == "운영 중 · 90명 배치 중, 대기 10명" and "신규 제안 2개" in op["description"]


def test_a_zip_bundle_can_be_chosen_and_is_remembered_for_rejudge_and_status(demo_client):
    assert demo_client.get("/api/datasets/active").json()["demo_name"] == "org-n100"
    res = demo_client.post("/api/datasets/demo", json={"name": "org-z100-operating"}, headers=JSON)
    assert res.status_code == 200, res.text
    assert res.json()["dataset_id"].endswith("-operating") and res.json()["demo_name"] == "org-z100-operating"
    assert res.json()["scenario"] == "operating"          # 화면의 '전부 다시 짜기' 안내(claude-b)
    assert demo_client.get("/api/datasets/active").json()["demo_name"] == "org-z100-operating"
    again = demo_client.post("/api/datasets/rejudge", headers=JSON)
    assert again.status_code == 200 and again.json()["dataset_id"].endswith("-operating")
    back = demo_client.post("/api/datasets/reset", headers=JSON)
    assert back.status_code == 200
    assert demo_client.get("/api/datasets/active").json()["demo_name"] == "org-n100"


def test_the_upload_check_bundle_cannot_be_chosen(demo_client):
    assert demo_client.post("/api/datasets/demo", json={"name": "org-n100-broken"}, headers=JSON).status_code == 404
