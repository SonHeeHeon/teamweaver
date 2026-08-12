import sqlite3
from collections import deque

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


def test_replacement_candidates_matches_dataset(conn):
    """반환 값(co_months 포함)이 Dataset에서 직접 계산한 기대값과 정확히 일치하는지,
    그리고 조인의 두 대칭 방향(후보가 collaboration.a_id인 경우와 b_id인 경우) 모두
    실제로 검증되는지 확인한다. OR 조건의 한쪽 분기가 빠지는 대칭성 회귀(공정성 비교를
    조용히 왜곡할 수 있는 버그)를 이 테스트가 잡아낸다.

    팀 구성을 id가 작은 사람과 큰 사람이 섞이도록 골라야 두 방향이 모두 나타난다 —
    collaboration은 항상 a_id < b_id로 저장되므로(id가 작은 쪽이 a_id), 팀이 전부
    후보들보다 id가 작으면(또는 크면) 조인의 한쪽 분기만 exercise된다.
    """
    c, ds = conn
    ids = [p.id for p in ds.people]
    team = ids[:3] + ids[-3:]
    team_set = set(team)
    skill, min_level = "Java", 3

    expected = []
    candidate_is_a = candidate_is_b = 0
    for p in ds.people:
        if p.id in team_set:
            continue
        level = p.skills.get(skill)
        if level is None or level < min_level:
            continue
        for cw in ds.coworks:
            if cw.a_id == p.id and cw.b_id in team_set:
                expected.append((p.id, level, cw.b_id, cw.co_months))
                candidate_is_a += 1
            elif cw.b_id == p.id and cw.a_id in team_set:
                expected.append((p.id, level, cw.a_id, cw.co_months))
                candidate_is_b += 1

    assert candidate_is_a > 0 and candidate_is_b > 0, (
        "테스트 데이터가 조인의 두 방향을 모두 exercise하지 못한다 — 팀 구성을 조정할 것")

    rows = sqlite_rag.replacement_candidates(c, skill, min_level, team)
    got = [(r["person_id"], r["level"], r["team_peer_id"], r["co_months"]) for r in rows]
    assert sorted(got) == sorted(expected)


def _bfs_hops(coworks, start):
    """ds.coworks로 만든 무방향 그래프에서 start로부터의 최단 홉 수를 계산한다.
    SQL 재귀 CTE가 반환하는 hops가 실제 최단 거리인지(임의의 도달 가능 거리가 아니라)
    검증하는 데 쓴다."""
    graph = {}
    for cw in coworks:
        graph.setdefault(cw.a_id, set()).add(cw.b_id)
        graph.setdefault(cw.b_id, set()).add(cw.a_id)
    dist = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in graph.get(node, ()):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                queue.append(nxt)
    return dist


def test_skill_within_hops_returns_minimal_and_complete(conn):
    """반환된 hops가 BFS로 계산한 실제 최단 거리와 일치하는지(최소성), 스킬 보유자가
    누락 없이 전부 반환되는지(완전성), 중복 행이 없는지를 Dataset에서 직접 계산한
    기대값으로 검증한다."""
    c, ds = conn
    start = ds.people[0].id
    skill, hops = "Java", 4
    by_id = {p.id: p for p in ds.people}
    dist = _bfs_hops(ds.coworks, start)

    expected_ids = {pid for pid, d in dist.items()
                    if 0 < d <= hops and by_id[pid].skills.get(skill) is not None}
    assert len(expected_ids) > 3, "테스트가 유의미하려면 여러 홉수의 후보가 필요하다"

    rows = sqlite_rag.skill_within_hops(c, start, skill, hops)
    seen = set()
    for r in rows:
        assert r["person_id"] not in seen, f"중복 행: {r['person_id']}"
        seen.add(r["person_id"])
        assert by_id[r["person_id"]].skills.get(skill) == r["level"]
        assert r["hops"] == dist[r["person_id"]], "최단 홉 수가 아니다"

    assert seen == expected_ids


def test_team_cohesion_matches_dataset(conn):
    """반환된 쌍 집합이 팀 내부 협업 엣지 전체와 정확히 일치하는지(완전성·중복 없음),
    co_months·polarity 값이 Dataset(및 그로부터 결정론적으로 재생성한 파싱 결과)과
    일치하는지 검증한다. 리뷰가 없는 쌍은 polarity가 None이어야 한다."""
    c, ds = conn
    team = [p.id for p in ds.people[:30]]
    team_set = set(team)
    parsed = parse_reviews_rule_based(ds.reviews)

    pair_polarities = {}
    for pr in parsed:
        key = tuple(sorted((pr.reviewer_id, pr.reviewee_id)))
        pair_polarities.setdefault(key, []).append(pr.text_polarity)

    expected = {}
    for cw in ds.coworks:
        if cw.a_id in team_set and cw.b_id in team_set:
            key = tuple(sorted((cw.a_id, cw.b_id)))
            pols = pair_polarities.get(key, [])
            expected[key] = (cw.co_months, sum(pols) / len(pols) if pols else None)
    assert len(expected) > 5, "테스트가 유의미하려면 팀 내부 쌍이 여러 개 필요하다"

    rows = sqlite_rag.team_cohesion(c, team)
    assert len(rows) == len(expected), "행 개수가 다르다(누락 또는 중복 의심)"
    got = {}
    for r in rows:
        key = (r["a_id"], r["b_id"])
        assert key not in got, f"중복 쌍: {key}"
        got[key] = (r["co_months"], r["polarity"])
    assert set(got) == set(expected)

    for key, (exp_months, exp_pol) in expected.items():
        got_months, got_pol = got[key]
        assert got_months == exp_months
        if exp_pol is None:
            assert got_pol is None
        else:
            assert got_pol == pytest.approx(exp_pol)
