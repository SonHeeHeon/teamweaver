"""Organisation-shaped rehearsal bundles (core/ingest/org_profile.py)."""
import json
import re

import pytest

from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import FLAGSHIP, LLM_ERA, LLM_ERA_MONTHS, generate_org_bundle
from tests.name_guard import banned_hits


@pytest.fixture(scope="module", params=[100, 200, 300])
def bundle(request, tmp_path_factory):
    size = request.param
    root = generate_org_bundle(tmp_path_factory.mktemp(f"n{size}"), size, seed=7)
    b, report = load_bundle(root)
    ds, parsed = to_dataset(b, report)
    return size, root, b, report, ds


def test_bundle_passes_the_contract_with_no_errors(bundle):
    _, _, _, report, _ = bundle
    assert report.errors == [] and report.warnings == []


def test_group_composition_matches_the_size(bundle):
    size, _, b, _, ds = bundle
    groups = {}
    for row in b.tables["people.csv"]:
        groups[row["person_id"][:2]] = groups.get(row["person_id"][:2], 0) + 1
    assert groups == {100: {"DP": 100}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 100, "AU": 100}}[size]
    # v5(2026-10-19): 사람 5명당 사업 1개(실행 사업 대부분이 계획 전에 시작한 진행 사업) + 운영 사업의 추가 과제(100명 1 · 200·300명 2)
    assert len(ds.projects) == size // 5 + {100: 1, 200: 2, 300: 2}[size]


def test_flagship_keeps_its_internal_seats(bundle):
    size, _, _, _, ds = bundle
    flagship = next(j for j in ds.projects if j.id == FLAGSHIP["project_id"])
    assert sum(flagship.grade_headcount.values()) == (8 if size == 100 else 28)
    assert flagship.phase.value == "실행"


def test_no_real_client_names_are_written(bundle):
    _, root, _, _, _ = bundle
    text = "".join(p.read_text(encoding="utf-8") for p in root.iterdir())
    assert banned_hits(text) == []                      # 이름은 적지 않고 지문으로 대조(tests/name_guard.py)


def test_llm_era_skills_never_exceed_the_era(bundle):
    _, _, b, _, _ = bundle
    for row in b.tables["person_skills.csv"]:
        if row["skill_name"] in LLM_ERA:
            assert row["experience_months"] <= LLM_ERA_MONTHS


def test_people_have_about_twenty_skills(bundle):
    """생성기가 만든 보유 기술 수(최근 10년 안에 쓴 것). 기술 이름 사전의 부분 인정으로 생기는 상위 기술(예: Spring → Java)은
    생성기 성질이 아니라서 세지 않는다(2026-10-09) -- 원천 행으로 센다."""
    from core.ingest.convert import lookback_start
    _, _, b, _, ds = bundle
    since = lookback_start(b.horizon[0])
    own = {(r["person_id"], r["skill_name"]) for r in b.tables["person_skills.csv"]
           if r["experience_months"] and (r.get("last_used_month") is None or r["last_used_month"] >= since)}
    avg = len(own) / len(ds.people)
    assert 15 <= avg <= 22


def test_projects_mix_execution_and_proposal_with_labels(bundle):
    _, _, b, _, _ = bundle
    rows = b.tables["projects.csv"]
    phases = [r["phase"] for r in rows]
    assert 0.2 <= phases.count("제안") / len(phases) <= 0.45
    assert all(re.match(r"^\[(제안|실행|운영|고도화|추가 과제)\] ", r["project_name"]) for r in rows[1:])


def test_generation_is_deterministic(tmp_path):
    a = generate_org_bundle(tmp_path / "a", 200, seed=11)
    c = generate_org_bundle(tmp_path / "c", 200, seed=11)
    assert json.loads((a / "manifest.json").read_text())["files"] == json.loads((c / "manifest.json").read_text())["files"]


def test_unknown_size_is_refused(tmp_path):
    with pytest.raises(ValueError):
        generate_org_bundle(tmp_path, 150, seed=1)


def test_current_roster_respects_grades_availability_and_locks(bundle):
    size, _, b, _, ds = bundle
    rows = b.tables["current_assignments.csv"]
    assert rows and any(r["locked"] == "Y" for r in rows) or size == 100
    grade = {p["person_id"]: p["career_grade"] for p in b.tables["people.csv"]}
    asked = {(r["project_id"], r["career_grade"]) for r in b.tables["project_grade_requirements.csv"]}
    assert all((r["project_id"], grade[r["person_id"]]) in asked for r in rows)
    assert all(0.3 <= r["alloc"] <= 1.0 for r in rows)
    assert len(ds.current) == len(rows) and any(c.project_id == "J001" for c in ds.current)


def test_roster_does_not_change_the_other_tables(tmp_path, monkeypatch):
    """The roster has its own RNG stream: with it removed, every other file is byte-identical -- except the files the
    v5 ongoing-history link derives from the roster (work history, skill months, reviews of the running projects)."""
    import core.ingest.org_profile as op
    a = generate_org_bundle(tmp_path / "a", 200, seed=5)
    monkeypatch.setattr(op, "_current_roster", lambda *args, **kw: [])
    c = op.generate_org_bundle(tmp_path / "c", 200, seed=5)
    linked = ("work_history.csv", "person_skills.csv", "reviews.csv", "review_items.csv")
    for f in a.iterdir():
        if f.name not in ("current_assignments.csv", "manifest.json") + linked:
            assert f.read_bytes() == (c / f.name).read_bytes(), f.name


def test_reviews_are_long_contextual_and_use_the_org_item_list(bundle):
    from core.ingest.review_text import ORG_REVIEW_ITEMS
    _, root, b, _, _ = bundle
    style = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["review_style"]
    short = set(style["harsh_reviews"]) | set(style["blunt_reviews"])
    reviews = [r for r in b.tables["reviews.csv"] if r["review_id"] not in short]   # 직설형·독한 글은 칭찬이 한 줄(2026-10-11)
    assert all(r["positive_text"].count("다.") >= 2 for r in reviews)           # opener + at least one strength
    assert sum(len(r["positive_text"]) for r in reviews) / len(reviews) > 120
    assert {i["item"] for i in b.tables["review_items.csv"]} <= set(ORG_REVIEW_ITEMS)
    assert "기획력" in ORG_REVIEW_ITEMS and "친화력" in ORG_REVIEW_ITEMS and len(ORG_REVIEW_ITEMS) == 20


def test_about_ten_reviews_per_person_a_year_and_ten_year_history(bundle):
    size, _, b, _, _ = bundle
    per_year = len(b.tables["reviews.csv"]) / size / 1.5              # three half-year rounds
    assert 6 <= per_year <= 12
    first = min(w["start_date"] for w in b.tables["work_history.csv"])
    horizon = b.horizon[0]
    assert (horizon.year - first.year) * 12 + horizon.month - first.month <= 120


def _bundle_with_abilities(tmp_path, monkeypatch, n=200, seed=3):
    import core.ingest.org_profile as op
    seen = {}
    real = op._person_traits

    def spy(rng, people):
        real(rng, people)
        seen.update({p["person_id"]: p["_ability"] for p in people})
    monkeypatch.setattr(op, "_person_traits", spy)
    b, rep = load_bundle(op.generate_org_bundle(tmp_path / "b", n, seed=seed))
    assert rep.errors == []
    return b, seen


def _corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5


def test_outcomes_follow_a_hidden_rule_that_reviews_only_partly_reveal(tmp_path, monkeypatch):
    """Outcomes cover scores 1-5, customer replacements only on poor projects, better hidden ability -> better
    outcomes. Reviews reflect ability only partly (measured 0.3-0.45; it was 0.97 before the Opus review MUST),
    so calibrating on outcomes cannot just read the answer back from the review term."""
    import collections
    b, ability = _bundle_with_abilities(tmp_path, monkeypatch)
    out = {r["project_code"]: r for r in b.tables["project_outcomes.csv"]}
    assert {r["customer_score"] for r in out.values()} == {1, 2, 3, 4, 5}
    for r in b.tables["replacements.csv"]:
        if r["requested_by"] == "고객":
            assert out[r["project_code"]]["customer_score"] <= 2
    team = collections.defaultdict(set)
    for w in b.tables["work_history.csv"]:
        team[w["project_code"]].add(w["person_id"])
    codes = sorted(out)
    team_ability = [sum(ability[m] for m in team[c]) / len(team[c]) for c in codes]
    assert _corr(team_ability, [out[c]["customer_score"] for c in codes]) > 0.3
    n_items = collections.Counter((i["review_id"], i["polarity"]) for i in b.tables["review_items.csv"])
    pol = collections.defaultdict(list)
    for r in b.tables["reviews.csv"]:
        pol[r["reviewee_id"]].append(n_items[(r["review_id"], "positive")] - n_items[(r["review_id"], "negative")])
    ids = sorted(pol)
    c = _corr([sum(pol[p]) / len(pol[p]) for p in ids], [ability[p] for p in ids])
    assert 0.15 < c < 0.7


def test_replaced_people_leave_mid_stint_and_have_no_later_row(bundle):
    _, _, b, _, _ = bundle
    rows = {}
    for w in b.tables["work_history.csv"]:
        rows.setdefault((w["project_code"], w["person_id"]), []).append(w)
    reps = b.tables["replacements.csv"]
    assert reps
    for r in reps:
        stints = rows[(r["project_code"], r["person_id"])]
        assert max(w["end_date"] for w in stints) == r["replaced_at"]


def test_pre_llm_work_summaries_do_not_mention_llm_skills(bundle):
    from core.ingest.org_profile import LLM_ERA
    _, _, b, _, _ = bundle
    horizon = b.horizon[0]
    for w in b.tables["work_history.csv"]:
        months_before = (horizon.year - w["end_date"].year) * 12 + horizon.month - w["end_date"].month
        if months_before > 36:
            assert not any(s in (w["summary"] or "") for s in LLM_ERA), w["summary"]


def test_unknown_people_in_replacements_are_a_warning_not_an_error(tmp_path):
    import csv
    from core.ingest.org_profile import generate_org_bundle
    root = generate_org_bundle(tmp_path / "b", 100, seed=5)
    path = root / "replacements.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0]["person_id"] = "LEFT-0001"                      # someone who has left the company
    import json
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m.pop("files", None)                                    # the edit would otherwise fail the hash check
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    _, rep = load_bundle(root)
    assert rep.errors == []
    assert any("LEFT-0001" in str(x) for x in rep.warnings)


def test_committed_demo_bundle_validates_and_matches_the_generator(tmp_path):
    """demo/org-n100 is what run_poc.sh boots; regenerate with `python -m rehearsal.make_demo` after
    changing the generator."""
    from pathlib import Path
    from core.ingest.org_profile import generate_org_bundle
    from rehearsal.run import SEED
    demo = Path(__file__).resolve().parents[2] / "demo" / "org-n100"
    _, rep = load_bundle(demo)
    assert rep.errors == []
    fresh = generate_org_bundle(tmp_path / "fresh", 100, seed=SEED)
    for f in sorted(fresh.iterdir()):
        assert (demo / f.name).read_bytes() == f.read_bytes(), f"{f.name} is stale: rerun python -m rehearsal.make_demo"
    operating = demo.parent / "org-n100-operating"
    fresh_op = generate_org_bundle(tmp_path / "fresh-op", 100, seed=SEED, scenario="operating")
    for f in sorted(fresh_op.iterdir()):
        assert (operating / f.name).read_bytes() == f.read_bytes(), f"operating {f.name} is stale: rerun make_demo"
    import io
    import zipfile
    for n, scenario, suffix in [(n, sc, sf) for n in (200, 300) for sc, sf in (("planning", ""), ("operating", "-operating"))]:
        root = generate_org_bundle(tmp_path / f"n{n}{suffix}", n, seed=SEED, scenario=scenario)
        with zipfile.ZipFile(demo.parent / f"org-n{n}{suffix}.zip") as zf:
            assert sorted(zf.namelist()) == sorted(p.name for p in root.iterdir())
            for p in root.iterdir():
                assert zf.read(p.name) == p.read_bytes(), f"org-n{n}{suffix}.zip is stale: rerun python -m rehearsal.make_demo"


def test_senior_careers_fill_the_window_and_juniors_start_late(bundle):
    """Careers are longer than 10 years for seniors (up to 25), but only the last 10 years are exported."""
    _, _, b, _, _ = bundle
    grade = {p["person_id"]: p["career_grade"] for p in b.tables["people.csv"]}
    first = {}
    for w in b.tables["work_history.csv"]:
        first[w["person_id"]] = min(first.get(w["person_id"], w["start_date"]), w["start_date"])
    window = min(first.values())
    seniors = [d for pid, d in first.items() if grade[pid] == "특급"]
    juniors = [d for pid, d in first.items() if grade[pid] == "초급"]
    # 13+ years of service: the window is full, except someone between assignments right at its start
    assert sum(d == window for d in seniors) / len(seniors) > 0.8
    assert all((d.year - window.year) * 12 + d.month - window.month <= 12 for d in seniors)
    assert all(d > window for d in juniors)                              # at most 6 years of service
    assert max(int(s["experience_months"]) for s in b.tables["person_skills.csv"]) <= 120



def test_operating_scenario_matches_the_described_situation(tmp_path):
    """User: most people are already on running projects; 2-3 new proposals are staffed from ~10 people just freed."""
    import json
    from core.ingest.org_profile import generate_org_bundle
    root = generate_org_bundle(tmp_path / "op", 100, seed=7, scenario="operating")
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    b, report = load_bundle(root)
    ds, _ = to_dataset(b, report)
    # v4(2026-10-19): 신규 제안 2개 + 운영 사업에 붙은 추가 과제 1개(작업 데이터 pipeline 모양)
    assert report.errors == [] and m["scenario"] == "operating" and len(m["proposals"]) == 3
    addon = m["proposals"][-1]
    assert m["pipeline"][addon]["parent"] in {j for j, e in m["lifecycle"].items() if e["stage"] == "운영"}
    on = {c.person_id for c in ds.current}
    # 생성기 v3(2026-10-19): 계획 기간에 육아휴직 달이 걸린 사람은 진행 사업·대기 인력 어디에도 없다
    away = {p.id for p in ds.people if min(p.availability) < 1.0}
    assert away and not (away & on) and not (away & set(m["bench"]))
    assert len(m["bench"]) == 10 and set(m["bench"]) == {p.id for p in ds.people} - on - away
    for p in ds.projects:
        team = [c for c in ds.current if c.project_id == p.id]
        if p.id in m["proposals"]:
            assert team == [] and p.start_month == 1
        else:                                   # a running project's seats are exactly its current team
            grades = {}
            for c in team:
                g = next(pp.grade for pp in ds.people if pp.id == c.person_id)
                grades[g] = grades.get(g, 0) + 1
            assert grades == {g: k for g, k in p.grade_headcount.items() if k}
    plan = generate_org_bundle(tmp_path / "plan", 100, seed=7)
    assert (root / "people.csv").read_bytes() == (plan / "people.csv").read_bytes()      # same organisation


# ---------------------------------------------------------------- 2026-10-11: 가상 이름·시연 글 다듬기(claude-b 요청, 사용자 결정)
def test_people_have_unique_virtual_korean_names(bundle):
    from core.ingest.names import virtual_name
    _, _, b, _, ds = bundle
    rows = b.tables["people.csv"]
    names = [r["display_name"] for r in rows]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[가-힣]{3}", n) for n in names)                     # 성 한 글자 + 이름 두 글자, "가상DP0001" 아님
    assert all(r["display_name"] == virtual_name(r["person_id"]) for r in rows)   # 같은 ID는 어느 묶음에서든 같은 이름
    assert {p.id: p.name for p in ds.people} == {r["person_id"]: r["display_name"] for r in rows}


def test_review_style_changes_only_the_prose(tmp_path):
    """직설형·어긋남을 끄고 켜도 고른 항목·사람·이력은 바이트 단위로 같다(항목 난수열 불변) -- 바뀌는 것은 글과 manifest뿐."""
    a = generate_org_bundle(tmp_path / "a", 200, seed=5)
    c = generate_org_bundle(tmp_path / "c", 200, seed=5, blunt_share=0.0, mismatch_share=0.0)
    for f in a.iterdir():
        if f.name not in ("reviews.csv", "manifest.json"):
            assert f.read_bytes() == (c / f.name).read_bytes(), f.name
    style = json.loads((c / "manifest.json").read_text(encoding="utf-8"))["review_style"]
    assert style["blunt_reviewers"] == [] and style["harsh_reviews"] == [] and style["soft_reviews"] == []


def test_weaknesses_get_one_sentence_per_item_in_normal_reviews(tmp_path):
    from core.ingest.review_text import NEGATIVE, NEGATIVE_ALT
    root = generate_org_bundle(tmp_path / "n", 100, seed=5, blunt_share=0.0, mismatch_share=0.0)
    b, _ = load_bundle(root)
    neg = {}
    for i in b.tables["review_items.csv"]:
        if i["polarity"] == "negative":
            neg.setdefault(i["review_id"], []).append(i["item"])
    for r in b.tables["reviews.csv"]:
        items = neg[r["review_id"]]
        for it in items:
            if "{skill}" in NEGATIVE[it] or "{skill}" in NEGATIVE_ALT[it]:
                continue
            assert NEGATIVE[it] in r["negative_text"] or NEGATIVE_ALT[it] in r["negative_text"], (r["review_id"], it)
        assert r["negative_text"].count("다.") == len(items)                  # 예전: 첫 항목만 문장, 나머지는 "또한 A, B 측면도"
        assert "측면도 보완하면" not in r["negative_text"]


def test_blunt_reviewers_are_an_exact_share_per_group_and_mismatches_follow_the_items(bundle):
    from core.ingest.org_profile import BLUNT_SHARE
    from core.ingest.review_text import BLUNT_CLOSERS, SOFT_CLOSERS
    size, root, b, _, _ = bundle
    style = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["review_style"]
    groups = {}
    for pid in style["blunt_reviewers"]:
        groups[pid[:2]] = groups.get(pid[:2], 0) + 1
    assert groups == {g: round(BLUNT_SHARE * 100) for g in ("DP", "AI", "AU")[:size // 100]}
    items = {}
    for i in b.tables["review_items.csv"]:
        items.setdefault(i["review_id"], {"positive": 0, "negative": 0})[i["polarity"]] += 1
    text = {r["review_id"]: r for r in b.tables["reviews.csv"]}
    assert style["harsh_reviews"] and style["soft_reviews"]
    for rid in style["harsh_reviews"]:
        assert items[rid]["positive"] >= items[rid]["negative"] and any(c in text[rid]["negative_text"] for c in BLUNT_CLOSERS)
    for rid in style["soft_reviews"]:
        assert items[rid]["negative"] > items[rid]["positive"] and any(c in text[rid]["negative_text"] for c in SOFT_CLOSERS)
    share = (len(style["harsh_reviews"]) + len(style["soft_reviews"])) / len(text)
    assert 0.02 < share < 0.09                                                   # 기본 5%
    blunt_people = set(style["blunt_reviewers"])
    by_blunt = {r["review_id"] for r in b.tables["reviews.csv"] if r["reviewer_id"] in blunt_people}
    tagged = set(style["blunt_reviews"]) | set(style["harsh_reviews"]) | set(style["soft_reviews"])
    assert set(style["blunt_reviews"]) == by_blunt - set(style["harsh_reviews"]) - set(style["soft_reviews"])   # 어긋남이 직설형보다 우선
    assert len(tagged) == len(style["blunt_reviews"]) + len(style["harsh_reviews"]) + len(style["soft_reviews"])  # 리뷰마다 성향 하나


def test_review_style_shares_are_checked(tmp_path):
    with pytest.raises(ValueError):
        generate_org_bundle(tmp_path / "x", 100, seed=5, blunt_share=1.5)


def test_v3_keeps_people_history_and_review_tables_byte_identical_to_v1(tmp_path):
    """생성기 v1(2026-10-11 전, 커밋 436aa9a의 demo/org-n100 manifest)과 같은 seed면 이름·리뷰 글·manifest 밖의 사람·이력·평가 표는 바이트 단위로 같다.
    항목을 고르는 난수열 앞에서 난수를 하나라도 더 쓰면 여기서 걸린다(같은 생성기를 두 번 돌리는 비교로는 못 잡는다 -- 폴백 리뷰).
    v3(2026-10-19)는 가용률·사업 정원·예산·현재 배치만 바꾼다(전일 투입 재설계) -- 그 표는 이 비교에서 뺀다. 리뷰 판정 동봉이 그대로 쓰이는 근거."""
    v3_changed = {"availability.csv", "projects.csv", "project_grade_requirements.csv", "current_assignments.csv",
                  "project_skill_requirements.csv",                         # v4(2026-10-19): 장기 사업·추가 과제의 기술 요구가 붙는다
                  "work_history.csv", "person_skills.csv", "review_items.csv"}   # v5: 진행 사업 경력을 이력·기술 경력·평가에 잇는다
    v1 = {"rate_card.csv": "773b8e1c324e522f13bf7d09549b7024db27a4ab765f182b24bf87b2037189d0",
          "person_skills.csv": "ae843b26f1e3d09656bc671c9489f5439213a0f4f4d4594f5f4518ba3e9c867e",
          "work_history.csv": "5bba826547277bf2c1de51cb9c1e945ba9c5aeb4b7b029139caf4c52be2ed002",
          "availability.csv": "40a8a6a4e9ceddf7c93e68e7b012e3db0cac50ed2865089bf721db6773f710ea",
          "projects.csv": "84cf274e4cd02cd9ed09c8eebfbdb50639672f61b00d06ec28cdcdcd5ad16f6b",
          "project_grade_requirements.csv": "a1daf8b346f30d034c4b57fb84dcae7dba6faa91dfbecf905927f79576897921",
          "project_skill_requirements.csv": "223c8c8c68c8e00aaedb35f94f15efab89cc76ac1f68cc649736f2bf9f38ae28",
          "review_items.csv": "6ee0ec28b6641eeb3512c992cd4630dc2e40abf9cac31146b0f383250305dabe",
          "current_assignments.csv": "f404b86f2dc48619815cfb0cffca967c5b48019bb8f8a4a8d2fc14cffd6b6152",
          "project_outcomes.csv": "866a90008129f085197bf6daf576e444ba6061aa35365af4b6cfde70fb44ecd7",
          "replacements.csv": "0217fb001f5b94d1e390afc763fb7044788d0bb7f4748001cb7bef8bbd90e375"}
    root = generate_org_bundle(tmp_path / "v2", 100, seed=2026)                   # v1 manifest의 seed(시연 묶음)
    files = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert {k: files[k] for k in v1 if k not in v3_changed} == {k: v for k, v in v1.items() if k not in v3_changed}
    v1_n300 = {"rate_card.csv": "773b8e1c324e522f13bf7d09549b7024db27a4ab765f182b24bf87b2037189d0",   # AI·AU 그룹까지(436aa9a demo/org-n300.zip)
               "person_skills.csv": "9aaf017b68cfda60f6a13d08655ac1f089c7146e3eedb7359489a64c06475338",
               "work_history.csv": "6463a3fa274bb266b973464bfd7e7f3a0f47c76674d1de544602973b96448c47",
               "availability.csv": "a43b0d7ef18a91faeb3649ebd56388d2dde2e13d9e8a623a5855cee64a08fbdf",
               "projects.csv": "b95c720a39e33529f0c2b8cd8654b0db57ff3161e588871fa107d82447a7efe8",
               "project_grade_requirements.csv": "a0cc9cfac4cb0e360b2b75f5e08bfc6582540fc76fd0d972ecfb1bd1d69fef3a",
               "project_skill_requirements.csv": "ae26d2fd5343f342e4f7442a39ee991491021f61e68b2d90c8c05c7f92e4620a",
               "review_items.csv": "10e9967e81e913eabe5bab0f7a5744c44203ad0a70fe650735ac94afe30aa44e",
               "current_assignments.csv": "7ee9b5108be18817d4baeb8c5a007a12784dcdb1bee0b4d4f8eb8ddbd941e616",
               "project_outcomes.csv": "5fbcde115cc6a07fa81dd48cd9d1e46ae39c08ccbcba451cd2d50914370acf58",
               "replacements.csv": "7c52ecaf8ea4a3db93cccf78f5f18fe19148f83d0688f2c16632a41918a9220a"}
    root = generate_org_bundle(tmp_path / "v2-n300", 300, seed=2026)
    files = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert {k: files[k] for k in v1_n300 if k not in v3_changed} == {k: v for k, v in v1_n300.items() if k not in v3_changed}


# ---------------------------------------------------------------- 2026-10-19: 생성기 v3(전일 투입 재설계, 설계 .omc/plan/2026-10-19-concurrency.md 4절)
@pytest.mark.parametrize("n", [100, 200, 300])
def test_v3_headcounts_fit_each_grade_without_project_concurrency(tmp_path, n):
    """등급·달마다 정원 합 ≤ 0.85 × 그 달 일할 수 있는(가용률 1.0) 그 등급 인원, 예산 ≥ 가장 싼 사람으로 전일 채운 비용."""
    from core.ingest.org_profile import PEAK_CAP
    b, report = load_bundle(generate_org_bundle(tmp_path / f"n{n}", n, seed=2026))
    ds, _ = to_dataset(b, report)
    months = len(ds.people[0].availability)
    for m in range(months):
        for g in {p.grade for p in ds.people}:
            supply = sum(1 for p in ds.people if p.grade == g and p.availability[m] >= 1.0)
            demand = sum(p.grade_headcount.get(g, 0) for p in ds.projects if m in p.months)
            assert demand <= PEAK_CAP * supply + 1e-9, (m, g, demand, supply)
    cheapest = {g: min(p.monthly_rate for p in ds.people if p.grade == g) for g in {p.grade for p in ds.people}}
    assert all(p.monthly_budget >= sum(cheapest[g] * k for g, k in p.grade_headcount.items()) for p in ds.projects)


def test_v3_leave_is_the_same_people_in_every_scenario(tmp_path):
    """육아휴직 3%(가용률 0인 달)·한 달 휴가 2%(가용률 그대로), 연초·운영 중이 같은 사람·같은 창."""
    plan = generate_org_bundle(tmp_path / "plan", 100, seed=2026)
    op = generate_org_bundle(tmp_path / "op", 100, seed=2026, scenario="operating")
    mp, mo = (json.loads((r / "manifest.json").read_text("utf-8")) for r in (plan, op))
    assert mp["leave"] == mo["leave"] and mp["leave"]["calibrated"] is False
    kinds = [x["kind"] for x in mp["leave"]["people"]]
    assert kinds.count("육아휴직") == 3 and kinds.count("한 달 휴가") == 2
    b, report = load_bundle(plan)
    ds, _ = to_dataset(b, report)
    parental = {x["person_id"] for x in mp["leave"]["people"] if x["kind"] == "육아휴직"}
    assert {a for p in ds.people for a in p.availability} <= {0.0, 1.0}
    assert {p.id for p in ds.people if min(p.availability) < 1.0} <= parental


def test_v3_current_roster_is_full_time_without_overlap_or_leave(tmp_path):
    b, report = load_bundle(generate_org_bundle(tmp_path / "plan", 100, seed=2026))
    ds, _ = to_dataset(b, report)
    people = {p.id: p for p in ds.people}
    projects = {p.id: p for p in ds.projects}
    assert ds.current and all(c.alloc == 1.0 for c in ds.current)
    busy: dict = {}
    for c in ds.current:
        for m in projects[c.project_id].months:
            assert people[c.person_id].availability[m] == 1.0
            assert (c.person_id, m) not in busy                                  # 동시 수행 없음
            busy[(c.person_id, m)] = c.project_id
    for j, p in projects.items():
        assert sum(people[c.person_id].monthly_rate for c in ds.current if c.project_id == j) <= p.monthly_budget


# ---------------------------------------------------------------- 2026-10-19 생성기 v4: 사업 수명 주기 섞기(b 요청 6번, 사용자 원문)
@pytest.mark.parametrize("scenario", ["planning", "operating"])
def test_v4_mixes_long_running_projects_and_add_on_tasks(tmp_path, scenario):
    """사용자: 24년 시작 운영 사업(내부 소수), 25년 고도화, 26년 상반기 시작, 운영 사업에 붙은 추가 과제(회사 인력 2명·6달, 외주 여러 명)."""
    import csv
    root = generate_org_bundle(tmp_path / scenario, 100, seed=2026, scenario=scenario)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    b, report = load_bundle(root)
    ds, _ = to_dataset(b, report)
    assert report.errors == [] and report.warnings == []          # 계획 전 시작·계획 뒤 끝은 경고 없이 자른다
    rows = {r["project_id"]: r for r in csv.DictReader(open(root / "projects.csv", encoding="utf-8"))}
    hc = {j.id: sum(j.grade_headcount.values()) for j in ds.projects}
    life = m["lifecycle"]
    stages = {e["stage"] for e in life.values()}
    assert {"운영", "고도화", "개발", "추가 과제"} <= stages
    first = m["horizon_start"]
    for pid, e in life.items():
        assert rows[pid]["start_month"] == e["started"]
        prefix = {"운영": "[운영] ", "고도화": "[고도화] ", "개발": "[실행] ", "추가 과제": "[추가 과제] "}[e["stage"]]
        assert rows[pid]["project_name"].startswith(prefix)
        if e["stage"] == "추가 과제":
            parent = e["parent"]
            assert life[parent]["stage"] == "운영" and hc[pid] == 2
            assert 3 <= e["external_headcount"] <= 6
            assert rows[parent]["start_month"] <= rows[pid]["start_month"] and rows[pid]["end_month"] <= rows[parent]["end_month"]
        else:
            assert e["started"] < first                            # 계획 전에 시작해 지금 진행 중
        if e["stage"] == "운영":
            assert hc[pid] <= 3 and rows[pid]["end_month"] > ds_last(m)    # 내부 소수, 계획 뒤까지 운영
    if scenario == "operating":
        addon = m["proposals"][-1]
        assert life[addon]["stage"] == "추가 과제" and m["pipeline"][addon]["parent"] == life[addon]["parent"]
        assert not [c for c in ds.current if c.project_id == addon]           # 아직 아무도 없다(남는 인력으로 짠다)


def ds_last(m: dict) -> str:
    y, mo = map(int, m["horizon_start"].split("-"))
    k = y * 12 + mo - 1 + m["horizon_months"] - 1
    return f"{k // 12}-{k % 12 + 1:02d}"


def test_v5_planning_projects_are_mostly_running_before_the_horizon(tmp_path):
    """사용자(2026-10-19): "기존 사업 20개는 여전히 전부 하반기 시작 프로젝트들인가? 그건 좀 잘못된거같은데" -- 연초 계획의 실행 사업
    대부분이 계획 전에 시작한 진행 사업이고(운영은 회사 인력 2~3명), 이름 머리말이 단계를 말한다. 번호·이름 뒷부분(고객·사업 종류)은 v4와 같다."""
    import csv
    root = generate_org_bundle(tmp_path / "p", 100, seed=2026)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    rows = list(csv.DictReader(open(root / "projects.csv", encoding="utf-8")))
    execution = [r for r in rows[1:] if r["phase"] == "실행" and m["lifecycle"].get(r["project_id"], {}).get("stage") != "추가 과제"]
    running = [r for r in execution if r["project_id"] in m["lifecycle"]]
    assert len(running) >= 0.5 * len(execution) and all(r["start_month"] < m["horizon_start"] for r in running)
    assert all(r["project_name"].startswith(("[운영]", "[고도화]", "[실행]")) for r in running)
    assert [r["project_id"] for r in rows[:20]] == [f"J{k:03d}" for k in range(1, 21)]


@pytest.mark.parametrize("seed,scenario,start", [(14, "planning", "2026-10"), (1, "operating", "2026-10"),
                                                 (2026, "planning", "2026-01"), (2026, "operating", "2027-03")])
def test_v4_bundles_load_for_other_seeds_and_horizon_starts(tmp_path, seed, scenario, start):
    """Codex 리뷰(2026-10-19): 추가 과제가 상위 기술의 클라우드 기술을 또 요구해 시드 14·1이 적재 거부됐고, 시작 연도를 박아 둬
    2026-01 시작 묶음에 끝보다 늦게 시작하는 사업이 생겼다 -- 장기 사업은 늘 계획 시작 전에 시작하고 기술 요구가 겹치지 않는다."""
    import csv
    root = generate_org_bundle(tmp_path / "b", 100, seed=seed, scenario=scenario, horizon_start=start)
    b, report = load_bundle(root)
    assert report.errors == [], [i.message for i in report.errors]
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    rows = {r["project_id"]: r for r in csv.DictReader(open(root / "projects.csv", encoding="utf-8"))}
    for pid, e in m["lifecycle"].items():
        if e["stage"] != "추가 과제":
            assert e["started"] < start <= rows[pid]["end_month"]
    pairs = [(r["project_id"], r["skill_name"]) for r in csv.DictReader(open(root / "project_skill_requirements.csv", encoding="utf-8"))]
    assert len(pairs) == len(set(pairs))



@pytest.mark.parametrize("scenario", ["planning", "operating"])
def test_v5_ongoing_work_is_in_the_history_and_reviews_stay_consistent(tmp_path, scenario):
    """v5(사용자 "진행해야겠어", 선택 "갈아 끼우고 평가도 새로"): 진행 사업 팀원은 합류 달부터 업무 이력이 그 사업이고 그 뒤 지난 이력과
    겹치지 않는다. 남은 평가는 모두 평가 기간에 두 사람이 같은 사업 이력에 있고, 진행 사업 팀원끼리의 평가가 새로 생긴다."""
    import csv
    import datetime as dt
    root = generate_org_bundle(tmp_path / scenario, 100, seed=2026, scenario=scenario)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    work = list(csv.DictReader(open(root / "work_history.csv", encoding="utf-8")))
    cur = list(csv.DictReader(open(root / "current_assignments.csv", encoding="utf-8")))
    reviews = list(csv.DictReader(open(root / "reviews.csv", encoding="utf-8")))
    on_running = {(c["person_id"], c["project_id"]) for c in cur
                  if m["lifecycle"].get(c["project_id"], {}).get("stage") not in (None, "추가 과제")}
    linked = [w for w in work if w["project_code"].startswith("J")]
    assert linked and m["ongoing_history"]["people"] == len(linked)
    assert all((w["person_id"], w["project_code"]) in on_running and w["status"] == "진행중" for w in linked)
    by_person: dict[str, list] = {}
    for w in work:
        by_person.setdefault(w["person_id"], []).append((w["start_date"], w["end_date"]))
    for spans in by_person.values():                                  # 한 사람은 한 번에 한 이력(전일 투입)
        spans.sort()
        assert all(spans[k][1] < spans[k + 1][0] for k in range(len(spans) - 1))
    rows = {}
    for w in work:
        rows.setdefault((w["person_id"], w["project_code"]), []).append((w["start_date"], w["end_date"]))
    for r in reviews:
        d = dt.date.fromisoformat(r["reviewed_at"])
        hi = dt.date(d.year, d.month, 1).isoformat()
        y, mo = (d.year, d.month - 5) if d.month > 5 else (d.year - 1, d.month + 7)
        lo = f"{y}-{mo:02d}-01"
        a = [x for x in rows.get((r["reviewer_id"], r["project_code"]), []) if x[0] <= hi and x[1] >= lo]
        b = [x for x in rows.get((r["reviewee_id"], r["project_code"]), []) if x[0] <= hi and x[1] >= lo]
        assert any(x[0] <= y2[1] and y2[0] <= x[1] for x in a for y2 in b), r["review_id"]
    assert any(r["project_code"].startswith("J") for r in reviews)
    assert m["ongoing_history"]["new_reviews"] == sum(r["project_code"].startswith("J") for r in reviews)
