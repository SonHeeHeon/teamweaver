import sqlite3
import pytest
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite
from core.rag import sqlite_rag
from core.rag.queries import RAG_QUERIES, RETURN_KEYS


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    ds = generate_dataset(60, 12, seed=42)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path_factory.mktemp("rag") / "rag.db"
    build_sqlite(ds, parsed, path)
    c = sqlite3.connect(path)
    yield c, ds
    c.close()


def test_all_five_implemented():
    assert set(sqlite_rag.ALL) == set(RAG_QUERIES)


def test_return_keys_match_contract(conn):
    c, ds = conn
    ids = [p.id for p in ds.people]
    calls = {
        "swap_diff": lambda: sqlite_rag.swap_diff(c, ids[0], ids[1]),
        "replacement_candidates": lambda: sqlite_rag.replacement_candidates(
            c, "Java", 3, ids[:5]),
        "team_cohesion": lambda: sqlite_rag.team_cohesion(c, ids[:6]),
        "overfamiliar_pairs": lambda: sqlite_rag.overfamiliar_pairs(c, 6),
        "skill_within_hops": lambda: sqlite_rag.skill_within_hops(c, ids[0], "Java", 2),
    }
    for name, call in calls.items():
        rows = call()
        assert isinstance(rows, list)
        for r in rows:
            assert tuple(r.keys()) == RETURN_KEYS[name], f"{name} 반환 키 불일치"


def test_overfamiliar_pairs_respects_threshold(conn):
    c, ds = conn
    rows = sqlite_rag.overfamiliar_pairs(c, 6)
    assert all(r["co_months"] >= 6 for r in rows)
    expected = {tuple(sorted((x.a_id, x.b_id))) for x in ds.coworks if x.co_months >= 6}
    assert {tuple(sorted((r["a_id"], r["b_id"]))) for r in rows} == expected


def test_skill_within_hops_only_returns_skill_holders(conn):
    c, ds = conn
    rows = sqlite_rag.skill_within_hops(c, ds.people[0].id, "Java", 3)
    by_id = {p.id: p for p in ds.people}
    for r in rows:
        assert by_id[r["person_id"]].skills.get("Java") == r["level"]
        assert 1 <= r["hops"] <= 3
        assert r["person_id"] != ds.people[0].id


def test_team_cohesion_pairs_are_within_team(conn):
    c, ds = conn
    team = [p.id for p in ds.people[:6]]
    for r in sqlite_rag.team_cohesion(c, team):
        assert r["a_id"] in team and r["b_id"] in team
        assert r["a_id"] < r["b_id"], "쌍은 정렬된 형태로 한 번만 나와야 한다"
