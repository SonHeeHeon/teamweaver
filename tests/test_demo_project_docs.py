"""시연용 가상 발주 자료(demo/project_docs, scripts/make_demo_project_docs.py)가 시연 묶음과 맞는지(2026-10-23, claude-b).

파일 해시·크기가 목록과 같고, 사업이 그 묶음에 있으며, PDF 글자에 "가상 자료"와 그 사업의 요구 기술이 모두 들어 있어야 한다
(AI 필요 기술 읽기 측정·화면 시연이 같은 자료를 쓴다). 생성기가 요구를 바꾸면 이 시험이 실패한다 -- 도구를 다시 돌린다."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "demo" / "project_docs"


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _manifests():
    out = {}
    for m in (ROOT / "demo").glob("*/manifest.json"):
        data = json.loads(m.read_text(encoding="utf-8"))
        out[data["dataset_id"]] = m.parent
    return out


def test_demo_documents_match_their_bundles():
    import io
    from pypdf import PdfReader
    from api.project_docs import KINDS
    index = json.loads((DOCS / "index.json").read_text(encoding="utf-8"))
    bundles = _manifests()
    assert index["docs"], "시연 자료가 없다"
    for d in index["docs"]:
        data = (DOCS / d["file"]).read_bytes()
        assert len(data) == d["size"] and hashlib.sha256(data).hexdigest() == d["sha256"], d["file"]
        assert d["kind"] in KINDS and "가상" in d["filename"]
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        assert "가상 자료" in text
        assert d["registered_at"]
        for ds, reg in d["registered_at"].items():
            folder = bundles[ds]
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["synthetic"] is True
            # 등록 시각이 그 묶음과 같아야 서버가 보인다(번호를 다시 쓴 사업에는 안 보이게)
            assert reg == ((manifest.get("pipeline") or {}).get(d["project_id"]) or {}).get("registered_at"), (ds, d["project_id"])
            projects = {r["project_id"]: r for r in _rows(folder / "projects.csv")}
            assert d["project_id"] in projects, (ds, d["project_id"])
            assert projects[d["project_id"]]["project_name"] in text.replace("\n", " ")
            skills = [r["skill_name"] for r in _rows(folder / "project_skill_requirements.csv") if r["project_id"] == d["project_id"]]
            assert skills and all(s in text for s in skills), (d["file"], skills)
