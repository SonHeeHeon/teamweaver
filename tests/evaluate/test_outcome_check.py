"""core/evaluate/outcome_check: past teams rebuilt from planning-time information only (2026-10-06)."""
import datetime as dt

from core.evaluate.outcome_check import correlations, project_features, regression, spearman, verdicts


def _w(pid, code, start, end, industry="금융"):
    return {"person_id": pid, "project_code": code, "start_date": dt.date(*start), "end_date": dt.date(*end),
            "industry": industry}


def _tables():
    work = [
        # A and B worked together for 14 months right before P1 (familiar under the new rule)
        _w("A", "OLD1", (2023, 1, 1), (2024, 2, 29)), _w("B", "OLD1", (2023, 1, 1), (2024, 2, 29)),
        # C worked with A long ago (2015) for 8 months: familiar only under the old 10-year/6-month rule
        _w("C", "OLD0", (2015, 1, 1), (2015, 8, 31), industry="공공"), _w("A", "OLD0", (2015, 1, 1), (2015, 8, 31), industry="공공"),
        # P1 starts 2024-03; D joins and leaves after 2 months (churn)
        _w("A", "P1", (2024, 3, 1), (2024, 12, 31)), _w("B", "P1", (2024, 3, 1), (2024, 12, 31)),
        _w("C", "P1", (2024, 3, 1), (2024, 12, 31)), _w("D", "P1", (2024, 3, 1), (2024, 4, 30)),
        # work AFTER P1 started must not count for P1
        _w("C", "LATER", (2024, 3, 1), (2025, 6, 30)), _w("D", "LATER", (2024, 3, 1), (2025, 6, 30)),
    ]
    outcomes = [{"project_code": "P1", "industry": "금융", "customer_score": 4, "follow_on": "Y", "schedule": "준수"}]
    repl = [{"project_code": "P1", "person_id": "D", "requested_by": "고객"}]
    return {"work_history.csv": work, "project_outcomes.csv": outcomes, "replacements.csv": repl}


def test_features_use_only_what_was_known_before_the_start():
    (row,) = project_features(_tables())
    # pairs: AB AC AD BC BD CD = 6; worked together before: AB (14 m, recent), AC (8 m, 2015) -> 2/6
    assert abs(row["prior_cowork"] - 2 / 6) < 1e-9
    assert abs(row["familiar_new"] - 1 / 6) < 1e-9          # only AB: >= 12 months within 36 months
    assert abs(row["familiar_old"] - 2 / 6) < 1e-9          # AB and AC: >= 6 months within 120 months
    assert abs(row["cowork_strength"] - (1.0 + 8 / 12) / 6) < 1e-9
    assert abs(row["industry_exp"] - 2 / 4) < 1e-9          # A, B had 금융 before; C only 공공; D nothing
    # D left after 2 months because the CUSTOMER asked: that follows from the outcome, so it is not churn
    assert row["churn"] == 0.0 and row["customer_replacements"] == 1
    assert row["observed_36"] is True                       # data starts 2015-01, P1 starts 2024-03
    assert row["team_size"] == 4 and row["follow_on"] == 1 and row["on_schedule"] == 1


def test_spearman_handles_ties_and_constant_input():
    assert abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12
    assert abs(spearman([1, 1, 2, 2], [1, 2, 3, 4]) - 0.894427) < 1e-5
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_regression_separates_familiarity_from_collaboration():
    """Outcomes driven by co-work only: the familiarity coefficient (co-work held fixed) must not be clearly
    positive, while co-work is; verdicts follow the regression."""
    import random
    rng = random.Random(1)
    rows = []
    for _ in range(300):
        cw = rng.random()
        fam = min(1.0, max(0.0, cw * 0.8 + rng.gauss(0, 0.1)))      # familiarity tracks co-work
        rows.append({"cowork_strength": cw, "familiar_new": fam, "industry_exp": rng.random(), "churn": rng.random(),
                     "prior_cowork": cw, "familiar_old": fam, "team_size": 5,
                     "customer_score": 3 + 2 * cw + rng.gauss(0, 0.5)})
    corr = correlations(rows, n_boot=200)
    assert corr["familiar_new"]["rho"] > 0.3                         # the naive view looks "pro-familiarity"
    reg = regression(rows, n_boot=200)
    assert reg["cowork_strength"]["ci95"][0] > 0
    assert reg["familiar_new"]["ci95"][0] < 0.1
    v = verdicts(corr, reg)
    assert v["collaboration_reward (lambda > 0)"] == "supported"
    assert v["over_familiarity_penalty (mu > 0)"] in ("not shown", "supported")


def test_internal_replacements_still_count_as_churn():
    t = _tables()
    t["replacements.csv"] = [{"project_code": "P1", "person_id": "D", "requested_by": "내부"}]
    (row,) = project_features(t)
    assert abs(row["churn"] - 1 / 4) < 1e-9 and row["customer_replacements"] == 0
