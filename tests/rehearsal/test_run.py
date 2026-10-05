"""Scale rehearsal runner helpers (rehearsal/run.py). The full runs are manual (minutes to an hour)."""
from pathlib import Path

from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from rehearsal.run import _pick_swap, _sse, _zip


def test_sse_parser_yields_events_with_json():
    lines = ["event: plan", 'data: {"label": "A"}', "", "event: done", 'data: {"count": 1}', ""]
    assert list(_sse(lines)) == [("plan", {"label": "A"}), ("done", {"count": 1})]


def test_zip_contains_every_bundle_file(tmp_path):
    import io, zipfile
    root = generate_org_bundle(tmp_path / "b", 100, seed=3)
    names = set(zipfile.ZipFile(io.BytesIO(_zip(root))).namelist())
    assert names == {p.name for p in root.iterdir()}


def test_swap_picks_someone_available_for_every_project_month(tmp_path):
    root = generate_org_bundle(tmp_path / "b", 100, seed=3)
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    j = g.projects[g.project_index["J001"]]
    plan = {"entries": [{"person_id": p.id, "project_id": "J001", "alloc": 0.5} for p in g.people[:5]]}
    swap = _pick_swap(g, plan, "J001", 0.3)
    assert swap["out_person_id"] in {p.id for p in g.people[:5]}
    inn = g.people[g.pid_index[swap["in_person_id"]]]
    assert swap["in_person_id"] not in {p.id for p in g.people[:5]}
    assert all(inn.availability[m] >= 0.3 for m in range(j.start_month, j.end_month + 1))
