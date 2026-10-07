"""운영 중 편성·진행 사업 보강·단순 규칙 대비 API(2026-10-06, claude-a 요청 docs/requests/2026-10-06-operating-staffing-ui.md).

계산은 claude-a의 core.evaluate(operating·staffing_sim·baseline)를 그대로 부른다 -- 여기서는 요청 검증·현재 배치·잠금·
데이터셋 버전 확인·직렬화만 한다. 사업 효과는 NOT_CALIBRATED: 화면 문구는 "계산상 개선(같은 평가 기준)".

- GET  /api/operating/state       현재 배치(Dataset.current)·잠금·대기 인력(bench)·신규 제안(proposals)
- POST /api/operating/compare     변경 예산 K=0..3 비교(SSE: start → progress… → row(K마다) → done)
- POST /api/staffing/candidates   보강 후보 순위
- POST /api/staffing/simulate     넣기·빼기 재평가
- POST /api/staffing/best         최선 n명
- POST /api/baseline              단순 규칙(기술 1등 우선) 대비
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset
from api.schemas import EntryIn, MilpParamsIn
from api.sse import sse_event
from core.domain.models import Grade
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine

router = APIRouter()

NOT_CALIBRATED = "계산상 개선(같은 평가 기준) -- 사업 효과는 검증 전(NOT_CALIBRATED)"
# 100명 실측(claude-a rehearsal/results/operating-check.json): K=0..3 각 0.5 / 1.2 / 11 / 28초. 화면 안내용.
EXPECTED_S_100 = {0: 0.5, 1: 1.2, 2: 11.0, 3: 28.0}
PROGRESS_EVERY_S = 2.0
# 운영 경로의 풀이 시간 상한(초, K 하나·호출 하나당). 공개 엔드포인트라 요청 하나가 CPU를 오래 잡지 않게(리뷰 S2).
# 100명 K=3이 약 25초, 300명 실측은 rehearsal/results/operating-check.json -- 그보다 넉넉하게.
OPERATING_TIME_LIMIT_MAX = 600
MAX_ENTRIES = 5000
# 무거운 비교(compare)는 서버 전체에서 한 번에 하나: 슬롯을 계산 스레드의 수명에 묶는다(화면이 끊겨도 스레드가 끝날
# 때까지 슬롯을 쥔다 -- 끊고 다시 보내기로 계산이 쌓이지 않게, 리뷰 S1). 바쁘면 기다리지 않고 429.
_COMPARE_SLOT = threading.Semaphore(1)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    milp_params: MilpParamsIn = MilpParamsIn()
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}


class CompareRequest(_Base):
    ks: list[Annotated[int, Field(ge=0, le=3)]] = Field(default=[0, 1, 2, 3], min_length=1, max_length=4)
    # 시연 묶음의 미리 계산 행을 쓰지 않고 다시 푼다(claude-a 2026-10-07, api/demo_precomputed -- 화면의 "다시 계산")
    fresh: bool = False


class _WithEntries(_Base):
    # 생략하면 현재 배치(Dataset.current). 교체·조정을 적용한 배치를 보낼 수도 있다.
    entries: list[EntryIn] | None = Field(default=None, max_length=MAX_ENTRIES)


class CandidatesRequest(_WithEntries):
    project_id: str
    include_pull: bool = False
    budget_add: int | None = Field(default=None, ge=0)
    top: int = Field(default=10, ge=1, le=50)


class AddIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str
    project_id: str
    alloc: float = Field(gt=0.0, le=1.0)


class SimulateRequest(_WithEntries):
    project_id: str
    adds: list[AddIn] = Field(default=[], max_length=20)
    removes: list[tuple[str, str]] = Field(default=[], max_length=50)
    extra_seats: dict[Grade, Annotated[int, Field(ge=0, le=10)]] = {}
    budget_add: int = Field(default=0, ge=0)


class BestRequest(_WithEntries):
    project_id: str
    n: int = Field(ge=1, le=5)
    grade: Grade | None = None
    pull_budget: int = Field(default=0, ge=0, le=5)
    budget_add: int | None = Field(default=None, ge=0)


class BaselineRequest(_Base):
    entries: list[EntryIn] = Field(max_length=MAX_ENTRIES)


# --- 공통 -------------------------------------------------------------------------------

def _params(req: _Base):
    p = req.milp_params.to_milp_params()
    return p.model_copy(update={"time_limit": min(p.time_limit, OPERATING_TIME_LIMIT_MAX)})


def _matrices(dataset: ActiveDataset, weights):
    eng = ScoringEngine(dataset.graph)
    return eng.skill_matrix(weights), eng.synergy_matrix()


def _current_entries(dataset: ActiveDataset) -> list[AssignEntry]:
    return [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in dataset.current]


def _locked(dataset: ActiveDataset) -> set[tuple[str, str]]:
    return {(c.person_id, c.project_id) for c in dataset.current if c.locked}


def _entries(req: _WithEntries, dataset: ActiveDataset) -> list[AssignEntry]:
    if req.entries is None:
        if not dataset.current:
            raise HTTPException(status_code=422, detail="현재 배치가 없는 데이터다 -- 운영 중 데이터를 고르거나 배치를 보낸다.")
        return _current_entries(dataset)
    _check_ids(dataset, projects=[e.project_id for e in req.entries], people=[e.person_id for e in req.entries])
    return [AssignEntry(**e.model_dump()) for e in req.entries]


def _check_ids(dataset: ActiveDataset, *, projects=(), people=()) -> None:
    g = dataset.graph
    bad = [p for p in projects if p not in g.project_index] + [p for p in people if p not in g.pid_index]
    if bad:
        raise HTTPException(status_code=422, detail=f"이 데이터에 없는 ID: {', '.join(sorted(set(bad))[:5])}")


def _light(request: Request) -> anyio.Semaphore:
    """보강 후보·재평가·최선 n명·단순 규칙(1~2초)은 동시에 둘까지. 비교(compare)와는 슬롯이 따로라 그 뒤에 줄 서지 않는다.
    스레드 계산은 취소되지 않으므로(anyio 기본) 슬롯은 계산이 끝날 때까지 쥔다."""
    sem = getattr(request.app.state, "operating_light", None)
    if sem is None:
        sem = request.app.state.operating_light = anyio.Semaphore(2)
    return sem


def _dump(entries) -> list[dict]:
    return [e.model_dump() if hasattr(e, "model_dump") else e for e in entries]


def _run(fn):
    try:
        return fn()
    except ValueError as exc:                  # 평가기가 거절한 입력(투입률·달 등). ID는 미리 확인한다 -- KeyError는 버그라 숨기지 않는다
        raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc


# --- 운영 중 편성 -------------------------------------------------------------------------

@router.get("/api/operating/state")
def operating_state(dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    g = dataset.graph
    available = bool(dataset.current)
    operating = dataset.scenario.get("scenario") == "operating"
    return {
        "dataset_version": dataset.info.version,
        "available": available,
        "scenario": dataset.scenario.get("scenario"),
        "bench": dataset.scenario.get("bench", []),
        "proposals": dataset.scenario.get("proposals", []),
        "current": [c.model_dump() for c in dataset.current],
        "projects": [{"id": p.id, "name": p.name, "grade_headcount": {k.value: v for k, v in p.grade_headcount.items()},
                      "monthly_budget": p.monthly_budget, "start_month": p.start_month, "end_month": p.end_month}
                     for p in g.projects],
        "expected_s_100": EXPECTED_S_100,
        "n_people": len(g.people),
        "note": NOT_CALIBRATED,
        "hint": (None if available and (operating or dataset.scenario.get("bench")) else
                 "현재 배치가 없는 데이터다. 데이터 탭에서 '운영 중' 시연 데이터를 고르거나 current_assignments.csv가 든 "
                 "묶음을 올린다." if not available else
                 "이 데이터는 운영 중 시나리오가 아니다(대기 인력·신규 제안 표시 없음). 비교는 되지만, 데이터 탭에서 "
                 "'운영 중' 시연 데이터를 고르면 신규 제안 장면을 볼 수 있다."),
    }


@router.post("/api/operating/compare")
async def operating_compare(req: CompareRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset)):
    check_dataset_version(req.dataset_version, dataset)
    if not dataset.current:
        raise HTTPException(status_code=422, detail="현재 배치가 없는 데이터다 -- 운영 중 데이터를 고른다.")
    params = _params(req)
    ks = sorted(set(req.ks))
    # 같은 데이터·같은 설정·같은 K 목록으로 미리 계산해 검증을 통과한 행이 있으면 계산 없이 그것을 흘린다(시연 확장 E,
    # claude-a 2026-10-07). 행마다 precomputed_at(계산 시각)을 실어 화면이 "미리 계산"으로 보이게 한다.
    from api.demo_precomputed import operating_rows
    # 미리 계산은 기본 가중치({})로 계산·검증했다 -- 가중치를 바꾼 요청이면 쓰지 않는다(리뷰 MUST)
    pre = None if (req.fresh or req.weights) else operating_rows(request.app, params, ks, dataset.info.version)
    if pre is not None:
        pre_rows, pre_at = pre

        async def precomputed_stream():
            yield sse_event("start", {"ks": ks, "n_people": len(dataset.graph.people), "precomputed_at": pre_at,
                                      "expected_s_100": {k: EXPECTED_S_100.get(k) for k in ks}, "note": NOT_CALIBRATED})
            for row in pre_rows:
                yield sse_event("row", {**row, "dataset_version": dataset.info.version, "precomputed_at": pre_at})
            yield sse_event("done", {"elapsed_s": 0.0, "count": len(pre_rows), "precomputed_at": pre_at})
        return StreamingResponse(precomputed_stream(), media_type="text/event-stream")
    S, C = await anyio.to_thread.run_sync(lambda: _matrices(dataset, req.weights))
    from core.evaluate.operating import compare_move_budgets      # 슬롯을 잡기 전에(import 실패로 슬롯이 새지 않게)
    if not _COMPARE_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="다른 운영 편성 비교가 계산 중이다. 끝난 뒤 다시 실행한다.")

    out: "queue.Queue" = queue.Queue()

    def work():
        try:
            out.put(("rows", compare_move_budgets(dataset.graph, S, C, params, dataset.current, ks=ks)))
        except Exception as exc:                               # noqa: BLE001 -- SSE error 이벤트로 전한다
            out.put(("error", exc))
        finally:
            _COMPARE_SLOT.release()                             # 계산이 끝나야 슬롯이 풀린다(화면이 끊겨도)

    # 스레드는 응답을 돌려주기 전에 시작한다 -- 슬롯은 언제나 이 스레드가 풀어 준다(스트림을 읽든 말든).
    t0 = time.monotonic()
    try:
        threading.Thread(target=work, daemon=True).start()
    except BaseException:
        _COMPARE_SLOT.release()                                 # 스레드를 못 띄우면 슬롯을 바로 돌려준다
        raise

    async def stream():
        yield sse_event("start", {"ks": ks, "n_people": len(dataset.graph.people),
                                  "expected_s_100": {k: EXPECTED_S_100.get(k) for k in ks}, "note": NOT_CALIBRATED})
        # 끝날 때까지 진행 표시를 보낸다. K마다 넘겨받는 고리(compare_move_budgets(on_row=), claude-a 2026-10-07)가 생겼다 --
        # K별 실시간 송출 전환은 claude-b 몫(docs/work-split.md).
        while True:
            try:
                kind, payload = await anyio.to_thread.run_sync(lambda: out.get(timeout=PROGRESS_EVERY_S))
            except queue.Empty:
                yield sse_event("progress", {"elapsed_s": round(time.monotonic() - t0, 1)})
                continue
            break
        if kind == "error":
            yield sse_event("error", {"message": str(payload)[:300]})
            return
        for row in payload:
            yield sse_event("row", {**row, "dataset_version": dataset.info.version})
        yield sse_event("done", {"elapsed_s": round(time.monotonic() - t0, 1), "count": len(payload)})

    return StreamingResponse(stream(), media_type="text/event-stream")


# --- 진행 사업 보강 -------------------------------------------------------------------------

@router.post("/api/staffing/candidates")
async def staffing_candidates(req: CandidatesRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset)):
    check_dataset_version(req.dataset_version, dataset)
    _check_ids(dataset, projects=[req.project_id])
    entries = _entries(req, dataset)
    params = _params(req)
    S, C = _matrices(dataset, req.weights)
    from core.evaluate.staffing_sim import rank_candidates

    def go():
        return rank_candidates(dataset.graph, S, C, params, entries, req.project_id, include_pull=req.include_pull,
                               budget_add=req.budget_add, top=req.top, locked=_locked(dataset))
    async with _light(request):
        rows = await anyio.to_thread.run_sync(lambda: _run(go))
    for r in rows:                      # 빈자리 감점(점수)을 자리 수로도 -- 화면이 "−1자리"로 보인다
        r["unfilled_seats_delta"] = (int(round(r["delta"]["unfilled"] / params.slack_penalty))
                                     if params.slack_penalty else 0)
    return {"dataset_version": dataset.info.version, "project_id": req.project_id, "candidates": rows,
            "note": NOT_CALIBRATED}


@router.post("/api/staffing/simulate")
async def staffing_simulate(req: SimulateRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset)):
    check_dataset_version(req.dataset_version, dataset)
    _check_ids(dataset, projects=[req.project_id] + [a.project_id for a in req.adds] + [r[1] for r in req.removes],
               people=[a.person_id for a in req.adds] + [r[0] for r in req.removes])
    entries = _entries(req, dataset)
    locked = _locked(dataset)
    hit = [f"{p}@{j}" for p, j in req.removes if (p, j) in locked]
    if hit:
        raise HTTPException(status_code=422, detail=f"잠긴 배치는 뺄 수 없다: {', '.join(hit)}")
    params = _params(req)
    S, C = _matrices(dataset, req.weights)
    from core.evaluate.staffing_sim import graph_with_extra_seats, simulate

    def go():
        g2 = graph_with_extra_seats(dataset.graph, req.project_id, dict(req.extra_seats), req.budget_add)
        return simulate(dataset.graph, S, C, params, entries,
                        adds=[AssignEntry(**a.model_dump()) for a in req.adds],
                        removes=[tuple(r) for r in req.removes], graph_after=g2)
    async with _light(request):
        res = await anyio.to_thread.run_sync(lambda: _run(go))
    return {**res, "entries": _dump(res["entries"]), "new_violations": [list(v) for v in res["new_violations"]],
            "dataset_version": dataset.info.version, "note": NOT_CALIBRATED}


@router.post("/api/staffing/best")
async def staffing_best(req: BestRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset)):
    check_dataset_version(req.dataset_version, dataset)
    _check_ids(dataset, projects=[req.project_id])
    entries = _entries(req, dataset)
    params = _params(req)
    S, C = _matrices(dataset, req.weights)
    from core.evaluate.staffing_sim import best_additions

    def go():
        return best_additions(dataset.graph, S, C, params, entries, req.project_id, req.n, grade=req.grade,
                              pull_budget=req.pull_budget, budget_add=req.budget_add, locked=_locked(dataset))
    async with _light(request):
        res = await anyio.to_thread.run_sync(lambda: _run(go))
    if res.get("entries") is not None:
        res = {**res, "entries": _dump(res["entries"])}
    return {**res, "dataset_version": dataset.info.version, "note": NOT_CALIBRATED}


# --- 단순 규칙 대비 -------------------------------------------------------------------------

@router.post("/api/baseline")
async def baseline(req: BaselineRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset)):
    check_dataset_version(req.dataset_version, dataset)
    _check_ids(dataset, projects=[e.project_id for e in req.entries], people=[e.person_id for e in req.entries])
    params = _params(req)
    S, C = _matrices(dataset, req.weights)
    entries = [AssignEntry(**e.model_dump()) for e in req.entries]
    from core.evaluate.baseline import compare_with_baseline
    async with _light(request):
        res = await anyio.to_thread.run_sync(lambda: _run(lambda: compare_with_baseline(dataset.graph, S, C, params,
                                                                                         entries)))
    return {**res, "dataset_version": dataset.info.version, "note": NOT_CALIBRATED}
