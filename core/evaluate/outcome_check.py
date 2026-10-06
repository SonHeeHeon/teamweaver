"""Check the model's assumptions against past project outcomes (project_outcomes.csv, replacements.csv).

For every finished past project the team is rebuilt from work_history, and features are computed from what was
known BEFORE the project started (no future information):

- prior_cowork      share of member pairs who had already worked on the same project at the same time
                    (what the collaboration term C rewards through cowork months)
- cowork_strength   mean of min(months together / 12, 1) over member pairs -- C's cowork component
- familiar_new      share of pairs with >= 12 months together in the 36 months before the start
                    (the service's over-familiarity rule since 2026-10-06; the penalty mu punishes these)
- familiar_old      share of pairs with >= 6 months together in the 120 months before the start (old rule)
- industry_exp      share of members with earlier work in the project's industry
- team_size

`churn` (share of stints shorter than 4 months) is observed DURING the project, so it is reported on its own,
never used as a control or as something a plan could have used. Stints cut short by a CUSTOMER-requested replacement
are left out of it: those happen because the project went badly, so counting them would read the outcome back
(Opus review MUST). `observed_36` marks projects whose 36 months before the start lie inside the data -- the export
keeps only the last 10 years, so early projects see a truncated history (sensitivity reported separately).

The question for the model: do the signs agree with what it assumes -- prior collaboration good (lambda > 0),
over-familiarity bad (mu > 0)? Correlations come with bootstrap 95 % intervals. With synthetic outcomes this checks
the procedure, not the real world (NOT_CALIBRATED): the hidden rule that made the outcomes is known to include
prior co-work and industry experience.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from collections import defaultdict
from itertools import combinations

FEATURES = ("prior_cowork", "cowork_strength", "familiar_new", "familiar_old", "industry_exp", "team_size")
CONTROLS = ("churn",)
OUTCOMES = ("customer_score", "follow_on", "on_schedule", "customer_replacements")


def _month_index(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def _months(start: dt.date, end: dt.date) -> set[int]:
    return set(range(_month_index(start), _month_index(end) + 1))


def project_features(tables: dict[str, list[dict]]) -> list[dict]:
    """One row per past project with an outcome: planning-time features + outcomes."""
    work = tables["work_history.csv"]
    outcomes = tables.get("project_outcomes.csv") or []
    repl = tables.get("replacements.csv") or []
    by_code: dict[str, list[dict]] = defaultdict(list)
    by_person: dict[str, list[dict]] = defaultdict(list)
    for w in work:
        by_code[w["project_code"]].append(w)
        by_person[w["person_id"]].append(w)
    customer_repl = defaultdict(int)
    replaced_by_customer: set[tuple[str, str]] = set()
    for r in repl:
        if r.get("requested_by") == "고객":
            customer_repl[r["project_code"]] += 1
            replaced_by_customer.add((r["project_code"], r["person_id"]))
    data_start = _month_index(min(w["start_date"] for w in work)) if work else 0
    rows = []
    for o in outcomes:
        team_rows = by_code.get(o["project_code"], [])
        members = sorted({w["person_id"] for w in team_rows})
        if len(members) < 2:
            continue
        start = min(w["start_date"] for w in team_rows)
        s_idx = _month_index(start)
        # months each pair shared on the same project before the start (date overlap, like the ingest's coworks)
        before: dict[str, dict[str, set[int]]] = {}
        for m in members:
            per_code: dict[str, set[int]] = defaultdict(set)
            for w in by_person[m]:
                if w["start_date"] < start:
                    months = {x for x in _months(w["start_date"], min(w["end_date"], start - dt.timedelta(days=1)))
                              if x < s_idx}
                    per_code[w["project_code"]] |= months
            before[m] = per_code
        pairs = list(combinations(members, 2))
        together, recent36, recent120 = [], [], []
        for a, b in pairs:
            shared = set()
            for code, ma in before[a].items():
                if code in before[b]:
                    shared |= ma & before[b][code]
            together.append(len(shared))
            recent36.append(sum(1 for x in shared if x >= s_idx - 36))
            recent120.append(sum(1 for x in shared if x >= s_idx - 120))
        industry = o.get("industry")
        exp = [any(w["start_date"] < start and w.get("industry") == industry for w in by_person[m]) for m in members]
        stints = [len(_months(w["start_date"], w["end_date"])) for w in team_rows
                  if (o["project_code"], w["person_id"]) not in replaced_by_customer]
        rows.append({
            "project_code": o["project_code"], "start": start.isoformat(),
            "prior_cowork": sum(t > 0 for t in together) / len(pairs),
            "cowork_strength": sum(min(t / 12, 1.0) for t in together) / len(pairs),
            "familiar_new": sum(r >= 12 for r in recent36) / len(pairs),
            "familiar_old": sum(r >= 6 for r in recent120) / len(pairs),
            "industry_exp": sum(exp) / len(members),
            "team_size": len(members),
            "churn": sum(s < 4 for s in stints) / len(stints) if stints else 0.0,
            "observed_36": s_idx - data_start >= 36,
            "customer_score": int(o["customer_score"]),
            "follow_on": 1 if o["follow_on"] == "Y" else 0,
            "on_schedule": 1 if o["schedule"] == "준수" else 0,
            "customer_replacements": customer_repl[o["project_code"]],
        })
    return rows


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def _ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    ranks = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    return _pearson(_ranks(x), _ranks(y))


def correlations(rows: list[dict], outcome: str = "customer_score", n_boot: int = 1000,
                 seed: int = 2026) -> dict[str, dict]:
    """Spearman correlation of every feature/control with the outcome, with a bootstrap 95 % interval."""
    rng = random.Random(seed)
    y = [r[outcome] for r in rows]
    out = {}
    for f in FEATURES + CONTROLS:
        x = [r[f] for r in rows]
        rho = spearman(x, y)
        boots = []
        for _ in range(n_boot):
            idx = [rng.randrange(len(rows)) for _ in rows]
            b = spearman([x[i] for i in idx], [y[i] for i in idx])
            if b is not None:
                boots.append(b)
        boots.sort()
        lo = boots[int(0.025 * len(boots))] if boots else None
        hi = boots[int(0.975 * len(boots)) - 1] if boots else None
        out[f] = {"rho": None if rho is None else round(rho, 3),
                  "ci95": None if lo is None else [round(lo, 3), round(hi, 3)],
                  "planning_time": f in FEATURES, "n": len(rows)}
    return out


def verdicts(corr: dict[str, dict], reg: dict[str, dict] | None = None) -> dict[str, str]:
    """Plain-language verdicts on the model's assumptions (95 % interval excludes 0 or not).

    With a regression the collaboration and familiarity verdicts use it (familiar_new holding co-work fixed = the
    extra effect the penalty targets); without one they fall back to the simple correlations."""
    def sign_of(c):
        if c is None or c.get("ci95") is None:
            return "unknown"
        lo, hi = c["ci95"]
        return "positive" if lo > 0 else "negative" if hi < 0 else "unclear"
    src = reg or {}
    collab = sign_of(src.get("cowork_strength") or corr["cowork_strength"])
    familiar = sign_of(src.get("familiar_new") or corr["familiar_new"])
    industry = sign_of(src.get("industry_exp") or corr["industry_exp"])
    return {
        "collaboration_reward (lambda > 0)": {"positive": "supported", "negative": "contradicted"}.get(collab, "not shown"),
        "over_familiarity_penalty (mu > 0)": {"negative": "supported", "positive": "contradicted"}.get(familiar, "not shown"),
        "industry_experience": {"positive": "supported", "negative": "contradicted"}.get(industry, "not shown"),
    }


def regression(rows: list[dict], outcome: str = "customer_score",
               features: tuple[str, ...] = ("cowork_strength", "familiar_new", "industry_exp"),
               n_boot: int = 1000, seed: int = 2026) -> dict[str, dict]:
    """Least squares on standardised features with bootstrap 95 % intervals.

    Familiar pairs are almost always pairs that worked together, so the simple correlation of familiar_new mixes
    "worked together" with "worked together a lot, recently". Holding cowork_strength fixed separates the two:
    the coefficient of familiar_new is the extra effect of long recent familiarity -- what the penalty mu targets.
    Only planning-time features: churn happens during the project (a post-treatment variable) and is not a control.
    The features are correlated (co-work vs familiarity / industry ~0.6-0.7), so wide intervals mean "cannot tell",
    not "no effect" -- the same data cannot even recover industry experience, which the generator weights at 0.5."""
    import numpy as np
    X = np.array([[r[f] for f in features] for r in rows], dtype=float)
    y = np.array([r[outcome] for r in rows], dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(rows)), (X - mu) / sd])

    def fit(Zs, ys):
        return np.linalg.lstsq(Zs, ys, rcond=None)[0][1:]
    coef = fit(Z, y)
    rng = np.random.default_rng(seed)
    boots = np.array([fit(Z[idx], y[idx]) for idx in (rng.integers(0, len(rows), len(rows)) for _ in range(n_boot))])
    lo, hi = np.percentile(boots, [2.5, 97.5], axis=0)
    return {f: {"coef_per_sd": round(float(c), 3), "ci95": [round(float(a), 3), round(float(b), 3)]}
            for f, c, a, b in zip(features, coef, lo, hi)}
