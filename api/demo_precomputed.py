"""시연 묶음의 미리 계산 결과(시연 확장 E, 2026-10-06, claude-a -- 사용자 지시 "남은 선택 항목도 개발해줘").

300명은 안 A 하나에 약 3분, 안 A~D면 10분이 넘는다(rehearsal/results/baseline-check.json) -- 시연 중에 기다릴 수 없다.
시간 한도에서 멈춘 해라 서비스는 결과를 캐시하지도 않는다(C2). 그래서 같은 데이터·같은 설정으로 미리 계산한 결과를
`<시연 폴더>/precomputed/<묶음 id>.json`(python -m rehearsal.precompute_demo)에 두고, 묶음을 켤 때 아래를 **모두** 만족하면
결과 캐시에 넣는다. 화면에는 "미리 계산(시각)"으로 보이고(응답의 precomputed_at), "다시 계산"(fresh)이면 실시간으로 푼다.
  1. 파일의 데이터셋 버전 == 지금 데이터셋 버전(리뷰 글 판정값까지 포함한 계산 버전)
  2. 파일의 설정 == 지금 서버 설정(to_milp_params) -- 다르면 웹 요청의 캐시 키와 맞지 않는다
  3. 각 안(운영 중 비교는 K마다)을 지금 평가기(plan_eval)로 다시 채점하면 **미리 계산 때 같은 평가기가 낸 점수**
     (eval_objective)와 같고, 안은 제약 위반이 없다 -- 코드(목적식·제약)가 바뀌었으면 버린다.
     솔버 목적값(plan.objective)과 비교하지 않는다: 솔버는 내림 전 투입률로 목적을 재고, 화면·평가기는 둘째 자리에서
     내림한 투입률을 쓰므로 0.01 단위로 다르다(tests/test_plan_eval.py가 기록한 차이, 리뷰 MUST).
하나라도 어긋나면 넣지 않고 이유를 남긴다(/api/datasets/active의 precomputed.skipped). 실시간 계산이 그대로 동작한다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from api.cache import ResultCache
from core.evaluate.plan_eval import evaluate_plan
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry, PlanAssignment
from core.scoring.engine import ScoringEngine

log = logging.getLogger(__name__)
FORMAT = 1
DIRNAME = "precomputed"


@dataclass
class Precomputed:
    preset: str
    computed_at: str
    optimize_key: str | None = None
    plans: list[PlanAssignment] = field(default_factory=list)
    stop_reason: str | None = None           # 대안이 모자란 이유(시간 초과 등) -- 화면이 조건 미충족과 구분한다
    operating: dict | None = None            # {"key": ..., "ks": [...], "rows": [...]}
    skipped: list[str] = field(default_factory=list)

    def status(self) -> dict:
        return {"preset": self.preset, "computed_at": self.computed_at, "plans": len(self.plans),
                "operating": self.operating is not None, "skipped": self.skipped}


def preset_id(bundle_path: Path) -> str:
    p = Path(bundle_path)
    return p.stem if p.suffix.lower() == ".zip" else p.name


def precomputed_path(bundle_path: Path) -> Path:
    p = Path(bundle_path).expanduser()
    return p.parent / DIRNAME / f"{preset_id(p)}.json"


def operating_key(params: MilpParams, ks, dataset_version: str) -> str:
    payload = json.dumps({"milp_params": params.model_dump(), "ks": sorted(int(k) for k in ks),
                          "dataset": dataset_version}, sort_keys=True)
    import hashlib
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-6) -> bool:
    return abs(a - b) <= max(abs_, rel * max(1.0, abs(a), abs(b)))


def load_precomputed(bundle_path: Path, active, params: MilpParams) -> Precomputed | None:
    """검증을 통과한 것만 담아 돌려준다. 파일이 없으면 None. 스레드에서 돈다(평가기 재채점)."""
    path = precomputed_path(bundle_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return Precomputed(preset=preset_id(bundle_path), computed_at="",
                           skipped=[f"파일을 읽지 못했다({type(exc).__name__})"])
    pre = Precomputed(preset=preset_id(bundle_path), computed_at=str(data.get("computed_at", "")))
    version = active.info.version
    if data.get("format") != FORMAT:
        pre.skipped.append(f"형식이 다르다({data.get('format')})")
        return pre
    if data.get("dataset_version") != version:
        pre.skipped.append("데이터셋 버전이 다르다(묶음 내용이나 리뷰 글 판정값이 미리 계산 때와 다르다)")
        return pre
    graph = active.graph
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    opt = data.get("optimize")
    try:
        _check_plans(pre, opt, graph, S, C, params, version)
    except (KeyError, TypeError, ValueError) as exc:          # 모양이 깨진 기록 -- 그 부분만 버린다
        pre.plans, pre.optimize_key = [], None
        pre.skipped.append(f"안 A~D: 기록 모양이 맞지 않다({type(exc).__name__})")
    try:
        _check_operating(pre, data.get("operating"), graph, S, C, params, version)
    except (KeyError, TypeError, ValueError) as exc:
        pre.operating = None
        pre.skipped.append(f"운영 중 비교: 기록 모양이 맞지 않다({type(exc).__name__})")
    return pre


def _check_plans(pre: Precomputed, opt, graph, S, C, params: MilpParams, version: str) -> None:
    if opt:
        try:
            fparams = MilpParams(**opt["milp_params"])
        except (TypeError, ValueError) as exc:
            fparams = None
            pre.skipped.append(f"안 A~D: 설정을 읽지 못했다({exc})")
        if fparams is not None and fparams.model_dump() != params.model_dump():
            pre.skipped.append("안 A~D: 지금 서버 설정이 미리 계산 때와 다르다")
        elif opt.get("weights") != {}:
            pre.skipped.append("안 A~D: 기본 가중치({})로 계산한 기록이 아니다")
        elif fparams is not None:
            plans, bad = [], None
            for raw in opt.get("plans", []):
                plan = PlanAssignment(entries=[AssignEntry(**e) for e in raw["entries"]], objective=raw["objective"],
                                      unfilled=list(raw.get("unfilled", [])), violations=list(raw.get("violations", [])),
                                      time_limited=bool(raw.get("time_limited", False)), label=raw.get("label", "A"))
                ev = evaluate_plan(graph, S, C, params, plan.entries)
                recorded = float(raw["eval_objective"])
                if ev.violations or not _close(ev.objective.total, recorded):
                    bad = (f"안 {plan.label}: 지금 평가기로 다시 채점하면 맞지 않는다(위반 {len(ev.violations)}건, "
                           f"점수 {ev.objective.total:.4f} vs 기록 {recorded:.4f})")
                    break
                plans.append(plan)
            if bad:
                pre.skipped.append(bad)
            elif not plans:
                pre.skipped.append("안 A~D: 기록에 안이 없다")
            else:
                pre.plans = plans
                pre.stop_reason = opt.get("stop_reason")
                pre.optimize_key = ResultCache.key({}, params, int(opt.get("n_alternatives", 3)), version)


def _check_operating(pre: Precomputed, op, graph, S, C, params: MilpParams, version: str) -> None:
    if op:
        try:
            fparams = MilpParams(**op["milp_params"])
        except (TypeError, ValueError) as exc:
            fparams = None
            pre.skipped.append(f"운영 중 비교: 설정을 읽지 못했다({exc})")
        if fparams is not None and fparams.model_dump() != params.model_dump():
            pre.skipped.append("운영 중 비교: 지금 서버 설정이 미리 계산 때와 다르다")
        elif fparams is not None:
            bad = None
            for row in op.get("rows", []):
                if not row.get("accepted"):
                    continue
                if not row.get("entries"):              # 채택 행인데 배치가 없으면 확인할 수 없다 -- 버린다(리뷰 nit)
                    bad = f"운영 중 비교 K={row.get('k')}: 배치 기록이 없다"
                    break
                entries = [AssignEntry(**e) for e in row["entries"]]
                ev = evaluate_plan(graph, S, C, params, entries)
                if (sorted(v.code for v in ev.violations) != sorted(row.get("violations", []))
                        or not _close(ev.objective.total, row["objective"], abs_=1e-3)):
                    bad = f"운영 중 비교 K={row.get('k')}: 지금 평가기로 다시 채점하면 맞지 않는다"
                    break
            if bad:
                pre.skipped.append(bad)
            else:
                ks = [int(k) for k in op.get("ks", [])]
                pre.operating = {"key": operating_key(params, ks, version), "ks": ks, "rows": op.get("rows", []),
                                 "computed_at": pre.computed_at}


def install(app, pre: Precomputed | None) -> None:
    """검증된 미리 계산 결과를 결과 캐시와 등록부에 넣는다(데이터셋을 바꿀 때마다 다시 부른다)."""
    app.state.precomputed_keys = {}
    app.state.precomputed_operating = None
    app.state.precomputed_status = pre.status() if pre is not None else None
    if pre is None:
        return
    if pre.optimize_key and pre.plans:
        app.state.cache.put(pre.optimize_key, pre.plans)
        app.state.precomputed_keys[pre.optimize_key] = {"at": pre.computed_at, "stop_reason": pre.stop_reason}
    app.state.precomputed_operating = pre.operating
    if pre.skipped:
        log.warning("미리 계산 결과(%s) 일부를 쓰지 않는다: %s", pre.preset, "; ".join(pre.skipped))


def precomputed_at(app, key: str) -> str | None:
    return ((getattr(app.state, "precomputed_keys", None) or {}).get(key) or {}).get("at")


def precomputed_stop_reason(app, key: str) -> str | None:
    return ((getattr(app.state, "precomputed_keys", None) or {}).get(key) or {}).get("stop_reason")


def forget(app, key: str) -> None:
    """같은 키에 실시간 결과가 들어가면 그건 미리 계산이 아니다."""
    (getattr(app.state, "precomputed_keys", None) or {}).pop(key, None)


def operating_rows(app, params: MilpParams, ks, dataset_version: str) -> tuple[list[dict], str] | None:
    """운영 중 비교(K별)의 미리 계산 행 -- 같은 데이터·설정·K 목록일 때만. (행, 계산 시각) 또는 None."""
    op = getattr(app.state, "precomputed_operating", None)
    if not op or op["key"] != operating_key(params, ks, dataset_version):
        return None
    return op["rows"], op["computed_at"]


def prepare_for(app, active, bundle_path: Path | None) -> Precomputed | None:
    """활성 후보가 시연 묶음이면 그 묶음의 미리 계산 결과를 검증해 돌려준다(스레드에서 부른다)."""
    if active.info.source != "demo-bundle" or bundle_path is None:
        return None
    try:
        params = app.state.settings_store.current().settings.to_milp_params(n_people=len(active.graph.people))
        return load_precomputed(bundle_path, active, params)
    except Exception as exc:                        # noqa: BLE001 -- 미리 계산이 깨져도 전환·부팅은 된다
        log.error("미리 계산 결과를 확인하지 못했다(%s): %s", bundle_path, exc)
        return Precomputed(preset=preset_id(bundle_path), computed_at="",
                           skipped=[f"확인 중 오류({type(exc).__name__})"])
