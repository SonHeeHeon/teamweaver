"""야간 배치 시험 공용 도우미(2026-10-18, claude-b)."""
import json
import time

from core.evaluate.plan_eval import evaluate_plan
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine


def current_entries(active):
    return [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in active.current]


def evaluated(active, params, entries):
    eng = ScoringEngine(active.graph)
    return evaluate_plan(active.graph, eng.skill_matrix({}), eng.synergy_matrix(), params, entries)


def run_now(app, minutes: int = 10) -> dict:
    n = app.state.nightly
    assert n.start_run("manual", time.time() + minutes * 60)
    n._run_thread.join(180)
    assert not n.running
    return n.store.read("report.json")


def web_settings(c) -> dict:
    s = c.get("/api/settings").json()
    return {**s["settings"], "time_limit": s["effective_time_limit"]}


def sse(res):
    out, event = [], None
    for line in res.text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1])))
    return out
