"""Scale rehearsal runner: one organisation size per run (100 / 200 / 300 people).

    uv run --group benchmark python -m rehearsal.run --size 100 [--stage pipeline|sweep|all] [--no-llm] [--no-pdf]

pipeline  The real API path on a real uvicorn server (a subprocess on a free local port, so streaming arrival
          times and the Playwright PDF behave as in production): upload the zip bundle -> /api/optimize (A..D, admin
          defaults) -> /api/whatif (real LLM unless --no-llm) -> /api/plans/apply-swap -> /api/report (PDF).
          Every step is timed; plan quality and the stream's stop reason are recorded.
sweep     Plan A alone at several time limits with HiGHS (the service solver since 2026-10-05) and CBC (the
          previous one, kept as MilpParams.solver="cbc" for comparison only). The
          reference for "quality" is the best objective found at that size by any run.

Results go to rehearsal/results/n{size}/{pipeline,sweep}.json. Data is synthetic (core/ingest/org_profile.py)
and business value stays NOT_CALIBRATED: these numbers say how fast and how close to the best the solver gets,
not whether the plans are good for a real organisation.
"""
import argparse
import contextlib
import io
import json
import os
import platform
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
SEED = 2026
SWEEP_LIMITS = {100: [30, 60, 120, 240], 200: [60, 120, 240, 600], 300: [60, 120, 240, 600, 1200]}
# 300 = DP 100 + AI 100 + business automation 100 (core/ingest/org_profile.SIZES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=ROOT.parent).stdout.strip()
    except OSError:
        commit = None
    return {"commit": commit, "python": platform.python_version(), "machine": platform.machine(),
            "system": f"{platform.system()} {platform.release()}", "started_at": _now()}


def _bundle(size: int, workdir: Path) -> Path:
    from core.ingest.org_profile import generate_org_bundle
    return generate_org_bundle(workdir / f"org-n{size}", size, seed=SEED)


def _zip(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.iterdir()):
            zf.write(p, p.name)
    return buf.getvalue()


def _sse(lines):
    event, data = None, []
    for raw in lines:
        line = raw if isinstance(raw, str) else raw.decode()
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and event:
            yield event, json.loads("\n".join(data)) if data else {}
            event, data = None, []


def _pick_swap(graph, plan: dict, project_id: str, min_alloc: float) -> dict | None:
    """A realistic manual swap. Out: a plan member (the flagship's weakest first, then the weakest elsewhere).
    In: the best-scoring person of the same grade, not in the plan, with at least min_alloc available in every
    month of that project -- so the swap does not create a grade shortfall or availability breach by itself."""
    from core.scoring.engine import ScoringEngine
    S = ScoringEngine(graph).skill_matrix({})
    used = {e["person_id"] for e in plan["entries"]}
    score = lambda pid, jid: S[graph.pid_index[pid], graph.project_index[jid]]
    entries = sorted(plan["entries"], key=lambda e: (e["project_id"] != project_id, score(e["person_id"], e["project_id"])))
    for out in entries:
        proj = graph.projects[graph.project_index[out["project_id"]]]
        months = range(proj.start_month, proj.end_month + 1)
        leaving = graph.people[graph.pid_index[out["person_id"]]]
        need = max(min_alloc, out["alloc"])          # the newcomer inherits the leaver's allocation
        # same grade (no headcount shortfall) and same role type (consulting rates are 10% higher -> budget)
        cands = [p.id for p in graph.people if p.id not in used and p.grade == leaving.grade
                 and p.monthly_rate == leaving.monthly_rate and all(p.availability[m] >= need for m in months)]
        if cands:
            inn = max(cands, key=lambda pid: score(pid, out["project_id"]))
            return {"out_person_id": out["person_id"], "in_person_id": inn, "project_id": out["project_id"]}
    return None


@contextlib.contextmanager
def _server(work: Path, llm: bool):
    """uvicorn api.main:app on a free local port; yields the base URL."""
    import socket
    import httpx
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    env = {**os.environ, "TEAMWEAVER_SKIP_WARM": "1", "TEAMWEAVER_DATA_DIR": str(work / "data"),
           "TEAMWEAVER_SETTINGS_PATH": str(work / "settings.json")}
    if not llm:
        env["OPENAI_API_KEY"] = ""
        env["OPENAI_BASE_URL"] = "http://127.0.0.1:9"      # no key -> client init fails -> rule-based fallback
    log = open(work / "server.log", "w")
    proc = subprocess.Popen(["uv", "run", "--group", "benchmark", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT.parent, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(300):
            try:
                if httpx.get(base + "/api/meta", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f"server did not start, see {work / 'server.log'}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def run_pipeline(size: int, llm: bool, pdf: bool, as_real: bool = False) -> dict:
    """as_real: 묶음을 실데이터로 표시해(manifest synthetic=false) 올린다 -- 평가 원문 비공개 근거, AI 설명 비공개
    모드, PDF 근거 재확인까지 실데이터 경로를 그대로 탄다(2026-10-06 사용자: "이걸 실 데이터라고 가정하고 검증")."""
    import httpx
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    work = Path(tempfile.mkdtemp(prefix=f"rehearsal-n{size}-"))
    out = {"size": size, "env": _env_info(), "llm": llm, "as_real": as_real, "steps": {}}
    t0 = time.perf_counter()
    root = _bundle(size, work)
    if as_real:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest["synthetic"] = False                    # 파일 해시는 manifest 밖이라 그대로 맞는다
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    data = _zip(root)
    out["steps"]["generate_s"] = round(time.perf_counter() - t0, 2)
    out["bundle"] = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    out["bundle"].pop("files", None)
    b, rep0 = load_bundle(root)
    ds, parsed = to_dataset(b, rep0)
    graph = MemoryGraph.build(ds, parsed)          # same data as the server's; used only to pick a swap
    with _server(work, llm) as base, httpx.Client(base_url=base, timeout=None) as c:
        t = time.perf_counter()
        headers = {"content-type": "application/zip"}
        if os.environ.get("TEAMWEAVER_ADMIN_TOKEN"):
            headers["x-admin-token"] = os.environ["TEAMWEAVER_ADMIN_TOKEN"]
        r = c.post("/api/datasets", content=data, headers=headers)
        out["steps"]["upload_s"] = round(time.perf_counter() - t, 2)
        body = r.json()
        out["upload"] = {"status": r.status_code, "activated": body.get("activated"),
                         "errors": len((body.get("report") or {}).get("errors", [])),
                         "warnings": len((body.get("report") or {}).get("warnings", [])),
                         "notes": (body.get("report") or {}).get("notes", [])}
        if r.status_code != 200:
            out["error"] = body
            return out
        meta = c.get("/api/meta").json()
        sresp = c.get("/api/settings").json()
        settings = dict(sresp["settings"])
        # 화면과 같게: 자동 계산 시간이면 서버가 정한 실제 값을 보낸다(web effectiveSettings)
        settings["time_limit"] = sresp.get("effective_time_limit", settings["time_limit"])
        out["settings"] = settings
        out["active"] = c.get("/api/datasets/active").json()
        req = {"weights": {}, "milp_params": settings, "n_alternatives": 3, "dataset_version": meta["dataset_version"]}
        plans, done = [], None
        t = time.perf_counter()
        with c.stream("POST", "/api/optimize", json=req) as resp:
            for ev, payload in _sse(resp.iter_lines()):
                if ev == "plan":
                    plans.append({"label": payload["label"], "arrived_s": round(time.perf_counter() - t, 2),
                                  "objective": payload["objective"], "fulfillment": payload["fulfillment"],
                                  "optimization_ratio": payload["optimization_ratio"],
                                  "entries": len(payload["entries"]), "unfilled": payload["unfilled"],
                                  "people": len({e["person_id"] for e in payload["entries"]}),
                                  "time_limited": payload.get("time_limited"),
                                  "_payload": payload})
                elif ev in ("done", "error"):
                    done = {"event": ev, **payload}
        out["steps"]["optimize_s"] = round(time.perf_counter() - t, 2)
        out["optimize_done"] = done
        if plans:
            best = max(plans, key=lambda p: p["objective"])["_payload"]       # the plan a user would pick
            out["picked_plan"] = best["label"]
            swap = _pick_swap(graph, best, out["bundle"].get("flagship_project", "J001"), settings["min_alloc"])
            out["swap"] = swap
            if swap:
                wreq = {"entries": best["entries"], "swap": swap, "weights": {}, "milp_params": settings,
                        "dataset_version": meta["dataset_version"]}
                t = time.perf_counter()
                w = c.post("/api/whatif", json=wreq)
                out["steps"]["whatif_s"] = round(time.perf_counter() - t, 2)
                wj = w.json()
                out["whatif"] = {"status": w.status_code, "objective_delta": wj.get("objective_delta"),
                                 "feasible": wj.get("feasible"), "fallback_used": wj.get("fallback_used"),
                                 "evidence": len((wj.get("briefing") or {}).get("evidence", [])),
                                 "evidence_kinds": sorted({e.get("kind") for e in (wj.get("briefing") or {}).get("evidence", [])}),
                                 "rationale": (wj.get("briefing") or {}).get("rationale")}
                t = time.perf_counter()
                ap = c.post("/api/plans/apply-swap", json=wreq)
                out["steps"]["apply_s"] = round(time.perf_counter() - t, 2)
                apj = ap.json()
                out["apply"] = {"status": ap.status_code, "feasible": apj.get("feasible"),
                                "objective": apj.get("objective"), "warnings": apj.get("warnings")}
                if pdf:
                    rep = {"plan_label": best["label"], "entries": best["entries"], "objective": best["objective"],
                           "fulfillment": best["fulfillment"], "optimization_ratio": best["optimization_ratio"],
                           "unfilled": best["unfilled"], "briefing": wj.get("briefing"),
                           "fallback_used": wj.get("fallback_used", False), "milp_params": settings,
                           "dataset_version": meta["dataset_version"], "applied_swaps": [swap],
                           "base_entries": best["entries"], "weights": {}, "plan_token": best.get("plan_token")}
                    t = time.perf_counter()
                    p = c.post("/api/report", json=rep)
                    out["steps"]["pdf_s"] = round(time.perf_counter() - t, 2)
                    out["pdf"] = {"status": p.status_code, "bytes": len(p.content) if p.status_code == 200 else None,
                                  "detail": None if p.status_code == 200 else p.text[:300]}
    for p in plans:
        p.pop("_payload")
    out["plans"] = plans
    out["finished_at"] = _now()
    return out


def run_sweep(size: int, limits: list[int]) -> dict:
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    work = Path(tempfile.mkdtemp(prefix=f"rehearsal-sweep-n{size}-"))
    bundle, report = load_bundle(_bundle(size, work))
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    base = PlacementSettings().to_milp_params()
    runs = []
    for k, limit in enumerate(limits):
        # alternate the order so the process's first-solve start-up cost does not always land on the same solver
        for solver in (("cbc", "highs") if k % 2 == 0 else ("highs", "cbc")):
            params = base.model_copy(update={"time_limit": limit, "solver": solver})
            t = time.perf_counter()
            row = {"solver": solver, "time_limit": limit, "gap": params.gap}
            try:
                assessment = solve_milp_assessment(graph, S, C, params)
                cand = assessment.accepted
                native = assessment.native_capture
                ev = getattr(native, "evidence", None)
                pidx, jidx = graph.pid_index, graph.project_index
                row.update({"accepted": cand is not None,
                            "objective": None if cand is None else cand.objective,
                            # objective is dominated by the -100/seat unfilled penalty; keep the parts readable too
                            "skill_term": None if cand is None else round(sum(
                                S[pidx[e.person_id], jidx[e.project_id]] * e.alloc for e in cand.plan.entries), 4),
                            "unfilled_seats": None if cand is None else len(cand.plan.unfilled),
                            # HiGHS can report IntegerFeasible with no real incumbent; judge by acceptance
                            "termination": (getattr(ev, "termination_reason", None) if cand is not None
                                            else "no_accepted_solution"),
                            "native_status": getattr(ev, "native_status", None),
                            "refinement": assessment.refinement.reason})
            except Exception as exc:          # noqa: BLE001 -- a failed run is a result, not a crash
                row.update({"accepted": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
            row["wall_s"] = round(time.perf_counter() - t, 2)
            runs.append(row)
            print(f"[sweep n{size}] {solver} {limit}s -> {row.get('objective')} "
                  f"{row.get('termination')} {row['wall_s']}s", flush=True)
    best = max((r["objective"] for r in runs if r.get("objective") is not None), default=None)
    for r in runs:
        if best is not None and r.get("objective") is not None:
            r["gap_to_best"] = round((best - r["objective"]) / abs(best), 6) if best else 0.0
    return {"size": size, "env": _env_info(), "limits": limits, "best_objective": best, "runs": runs,
            "people": len(graph.people), "projects": len(graph.projects), "finished_at": _now()}


PAIR_CAPS = (0, 200, 400, 800, None)      # None = the pre-2026-10-05 default (pair_keep_ratio 0.15, max_pairs 5000)
# Fixed yardstick: every plan is re-scored under the OLD default objective, so the comparison does not move when the
# service default changes (it changed to max_pairs=200 because of these very results).
LEGACY_PAIRS = {"pair_keep_ratio": 0.15, "max_pairs": 5000}


def run_pairs(size: int, time_limit: int = 120, caps=PAIR_CAPS) -> dict:
    """Model size, not time, limited the 200-person run: synergy pair variables grow with pairs x projects
    (~3,000 pairs x 40 projects at 200 people). Solve plan A with the synergy reward capped to the top-|C| `cap`
    pairs (0 = reward term off) and score every plan under the FULL service objective with plan_eval, so plans
    from smaller models are judged by the same yardstick as the default one."""
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import _overfamiliar_pairs, pruned_pairs, solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    work = Path(tempfile.mkdtemp(prefix=f"rehearsal-pairs-n{size}-"))
    bundle, report = load_bundle(_bundle(size, work))
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    full = PlacementSettings().to_milp_params().model_copy(update={"time_limit": time_limit, **LEGACY_PAIRS})
    runs = []
    for cap in caps:
        upd = {} if cap is None else ({"pair_keep_ratio": 0.0, "max_pairs": 1} if cap == 0
                                      else {"pair_keep_ratio": 1.0, "max_pairs": cap})
        params = full.model_copy(update=upd)
        n_pairs = len(pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)) if cap != 0 else 0
        row = {"cap": "default" if cap is None else cap, "reward_pairs": n_pairs,
               "overfamiliar_pairs": len(_overfamiliar_pairs(graph, params.clique_threshold_months, getattr(params, "clique_window_months", None))),
               "y_vars_approx": (n_pairs + len(_overfamiliar_pairs(graph, params.clique_threshold_months, getattr(params, "clique_window_months", None)))) * len(graph.projects)}
        t = time.perf_counter()
        try:
            a = solve_milp_assessment(graph, S, C, params)
            if a.accepted is None:
                raise RuntimeError(f"no accepted solution ({a.refinement.reason})")
            ev = evaluate_plan(graph, S, C, full, a.accepted.plan.entries)
            o = ev.objective
            row.update({"accepted": True, "full_objective": round(o.total, 3), "skill": round(o.skill, 3),
                        "synergy": round(o.synergy, 3), "unfilled_seats": sum(s.missing for s in ev.shortfalls),
                        "violations": len(ev.violations),
                        "termination": getattr(a.validation_candidate.evidence, "termination_reason", None)})
        except Exception as exc:  # noqa: BLE001
            row.update({"accepted": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
        row["wall_s"] = round(time.perf_counter() - t, 2)
        runs.append(row)
        print(f"[pairs n{size}] cap={row['cap']} pairs={n_pairs} -> {row.get('full_objective')} "
              f"unfilled={row.get('unfilled_seats')} {row['wall_s']}s", flush=True)
    return {"size": size, "env": _env_info(), "time_limit": time_limit, "runs": runs,
            "yardstick_params": full.model_dump(),
            "people": len(graph.people), "projects": len(graph.projects), "finished_at": _now()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--size", type=int, required=True, choices=sorted(SWEEP_LIMITS))
    ap.add_argument("--stage", choices=("pipeline", "sweep", "pairs", "all"), default="all")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--as-real", action="store_true", help="upload the bundle marked as real data (synthetic=false)")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--limits", type=int, nargs="*", help="override the sweep time limits (seconds)")
    args = ap.parse_args()
    outdir = RESULTS / f"n{args.size}"
    outdir.mkdir(parents=True, exist_ok=True)
    if args.stage in ("pipeline", "all"):
        res = run_pipeline(args.size, llm=not args.no_llm, pdf=not args.no_pdf, as_real=args.as_real)
        name = "pipeline-real.json" if args.as_real else "pipeline.json"
        (outdir / name).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"[pipeline n{args.size}] steps={res.get('steps')} plans={[(p['label'], p['arrived_s']) for p in res.get('plans', [])]}",
              flush=True)
    if args.stage == "pairs":
        res = run_pairs(args.size)
        (outdir / "pairs.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.stage in ("sweep", "all"):
        res = run_sweep(args.size, args.limits or SWEEP_LIMITS[args.size])
        (outdir / "sweep.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
