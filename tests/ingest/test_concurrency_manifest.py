"""manifest `concurrency`(프로젝트 동시 수행 허용, 2026-10-19) → Person.max_concurrent."""
import json

from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle


def _with(tmp_path, entries):
    root = generate_org_bundle(tmp_path / "b", 100, seed=5)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m.pop("files", None)
    m["concurrency"] = entries
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    return load_bundle(root)


def test_a_granted_person_gets_their_limit_and_others_stay_at_one(tmp_path):
    b, rep = _with(tmp_path, [{"person_id": "DP0003", "max_projects": 2, "reason": "기술 지원", "memo": "잠깐 봐줌"}])
    assert rep.errors == []
    ds, _ = to_dataset(b, rep)
    limits = {p.id: p.max_concurrent for p in ds.people}
    assert limits["DP0003"] == 2 and {v for k, v in limits.items() if k != "DP0003"} == {1}


def test_bad_grants_are_rejected(tmp_path):
    b, rep = _with(tmp_path, [{"person_id": "NOPE", "max_projects": 2, "reason": "기술 지원"},
                              {"person_id": "DP0003", "max_projects": 5, "reason": "그냥"},
                              {"person_id": "DP0003", "max_projects": 2, "reason": "인력 부족"}])
    text = " ".join(str(e) for e in rep.errors)
    assert "없는 사람" in text and "max_projects" in text and "reason" in text and "중복" in text


def test_no_grants_means_nobody_runs_two_projects(tmp_path):
    root = generate_org_bundle(tmp_path / "plain", 100, seed=5)
    b, rep = load_bundle(root)
    ds, _ = to_dataset(b, rep)
    assert {p.max_concurrent for p in ds.people} == {1}


def test_a_non_string_person_id_is_a_report_error_not_a_crash(tmp_path):
    b, rep = _with(tmp_path, [{"person_id": ["DP0003"], "max_projects": 2, "reason": "기술 지원"}])
    assert any("없는 사람" in str(e) for e in rep.errors)
