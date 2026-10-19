"""더 강한 비교 상대: 제약을 지키는 휴리스틱 + 국소 탐색 vs MILP(2026-10-18, claude-a).

지금 화면·보고서의 "단순 규칙"(core.optimize.greedy)은 예산·협업을 보지 않는다(Codex 평가 2026-10-18 B). 여기서는
솔버 없이 숙련자가 짤 법한 방식을 같은 시간 안에 최대한 돌린다.
  1. 필수 자리 채우기: 사업 시작 순서대로, 등급 자리마다 (기술 적합 + 이미 넣은 팀원과의 협업 보상 - 익숙한 쌍 감점)이
     가장 큰 사람을 최소 투입률로 넣는다. 가용률·동시 사업 상한·월 예산을 넘는 사람은 고르지 않는다.
  2. 투입률 올리기: 남은 예산·가용률 안에서 (기술 적합 / 단가)가 높은 배치부터 투입률을 올린다.
  3. 정원에 없는 등급 더 넣기: 예산이 남으면 이득이 큰 사람을 최소 투입률로 더한다(MILP도 허용하는 선택).
  4. 국소 탐색: MILP 안 하나의 시간 한도만큼 무작위로 "같은 등급 사람 바꾸기"·"투입률 옮기기"를 시도해 점수가 오르면 받는다.
채점은 서비스 평가기(plan_eval)로 MILP 안 A(미리 계산 결과)와 똑같이 한다. 제약 위반이 있으면 그대로 기록한다.

    uv run --group benchmark python -m rehearsal.strong_baseline
결과: rehearsal/results/strong-baseline.json
2026-10-19: 미리 계산의 설정을 따른다 -- 전일 투입이면 휴리스틱도 100%만(투입률 올리기·옮기기 없음, 같은 등급 바꾸기만).
"""
from __future__ import annotations

import json
import random
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = ROOT / "rehearsal" / "results" / "strong-baseline.json"
BUNDLES = {100: "org-n100", 200: "org-n200.zip", 300: "org-n300.zip"}
TOL = 1e-6


def _floor2(x: float) -> float:
    return int(x * 100 + 1e-9) / 100


class State:
    def __init__(self, graph, S, C, params):
        from core.optimize.milp import _overfamiliar_pairs, familiar_exempt, pruned_pairs
        self.g, self.S, self.p = graph, S, params
        n = len(graph.people)
        self.R = np.zeros((n, n))
        for a, b in pruned_pairs(C, params.pair_keep_ratio, params.max_pairs):
            self.R[a, b] = self.R[b, a] = C[a, b]
        self.P = np.zeros((n, n))
        for a, b in _overfamiliar_pairs(graph, params.clique_threshold_months,
                                        getattr(params, "clique_window_months", None)):
            self.P[a, b] = self.P[b, a] = 1.0
        # 지금 같은 사업에 함께 있는 쌍은 그 사업에서 감점하지 않는다(2026-10-19 사용자 결정 -- 평가기·MILP와 같은 규칙, Codex 리뷰:
        # 빠뜨리면 휴리스틱이 기존 팀을 흩는 것을 이득으로 보고 실제 점수를 낮추는 교체를 받아들였다)
        self.exempt: dict[int, set] = {}
        for a, b, j in familiar_exempt(graph):
            self.exempt.setdefault(j, set()).add((a, b))
        self.rem = [list(map(float, pp.availability)) for pp in graph.people]
        self.conc = [[0] * len(pp.availability) for pp in graph.people]
        self.alloc: dict[tuple[int, int], float] = {}
        self.team: list[set[int]] = [set() for _ in graph.projects]
        self.cost = [0.0] * len(graph.projects)

    def rate(self, i):
        return self.g.people[i].monthly_rate

    def free(self, i, j) -> float:
        return min(self.rem[i][m] for m in self.g.projects[j].months)

    def can_join(self, i, j) -> bool:
        return i not in self.team[j] and all(self.conc[i][m] < self.p.max_concurrent_projects
                                             for m in self.g.projects[j].months)

    def budget_left(self, j) -> float:
        return self.g.projects[j].monthly_budget - self.cost[j]

    def pen(self, i, members: list, j: int) -> float:
        """i가 j의 members와 함께일 때 익숙한 쌍 수(지금 그 사업에 함께 있는 쌍은 빼고)."""
        if not members:
            return 0.0
        ex = self.exempt.get(j)
        if not ex:
            return float(self.P[i, members].sum())
        return float(sum(self.P[i, t] for t in members if (min(i, t), max(i, t)) not in ex))

    def gain_join(self, i, j, a) -> float:
        t = list(self.team[j])
        return (float(self.S[i, j]) * a + self.p.lam * float(self.R[i, t].sum())
                - self.p.mu * self.pen(i, t, j)) if t else float(self.S[i, j]) * a

    def add(self, i, j, a):
        self.alloc[(i, j)] = a
        self.team[j].add(i)
        self.cost[j] += self.rate(i) * a
        for m in self.g.projects[j].months:
            self.rem[i][m] -= a
            self.conc[i][m] += 1

    def remove(self, i, j) -> float:
        a = self.alloc.pop((i, j))
        self.team[j].discard(i)
        self.cost[j] -= self.rate(i) * a
        for m in self.g.projects[j].months:
            self.rem[i][m] += a
            self.conc[i][m] -= 1
        return a

    def change(self, i, j, new):
        old = self.alloc[(i, j)]
        d = new - old
        self.alloc[(i, j)] = new
        self.cost[j] += self.rate(i) * d
        for m in self.g.projects[j].months:
            self.rem[i][m] -= d

    def entries(self):
        from core.optimize.types import AssignEntry
        return [AssignEntry(person_id=self.g.people[i].id, project_id=self.g.projects[j].id, alloc=a)
                for (i, j), a in sorted(self.alloc.items())]


def heuristic(graph, S, C, params, seconds: float, seed: int = 0) -> tuple[list, dict]:
    rng = random.Random(seed)
    st = State(graph, S, C, params)
    ppl, projs = graph.people, graph.projects
    # 전일 투입(2026-10-19 서비스 기본): 투입률은 1.0뿐 -- 가용률 남은 양(rem)이 겹침·휴직을 막는다(허용 0명 기준). 투입률 올리기·옮기기는 없다
    full = getattr(params, "allocation_mode", "fixed") == "full"
    lo = 1.0 if full else params.min_alloc
    by_grade: dict = {}
    for i, pp in enumerate(ppl):
        by_grade.setdefault(pp.grade, []).append(i)
    missing = 0
    # 1. 필수 자리
    for j in sorted(range(len(projs)), key=lambda j: projs[j].start_month):
        for grade, need in sorted(projs[j].grade_headcount.items(), key=lambda t: t[0].value):
            for _ in range(need):
                best, bi = None, None
                for i in by_grade.get(grade, []):
                    if not st.can_join(i, j) or st.free(i, j) < lo - TOL or st.rate(i) * lo > st.budget_left(j) + TOL:
                        continue
                    v = st.gain_join(i, j, lo)
                    if best is None or v > best:
                        best, bi = v, i
                if bi is None:
                    missing += 1
                else:
                    st.add(bi, j, lo)

    def raise_allocs():
        if full:
            return
        moved = True
        while moved:
            moved = False
            cand = sorted(st.alloc, key=lambda k: -float(S[k]) / max(st.rate(k[0]), 1e-9))
            for (i, j) in cand:
                a = st.alloc[(i, j)]
                room = min(1.0 - a, st.free(i, j), st.budget_left(j) / max(st.rate(i), 1e-9))
                new = _floor2(a + room)              # 내림만 -- 올림하면 가용률·예산을 넘을 수 있다(Codex 리뷰)
                if new - a >= 0.01 - TOL and float(S[i, j]) > 0:
                    st.change(i, j, new)
                    moved = True
    # 3. 정원에 없는 등급(자유) 더 넣기 -- 이득이 있을 때만, 2와 번갈아
    def add_free():
        added = False
        for j, pj in enumerate(projs):
            free_grades = {pp.grade for pp in ppl} - set(pj.grade_headcount)
            best, bi = 0.0, None
            for g_ in free_grades:
                for i in by_grade.get(g_, []):
                    if not st.can_join(i, j) or st.free(i, j) < lo - TOL or st.rate(i) * lo > st.budget_left(j) + TOL:
                        continue
                    v = st.gain_join(i, j, lo)
                    if v > best:
                        best, bi = v, i
            if bi is not None:
                st.add(bi, j, lo)
                added = True
        return added
    raise_allocs()
    for _ in range(3):
        if not add_free():
            break
        raise_allocs()

    # 4. 국소 탐색(빠른 차분 점수)
    t_end = time.perf_counter() + seconds
    tries = accepted = 0
    keys = list(st.alloc)
    while time.perf_counter() < t_end:
        tries += 1
        if not keys:
            break
        i, j = rng.choice(keys)
        if (i, j) not in st.alloc:
            keys = list(st.alloc)
            continue
        if full or rng.random() < 0.6:               # 같은 등급 사람 바꾸기(투입률 그대로) -- 전일 투입은 이것만
            a = st.alloc[(i, j)]
            k = rng.choice(by_grade[ppl[i].grade])
            if k == i or not st.can_join(k, j) or st.free(k, j) < a - TOL:
                continue
            if st.cost[j] - st.rate(i) * a + st.rate(k) * a > projs[j].monthly_budget + TOL:
                continue
            t = [q for q in st.team[j] if q != i]
            d = (float(S[k, j] - S[i, j]) * a + params.lam * float(st.R[k, t].sum() - st.R[i, t].sum())
                 - params.mu * (st.pen(k, t, j) - st.pen(i, t, j)))
            if d > 1e-9:
                st.remove(i, j)
                st.add(k, j, a)
                accepted += 1
                keys = list(st.alloc)
        else:                                        # 같은 사업 안에서 투입률 옮기기(예산 중립에 가깝게)
            others = [q for q in st.team[j] if q != i]
            if not others:
                continue
            q = rng.choice(others)
            step = 0.1
            ai, aq = st.alloc[(i, j)], st.alloc[(q, j)]
            if aq - step < lo - TOL or ai + step > 1.0 + TOL or st.free(i, j) < step - TOL:
                continue
            dcost = st.rate(i) * step - st.rate(q) * step
            if st.cost[j] + dcost > projs[j].monthly_budget + TOL:
                continue
            d = float(S[i, j] - S[q, j]) * step
            if d > 1e-9:
                st.change(q, j, _floor2(aq - step + 1e-9))    # 둘째 자리 내림(부동소수 잔차 제거, 올리지 않음)
                st.change(i, j, _floor2(ai + step + 1e-9))
                accepted += 1
        if tries % 2000 == 0:
            raise_allocs()
    raise_allocs()
    return st.entries(), {"tries": tries, "accepted": accepted, "missing_seats_construct": missing}


def main() -> None:
    from api.demos import build_demo
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.greedy import solve_greedy
    from core.optimize.milp import MilpParams
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    from collections import Counter
    res = {"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sizes": {}}
    for n, name in BUNDLES.items():
        act = build_demo(DEMO / name)
        g = act.graph
        eng = ScoringEngine(g)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        pre = json.loads((DEMO / "precomputed" / f"{name.removesuffix('.zip')}.json").read_text("utf-8"))
        if pre.get("dataset_version") != act.info.version:     # 미리 계산 때와 다른 데이터면 그 안 A와 비교할 수 없다(Codex 리뷰)
            raise SystemExit(f"{name}: 데이터 버전이 미리 계산과 다르다 -- rehearsal.precompute_demo부터 다시 돌릴 것")
        params = MilpParams(**pre["optimize"]["milp_params"])

        def score(entries):
            ev = evaluate_plan(g, S, C, params, entries)
            o = ev.objective
            return {"total": round(o.total, 3), "skill": round(o.skill, 3), "synergy": round(o.synergy, 3),
                    "overfamiliarity": round(o.overfamiliarity, 3),
                    "unfilled_seats": sum(s.missing for s in ev.shortfalls),
                    "violations": dict(Counter(v.code for v in ev.violations)), "assignments": len(entries)}
        milp = score([AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]])
        simple = score(list(solve_greedy(g, S, params.max_concurrent_projects, min_alloc=params.min_alloc,
                                         full=params.allocation_mode == "full").entries))
        t0 = time.perf_counter()
        ents, stats = heuristic(g, S, C, params, seconds=params.time_limit)
        strong = score(ents)
        strong["elapsed_s"] = round(time.perf_counter() - t0, 1)
        strong.update(stats)
        best_bound = pre["optimize"]["plans"][0].get("best_bound")
        res["sizes"][str(n)] = {"time_limit_s": params.time_limit, "milp_plan_a": milp, "simple_rule": simple,
                                "strong_heuristic": strong, "milp_best_bound": best_bound}
        print(n, json.dumps(res["sizes"][str(n)], ensure_ascii=False), flush=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    print("저장:", OUT)


if __name__ == "__main__":
    main()
