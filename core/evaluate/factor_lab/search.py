"""Search harness: candidate models (factor weights) -> MILP plan -> simulated outcome under every scenario.

A candidate model replaces the skill-fit matrix S given to the service MILP with a weighted mix of factors
(scaled back to [0, 1]); everything else -- constraints, collaboration term, solver (HiGHS) -- is the
production formulation, so a candidate is judged by the plans the real optimiser would make with it.
"""
import itertools
import random
import time
from dataclasses import dataclass

import numpy as np

from core.evaluate.factor_lab.factors import FACTORS
from core.evaluate.factor_lab.simulator import HELD_OUT, SCENARIOS, Scenario, simulate
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, _overfamiliar_pairs, solve_milp_assessment
from core.scoring.engine import ScoringEngine

BASELINE = {"S": 1.0}


@dataclass
class CandidateResult:
    name: str
    weights: dict[str, float]
    accepted: bool
    solve_s: float
    fill_rate: float
    outcomes: dict[str, float]          # scenario -> mean simulated outcome over seeds
    unfilled: int
    error: str | None = None
    outcome_sd: dict[str, float] | None = None     # scenario -> sd over seeds (seeds only reweight noise)
    termination: str | None = None                 # solver stop reason: a time-limited plan may explain a gap
    scale: dict[str, float] | None = None          # mean of the raw mix, the factor applied, mean after


def model_matrix(F: dict[str, np.ndarray], weights: dict[str, float], with_scale: bool = False):
    """Weighted mix rescaled to the current S's mean (then clipped to [0, 1]), so every candidate keeps the
    same balance against the MILP's collaboration term and unfilled penalty -- otherwise a sparse factor would
    shrink the matrix and a floored one (G) would add a constant, and part of a candidate's difference would
    come from the objective balance rather than the factors (review SHOULD-3)."""
    wsum = sum(weights.values())
    if wsum <= 0:
        raise ValueError("weights must have a positive sum")
    raw = sum(w * F[k] for k, w in weights.items()) / wsum
    target, mean = float(F["S"].mean()), float(raw.mean())
    k = target / mean if mean > 0 else 1.0
    out = np.clip(raw * k, 0.0, 1.0)
    if with_scale:
        return out, {"raw_mean": round(mean, 4), "factor": round(k, 4), "mean": round(float(out.mean()), 4)}
    return out


def candidates(n_random: int, seed: int) -> list[tuple[str, dict[str, float]]]:
    """Baseline, each extra factor added to S on its own, a few hand-made mixes, then random mixes."""
    out = [("base:S", dict(BASELINE))]
    for k in FACTORS[1:]:
        out.append((f"S+{k}", {"S": 1.0, k: 0.5}))
    out += [("S+D+M", {"S": 1.0, "D": 0.5, "M": 0.5}),
            ("S+D+R+M", {"S": 1.0, "D": 0.4, "R": 0.3, "M": 0.4}),
            ("all", {k: (1.0 if k == "S" else 0.3) for k in FACTORS})]
    rng = random.Random(seed)
    for n in range(n_random):
        w = {"S": 1.0}
        for k in FACTORS[1:]:
            v = rng.choice((0.0, 0.0, 0.25, 0.5, 1.0))
            if v:
                w[k] = v
        out.append((f"rand{n:02d}", w))
    seen, uniq = set(), []
    for name, w in out:
        key = tuple(sorted(w.items()))
        if key not in seen:
            seen.add(key)
            uniq.append((name, w))
    return uniq


def evaluate(graph: MemoryGraph, F: dict[str, np.ndarray], cands, params: MilpParams,
             scenarios: tuple[Scenario, ...] = SCENARIOS, seeds=(1, 2, 3), log=print) -> list[CandidateResult]:
    eng = ScoringEngine(graph)
    C = eng.synergy_matrix()
    overfam = {tuple(sorted(p)) for p in _overfamiliar_pairs(graph, params.clique_threshold_months)}
    out = []
    for name, w in cands:
        t = time.perf_counter()
        try:
            M, scale = model_matrix(F, w, with_scale=True)
            a = solve_milp_assessment(graph, M, C, params)
            cand = a.accepted
            if cand is None:
                raise RuntimeError(f"no accepted solution ({a.refinement.reason})")
            entries, unfilled = cand.plan.entries, cand.plan.unfilled
            res = [simulate(graph, F, C, overfam, entries, sc, s, unfilled) for sc in scenarios for s in seeds]
            chunks = {sc.name: [r.total for r in res[k * len(seeds):(k + 1) * len(seeds)]]
                      for k, sc in enumerate(scenarios)}
            ev = getattr(a.validation_candidate, "evidence", None)
            out.append(CandidateResult(name, w, True, round(time.perf_counter() - t, 2), res[0].fill_rate,
                                       {s: float(np.mean(v)) for s, v in chunks.items()}, len(unfilled),
                                       outcome_sd={s: float(np.std(v)) for s, v in chunks.items()},
                                       termination=getattr(ev, "termination_reason", None), scale=scale))
        except Exception as exc:  # noqa: BLE001 -- a failed candidate is a result
            out.append(CandidateResult(name, w, False, round(time.perf_counter() - t, 2), 0.0, {}, -1,
                                       f"{type(exc).__name__}: {exc}"[:200]))
        r = out[-1]
        log(f"[factor-lab] {name} {w} -> {r.outcomes} fill={r.fill_rate:.3f} {r.solve_s}s {r.error or ''}")
    return out


def summarize(results: list[CandidateResult]) -> list[dict]:
    """Per candidate: outcome as % of the best candidate in each scenario, the worst case over the in-sample
    assumptions (T1-T5) and the worst case over the held-out ones (T6-T7)."""
    ok = [r for r in results if r.accepted]
    if not ok:
        return []
    names = list(ok[0].outcomes)
    best = {s: max(r.outcomes[s] for r in ok) for s in names}
    rows = []
    for r in ok:
        pct = {s: (100 * r.outcomes[s] / best[s] if best[s] > 0 else 0.0) for s in names}
        ins = [v for s, v in pct.items() if s not in HELD_OUT]
        held = [v for s, v in pct.items() if s in HELD_OUT]
        rows.append({"name": r.name, "weights": r.weights, "pct": pct, "worst_pct": min(ins) if ins else 0.0,
                     "held_out_pct": min(held) if held else None,
                     "mean_pct": float(np.mean(list(pct.values()))), "fill_rate": r.fill_rate,
                     "unfilled": r.unfilled, "solve_s": r.solve_s, "termination": r.termination,
                     "scale": r.scale})
    rows.sort(key=lambda x: -x["worst_pct"])
    return rows
