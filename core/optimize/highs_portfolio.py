"""여러 시드로 같은 HiGHS 모델을 동시에 풀고 가장 좋은 해를 고른다(시드 포트폴리오, 2026-10-06).

왜: 실제 같은 10년 이력(`core/ingest/org_profile`)에서는 MILP가 최적 증명에 이르지 못하고, 시간 한도에서 멈춘
해의 품질이 탐색 경로(난수 시드)에 따라 크게 흔들린다. 100명 안 A, 60초: 시드 0~3이 29.4 / 29.8 / 32.8 / 31.0,
같은 문제를 20분 풀어도 33.9(상한 42.6). 시드 4개를 동시에 돌려 최선을 고르면 같은 벽시계 시간에 33.3을 얻는다
(`.omc/plan/2026-10-06-solve-stability.md`).

어떻게:
- PuLP 모델을 한 번 만들고 HiGHS 모델을 숫자 배열로 꺼내(HighsLp는 피클이 안 된다), 호출마다 시드별 spawn
  프로세스를 띄운다. 공유 풀을 쓰지 않는 이유(Opus 리뷰): 동시 요청이 서로 줄을 서고, 풀을 키울 때 남의 작업을
  취소하며, 작업 프로세스가 죽으면 깨진 풀이 남는다. `multiprocessing.Pool`도 안 된다 -- 작업 프로세스가 죽으면
  그 작업을 잃어 결과를 영원히 기다린다(2차 리뷰 MUST). 그래서 시드마다 Process를 직접 띄우고, 결과 없이 죽은
  프로세스는 바로 오류로, 마감(시간 한도 + 여유)을 넘긴 시드는 끝내고 오류로 기록한다. spawn 비용 0.1초 안팎.
  스레드는 안 된다 -- 한 프로세스 안의 HiGHS 인스턴스들이 전역 스케줄러를 나눠 써서 같은 30초에 해가 27.7 → 13.7로
  나빠졌다(측정). fork는 API 서버(스레드 사용) 안에서 위험해 쓰지 않는다.
- PuLP는 최대화를 비용 부호만 뒤집어 넘기므로 HiGHS 쪽은 항상 최소화다(sense_를 설정하지 않음, 리뷰 실측) ->
  native 목적값이 가장 작은 해를 고른다(같으면 낮은 시드).
- 시드 s가 gap 안 최적을 증명하면 s보다 큰 시드만 끝내고 작은 시드는 끝날 때까지 기다린 뒤 s 이하에서 고른다.
  그래서 끝까지 풀리는 문제는 늘 시드 0(포트폴리오 이전과 같은 해)이 뽑힌다 -- 먼저 끝난 시드를 고르면 실행마다
  달라졌다(2차 리뷰 SHOULD, knapsack 25회 중 1회 시드 2). 상한은 끝난 시드들 중 가장 단단한 값이고, 고른 해가 그
  상한 대비 gap(상대·절대) 안이면 최적으로 표시한다.
- 해가 있는지는 모델 상태가 아니라 primal 해 상태로 판단한다(PuLP는 시간 한도면 해가 없어도 IntegerFeasible로 옮긴다).
- 시드별 오류(모델 적재·풀이 실패, 예외, 비정상 종료, 마감 초과)는 `portfolio["errors"]`에 남긴다. 모든 시드가
  오류면 RuntimeError로 이유를 올린다 -- "해 없음"으로 위장되지 않게.

seeds=1이면 PuLP의 HiGHS와 똑같이 동작한다(기본값, 시험 속도와 기존 결과 유지).

멈춤(2026-10-12, `core/optimize/interrupt`): 호출 스레드에 멈춤 신호가 걸려 있으면 풀이 시작 전에 이미 멈춤이면
`SolveCancelled`, 풀이 중이면 HiGHS 중단 콜백으로 멈춘다(시드 1은 이 프로세스의 콜백, 시드 2+는 프로세스 공유 이벤트를
각 시드의 콜백이 본다). finish면 그때까지의 해로 위와 같이 고르고(`self.interrupted`), abort면 시드를 바로 끝내고
`SolveCancelled`. 중단을 받고도 INTERRUPT_GRACE_S 안에 답하지 않는 시드는 끝내고 오류로 기록한다.
"""
from __future__ import annotations

import math
import multiprocessing
import time

import logging

import numpy as np
import pulp
from pulp import constants

from core.optimize.interrupt import SolveCancelled, current as current_stop

log = logging.getLogger(__name__)

INTERRUPT_GRACE_S = 10.0          # finish 멈춤 뒤 시드가 해를 돌려줄 때까지 기다리는 시간

_FEASIBLE = 2                     # HiGHS SolutionStatus kSolutionStatusFeasible
_OPTIONS = ("time_limit", "mip_rel_gap", "mip_abs_gap", "threads", "output_flag")


def _option(h, name: str):
    """highspy 1.15는 (상태, 값)을, 일부 버전은 값만 돌려준다."""
    value = h.getOptionValue(name)
    return value[1] if isinstance(value, tuple) else value


def _export(h, extra_options: dict) -> dict:
    """highspy 모델 → 피클 가능한 배열."""
    lp = h.getLp()
    a = lp.a_matrix_
    return {
        "num_col": int(lp.num_col_), "num_row": int(lp.num_row_),
        "sense": int(lp.sense_), "offset": float(lp.offset_),
        "col_cost": np.asarray(lp.col_cost_, dtype=float), "col_lower": np.asarray(lp.col_lower_, dtype=float),
        "col_upper": np.asarray(lp.col_upper_, dtype=float), "row_lower": np.asarray(lp.row_lower_, dtype=float),
        "row_upper": np.asarray(lp.row_upper_, dtype=float), "a_format": int(a.format_),
        "a_start": np.asarray(a.start_, dtype=np.int32), "a_index": np.asarray(a.index_, dtype=np.int32),
        "a_value": np.asarray(a.value_, dtype=float),
        "integrality": np.asarray([int(x) for x in lp.integrality_], dtype=np.uint8),
        "options": {**{name: _option(h, name) for name in _OPTIONS}, **extra_options},
    }


def _interrupt_callbacks():
    import highspy
    t = highspy.cb.HighsCallbackType
    # MIP 탐색 중·뿌리 LP(심플렉스·내점법) 중 모두 멈출 수 있게 -- 큰 모델은 뿌리 LP만 수십 초 걸린다
    return [t.kCallbackMipInterrupt, t.kCallbackSimplexInterrupt, t.kCallbackIpmInterrupt]


def _interrupt_when_set(callback_type, message, data_out, data_in, flag) -> None:
    """HiGHS 중단 콜백: flag(threading.Event·multiprocessing.Event·SolveStop -- is_set()만 본다)가 켜지면 멈춘다."""
    if flag.is_set():
        data_in.user_interrupt = True


def _solve_exported(model: dict, seed: int, stop_event=None) -> dict:
    """작업 프로세스: 배열로 모델을 다시 만들고 시드만 바꿔 푼다. 실패는 예외 대신 error로 돌려준다.
    stop_event(프로세스 공유 이벤트)가 켜지면 HiGHS가 그때까지의 해를 들고 멈춘다(상태 kInterrupt)."""
    try:
        import highspy
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if stop_event is not None:
            h.setCallback(_interrupt_when_set, stop_event)
            for cb_type in _interrupt_callbacks():
                h.startCallback(cb_type)
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = model["num_col"], model["num_row"]
        lp.sense_ = highspy.ObjSense(model["sense"])
        lp.offset_ = model["offset"]
        lp.col_cost_, lp.col_lower_, lp.col_upper_ = model["col_cost"], model["col_lower"], model["col_upper"]
        lp.row_lower_, lp.row_upper_ = model["row_lower"], model["row_upper"]
        lp.a_matrix_.format_ = highspy.MatrixFormat(model["a_format"])
        lp.a_matrix_.num_col_, lp.a_matrix_.num_row_ = model["num_col"], model["num_row"]
        lp.a_matrix_.start_, lp.a_matrix_.index_ = model["a_start"], model["a_index"]
        lp.a_matrix_.value_ = model["a_value"]
        lp.integrality_ = [highspy.HighsVarType(int(x)) for x in model["integrality"]]
        error = None
        if h.passModel(lp) == highspy.HighsStatus.kError:
            error = "passModel failed"
        for name, value in model["options"].items():
            h.setOptionValue(name, value)
        h.setOptionValue("random_seed", int(seed))
        if error is None and model.get("start") is not None:
            _set_start(h, model["start"])
        if error is None and h.run() == highspy.HighsStatus.kError:
            error = f"run failed ({h.modelStatusToString(h.getModelStatus())})"
        info = h.getInfo()
        feasible = error is None and info.primal_solution_status == _FEASIBLE
        return {
            "seed": seed, "error": error,
            "model_status": int(h.getModelStatus()),
            "feasible": feasible,
            "objective": float(info.objective_function_value) if feasible else None,
            "dual_bound": float(info.mip_dual_bound) if error is None and info.valid else None,
            "col_value": np.asarray(h.getSolution().col_value, dtype=float) if feasible else None,
        }
    except Exception as exc:                            # noqa: BLE001 -- 한 시드의 실패가 다른 시드를 막지 않게
        return {"seed": seed, "error": f"{type(exc).__name__}: {exc}", "model_status": -1, "feasible": False,
                "objective": None, "dual_bound": None, "col_value": None}


def _set_start(h, values) -> None:
    """초기해(2026-10-19, 전일 투입): 열 순서의 값. 가능하지 않은 시작이면 HiGHS가 쓰지 않는다 -- 풀이는 그대로 이어진다."""
    import highspy
    sol = highspy.HighsSolution()
    sol.col_value = [float(v) for v in values]
    sol.value_valid = True
    h.setSolution(sol)


def start_vector(lp, start: dict[str, float]) -> np.ndarray:
    """변수 이름 → 값 사전을 PuLP가 HiGHS에 넘긴 열 순서(var.index)로. 없는 변수는 0."""
    values = np.zeros(len(lp.variables()), dtype=float)
    for var in lp.variables():
        values[var.index] = float(start.get(var.name, 0.0))
    return values


def _seed_worker(seed: int, model_conn, out_conn, stop_event=None) -> None:
    """모델은 시작 인자가 아니라 시작 뒤 전용 파이프로 받는다(아래 _run_seeds 설명). stop_event는 작아서 시작 인자로 넘긴다."""
    try:
        try:
            model = model_conn.recv()
        finally:
            model_conn.close()
        out_conn.send(_solve_exported(model, seed, stop_event))
    except (EOFError, OSError):
        pass                                            # 부모가 사라졌거나 모델을 못 받았다 -- 조용히 끝낸다
    finally:
        out_conn.close()


def _error_run(seed: int, message: str) -> dict:
    return {"seed": seed, "error": message, "model_status": -1, "feasible": False,
            "objective": None, "dual_bound": None, "col_value": None}


def _run_seeds(model: dict, seeds: int, optimal_status: int, deadline_s: float | None,
               stop=None) -> tuple[list[dict], int | None]:
    """시드마다 spawn 프로세스와 전용 파이프. (결과들, 증명한 가장 낮은 시드 또는 None)을 돌려준다.

    전용 파이프인 이유: 공유 큐는 결과를 쓰는 도중에 끝낸 프로세스가 통로를 깨뜨릴 수 있다. 결과 없이 죽은 프로세스는
    파이프 EOF로 바로 드러난다(Pool처럼 영원히 기다리지 않는다).
    모델을 시작 인자로 넘기지 않는 이유(2026-10-06 실측 멈춤): spawn의 start()는 인자를 자식 시작 파이프에 다 쓸 때까지
    그 파이프의 읽기 끝을 부모도 쥐고 있어, 자식이 시작 중에 죽으면 64KB를 넘는 쓰기가 영원히 막힌다(main 가드 없는
    스크립트에서 재현). 그래서 시작 인자는 작게 두고, 모델은 시작 뒤 전용 파이프로 보낸다 -- 부모가 자식 쪽 끝을 닫아 두므로
    자식이 죽으면 보내기가 BrokenPipe로 바로 끝난다."""
    from multiprocessing.connection import wait
    ctx = multiprocessing.get_context("spawn")
    procs, readers = {}, {}
    runs: dict[int, dict] = {}
    proven_at: int | None = None
    shared_stop = ctx.Event() if stop is not None else None   # 멈춤을 시드 프로세스들에 전한다
    grace_end: float | None = None
    try:
        senders = {}
        for s in range(seeds):                          # try 안: 중간 시드가 못 뜨면 앞서 띄운 시드도 정리한다(3차 리뷰)
            model_r, model_w = ctx.Pipe(duplex=False)
            reader, writer = ctx.Pipe(duplex=False)
            readers[s] = reader
            p = ctx.Process(target=_seed_worker, args=(s, model_r, writer, shared_stop), daemon=True)
            p.start()
            model_r.close()                             # 자식 쪽 끝은 닫는다: 자식이 죽으면 보내기가 BrokenPipe로 끝난다
            writer.close()                              # 부모 쪽 쓰기 끝을 닫아야 자식이 죽으면 EOF가 온다
            procs[s], senders[s] = p, model_w
        for s, conn in senders.items():
            try:
                conn.send(model)
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                runs[s] = _error_run(s, f"could not hand the model to the worker ({type(exc).__name__})")
            finally:
                conn.close()
        end = None if deadline_s is None else time.monotonic() + deadline_s
        while True:
            needed = [s for s in procs if s not in runs and (proven_at is None or s < proven_at)]
            if not needed:
                break
            if stop is not None and stop.is_set():
                if stop.aborting:                       # 받을 사람이 없다 -- 바로 끝낸다(finally가 프로세스를 정리)
                    raise SolveCancelled("멈춤(abort) -- 시드 풀이를 끝냈다")
                if grace_end is None:                   # finish: 각 시드가 그때까지의 해를 들고 멈추게 한다
                    shared_stop.set()
                    grace_end = time.monotonic() + INTERRUPT_GRACE_S
                elif time.monotonic() > grace_end:
                    for s in needed:
                        runs[s] = _error_run(s, f"interrupted but no result within {INTERRUPT_GRACE_S:.0f}s")
                    break
            # (모델을 못 넘긴 시드는 이미 runs에 오류로 들어 있다)
            by_conn = {readers[s]: s for s in needed}
            for conn in wait(list(by_conn), timeout=0.2):
                seed = by_conn[conn]
                if proven_at is not None and seed > proven_at:
                    continue                            # 같은 배치에서 이미 필요 없어진 시드
                try:
                    r = conn.recv()
                except (EOFError, OSError):
                    procs[seed].join(timeout=1)
                    r = _error_run(seed, f"worker exited without a result (exitcode {procs[seed].exitcode})")
                runs[seed] = r
                if r["model_status"] == optimal_status and r["feasible"] and (proven_at is None or seed < proven_at):
                    proven_at = seed
                    for s, p in procs.items():          # 더 큰 시드는 필요 없다
                        if s > proven_at and s not in runs and p.is_alive():
                            p.terminate()
            if end is not None and time.monotonic() > end:
                for s in [s for s in needed if proven_at is None or s < proven_at]:
                    if s not in runs:
                        runs[s] = _error_run(s, f"no result within {deadline_s:.0f}s (hung or too slow)")
                break
        return sorted(runs.values(), key=lambda r: r["seed"]), proven_at
    finally:
        for p in procs.values():
            if p.is_alive():
                p.terminate()
        for p in procs.values():
            p.join(timeout=5)
        for reader in readers.values():
            reader.close()


class HighsPortfolio(pulp.HiGHS):
    """`pulp.HiGHS`와 같은 인자 + seeds. 풀이 뒤 `self.portfolio`에 시드별 결과 요약과 상한을 남긴다
    (seeds=1이면 None). 상한·목적은 PuLP 쪽 방향(최대화면 최대화)이고 목적식 상수를 포함한다."""

    def __init__(self, seeds: int = 1, start: dict[str, float] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.seeds = max(1, int(seeds))
        self.start = start                              # 초기해(변수 이름 → 값), 시드마다 같은 시작
        self.portfolio: dict | None = None
        self.interrupted = False                        # 멈춤(finish)으로 끝난 풀이인가 -- milp가 종료 사유를 user_interrupt로

    def actualSolve(self, lp):
        stop = current_stop()
        if stop is not None:
            if stop.is_set():                           # 멈춤 뒤에는 새 풀이를 시작하지 않는다(다음 대안·다음 K)
                raise SolveCancelled("멈춤 요청 뒤라 풀이를 시작하지 않았다")
            # 이 프로세스 안에서 푸는 경로(시드 1·포트폴리오 실패 시 한 번 풀기)가 멈춤을 보게 한다
            self.callbackTuple = (_interrupt_when_set, stop)
            self.callbacksToActivate = _interrupt_callbacks()
        try:
            return self._solve(lp, stop)
        finally:
            self.interrupted = stop is not None and stop.is_set()
            if stop is not None and stop.aborting:
                raise SolveCancelled("멈춤(abort) -- 풀이 결과를 버렸다")

    def callSolver(self, lp):
        # 한 프로세스 경로(시드 1·포트폴리오 실패 시 한 번 풀기): PuLP가 모델을 넘긴 뒤 풀기 직전에 초기해를 싣는다
        if self.start:
            _set_start(lp.solverModel, start_vector(lp, self.start))
        super().callSolver(lp)

    def _solve(self, lp, stop):
        if self.seeds <= 1:
            return super().actualSolve(lp)
        import highspy
        self.createAndConfigureSolver(lp)
        self.buildSolverModel(lp)
        model = _export(lp.solverModel, dict(self.optionsDict))
        if self.start:
            model["start"] = start_vector(lp, self.start)
        lp.solverModel.clearModel()                     # 부모는 배열만 있으면 된다(300명이면 사본 하나가 수백 MB)
        optimal = int(highspy.HighsModelStatus.kOptimal)
        limit = model["options"].get("time_limit")
        deadline = (float(limit) + max(30.0, 0.25 * float(limit))
                    if limit is not None and math.isfinite(float(limit)) and float(limit) < 1e20 else None)
        runs, proven_at = _run_seeds(model, self.seeds, optimal, deadline, stop)
        # HiGHS 쪽은 최소화(PuLP가 최대화를 뒤집어 넘긴다). PuLP 방향 값 = sign * native + 상수
        sign = -1.0 if lp.sense == constants.LpMaximize else 1.0
        const = float(lp.objective.constant) if lp.objective is not None else 0.0
        if proven_at is not None:                       # 증명한 가장 낮은 시드 이하만 본다(해·상한 모두 결정적)
            runs = [r for r in runs if r["seed"] <= proven_at]
        feasible = [r for r in runs if r["feasible"] and r["objective"] is not None and math.isfinite(r["objective"])]
        native_bounds = [r["dual_bound"] for r in runs
                         if r["dual_bound"] is not None and math.isfinite(r["dual_bound"]) and abs(r["dual_bound"]) < 1e20]
        tightest = max(native_bounds) if native_bounds else None          # 최소화의 하한: 클수록 단단하다
        self.portfolio = {
            "seeds": self.seeds,
            "objectives": {str(r["seed"]): (sign * r["objective"] + const if r["feasible"] else None) for r in runs},
            "errors": {str(r["seed"]): r["error"] for r in runs if r.get("error")},
            "best_bound": None if tightest is None else sign * tightest + const,
            "chosen_seed": None,
        }
        if not feasible and runs and all(r.get("error") for r in runs):
            if stop is not None and stop.is_set():
                # 멈춤 뒤 유예 안에 아무 시드도 해를 못 냈다 -- 한 번 더 풀어 되살리지 않는다(Codex 리뷰 2026-10-12)
                raise SolveCancelled("멈춤 뒤 해를 돌려준 시드가 없다")
            # 동시 풀이 자체가 실패했다(작업 프로세스를 못 띄움·모두 죽음). 계획을 못 내는 대신 보통 방식(한 프로세스,
            # 시드 0)으로 한 번 풀고, 실패 사실은 portfolio에 남긴다(증거 options에 실린다) -- 숨기지 않는다.
            log.warning("HiGHS seed portfolio failed for every seed, solving once in-process: %s", self.portfolio["errors"])
            errors = self.portfolio["errors"]
            status = super().actualSolve(lp)
            self.portfolio = {"seeds": self.seeds, "objectives": {}, "errors": errors, "best_bound": None,
                              "chosen_seed": None, "fallback": "single_in_process"}
            return status
        if not feasible:
            infeasible = {int(highspy.HighsModelStatus.kInfeasible), int(highspy.HighsModelStatus.kUnboundedOrInfeasible)}
            if any(r["model_status"] in infeasible for r in runs):
                lp.assignStatus(constants.LpStatusInfeasible, constants.LpSolutionInfeasible)
                return constants.LpStatusInfeasible
            for var in lp.variables():
                var.varValue = None
            lp.assignStatus(constants.LpStatusNotSolved, constants.LpSolutionNoSolutionFound)
            return constants.LpStatusNotSolved
        best_native = min(r["objective"] for r in feasible)
        tol = 1e-9 * max(1.0, abs(best_native))
        best = min((r for r in feasible if r["objective"] <= best_native + tol), key=lambda r: r["seed"])
        self.portfolio["chosen_seed"] = best["seed"]
        col = best["col_value"]
        for var in lp.variables():
            var.varValue = float(col[var.index])
            var.modified = False
        for constraint in lp._constraints.values():
            constraint.modifier = False
        rel_gap = float(model["options"].get("mip_rel_gap") or 0.0)
        abs_gap = float(model["options"].get("mip_abs_gap") or 0.0)
        proven = best["model_status"] == optimal or (
            tightest is not None
            and abs(best_native - tightest) <= max(rel_gap * max(abs(best_native), 1e-9), abs_gap) + 1e-9)
        status, sol_status = ((constants.LpStatusOptimal, constants.LpSolutionOptimal) if proven
                              else (constants.LpStatusOptimal, constants.LpSolutionIntegerFeasible))
        lp.assignStatus(status, sol_status)
        return status
