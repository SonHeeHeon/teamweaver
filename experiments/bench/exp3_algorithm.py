"""실험 3: Greedy vs MILP.

핵심 논지는 "MILP가 점수가 높다"가 아니라 "Greedy는 예산·정원 제약을 위반하는
해를 내놓는 반면 MILP는 제약을 지키며 미충원으로 정직하게 부족을 드러낸다"이다.
따라서 목적값뿐 아니라 제약 위반 건수를 함께 집계한다.

**회복성(코디네이터 리뷰 반영, 2026-08-07):** 최초 스윕 실행 중 대규모 스케일에서
`solve_milp`가 `RuntimeError: MILP failed: Infeasible`를 던져 전체 스윕이 죽고
그 전까지 끝낸 스케일들의 결과까지 통째로 유실됐다(당시 `run()`은 전체 스케일을
다 돌고 나서야 한 번에 dict를 반환했으므로, 마지막 스케일에서 죽으면 이전 스케일의
작업이 메모리에서만 존재하다 사라짐). 이를 구조적으로 고친다:
  1. 스케일(=한 (n_people, seed) 조합) 단위로 예외를 잡는다 — 한 스케일이 죽어도
     `failures`에 기록하고 다음 스케일로 진행한다.
  2. `on_scale_done` 콜백을 스케일이 끝날 때마다(성공/실패 무관) 그때까지 누적된
     결과로 호출한다 — 호출자(`main()`)가 매 스케일마다 중간 저장(체크포인트)할 수
     있게 하기 위함이다.
  3. 각 MILP 행에 `hit_time_limit`을 기록한다. `solve_milp`는 CBC의 내부 상태
     (Optimal 증명 여부)를 반환하지 않으므로(기존 계약을 바꾸지 않기 위해
     `solve_milp` 자체는 수정하지 않았다) solve_ms가 설정된 time_limit에 근접했는지로
     간접 판정하는 휴리스틱이다 — 정확한 CBC status 판독이 아님을 명시한다.
"""
import logging
import time

from core.optimize.alternatives import generate_plans
from core.optimize.greedy import solve_greedy
from core.optimize.metrics import matching_fulfillment, optimization_ratio
from core.optimize.milp import MilpParams, solve_milp
from core.scoring.engine import ScoringEngine
from experiments.bench import datasets, harness

logger = logging.getLogger(__name__)

# 최종 리뷰 Critical 2: 리포트가 이 스윕을 "100인 데모 fixture"라고 불러 -- 커밋된
# fixture(fixtures/*.json, LLM 모드 리뷰)를 실제로 로드하는 것으로 오해하게 만들었다.
# 실제로는 datasets.build_scale()이 매 스케일마다 core.datagen.generator로 데이터를
# *새로 생성*한다(rule-based 파싱의 템플릿 리뷰) -- 스케일 스윕은 fixture에 없는
# n=50/200/300/...까지 다뤄야 하므로 애초에 fixture만으로는 불가능하고, 이 자체는
# 정상이다. 문제는 라벨이었다: n=100 지점조차 커밋된 fixture와 인원·프로젝트 수만
# 같을 뿐 리뷰 텍스트(따라서 C 행렬, optimization_ratio)는 다르다. 이 상수를
# run()의 반환값에 실어 report.py가 "100인 데모 fixture"라고 잘못 부르지 않도록 한다.
DATA_SOURCE_NOTE = (
    "이 스윕의 모든 행은 datasets.build_scale()이 규모마다 새로 생성한 데이터셋을 쓴다 "
    "(core.datagen.generator, seed=42 고정, rule-based 파싱의 템플릿 리뷰) — 커밋된 "
    "LLM 모드 데모 fixture(fixtures/*.json)를 로드하지 않는다. n=100 지점도 인원·"
    "프로젝트 수만 fixture와 같을 뿐 리뷰 텍스트가 달라 C 행렬과 optimization_ratio가 "
    "fixture와 다르다 — 프로젝트 개수/인원 수가 같다고 같은 데이터가 아니다.")

# solve_ms(벽시계)가 time_limit(초)의 이 비율 이상이면 "시간 상한에 도달"로 간주한다.
# CBC의 timeLimit 파라미터는 자체 솔브 시간만 제한하고, 우리가 측정하는 solve_ms는
# 모델 구성(파이썬 쪽) 오버헤드까지 포함하므로 100%가 아니라 약간의 여유(5%)를 둔다.
TIME_LIMIT_HIT_RATIO = 0.95


def _hit_time_limit(solve_ms: float, time_limit_s: int) -> bool:
    return solve_ms >= TIME_LIMIT_HIT_RATIO * time_limit_s * 1000.0


def _budget_violations(ds, plan) -> int:
    by_pid = {p.id: p for p in ds.people}
    cost: dict[str, float] = {}
    for e in plan.entries:
        cost[e.project_id] = cost.get(e.project_id, 0.0) + by_pid[e.person_id].monthly_rate * e.alloc
    return sum(1 for j in ds.projects if cost.get(j.id, 0.0) > j.monthly_budget + 1e-6)


def _run_one_scale(n_people, n_projects, seed, pair_caps, time_limit):
    """단일 (n_people, seed) 조합에 대해 greedy/milp/alternatives를 전부 수행하고
    (rows, violations, alternatives) 조각을 반환한다.

    예외는 그대로 전파한다 — 호출자(`run`)가 스케일 단위로 잡아서 실패를 기록하고
    다음 스케일로 진행한다. 즉 한 스케일은 원자적이다: 도중에 실패하면(예: MILP 쪽이
    죽으면) 이미 구했던 greedy 결과까지 포함해 그 스케일의 행은 하나도 채택하지
    않는다 — 알고리즘 비교가 목적인데 절반만 있는 스케일은 비교 대상으로 쓸 수 없기
    때문이다.
    """
    rows, violations, alternatives = [], [], []
    ds, parsed, g = datasets.build_scale(n_people, n_projects, seed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    base = MilpParams(time_limit=time_limit)

    t0 = time.perf_counter()
    gp = solve_greedy(g, S)
    greedy_ms = (time.perf_counter() - t0) * 1000.0
    rows.append({"algorithm": "greedy", "n_people": n_people, "n_projects": n_projects,
                 "seed": seed, "pair_cap": None, "solve_ms": greedy_ms,
                 "hit_time_limit": False,
                 "objective": gp.objective,
                 "optimization_ratio": optimization_ratio(g, S, gp, base),
                 "fulfillment": matching_fulfillment(g, gp, {}),
                 "unfilled": len(gp.unfilled), "entries": len(gp.entries)})
    violations.append({"algorithm": "greedy", "n_people": n_people, "n_projects": n_projects,
                       "seed": seed,
                       "budget_violations": _budget_violations(ds, gp),
                       "reported_violations": len(gp.violations),
                       "unfilled_slots": len(gp.unfilled)})

    for cap in pair_caps:
        params = base.model_copy(update={"max_pairs": cap})
        t0 = time.perf_counter()
        mp = solve_milp(g, S, C, params)
        milp_ms = (time.perf_counter() - t0) * 1000.0
        hit_limit = _hit_time_limit(milp_ms, time_limit)
        if hit_limit:
            logger.warning(f"MILP hit time_limit={time_limit}s at n_people={n_people} "
                           f"pair_cap={cap} (solve_ms={milp_ms:.0f})")
        rows.append({"algorithm": "milp", "n_people": n_people, "n_projects": n_projects,
                     "seed": seed, "pair_cap": cap, "solve_ms": milp_ms,
                     "hit_time_limit": hit_limit,
                     "objective": mp.objective,
                     "optimization_ratio": optimization_ratio(g, S, mp, params),
                     "fulfillment": matching_fulfillment(g, mp, {}),
                     "unfilled": len(mp.unfilled), "entries": len(mp.entries),
                     # 최종 리뷰 Important 6: optimization_ratio는 분자·분모 모두 skill 항만
                     # 쓰지만(core/optimize/metrics.py), 배치 자체는 solve_milp가 skill +
                     # lam*synergy - mu*clique - slack_penalty를 최대화해 고른 것이다.
                     # 어떤 lam/mu로 solve됐는지를 기록해 두지 않으면 "ratio가 skill 항만
                     # 잰다"는 사실을 리포트가 서술할 근거가 없다.
                     "milp_params": params.model_dump()})
        # 예산 준수는 max_pairs(=synergy pruning 정도)와 무관한 하드 제약이므로
        # cap마다 개별로 확인한다 — 앞서는 pair_caps[0]만 확인하고 나머지는
        # "당연히 같을 것"이라는 추론으로 남겨뒀다(코디네이터 리뷰, 2026-08-08 반영:
        # 리포트가 측정 대신 추론을 제시하고 있었다). cap을 기록해 둘을 구분한다.
        violations.append({"algorithm": "milp", "n_people": n_people, "n_projects": n_projects,
                           "seed": seed,
                           "pair_cap": cap,
                           "budget_violations": _budget_violations(ds, mp),
                           "reported_violations": len(mp.violations),
                           "unfilled_slots": len(mp.unfilled)})

    t0 = time.perf_counter()
    plans = generate_plans(g, S, C, base, n_alternatives=3)
    alt_ms = (time.perf_counter() - t0) * 1000.0
    obj_a = plans[0].objective
    alternatives.append({
        "n_people": n_people, "seed": seed, "plan_count": len(plans),
        "total_ms": alt_ms,
        "labels": [p.label for p in plans],
        "quality_vs_a": [(p.objective / obj_a) if obj_a > 0 else None for p in plans],
        "jaccard_vs_a": [len(p.pairs() & plans[0].pairs()) /
                         max(1, len(p.pairs() | plans[0].pairs())) for p in plans],
    })
    return rows, violations, alternatives


def run(scales=None, seeds=(42,), pair_caps=(1000, 5000), time_limit: int = 180,
        on_scale_done=None, skip_scales: dict[int, str] | None = None) -> dict:
    """스케일 스윕을 실행한다.

    스케일 하나가 예외를 던져도(예: 대규모에서 CBC가 `Infeasible`을 보고하거나
    메모리 압박으로 실패) 전체 스윕은 죽지 않는다 — `failures`에 기록하고 다음
    스케일로 진행한다. `on_scale_done`이 주어지면 스케일이 끝날 때마다(성공/실패/
    스킵 무관) 그 시점까지 누적된 결과 dict로 호출된다 — 호출자가 스케일 단위로
    중간 저장(체크포인트)할 수 있게 하기 위함이다.

    `skip_scales`(선택, {n_people: reason})가 주어지면 해당 n_people은 아예
    시도하지 않고(예: n=300/500/1000이 실용적 세션 예산을 초과할 것으로 판단됨)
    예외 경로와 **동일한 키 스키마**(n_people, n_projects, seed, status, reason)로
    `failures`에 기록한다 — 코디네이터 리뷰 반영(2026-08-08): 이전에는 이런
    스킵 결정을 저장소 밖 도구가 JSON을 직접 편집해 기록했는데, `main()`을
    재실행하면 그 항목이 재현되지 않아 커밋된 결과물의 provenance(출처 추적성)가
    깨졌다. 이제는 스킵도 `run()`이 직접 만들어낸다 — `main()`을 다시 돌리면
    똑같은 결과가 나온다.
    """
    scales = scales or datasets.SCALES
    skip_scales = skip_scales or {}
    rows, violations, alternatives, failures = [], [], [], []

    for n_people, n_projects in scales:
        for seed in seeds:
            if n_people in skip_scales:
                failures.append({"n_people": n_people, "n_projects": n_projects, "seed": seed,
                                 "status": "skipped", "reason": skip_scales[n_people]})
                if on_scale_done is not None:
                    on_scale_done({"rows": rows, "violations": violations,
                                  "alternatives": alternatives, "failures": failures})
                continue
            try:
                r, v, a = _run_one_scale(n_people, n_projects, seed, pair_caps, time_limit)
                rows.extend(r)
                violations.extend(v)
                alternatives.extend(a)
            except Exception as exc:
                logger.error(f"scale (n_people={n_people}, n_projects={n_projects}, "
                            f"seed={seed}) failed: {type(exc).__name__}: {exc}")
                failures.append({"n_people": n_people, "n_projects": n_projects, "seed": seed,
                                 "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
            if on_scale_done is not None:
                on_scale_done({"rows": rows, "violations": violations,
                              "alternatives": alternatives, "failures": failures})

    return {"rows": rows, "violations": violations, "alternatives": alternatives,
           "failures": failures, "data_source": DATA_SOURCE_NOTE}


# n=300/500/1000: n=50/100/200에서 실측한 MILP solve_ms 증가(766.7ms -> 6933.5ms
# -> 22628.7ms, 각각 9.04배·3.26배)를 근거로, 스케일 하나당 최대 6번의 MILP급
# solve(greedy+cap 2개+대안 최대 3개)가 누적되면 실용적 세션 예산을 넘어설 것으로
# 판단해 건너뛴다. n=300은 실제로 두 차례 시도했고(둘 다 CBC가 정상 진행 중이었지,
# 멈춘 게 아니었다) 예산 안에 끝내지 못해 중단했다 — task-5-report.md 참고.
_SKIP_REASON = ("extrapolated beyond practical session budget: milp cap=1000 solve_ms "
               "766.7 -> 6933.5 -> 22628.7 at n=50/100/200 (9.04x, then 3.26x per "
               "doubling); n=300 was attempted twice and killed (CBC healthy, not "
               "hung, but not converging inside budget) before this skip list was added")
DEFAULT_SKIP_SCALES = {300: _SKIP_REASON, 500: _SKIP_REASON, 1000: _SKIP_REASON}


def main():
    def _checkpoint(partial):
        path = harness.save_result("exp3_algorithm", partial)
        n_done = len({(r["n_people"], r["seed"]) for r in partial["rows"]}) + len(partial["failures"])
        print(f"checkpoint: saved {path} after scale #{n_done} "
              f"(rows={len(partial['rows'])} failures={len(partial['failures'])})")

    out = run(on_scale_done=_checkpoint, skip_scales=DEFAULT_SKIP_SCALES)
    path = harness.save_result("exp3_algorithm", out)
    gv = sum(v["budget_violations"] for v in out["violations"] if v["algorithm"] == "greedy")
    mv = sum(v["budget_violations"] for v in out["violations"] if v["algorithm"] == "milp")
    hit = [r for r in out["rows"] if r.get("hit_time_limit")]
    print(f"saved: {path}  greedy_budget_violations={gv}  milp_budget_violations={mv}  "
          f"time_limit_hits={len(hit)}  failed_scales={len(out['failures'])}")
    if out["failures"]:
        print(f"failures: {out['failures']}")


if __name__ == "__main__":
    main()
