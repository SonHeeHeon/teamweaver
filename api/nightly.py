"""야간 배치(2026-10-18, claude-b -- 사용자 요청 "관리자가 퇴근한 시점인 매일 밤(새벽 00시부터 05시까지) 배치 작업으로
돌만한게 있을까?" → "1,2,3번 좋네. 진행해봐줘"). 계획: `.omc/plan/2026-10-18-nightly-batch.md`(로컬).

대상은 **지금 켜져 있는 데이터 한 묶음**(관리자가 올렸거나 고른 시연 묶음)이다. 다른 묶음·옛 데이터는 다루지 않는다.

(2026-10-18 재정비, 사용자 결정 "축소해서 이번 작업에 합침" -- 3가지 핵심 상황 설계 `.omc/plan/2026-10-18-three-scenarios.md`):
운영 중에는 "매일 더 나은 배치 찾기"가 아니라 **입력이 바뀐 것만** 미리 해 둔다. 시스템이 스스로 제안을 띄우지 않고, **AI 비용을 쓰지 않는다**
(소명 글은 부서장이 팀을 확정해 검토로 넘길 때 화면에서 쓴다 -- 쓴 글은 api.justification_cache가 저장해 다시 돈 내지 않는다).
고정(자물쇠)은 현재 배치의 `locked`라 모든 계산이 자동으로 지킨다.

1. 긴급 대기 명단 -- 진행 사업마다 "지금 바로 넣을 수 있는 사람" 상위 5명(`standby.json`, `GET /api/emergency/standby`).
2. 밤 풀이 -- 아침에 바로 나오지 않는 요청을 낮보다 긴 시간 한도로 풀어 둔다. **시간 한도만** 늘리고 키·서명은 낮 설정 그대로.
   - 연간 계획(안 A~D): 현재 배치가 없는 백지 데이터일 때만.
   - 운영 중 비교(K 0~5, 2026-10-20): 채울 신규 사업이 있을 때만.
   - 낮에 실제로 보낸 요청(`RequestRecorder` 기록 중 캐시에 없는 것).
   결과는 `<데이터 폴더>/nightly/prepared.json` → 결과 캐시·"미리 계산" 등록부. 이미 캐시에 있는 요청(시연 미리 계산 포함)은 건너뛴다.
   넣기 전·재기동 때 데이터 버전·평가기 재채점·위반 없음을 다시 확인한다. 끝까지 풀지 못한 묶음은 저장하지 않는다.
3. 밤사이 점검 -- 데이터 복원·미리 계산 수용·리뷰 판정 동봉·저장된 교체·모델(Phase 0 소형)·PDF 브라우저·AI 연결(비용 없는 조회)·
   저장 공간 → `<데이터 폴더>/nightly/report.json`(+ history/), `GET /api/nightly`.

실행: 서버 안의 예약 실행기가 매일 시간대 시작(기본 00:00)에 돈다(`TEAMWEAVER_NIGHTLY=off`로 끔, 시간대
`TEAMWEAVER_NIGHTLY_WINDOW=00:00-05:00`). 풀이는 시간대 끝 15분 전까지(멈춤 finish -- 그때까지의 해), 전체는 시간대 안에서 끝낸다.
서버가 시간대 **안에서** 켜지면 그날 밤은 돌지 않는다(다음 시작까지 기다림) -- 새벽에 시험 서버마다 배치가 도는 사고를 막는다.
관리자는 `POST /api/nightly/run`(지금 한 번)·`/stop`으로 다룬다. 새 풀이 자리는 하나만 쓴다(`TEAMWEAVER_OPTIMIZE_SLOTS` 기본 2 --
밤에 들어온 사람은 남은 자리로 계산한다). 운영 비교는 기존 단일 자리를 쓴다(그동안 화면의 운영 비교는 429).

모든 데이터는 가상이고 사업 효과는 NOT_CALIBRATED -- 보고서의 숫자는 계산상 값이다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

from api.storage import atomic_write, data_dir, ensure_private_dir
from api.settings import horizon_months_of

log = logging.getLogger(__name__)

ENABLE_ENV = "TEAMWEAVER_NIGHTLY"
WINDOW_ENV = "TEAMWEAVER_NIGHTLY_WINDOW"
DEFAULT_WINDOW = "00:00-05:00"
FORMAT = 1
DIRNAME = "nightly"

# 밤 시간 한도: 낮 한도 × 배수, 최소·최대 사이. 남은 시간에 다 못 들어가면 줄이고, 낮 한도보다 짧아지면 풀지 않는다.
NIGHT_FACTOR = 10
OPERATING_FACTOR = 5
NIGHT_MIN_S = 300
NIGHT_MAX_S = 1800
SOLVE_MARGIN_S = 15 * 60          # 풀이는 마감 15분 전까지(소명·점검·보고서 몫)
SOLVE_OVERHEAD_S = 20             # 풀이마다 붙는 준비·보정·검증 시간(모델 만들기·C1·독립 검증) -- 시간 한도 밖이다
MAX_RECORDED = 30                 # 낮 요청 기록 수
MAX_RECORDED_SOLVES = 5           # 한 밤에 다시 풀 낮 요청 수
RECORD_BODY_MAX = 64 * 1024
MAX_PREPARED = 12                 # prepared.json에 남기는 풀이 수(지금 데이터 버전만)
HISTORY_KEEP = 14
MIN_RUN_S = 60                    # 남은 시간이 이보다 짧으면 시작하지 않는다(잠자기에서 깨어난 아침 등)
DISK_MIN_FREE = 1 << 30
HR_SYNC_MAX_S = 3600              # 인사 시스템 받기(받기·검증·새 평가 글 판정) 상한 -- 마감까지 남은 시간이 더 짧으면 그만큼


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "on").strip().lower() not in ("off", "0", "false", "no")


def parse_window(raw: str | None) -> tuple[dtime, dtime]:
    """"HH:MM-HH:MM"(같은 날, 시작 < 끝). 틀리면 ValueError."""
    text = (raw or DEFAULT_WINDOW).strip()
    try:
        a, b = (s.strip() for s in text.split("-"))
        start, end = dtime.fromisoformat(a), dtime.fromisoformat(b)
    except ValueError as exc:
        raise ValueError(f"시간대 형식이 아니다(HH:MM-HH:MM): {text!r}") from exc
    if not start < end:
        raise ValueError(f"시간대는 같은 날 안에서 시작 < 끝이어야 한다: {text!r}")
    return start, end


def window() -> tuple[dtime, dtime]:
    try:
        return parse_window(os.environ.get(WINDOW_ENV))
    except ValueError as exc:
        log.warning("%s -- 기본 시간대 %s를 쓴다", exc, DEFAULT_WINDOW)
        return parse_window(DEFAULT_WINDOW)


def next_start(now: datetime, win: tuple[dtime, dtime]) -> datetime:
    """now 이후(같은 시각 제외) 가장 가까운 시간대 시작. 시간대 안에서 켜졌으면 다음 날 시작이다."""
    today = datetime.combine(now.date(), win[0])
    return today if now < today else today + timedelta(days=1)


def night_limit(day_limit: int, n_solves: int, remaining_s: float, factor: int = NIGHT_FACTOR) -> int | None:
    """밤 한도(초). 낮 한도보다 짧아지면 None(밤에 풀 의미가 없다)."""
    want = min(max(int(day_limit) * factor, NIGHT_MIN_S), NIGHT_MAX_S)
    # 남은 시간의 90%만 나눈다 -- 풀이마다 준비·검증 시간이 붙어, 꽉 채우면 마지막 풀이가 마감 멈춤에 걸린다(10-18 스모크:
    # 운영 비교 K 4개 × 75초가 마감 직전에 멈춰 저장되지 않았다)
    # 풀이마다 준비·검증 시간(SOLVE_OVERHEAD_S)도 뺀다(Opus 대체 리뷰 NIT: 짧은 수동 실행에서 마지막 대안이 마감에 걸려 묶음이 버려진다)
    n = max(1, n_solves)
    fit = int((0.9 * remaining_s - SOLVE_OVERHEAD_S * n) / n)
    lim = min(want, fit)
    return lim if lim >= int(day_limit) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-6) -> bool:
    return abs(a - b) <= max(abs_, rel * max(1.0, abs(a), abs(b)))


# --- 저장소 -------------------------------------------------------------------------------------------------------

class NightlyStore:
    def __init__(self, root: Path):
        self.dir = root
        self._lock = threading.Lock()

    def read(self, name: str):
        try:
            return json.loads((self.dir / name).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def write(self, name: str, data) -> None:
        with self._lock:
            path = self.dir / name
            ensure_private_dir(path.parent)
            atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1, default=str).encode("utf-8"))

    def write_report(self, report: dict) -> None:
        self.write("report.json", report)
        stamp = (report.get("started_at") or _now_iso()).replace(":", "").replace("-", "")[:13]
        self.write(f"history/{stamp}.json", report)
        files = sorted((self.dir / "history").glob("*.json"))
        for old in files[:max(0, len(files) - HISTORY_KEEP)]:
            old.unlink(missing_ok=True)


# --- 낮 요청 기록 ---------------------------------------------------------------------------------------------------

WATCHED = {"/api/optimize": "optimize", "/api/operating/compare": "operating"}


class RequestRecorder:
    """`/api/optimize`·`/api/operating/compare` 요청 본문(가중치·설정·K -- 개인정보 없음)을 최근 30건만 남긴다.
    순수 ASGI: 받은 본문 조각을 그대로 흘려보내며 복사만 한다 -- 응답 스트림·끊김 감지(CancellableStreamResponse)는
    건드리지 않는다(BaseHTTPMiddleware는 스트리밍·끊김을 흔들 수 있어 쓰지 않는다). 기록이 실패해도 요청은 그대로 간다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        path, root = scope.get("path", ""), scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):] or "/"
        kind = WATCHED.get(path)
        if kind is None:
            return await self.app(scope, receive, send)
        chunks: list[bytes] = []
        state = {"size": 0, "done": False, "recorded": False}

        async def wrapped_receive():
            msg = await receive()
            if not state["done"] and msg.get("type") == "http.request":
                body = msg.get("body", b"") or b""
                state["size"] += len(body)
                if state["size"] <= RECORD_BODY_MAX:
                    chunks.append(body)
                if not msg.get("more_body", False):
                    state["done"] = True
            return msg

        async def wrapped_send(msg):
            # 서버가 받아들인 요청(200)만 남긴다 -- 401·409·422·429 본문은 기록하지 않는다(Opus 대체 리뷰 2026-10-18).
            # 파일 쓰기는 이벤트 루프 밖 스레드에서 한다.
            if (msg.get("type") == "http.response.start" and msg.get("status") == 200 and state["done"]
                    and not state["recorded"] and state["size"] <= RECORD_BODY_MAX):
                state["recorded"] = True
                nightly = getattr(getattr(scope.get("app"), "state", None), "nightly", None)
                if nightly is not None:
                    try:
                        threading.Thread(target=nightly.remember_safe, args=(kind, b"".join(chunks)), daemon=True).start()
                    except RuntimeError:                # 스레드를 못 띄우면 기록만 포기한다 -- 응답은 그대로
                        log.debug("요청 기록 스레드를 띄우지 못했다", exc_info=True)
            await send(msg)

        return await self.app(scope, wrapped_receive, wrapped_send)


# --- 실행기 ---------------------------------------------------------------------------------------------------------

class Nightly:
    def __init__(self, app, root: Path | None = None):
        self.app = app
        self.store = NightlyStore(root if root is not None else data_dir() / DIRNAME)
        self._lock = threading.Lock()
        self._req_lock = threading.Lock()
        self._run_thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._stop_mode: str | None = None
        self._current_stop = None
        self._sched_thread: threading.Thread | None = None
        self._sched_stop = threading.Event()
        self.progress: dict | None = None
        self.next_run_at: datetime | None = None
        self.installed: list[dict] = []
        self.loop: asyncio.AbstractEventLoop | None = None    # 서버 실행 루프(attach) -- 밤 스레드가 인사 시스템 받기를 맡긴다

    # 낮 요청 기록 ----------------------------------------------------------------------------------------------
    def remember(self, kind: str, raw: bytes) -> None:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(body, dict):
            return
        body = {k: v for k, v in body.items() if k != "fresh"}
        canon = json.dumps({"kind": kind, "body": body}, sort_keys=True, ensure_ascii=False)
        with self._req_lock:
            items = [r for r in (self.store.read("requests.json") or []) if isinstance(r, dict)]
            items = [r for r in items if json.dumps({"kind": r.get("kind"), "body": r.get("body")}, sort_keys=True,
                                                    ensure_ascii=False) != canon]
            items.append({"kind": kind, "body": body, "at": _now_iso()})
            self.store.write("requests.json", items[-MAX_RECORDED:])

    def remember_safe(self, kind: str, raw: bytes) -> None:
        try:
            self.remember(kind, raw)
        except Exception:                               # noqa: BLE001 -- 기록 실패가 요청을 막지 않는다
            log.debug("요청 기록 실패", exc_info=True)

    def recorded(self) -> list[dict]:
        return [r for r in (self.store.read("requests.json") or []) if isinstance(r, dict) and isinstance(r.get("body"), dict)]

    # 예약 ----------------------------------------------------------------------------------------------------------
    def start_scheduler(self) -> None:
        if self._sched_thread is not None:
            return
        self._sched_stop.clear()
        self._sched_thread = threading.Thread(target=self._scheduler, name="nightly-scheduler", daemon=True)
        self._sched_thread.start()

    def _scheduler(self) -> None:
        while not self._sched_stop.is_set():
            win = window()
            nxt = next_start(datetime.now(), win)
            self.next_run_at = nxt
            while True:                                 # 시계가 바뀌거나 잠들었다 깨도 다시 잰다(최대 10분 간격)
                remaining = (nxt - datetime.now()).total_seconds()
                if remaining <= 0:
                    break
                if self._sched_stop.wait(min(remaining, 600)):
                    return
            end = datetime.combine(nxt.date(), win[1])
            started = self.start_run("schedule", end.timestamp())
            if not started:
                log.info("야간 배치를 시작하지 않았다(이미 실행 중이거나 시간대가 지났다)")
            if self._sched_stop.wait(1.0):              # 같은 시작 시각을 두 번 잡지 않게
                return

    # 실행 ----------------------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        t = self._run_thread
        return t is not None and t.is_alive()

    def start_run(self, trigger: str, deadline_ts: float) -> bool:
        with self._lock:
            if self.running or deadline_ts - time.time() < MIN_RUN_S:
                return False
            self._cancel.clear()
            self._stop_mode = None
            self.progress = {"phase": "시작", "trigger": trigger, "started_at": _now_iso(),
                             "deadline": datetime.fromtimestamp(deadline_ts, timezone.utc).isoformat(timespec="seconds")}
            self._run_thread = threading.Thread(target=self._run_safe, args=(trigger, deadline_ts),
                                                name="nightly-run", daemon=True)
            self._run_thread.start()
            return True

    def request_stop(self, mode: str = "finish") -> bool:
        with self._lock:
            if not self.running:
                return False
            if self._stop_mode != "abort":
                self._stop_mode = mode
            self._cancel.set()
            stop = self._current_stop
        if stop is not None:
            stop.request(mode)
        return True

    def new_stop(self):
        """풀이 하나의 멈춤 신호. 이미 멈춤 요청이 왔으면 그 상태를 물려받는다 -- 자리 대기·행렬 계산 중에 온 멈춤이
        사라지지 않게(Codex 리뷰 2026-10-18: 준비 중 /stop·종료 뒤에도 새 풀이가 멈춤 없이 시작됐다). 등록과 멈춤 요청은 같은 잠금."""
        from core.optimize.interrupt import SolveStop
        with self._lock:
            stop = SolveStop()
            if self._cancel.is_set():
                stop.request(self._stop_mode or "finish")
            self._current_stop = stop
            return stop

    def clear_stop(self) -> None:
        with self._lock:
            self._current_stop = None

    def shutdown(self, timeout: float = 10.0) -> None:
        self._sched_stop.set()
        self.request_stop("abort")
        for t in (self._run_thread, self._sched_thread):
            if t is not None and t.is_alive():
                t.join(timeout)

    def status(self) -> dict:
        win = window()
        return {"enabled": enabled(), "window": f"{win[0].strftime('%H:%M')}-{win[1].strftime('%H:%M')}",
                "next_run_at": self.next_run_at.isoformat(timespec="minutes") if (enabled() and self.next_run_at) else None,
                "running": self.running, "progress": self.progress if self.running else None,
                "installed": self.installed, "last": self.store.read("report.json")}

    def _run_safe(self, trigger: str, deadline_ts: float) -> None:
        try:
            self._run(trigger, deadline_ts)
        except Exception:                               # noqa: BLE001 -- 실행 스레드가 조용히 죽지 않게 남긴다
            log.exception("야간 배치가 예기치 않게 끝났다")

    def _hr_sync(self, deadline_ts: float) -> dict | None:
        """인사 시스템 받기(API 모드일 때만, 2026-10-19 claude-a) -- 밤 작업 맨 앞. 바뀌었으면 새 판으로 켠 뒤 그 데이터로 나머지를 한다.
        켜기는 업로드와 같은 비동기 경로(api.hr_source.sync → api.workspace.commit)라 서버 실행 루프에 맡기고 끝날 때까지 기다린다."""
        from api import hr_source
        cfg, err = hr_source.config()
        if cfg is None and err is None:
            return None
        check = {"id": "hr_sync", "title": "인사 시스템 받기", "status": "fail", "detail": ""}
        loop = self.loop
        if loop is None or loop.is_closed():
            check["detail"] = "서버 실행 루프를 찾지 못해 받지 않았다"
            return check
        self.progress = {**(self.progress or {}), "phase": "인사 시스템 받기"}
        fut = asyncio.run_coroutine_threadsafe(hr_source.sync(self.app, trigger="nightly"), loop)   # 기다림만 끊고 취소는 안 한다
        give_up = time.time() + max(1.0, min(HR_SYNC_MAX_S, deadline_ts - time.time()))
        later = " -- 받기는 계속되고 결과는 설정 화면의 '마지막 받기'에 남는다"
        try:
            while True:
                try:
                    r = fut.result(timeout=1.0)
                    break
                except TimeoutError:
                    # 취소하지 않는다 -- 켜기가 시작됐으면 끝까지 가야 저장본·이력이 어긋나지 않는다(Codex 리뷰 2026-10-19).
                    # 멈춤 요청·시간 초과면 기다리기만 그만둔다(1초마다 확인 -- 멈추기가 최대 1시간 막히지 않게, Codex 재리뷰)
                    if self._cancel.is_set():
                        check.update(status="warn", detail="멈춤 요청으로 기다리지 않았다" + later)
                        return check
                    if time.time() >= give_up:
                        check.update(status="warn", detail="시간 안에 끝나지 않아 기다리지 않고 지금 데이터로 진행했다" + later)
                        return check
        except hr_source.SyncBusy:
            check.update(status="warn", detail="이미 받는 중이라 이번 밤에는 건너뛰었다")
            return check
        except Exception as exc:                        # noqa: BLE001 -- 받기가 깨져도 밤 점검은 지금 데이터로 한다
            log.exception("인사 시스템 받기 실패")
            check["detail"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            return check
        labels = {"applied": "새 판으로 켰다", "unchanged": "원천이 그대로다"}
        ok = r["status"] in labels
        check.update(status="ok" if ok else "fail",
                     detail=(f"{labels[r['status']]} -- {r.get('host')} · 바뀐 표 {len(r.get('changed') or [])}개"
                             if ok else f"지금 데이터 그대로 -- {r.get('detail')}"))
        return check

    def _run(self, trigger: str, deadline_ts: float) -> None:
        app = self.app
        hr_check = self._hr_sync(deadline_ts)
        dataset = app.state.dataset
        t0 = time.time()
        total = max(0.0, deadline_ts - t0)
        solve_until = deadline_ts - min(SOLVE_MARGIN_S, 0.25 * total)
        info = dataset.info
        report: dict = {"format": FORMAT, "trigger": trigger, "started_at": _now_iso(),
                        "deadline": datetime.fromtimestamp(deadline_ts, timezone.utc).isoformat(timespec="seconds"),
                        "dataset": {"id": info.dataset_id, "version": info.version, "source": info.source,
                                    "synthetic": info.synthetic, "people": info.people, "projects": info.projects},
                        "checks": [hr_check] if hr_check else [], "solves": [], "standby": None, "stopped": False}
        acquired = False
        try:
            dataset.acquire()
            acquired = True
            _prune_justifications(app.state.dataset.info.version)   # 옛 데이터의 소명 글은 남기지 않는다(지금 켜진 데이터 기준)
            run = _Run(self, dataset, report, solve_until, deadline_ts)
            run.phase("데이터 확인", run.check_data)
            run.phase("미리 계산 확인", run.check_precomputed)
            run.phase("리뷰 판정 동봉 확인", run.check_judgments)
            run.phase("저장된 교체 확인", run.check_plan_edits)
            run.phase("저장 공간 확인", run.check_disk)
            run.phase("모델 검사", run.check_model)
            run.phase("PDF 준비 확인", run.check_pdf)
            run.phase("긴급 대기 명단", run.standby)
            run.phase("연간 계획 풀이", run.solve_default)
            run.phase("운영 중 비교 풀이", run.solve_operating)
            run.phase("낮 요청 다시 풀기", run.solve_recorded)
            run.phase("AI 연결 확인", run.check_ai)
        except Exception as exc:                        # noqa: BLE001 -- 시작하지 못해도 보고서는 남긴다
            log.exception("야간 배치를 시작하지 못했다")
            report["checks"].append({"id": "start", "title": "시작", "status": "fail",
                                     "detail": f"{type(exc).__name__}: {str(exc)[:200]}"})
        finally:
            if acquired:
                dataset.release()
            self.clear_stop()
            report["stopped"] = self._cancel.is_set()
            report["finished_at"] = _now_iso()
            report["elapsed_s"] = round(time.time() - t0, 1)
            _summarize(report)
            try:
                self.store.write_report(report)
            except OSError:
                log.exception("야간 배치 보고서를 쓰지 못했다")
            log.info("야간 배치 끝: %s", report.get("summary"))

    # 밤 결과 넣기 ------------------------------------------------------------------------------------------------
    def install_prepared(self) -> list[dict]:
        """prepared.json의 밤 결과 중 지금 데이터에 맞고 검증을 통과한 것을 캐시에 넣는다. 부팅 때·밤 실행 뒤에 부른다."""
        app = self.app
        dataset = app.state.dataset
        data = self.store.read("prepared.json")
        out: list[dict] = []
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            self.installed = out
            return out
        if data.get("dataset_version") != dataset.info.version:
            self.installed = [{"status": "skipped", "reason": "다른 데이터의 밤 결과다"}]
            return self.installed
        for entry in data.get("entries", []):
            try:
                out.append(_install_entry(app, dataset, entry))
            except (KeyError, TypeError, ValueError) as exc:
                out.append({"kind": entry.get("kind"), "status": "skipped", "reason": f"기록 모양이 맞지 않다({type(exc).__name__})"})
        self.installed = out
        return out

    def _save_prepared(self, version: str, entry: dict) -> None:
        data = self.store.read("prepared.json")
        if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("dataset_version") != version:
            data = {"format": FORMAT, "dataset_version": version, "entries": []}
        entries = [e for e in data.get("entries", []) if e.get("key") != entry["key"]] + [entry]
        data["entries"] = entries[-MAX_PREPARED:]
        data["updated_at"] = _now_iso()
        self.store.write("prepared.json", data)


# --- 넣기·검증(부팅·밤 공용) ----------------------------------------------------------------------------------------

def _matrices(graph, weights: dict):
    from core.scoring.engine import ScoringEngine
    eng = ScoringEngine(graph)
    return eng.skill_matrix(weights or {}), eng.synergy_matrix()


def _verify_plans(graph, weights: dict, params, raw_plans: list[dict]):
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import PlanAssignment
    S, C = _matrices(graph, weights)
    plans = []
    for raw in raw_plans:
        plan = PlanAssignment.model_validate({k: raw[k] for k in PlanAssignment.model_fields if k in raw})
        ev = evaluate_plan(graph, S, C, params, plan.entries)
        if ev.violations or not _close(ev.objective.total, float(raw["eval_objective"])):
            return None, f"안 {plan.label}: 지금 평가기로 다시 채점하면 맞지 않는다(위반 {len(ev.violations)}건)"
        plans.append(plan)
    return (plans, None) if plans else (None, "안이 없다")


def _verify_rows(graph, params, rows: list[dict]) -> str | None:
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    S, C = _matrices(graph, {})
    for row in rows:
        if not row.get("accepted"):
            continue
        if not row.get("entries"):
            return f"K={row.get('k')}: 배치 기록이 없다"
        ev = evaluate_plan(graph, S, C, params, [AssignEntry(**e) for e in row["entries"]])
        if (sorted(v.code for v in ev.violations) != sorted(row.get("violations", []))
                or not _close(ev.objective.total, row["objective"], abs_=1e-3)):
            return f"K={row.get('k')}: 지금 평가기로 다시 채점하면 맞지 않는다"
    return None


def _server_params(app, dataset):
    return app.state.settings_store.current().settings.to_milp_params(n_people=len(dataset.graph.people),
                                                                         months=horizon_months_of(dataset.graph))


def _operating_params(params):
    from api.routes.operating import OPERATING_TIME_LIMIT_MAX
    return params.model_copy(update={"time_limit": min(params.time_limit, OPERATING_TIME_LIMIT_MAX)})


DEFAULT_KS = (0, 1, 2, 3, 4, 5)     # = api.routes.operating.OPERATING_KS(화면 기본과 같은 목록, 2026-10-20 K 최대 5 -- 시험이 맞춘다)
FAILED_STOPS = frozenset({"validation_rejected", "solver_failed"})
# 끝까지 풀지 못한 묶음(대안이 시간 안에 해를 못 찾음 포함) -- 낮의 cacheable()과 같은 기준. 저장하지 않고 다음 밤에 다시 푼다.
INCOMPLETE_STOPS = FAILED_STOPS | {"time_limit"}


def _install_entry(app, dataset, entry: dict) -> dict:
    from api.cache import ResultCache
    from api.demo_precomputed import operating_key
    from core.optimize.milp import MilpParams
    version = dataset.info.version
    params = MilpParams(**entry["milp_params"])
    kind = entry.get("kind")
    label = entry.get("target")
    if app.state.dataset is not dataset:
        return {"kind": kind, "target": label, "status": "skipped", "reason": "데이터가 바뀌었다"}
    if kind == "optimize":
        weights = dict(entry.get("weights") or {})
        key = ResultCache.key(weights, params, int(entry["n_alternatives"]), version)
        if key != entry.get("key"):
            return {"kind": kind, "target": label, "status": "skipped", "reason": "키가 맞지 않는다"}
        if (entry.get("stop_reason") in INCOMPLETE_STOPS
                or any(p.get("termination") == "user_interrupt" for p in entry.get("plans", []))):
            return {"kind": kind, "target": label, "status": "skipped", "reason": "끝까지 풀지 못한 묶음이다"}
        if app.state.cache.get(key) is not None:
            return {"kind": kind, "target": label, "status": "skipped",
                    "reason": "이미 바로 나오는 결과가 있다(시연 미리 계산·이전 결과)"}
        plans, bad = _verify_plans(dataset.graph, weights, params, entry.get("plans", []))
        if bad:
            return {"kind": kind, "target": label, "status": "skipped", "reason": bad}
        if app.state.dataset is not dataset:                    # 재채점(수 초) 중 데이터가 바뀌었다
            return {"kind": kind, "target": label, "status": "skipped", "reason": "데이터가 바뀌었다"}
        app.state.cache.put(key, plans)
        marks = getattr(app.state, "precomputed_keys", None)
        if marks is None:
            marks = app.state.precomputed_keys = {}
        marks[key] = {"at": entry.get("computed_at"), "stop_reason": entry.get("stop_reason"), "source": "nightly"}
        return {"kind": kind, "target": label, "status": "installed", "plans": len(plans), "key": key[:12]}
    if kind == "operating":
        existing = getattr(app.state, "precomputed_operating", None)
        if existing and existing.get("source") != "nightly" and dataset.info.source == "record":
            # 편성 기록에서 연 데이터: 기록에 담긴 K 결과(자리는 하나)를 밤 결과로 바꾸지 않는다 -- 기록 설정이 서버 설정과 달라
            # 키 검사를 통과해도 화면은 기록 설정으로 묻는다(claude-a 2026-10-19, 폴백 리뷰 P2: 기록 열기·부팅 때 덮였다)
            return {"kind": kind, "status": "skipped", "reason": "편성 기록의 결과를 덮지 않는다"}
        ks = [int(k) for k in entry["ks"]]
        key = operating_key(params, ks, version)
        if key != entry.get("key"):
            return {"kind": kind, "status": "skipped", "reason": "키가 맞지 않는다"}
        if existing and existing.get("key") == key and existing.get("source") != "nightly":
            return {"kind": kind, "status": "skipped", "reason": "이미 시연 미리 계산이 있다"}
        if existing and existing.get("source") != "nightly":
            default_key = operating_key(_operating_params(_server_params(app, dataset)), list(DEFAULT_KS), version)
            if existing.get("key") == default_key:      # 기본 요청을 덮는 시연 미리 계산은 그대로 둔다
                return {"kind": kind, "status": "skipped", "reason": "기본 요청의 시연 미리 계산을 덮지 않는다"}
        rows = entry.get("rows", [])
        if (sorted(int(r.get("k")) for r in rows) != sorted(ks)
                or not all(r.get("accepted") and not r.get("solve_failed") and r.get("termination") != "user_interrupt"
                           for r in rows)):
            return {"kind": kind, "status": "skipped", "reason": "K 행이 모자라거나 풀지 못한 K가 있다"}
        bad = _verify_rows(dataset.graph, params, rows)
        if bad:
            return {"kind": kind, "status": "skipped", "reason": bad}
        if app.state.dataset is not dataset:                    # 재채점 중 다른 묶음으로 바뀌었다 -- 새 묶음의 미리 계산을 덮지 않는다
            return {"kind": kind, "status": "skipped", "reason": "데이터가 바뀌었다"}
        app.state.precomputed_operating = {"key": key, "ks": ks, "rows": entry["rows"],
                                           "computed_at": entry.get("computed_at"), "source": "nightly"}
        return {"kind": kind, "status": "installed", "rows": len(entry["rows"]), "key": key[:12]}
    return {"kind": kind, "status": "skipped", "reason": "알 수 없는 종류"}


# --- 한 번의 실행 ---------------------------------------------------------------------------------------------------

class _Run:
    def __init__(self, nightly: Nightly, dataset, report: dict, solve_until: float, deadline: float):
        self.n = nightly
        self.app = nightly.app
        self.dataset = dataset
        self.report = report
        self.solve_until = solve_until
        self.deadline = deadline
        self.ai_calls = 0

    # 공통 ----------------------------------------------------------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self.n._cancel.is_set()

    def still_active(self) -> bool:
        return self.app.state.dataset is self.dataset

    def phase(self, name: str, fn) -> None:
        if self.cancelled:
            return
        if not self.still_active():
            self.n._cancel.set()
            self.check("dataset_changed", "데이터 전환", "warn", "실행 중 데이터가 바뀌어 남은 작업을 하지 않았다")
            return
        self.n.progress = {**(self.n.progress or {}), "phase": name}
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001 -- 한 단계가 실패해도 나머지 점검·보고서는 남긴다
            log.exception("야간 배치 단계 실패: %s", name)
            self.check(f"phase:{name}", name, "fail", f"{type(exc).__name__}: {str(exc)[:300]}")

    def check(self, cid: str, title: str, status: str, detail: str) -> None:
        self.report["checks"].append({"id": cid, "title": title, "status": status, "detail": detail})

    def _acquire(self, sem, until: float) -> bool:
        """자리를 기다린다(1초마다 멈춤·마감 확인). 얻은 뒤 멈춤이 와 있으면 바로 돌려주고 False."""
        while time.time() < until and not self.cancelled:
            if sem.acquire(timeout=1):
                if self.cancelled or not self.still_active():
                    sem.release()
                    return False
                return True
        return False

    # 3. 점검 ------------------------------------------------------------------------------------------------------
    def check_data(self) -> None:
        info = self.dataset.info
        label = {"demo-bundle": "시연 묶음", "upload": "업로드 데이터", "fixture": "기본 데이터"}.get(info.source, info.source)
        detail = f"{label} {info.dataset_id} · {info.people}명 · 사업 {info.projects}개 · {'가상' if info.synthetic else '실데이터'}"
        err = getattr(self.app.state, "dataset_restore_error", None)
        judge_err = getattr(info, "judge_error", None)
        if err:
            self.check("data", "데이터", "warn", f"{detail} — {err}")
        elif judge_err:
            self.check("data", "데이터", "warn", f"{detail} — 리뷰 글 판정 일부 실패: {str(judge_err)[:200]}")
        else:
            self.check("data", "데이터", "ok", detail)

    def check_precomputed(self) -> None:
        source = self.dataset.info.source
        if source not in ("demo-bundle", "record"):
            self.check("precomputed", "시연 미리 계산", "skipped", "시연 묶음·편성 기록이 아니다")
            return
        # 편성 기록을 열면 기록의 편성안이 같은 등록부에 들어간다(api/routes/records -- demo_precomputed.install)
        title = "편성 기록 결과" if source == "record" else "시연 미리 계산"
        st = getattr(self.app.state, "precomputed_status", None)
        if not st:
            self.check("precomputed", title, "skipped",
                       "기록에 편성안이 없다" if source == "record" else "이 묶음의 미리 계산 파일이 없다")
        elif st.get("skipped"):
            self.check("precomputed", title, "warn",
                       "쓰지 못한 부분이 있다: " + "; ".join(str(s) for s in st["skipped"])[:400])
        elif source == "record":
            # 화면은 기록의 계산 요청 그대로 묻는다 -- 서버 설정 키로 찾지 않고, 기록이 넣은 결과가 **결과 캐시에 아직 있는지** 본다
            # (캐시는 최근 32개만 두므로 등록부 표시가 남아도 결과는 밀려났을 수 있다 -- Codex 리뷰 2026-10-19)
            # 요청 스레드가 등록부를 바꿀 수 있어 복사본으로 본다(Codex 리뷰 2라운드)
            marks = dict(getattr(self.app.state, "precomputed_keys", None) or {})
            cache = getattr(self.app.state, "cache", None)
            live = [k for k, m in marks.items() if m.get("source") != "nightly" and cache is not None and cache.has(k)]
            if st.get("plans") and not live:
                self.check("precomputed", title, "warn",
                           "기록의 편성안이 결과 캐시에 없다 -- 화면이 기록 요청으로 다시 계산한다")
            else:
                self.check("precomputed", title, "ok",
                           f"안 {st.get('plans', 0)}개" + (" · 운영 중 비교" if st.get("operating") else "") + " 사용 중")
        else:
            from api.cache import ResultCache
            key = ResultCache.key({}, _server_params(self.app, self.dataset), 3, self.dataset.info.version)
            mark = (getattr(self.app.state, "precomputed_keys", None) or {}).get(key)
            cache = getattr(self.app.state, "cache", None)
            evicted = cache is not None and not cache.has(key)                # 등록부 표시가 남아도 캐시에서 밀려났을 수 있다
            if st.get("plans") and (mark is None or mark.get("source") == "nightly" or evicted):
                # 부팅 때는 맞았지만 그 뒤 관리자 설정이 바뀌었거나 "다시 계산"으로 바뀌었다 -- 시연 대본 숫자와 화면이 달라진다(10-18 스모크)
                self.check("precomputed", "시연 미리 계산", "warn",
                           "기본 요청에 시연 미리 계산이 쓰이지 않고 있다(관리자 설정이 미리 계산 때와 다르거나 다시 계산했다) "
                           "-- 화면 숫자가 시연 대본과 다를 수 있다. 밤 풀이가 이 설정으로 대신 준비한다")
            else:
                self.check("precomputed", "시연 미리 계산", "ok",
                           f"안 {st.get('plans', 0)}개" + (" · 운영 중 비교" if st.get("operating") else "") + " 사용 중")

    def check_judgments(self) -> None:
        src = getattr(self.dataset, "_kg_source", None)
        if self.dataset.info.source != "demo-bundle" or self.dataset.info.synthetic is not True or src is None:
            self.check("judgments", "리뷰 판정 동봉", "skipped", "가상 시연 묶음이 아니다")
            return
        from api import review_judge as rj
        from api.demos import demo_judgments_path
        keys = set(rj.cache_keys(src[1]))
        seed = rj._load_cache(demo_judgments_path())
        missing = sum(1 for k in keys if k not in seed)
        if missing:
            self.check("judgments", "리뷰 판정 동봉", "warn",
                       f"동봉 판정에 없는 글 {missing}/{len(keys)}건 -- 다른 기기에서 켜면 판정이 달라져 미리 계산이 쓰이지 않을 수 있다"
                       " (판정기 설정이 바뀌었으면 scripts/export_demo_judgments.py)")
        else:
            self.check("judgments", "리뷰 판정 동봉", "ok", f"리뷰 {len(keys)}건 모두 동봉 판정에 있다")

    def check_plan_edits(self) -> None:
        store = getattr(self.app.state, "plan_edit_store", None)
        if store is None or not store.dir.exists():
            self.check("plan_edits", "저장된 교체", "ok", "저장된 교체 없음")
            return
        from api.routes.plans import _replay_steps
        current, stale, broken, violating = 0, 0, 0, 0
        for path in sorted(store.dir.glob("*.json"))[:200]:
            rec = store._read(path)
            if rec is None:
                broken += 1
                continue
            if rec.get("dataset_version") != self.dataset.info.version:
                stale += 1
                continue
            if not rec.get("swaps"):
                continue
            current += 1
            try:
                steps = _replay_steps(self.dataset.graph, rec)
            except Exception:                           # noqa: BLE001 -- 다시 적용하지 못한 저장본을 센다
                broken += 1
                continue
            if steps and not steps[-1].get("feasible", True):
                violating += 1
        detail = f"지금 데이터 {current}건 · 옛 데이터 {stale}건(이어지지 않음)"
        if broken or violating:
            self.check("plan_edits", "저장된 교체", "warn",
                       f"{detail} · 다시 적용 실패 {broken}건 · 위반이 남은 명단 {violating}건")
        else:
            self.check("plan_edits", "저장된 교체", "ok", detail)

    def check_disk(self) -> None:
        root = data_dir()
        ensure_private_dir(root)
        probe = root / DIRNAME / ".write-check"
        ensure_private_dir(probe.parent)
        atomic_write(probe, b"ok")
        probe.unlink(missing_ok=True)
        free = shutil.disk_usage(root).free
        gb = free / (1 << 30)
        if free < DISK_MIN_FREE:
            self.check("disk", "저장 공간", "warn", f"데이터 폴더 남은 공간 {gb:.1f}GB(1GB 미만)")
        else:
            self.check("disk", "저장 공간", "ok", f"데이터 폴더 쓰기 가능 · 남은 공간 {gb:.0f}GB")

    def check_model(self) -> None:
        try:
            from experiments.bench.phase0_model import run as phase0
        except ImportError as exc:
            self.check("model", "모델 검사(Phase 0)", "skipped", f"검사 도구를 불러오지 못했다({exc})")
            return
        t = time.perf_counter()
        res = phase0(max_wall_seconds=120.0)
        status = res.get("calculation_status")
        detail = f"소형 정답 비교·불변식 {res.get('completed_cases')}건 · {time.perf_counter() - t:.1f}초"
        self.check("model", "모델 검사(Phase 0)", "ok" if status == "PASS" else "fail", f"{status} · {detail}")

    def check_pdf(self) -> None:
        dist = Path(__file__).resolve().parents[1] / "web" / "dist" / "index.html"
        if not dist.is_file():
            self.check("pdf", "PDF 준비", "warn", "화면 빌드(web/dist)가 없어 PDF를 만들 수 없다 -- cd web && npm run build")
            return
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch()
                browser.close()
        except Exception as exc:                        # noqa: BLE001 -- 브라우저가 없으면 PDF만 안 된다
            self.check("pdf", "PDF 준비", "warn",
                       f"PDF용 브라우저를 띄우지 못했다({type(exc).__name__}) -- uv run playwright install chromium")
            return
        self.check("pdf", "PDF 준비", "ok", "화면 빌드와 PDF용 브라우저 정상")

    def check_ai(self) -> None:
        """설명 글 AI가 아침에 쓸 수 있는지 -- 비용 없는 모델 조회만 한다(글은 쓰지 않는다)."""
        from api.deps import get_openai_client_or_none, llm_client_for
        from api.routes.justification import ai_model
        client, why = llm_client_for(self.dataset, get_openai_client_or_none())
        if client is None:
            reason = {"no_client": "AI 키가 없다 -- 설명 글은 정해진 틀로 나간다",
                      "external_blocked": "실데이터라 회사 밖 AI로 보내지 않는다(정해진 틀로 나간다)"}.get(why, str(why))
            self.check("ai", "AI 연결", "skipped", reason)
            return
        try:
            client.with_options(timeout=20.0, max_retries=0).models.retrieve(ai_model())   # 비용 없는 조회
        except Exception as exc:                        # noqa: BLE001
            self.check("ai", "AI 연결", "warn", f"AI 서버 확인 실패({type(exc).__name__}: {str(exc)[:160]})")
            return
        self.check("ai", "AI 연결", "ok", f"AI 서버·모델({ai_model()}) 확인")

    # 1. 밤 풀이 ---------------------------------------------------------------------------------------------------
    def _solve_entry(self, *, target: str, weights: dict, n_alt: int, params) -> dict:
        from api.cache import ResultCache
        from api.routes.optimize import solve_slots
        from core.evaluate.plan_eval import evaluate_plan
        from core.optimize.alternatives import generate_plans_streaming
        from core.optimize.interrupt import SolveCancelled, watching
        version = self.dataset.info.version
        key = ResultCache.key(weights, params, n_alt, version)
        row = {"kind": "optimize", "target": target, "weights": weights, "n_alternatives": n_alt,
               "day_time_limit": params.time_limit, "key": key[:12]}
        if self.app.state.cache.get(key) is not None:
            return {**row, "status": "skipped", "reason": "이미 아침에 바로 나온다(시연 미리 계산·이전 결과)"}
        lim = night_limit(params.time_limit, 1 + n_alt, self.solve_until - time.time())
        if lim is None:
            return {**row, "status": "skipped", "reason": "남은 시간이 낮 한도보다 짧다"}
        slots = solve_slots(self.app)
        if not self._acquire(slots, self.solve_until):
            return {**row, "status": "skipped", "reason": "계산 자리가 나지 않았다"}
        graph = self.dataset.graph
        outcome: dict = {}
        plans = []
        stop = timer = None
        t = time.perf_counter()
        try:                                                        # 자리를 얻은 뒤의 모든 경로에서 자리를 돌려준다
            S, C = _matrices(graph, weights)
            night = params.model_copy(update={"time_limit": lim})
            stop = self.n.new_stop()                                # 준비 중에 온 멈춤은 여기서 물려받는다
            timer = threading.Timer(max(0.0, self.solve_until - time.time()), stop.request, args=("finish",))
            timer.daemon = True
            timer.start()
            with watching(stop):
                for plan in generate_plans_streaming(graph, S, C, night, n_alt, outcome):
                    plans.append(plan)
        except SolveCancelled:
            outcome.setdefault("stop_reason", "time_limit")         # 밤 마감 -- 대안이 모자란 이유는 시간
        except RuntimeError as exc:                                 # 안 A 실패(해 없음·검증 거절)
            outcome["error"] = str(exc)[:200]
        finally:
            if timer is not None:
                timer.cancel()
            self.n.clear_stop()
            slots.release()
        elapsed = round(time.perf_counter() - t, 1)
        row.update({"night_time_limit": lim, "elapsed_s": elapsed})
        if stop is None or stop.aborting or not plans:
            return {**row, "status": "failed", "reason": outcome.get("error") or "멈춰서 결과가 없다"}
        if stop.mode is not None or outcome.get("stop_reason") in INCOMPLETE_STOPS:
            # 끝까지 풀지 못한 묶음은 저장하지 않는다 -- 멈춤·마감으로 풀던 안이 "사용자가 멈춤"으로 끝났거나, 대안이 실패·시간
            # 초과로 모자란 묶음이 아침 결과로 굳고, 다음 밤에도 같은 키라 다시 풀지 않게 된다(Codex 리뷰·Opus 대체 리뷰 2026-10-18).
            # 낮 경로의 cacheable()과 같은 기준이다. 다음 밤에 다시 푼다.
            why = "멈춤·마감으로 끝까지 풀지 못했다" if stop.mode is not None else f"대안을 다 풀지 못했다({outcome['stop_reason']})"
            return {**row, "status": "incomplete", "plans": len(plans), "reason": why}
        recorded, bad = [], None
        for plan in plans:
            ev = evaluate_plan(graph, S, C, params, plan.entries)
            if ev.violations:
                bad = f"안 {plan.label}: 평가기 위반 {len(ev.violations)}건"
                break
            recorded.append({**plan.model_dump(), "eval_objective": ev.objective.total})
        if bad:
            return {**row, "status": "failed", "reason": bad}
        entry = {"kind": "optimize", "target": target, "key": key, "weights": weights, "n_alternatives": n_alt,
                 "milp_params": params.model_dump(), "night_time_limit": lim, "computed_at": _now_iso(),
                 "stop_reason": outcome.get("stop_reason"), "elapsed_s": elapsed, "plans": recorded}
        a = plans[0]
        bound = a.best_bound
        row.update({"status": "prepared", "plans": len(plans), "labels": [p.label for p in plans],
                    "terminations": [p.termination for p in plans], "objective_a": a.objective,
                    "gap_a": (None if not bound else (bound - a.objective) / abs(bound)),
                    "unfilled_a": len(a.unfilled), "stop_reason": outcome.get("stop_reason")})
        if not self.still_active():
            return {**row, "status": "failed", "reason": "풀이 중 데이터가 바뀌었다"}
        self.n._save_prepared(self.dataset.info.version, entry)
        res = _install_entry(self.app, self.dataset, entry)
        row["installed"] = res.get("status") == "installed"
        if not row["installed"]:
            row["install_reason"] = res.get("reason")
        return row

    def solve_default(self) -> None:
        """연간 계획(백지 안 A~D)은 현재 배치가 없는 데이터일 때만 -- 운영 중에는 처음부터 다시 짜는 일이 거의 없다
        (사용자 2026-10-18: "연초라고 백지가 되는 상황은 거의 없을 가능성이 높아")."""
        if self.dataset.current:
            self.report["solves"].append({"kind": "optimize", "target": "연간 계획(안 A~D)", "status": "skipped",
                                          "reason": "운영 중 데이터(현재 배치 있음) -- 연간 계획은 백지 데이터일 때만"})
            return
        params = _server_params(self.app, self.dataset)
        self.report["solves"].append(self._solve_entry(target="연간 계획(안 A~D)", weights={}, n_alt=3, params=params))

    def solve_recorded(self) -> None:
        from pydantic import ValidationError
        from api.cache import ResultCache
        from api.routes.optimize import OptimizeRequest
        version = self.dataset.info.version
        seen: set[str] = set()
        done = 0
        skipped_other = 0
        for rec in reversed(self.n.recorded()):         # 최근 것부터
            if self.cancelled or done >= MAX_RECORDED_SOLVES or time.time() >= self.solve_until:
                break
            if rec.get("kind") != "optimize":
                continue
            try:
                req = OptimizeRequest.model_validate(rec["body"])
                params = req.milp_params.to_milp_params()
            except (ValidationError, ValueError, TypeError):
                continue
            if req.dataset_version not in (None, version):
                skipped_other += 1
                continue
            key = ResultCache.key(req.weights, params, req.n_alternatives, version)
            if key in seen:
                continue
            seen.add(key)
            if self.app.state.cache.get(key) is not None:
                continue
            self.report["solves"].append(self._solve_entry(target=f"낮 요청({rec.get('at', '')[:16]})",
                                                           weights=dict(req.weights), n_alt=req.n_alternatives,
                                                           params=params))
            done += 1
        if done == 0:
            self.report["solves"].append({"kind": "optimize", "target": "낮 요청", "status": "skipped",
                                          "reason": "다시 풀 낮 요청이 없다(모두 바로 나오거나 기록 없음)"
                                                    + (f" · 다른 데이터 요청 {skipped_other}건" if skipped_other else "")})

    def solve_operating(self) -> None:
        from api.demo_precomputed import operating_key
        from api.routes import operating as op_routes
        from core.evaluate.operating import compare_move_budgets
        from core.optimize.interrupt import SolveCancelled, watching
        row = {"kind": "operating", "target": f"운영 중 비교(K 0~{op_routes.OPERATING_KS[-1]})"}
        if not self.dataset.current:
            self.report["solves"].append({**row, "status": "skipped", "reason": "현재 배치가 없는 데이터다"})
            return
        if not (self.dataset.scenario.get("proposals") or []):
            self.report["solves"].append({**row, "status": "skipped", "reason": "채울 신규 사업이 없다"})
            return
        version = self.dataset.info.version
        params = _operating_params(_server_params(self.app, self.dataset))
        ks = list(DEFAULT_KS)
        key = operating_key(params, ks, version)
        existing = getattr(self.app.state, "precomputed_operating", None)
        if existing and existing.get("key") == key:
            self.report["solves"].append({**row, "status": "skipped", "reason": "이미 아침에 바로 나온다(미리 계산)"})
            return
        lim = night_limit(params.time_limit, len(ks), self.solve_until - time.time(), OPERATING_FACTOR)
        if lim is None:
            self.report["solves"].append({**row, "status": "skipped", "reason": "남은 시간이 낮 한도보다 짧다"})
            return
        if not self._acquire(op_routes._COMPARE_SLOT, self.solve_until):
            self.report["solves"].append({**row, "status": "skipped", "reason": "운영 비교 자리가 나지 않았다"})
            return
        graph = self.dataset.graph
        stop = timer = None
        t = time.perf_counter()
        rows: list[dict] = []
        try:                                                        # 자리를 얻은 뒤의 모든 경로에서 자리를 돌려준다
            S, C = _matrices(graph, {})
            stop = self.n.new_stop()                                # 준비 중에 온 멈춤은 여기서 물려받는다
            timer = threading.Timer(max(0.0, self.solve_until - time.time()), stop.request, args=("finish",))
            timer.daemon = True
            timer.start()
            with watching(stop):
                rows = compare_move_budgets(graph, S, C, params.model_copy(update={"time_limit": lim}),
                                            self.dataset.current, ks=ks)
        except SolveCancelled:
            rows = []
        finally:
            if timer is not None:
                timer.cancel()
            self.n.clear_stop()
            op_routes._COMPARE_SLOT.release()
        elapsed = round(time.perf_counter() - t, 1)
        if not all(r.get("accepted") and not r.get("solve_failed") for r in rows):
            # K 풀이가 실패한 행이 있으면 저장하지 않는다 -- 다음 밤에 다시 푼다(Codex 리뷰 2026-10-18)
            self.report["solves"].append({**row, "status": "failed", "night_time_limit": lim, "elapsed_s": elapsed,
                                          "reason": "풀지 못한 K가 있다(" + ", ".join(
                                              str(r.get("k")) for r in rows
                                              if not r.get("accepted") or r.get("solve_failed")) + ")"})
            return
        if stop is None or stop.mode is not None or sorted(int(r.get("k")) for r in rows) != ks:
            # 멈춘 비교는 K 행이 모자라거나 마지막 K가 덜 풀렸다 -- 미리 계산은 K가 하나씩 맞아야 쓰인다
            self.report["solves"].append({**row, "status": "failed", "night_time_limit": lim, "elapsed_s": elapsed,
                                          "reason": "마감으로 멈춰 K 비교를 끝내지 못했다"})
            return
        rows = json.loads(json.dumps(rows, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))
        bad = _verify_rows(graph, params, rows)
        if bad:
            self.report["solves"].append({**row, "status": "failed", "reason": bad})
            return
        entry = {"kind": "operating", "key": key, "ks": ks, "milp_params": params.model_dump(),
                 "night_time_limit": lim, "computed_at": _now_iso(), "elapsed_s": elapsed, "rows": rows}
        if not self.still_active():
            self.report["solves"].append({**row, "status": "failed", "reason": "풀이 중 데이터가 바뀌었다"})
            return
        self.n._save_prepared(version, entry)
        res = _install_entry(self.app, self.dataset, entry)
        self.report["solves"].append({**row, "status": "prepared", "night_time_limit": lim, "elapsed_s": elapsed,
                                      "rows": len(rows), "terminations": [r.get("termination") for r in rows],
                                      "installed": res.get("status") == "installed",
                                      **({} if res.get("status") == "installed" else {"install_reason": res.get("reason")})})

    # 긴급 대기 명단 --------------------------------------------------------------------------------------------
    def standby(self) -> None:
        """진행 사업마다 '지금 바로 넣을 수 있는 사람' 상위 5명(빼 오지 않음)을 뽑아 둔다 -- 사고가 나면 즉시 보이게
        (3가지 핵심 상황 설계 상황 3). 판마다·날마다 바뀐다(가용률·끝나는 사업). AI 비용 없음."""
        from api.routes.scenarios import standby_for
        ongoing = sorted({c.project_id for c in self.dataset.current})
        if not ongoing:
            self.report["standby"] = {"projects": 0, "skipped_reason": "현재 배치가 없는 데이터다"}
            return
        out: dict[str, list] = {}
        for pid in ongoing:
            if self.cancelled or not self.still_active() or time.time() >= self.solve_until:
                break
            out[pid] = standby_for(self.app, self.dataset, pid)
        if not self.still_active():
            return
        from api.routes.scenarios import params_fingerprint
        self.n.store.write("standby.json", {"dataset_version": self.dataset.info.version, "computed_at": _now_iso(),
                                            "params": params_fingerprint(self.app, self.dataset), "projects": out})
        self.report["standby"] = {"projects": len(out), "people": sum(len(v) for v in out.values())}


def _summarize(report: dict) -> None:
    checks = report.get("checks", [])
    attention = [c for c in checks if c["status"] in ("warn", "fail")]
    prepared = [s for s in report.get("solves", []) if s.get("status") == "prepared"]
    failed = [s for s in report.get("solves", []) if s.get("status") in ("failed", "incomplete")]
    sb = report.get("standby") or {}
    parts = [f"확인 필요 {len(attention) + len(failed)}건" if (attention or failed) else "점검 정상"]
    if prepared:
        parts.append("미리 풀어 둠 " + ", ".join(s["target"] for s in prepared))
    if sb.get("projects"):
        parts.append(f"긴급 대기 명단 {sb['projects']}개 사업")
    if report.get("stopped"):
        parts.append("중간에 멈춤")
    report["status"] = "attention" if (attention or failed) else "ok"
    report["attention"] = [{"title": c["title"], "detail": c["detail"]} for c in attention] + [
        {"title": s.get("target"), "detail": s.get("reason")} for s in failed]
    report["summary"] = " · ".join(parts)


# --- 앱 연결 -------------------------------------------------------------------------------------------------------

def _prune_justifications(keep_version: str) -> None:
    from api.justification_cache import JustificationCache
    try:
        JustificationCache().prune(keep_version)
    except OSError:
        log.warning("옛 데이터의 소명 글을 지우지 못했다", exc_info=True)


def attach(app) -> Nightly:
    """lifespan에서 부른다: 실행기를 만들고, 옛 데이터의 소명 글을 지우고, 지금 데이터에 맞는 밤 결과를 넣고,
    켜져 있으면 예약을 시작한다."""
    nightly = Nightly(app)
    app.state.nightly = nightly
    try:
        nightly.loop = asyncio.get_running_loop()      # lifespan 안 -- 밤 스레드가 인사 시스템 받기를 이 루프에 맡긴다
    except RuntimeError:
        nightly.loop = None
    _prune_justifications(app.state.dataset.info.version)
    try:
        nightly.install_prepared()
    except Exception:                                   # noqa: BLE001 -- 밤 결과가 깨져도 서버는 뜬다
        log.exception("밤 결과를 넣지 못했다")
    if enabled():
        nightly.start_scheduler()
    return nightly


def detach(app) -> None:
    nightly = getattr(app.state, "nightly", None)
    if nightly is not None:
        nightly.shutdown()
