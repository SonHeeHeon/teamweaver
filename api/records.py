"""편성 기록(JSON) 내보내기·불러오기(2026-10-18, claude-a -- 사용자 지시: 시연 데이터 고르기 대신 "과거에 편성한 걸 json으로
Export 하고 그걸 다시 넣어서 화면에서 볼 수 있는(2024년에 어떻게 했었지?) 기능", 결정: 기록 목록 + 열면 작업 화면).

기록 한 개 = 그때의 원천 CSV 묶음(원문) + 평가 글 판정값 + 계산 요청(가중치·배치 설정·대안 수) + 편성안 A~D(+ 운영 중 K 비교).
원천을 통째로 담는 이유: 몇 년 뒤에는 그때의 사람·사업이 지금 데이터에 없다. 서명값·비밀키는 담지 않는다(서버마다 다르다 --
열 때 이 서버가 다시 서명한다).

받아들이는 조건(불러오기·열기 공통, `build_and_verify`):
  1. 형식·판(FORMAT, VERSION), 파일 이름은 CSV 묶음 계약의 허용 목록뿐(경로 없음), 크기 상한.
  2. 원천 파일을 업로드와 같은 검증(core.ingest)으로 다시 만들고, 내용 해시 == 기록의 content_version. 이 해시는 기록이 스스로
     주장하는 값이라 실수로 고친 파일을 거르는 장치일 뿐 위조 방지는 아니다(폴백 리뷰 N3) -- 신뢰는 아래 재채점이 맡는다.
  3. 평가 글 판정값은 기록의 값을 쓴다(리뷰 순서대로 한 개씩). 기록에 판정값이 없으면(항목 점수로 계산한 데이터) 항목 점수로
     다시 만든다 -- 어느 경우에도 LLM을 부르지 않는다(폴백 리뷰 S2: 확인용 불러오기가 판정 캐시를 잘라 내지 않게).
  4. 편성안마다 지금 평가기(plan_eval)로 기록의 설정대로 다시 채점 -- 제약 위반이 하나라도 있으면 거절. 점수가 기록과 다르면
     받아들이고 "기록 당시 X · 지금 규칙 Y"로 알린다(옛 기록도 열 수 있게 -- 미리 계산처럼 "다르면 버림"이 아니다).
     화면·PDF에 나가는 값은 **이 서버가 다시 계산한 것만** 쓴다(폴백 리뷰 M3): 목적값 = 지금 평가기 점수, 빈자리 = 평가기가 센
     부족분. 기록에 적힌 솔버 증명(종료 사유·증명 상한·허용 차이)은 이 서버가 확인할 수 없으므로 버리고 종료 사유를 "record"로
     둔다 -- 서버가 그 값을 다시 서명해 "최선 증명"으로 세탁하지 않게. 증명을 보려면 화면에서 "다시 계산".
  5. 운영 중 K 행도 같다(평가기 재채점·위반 일치·옮긴 사람 수 ≤ K·증명 값 제거). 맞지 않으면 그 부분만 빼고 알린다.
열면 결과 캐시에 넣고(api.demo_precomputed.install -- 같은 등록부를 쓴다) 화면이 기록의 요청 그대로 /api/optimize를 부르면
캐시에서 바로 나온다(서명은 그 경로가 붙인다).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry, PlanAssignment

log = logging.getLogger(__name__)
FORMAT = "teamweaver.placement-record"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 50
MAX_FILE_CHARS = 8 * 1024 * 1024
TITLE_MAX = 120


class RecordError(ValueError):
    """기록을 받아들일 수 없는 이유(화면에 그대로 보인다)."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _params_from_request(milp_params: dict) -> MilpParams:
    from api.schemas import MilpParamsIn
    return MilpParamsIn(**(milp_params or {})).to_milp_params()


def request_fields(params: MilpParams) -> dict:
    """MilpParams → 화면이 보내는 배치 설정 모양(관리자 설정 PlacementSettings의 칸만 -- PDF·교체 검토 요청이 그 모양만 받는다).
    설정 화면에 없는 모델 값(보상 쌍 수·빈자리 감점 등)이 기본값과 다르면 그 모양으로 옮길 수 없다 -- ValueError."""
    from api.settings import PlacementSettings
    dump = params.model_dump()
    # 설정 칸 전체(PDF 요청이 모든 칸을 요구한다). 시간은 기록 당시 실제 값이라 자동 시간은 끈다. 동시 탐색 수는 서버 자원이라 비운다.
    # None도 뜻이 있는 값이다(clique_window_months=None = 전체 이력) -- 빼면 설정 기본값(36개월)이 들어간다(Codex 리뷰 P2)
    out = PlacementSettings(**{k: dump[k] for k in PlacementSettings.model_fields if k in dump and k != "solver_seeds"},
                            time_limit_auto=False, solver_seeds=None).model_dump()
    if _params_from_request(out).model_dump() != {**params.model_dump(), "solver_seeds": 1}:
        raise ValueError("배치 설정 화면에 없는 값이 기본값과 달라 기록 요청 모양으로 옮길 수 없다")
    return out


def normalize_request(req: dict) -> dict:
    """기록의 계산 요청을 화면이 보내는 모양으로(같은 계산이 되는 경우만). 옛 기록이 설정 화면에 없는 칸을 담았으면 빼 준다."""
    try:
        params = _params_from_request(req.get("milp_params") or {})
        return {**req, "milp_params": request_fields(params)}
    except (TypeError, ValueError):
        return req


# --- 파일 형식 ---------------------------------------------------------------

def parse_record(raw: bytes) -> dict:
    """모양만 확인한다(내용 검증은 build_and_verify)."""
    if len(raw) > MAX_BYTES:
        raise RecordError(f"기록 파일은 {MAX_BYTES // (1024 * 1024)}MB까지다.")
    try:
        rec = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RecordError(f"JSON 파일이 아니다({type(exc).__name__}).") from None
    if not isinstance(rec, dict) or rec.get("format") != FORMAT:
        raise RecordError("TeamWeaver 편성 기록 파일이 아니다(format이 다르다).")
    if rec.get("version") != VERSION:
        raise RecordError(f"지원하지 않는 기록 판이다(version {rec.get('version')!r}, 지원 {VERSION}).")
    title = rec.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        raise RecordError("제목이 없거나 너무 길다.")
    ds = rec.get("dataset")
    if not isinstance(ds, dict) or not isinstance(ds.get("files"), dict) or not ds["files"]:
        raise RecordError("원천 데이터(dataset.files)가 없다.")
    from api.datasets import _ALLOWED
    for name, text in ds["files"].items():
        if name not in _ALLOWED and name != "manifest.json":
            raise RecordError(f"허용하지 않는 파일 이름이다: {str(name)[:60]!r}")
        if not isinstance(text, str) or len(text) > MAX_FILE_CHARS:
            raise RecordError(f"{name}: 글이 아니거나 너무 크다.")
    if "manifest.json" not in ds["files"]:
        raise RecordError("manifest.json이 없다.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(ds.get("content_version", ""))):
        raise RecordError("원천 데이터 해시(content_version)가 없다.")
    req = rec.get("request")
    if not isinstance(req, dict) or not isinstance(req.get("weights", {}), dict):
        raise RecordError("계산 요청(request)이 없다.")
    opt = rec.get("optimize")
    if not isinstance(opt, dict) or not isinstance(opt.get("plans"), list) or not opt["plans"]:
        raise RecordError("편성안(optimize.plans)이 없다.")
    if len(opt["plans"]) > 7:
        raise RecordError("편성안이 너무 많다(최대 7개).")
    rj = rec.get("review_judgments")
    if rj is not None and (not isinstance(rj, dict) or not isinstance(rj.get("polarity"), list)):
        raise RecordError("평가 글 판정값 모양이 맞지 않다.")
    return rec


def summary(rec: dict, check: dict | None = None, **extra) -> dict:
    """목록에 보일 정보. check(build_and_verify 결과)가 있으면 기록이 적은 값이 아니라 다시 만든 데이터의 값을 쓴다
    (폴백 리뷰 S7: 기록 맨 위의 people·synthetic은 검증되지 않은 주장이다)."""
    facts = (check or {}).get("facts") or {}
    ds = rec["dataset"]
    exported = rec.get("exported_at")
    return {"title": str(rec["title"])[:TITLE_MAX],
            "kind": facts.get("kind") or ("operating" if rec.get("kind") == "operating" else "planning"),
            "exported_at": exported if isinstance(exported, str) and len(exported) <= 40 else None,
            "dataset_id": facts.get("dataset_id", ds.get("dataset_id") if isinstance(ds.get("dataset_id"), str) else None),
            "people": facts.get("people"), "projects": facts.get("projects"),
            "horizon_start": facts.get("horizon_start"), "horizon_months": facts.get("horizon_months"),
            "synthetic": facts.get("synthetic"), "plans": (check or {}).get("plans", len(rec["optimize"]["plans"])),
            "operating": bool((check or {}).get("operating")), **extra}


# --- 다시 만들고 확인하기 -------------------------------------------------------

def build_and_verify(rec: dict):
    """(ActiveDataset, Precomputed, 확인 요약). 받아들일 수 없으면 RecordError. 스레드에서 부른다.
    변조된 값(형이 틀린 가중치·None·짝 없는 서로게이트 등)은 500이 아니라 RecordError가 된다(폴백 리뷰 S1)."""
    try:
        return _build_and_verify(rec)
    except RecordError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError) as exc:
        raise RecordError(f"기록 내용이 올바르지 않다({type(exc).__name__}: {str(exc)[:160]})") from None


def _check_operating_row(graph, current, entries, k: int) -> None:
    """운영 중 K 행이 서비스 풀이의 규칙(core.optimize.incremental.move_budget_constraints, 신규 제안 편성 기본값)을 지키는지.
    지키지 않으면 ValueError. 일반 평가기(plan_eval)는 이 규칙을 모른다(Codex 리뷰 2라운드 P2)."""
    placed = {(e.person_id, e.project_id): float(e.alloc) for e in entries}
    before: dict[str, set] = {}
    team = {}
    for c in current:
        before.setdefault(c.person_id, set()).add(c.project_id)
        team[c.project_id] = team.get(c.project_id, 0) + 1
    moved = set()
    for c in current:
        a = placed.get((c.person_id, c.project_id))
        if a is None:
            if c.locked:
                raise ValueError(f"잠긴 배치 {c.person_id}·{c.project_id}가 빠졌다")
            moved.add(c.person_id)                       # 현재 배치가 하나라도 빠지면 옮긴 사람(겸직을 더하는 것은 이동이 아니다)
        elif abs(a - float(c.alloc)) > 0.011:
            raise ValueError(f"유지한 배치 {c.person_id}·{c.project_id}의 투입률이 바뀌었다")
    if len(moved) > k:
        raise ValueError(f"옮긴 사람 {len(moved)}명이 K={k}보다 많다")
    after: dict[str, set] = {}
    for (pid, proj) in placed:
        after.setdefault(pid, set()).add(proj)
    for pid in moved:                                    # 뺀 사람은 원래 없던 사업에 들어가야 한다
        if not (after.get(pid, set()) - before[pid]):
            raise ValueError(f"옮긴 {pid}가 새 사업에 들어가지 않았다")
    jdx = graph.project_index
    for proj, n in team.items():                         # 진행 중 사업은 인원이 늘지 않는다(빈 정원까지는 메울 수 있다)
        cap = max(n, sum(graph.projects[jdx[proj]].grade_headcount.values())) if proj in jdx else n
        if sum(1 for (_, pj) in placed if pj == proj) > cap:
            raise ValueError(f"진행 중 사업 {proj}의 인원이 늘었다")


def _build_and_verify(rec: dict):
    from api.datasets import build_active, bundle_version
    from api.demo_precomputed import Precomputed, _close, operating_key
    from api.cache import ResultCache
    from core.evaluate.plan_eval import evaluate_plan
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.scoring.engine import ScoringEngine

    ds_rec = rec["dataset"]
    with tempfile.TemporaryDirectory(prefix="teamweaver-record-") as tmp:
        root = Path(tmp) / "bundle"
        root.mkdir()
        for name, text in ds_rec["files"].items():
            (root / name).write_text(text, encoding="utf-8")
        content = bundle_version(root)
        if content != ds_rec["content_version"]:
            raise RecordError("원천 데이터가 기록 당시와 다르다(파일이 고쳐졌다) -- 받아들이지 않는다.")
        bundle, report = load_bundle(root)
        try:
            ds, parsed = to_dataset(bundle, report)
        except ValueError:
            errs = "; ".join(f"{e.file}: {e.message}" for e in report.errors[:3]) if report.errors else "변환 실패"
            raise RecordError(f"원천 데이터가 지금 입력 계약을 통과하지 못한다: {errs}") from None
        rj = rec.get("review_judgments")
        recorded = None
        if rj is None and parsed:
            pass                                          # 항목 점수(규칙 판정) 그대로 -- judge=False
        if rj is not None:
            pol = rj["polarity"]
            if len(pol) != len(parsed) or not all(isinstance(v, (int, float)) and -1.0 <= v <= 1.0 for v in pol):
                raise RecordError("평가 글 판정값이 리뷰 수와 맞지 않거나 범위(-1~1)를 벗어난다.")
            recorded = (str(rj.get("model") or "recorded"), [float(v) for v in pol])
        manifest = bundle.manifest
        synthetic = manifest.get("synthetic") if isinstance(manifest.get("synthetic"), bool) else None
        active = build_active(ds, parsed, dataset_id=str(manifest.get("dataset_id", "record")), version=content,
                              source="record", synthetic=synthetic, manifest=manifest, bundle=bundle,
                              judge=False, recorded_judgments=recorded)
    active.record_files = dict(ds_rec["files"])
    active.record_title = rec["title"]
    try:
        return _verify_plans(rec, active, manifest)
    except BaseException:
        active.retire()                                    # 만든 데이터셋(메모리 SQLite)을 닫는다(폴백 리뷰 S1)
        raise


def _verify_plans(rec: dict, active, manifest: dict):
    from api.cache import ResultCache
    from api.demo_precomputed import Precomputed, _close, operating_key
    from core.evaluate.plan_eval import evaluate_plan
    from core.scoring.engine import ScoringEngine

    req = rec["request"]
    try:
        params = _params_from_request(req.get("milp_params") or {})
    except (TypeError, ValueError) as exc:
        raise RecordError(f"배치 설정을 읽지 못했다: {str(exc)[:200]}") from None
    weights = {str(k): int(v) for k, v in (req.get("weights") or {}).items()}
    if any(not 1 <= v <= 5 for v in weights.values()):
        raise RecordError("기술 가중치는 1~5다.")
    n_alt = int(req.get("n_alternatives", 3))
    if not 0 <= n_alt <= 6:
        raise RecordError("대안 수는 0~6이다.")
    graph = active.graph
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(weights), eng.synergy_matrix()
    plans, score_changed = [], []
    labels = set()
    for raw in rec["optimize"]["plans"]:
        entries = [AssignEntry(**e) for e in raw["entries"]]
        label = str(raw.get("label") or "")
        if not re.fullmatch(r"[A-G]", label) or label in labels:
            raise RecordError(f"편성안 이름이 올바르지 않다({label[:8]!r}).")
        labels.add(label)
        ev = evaluate_plan(graph, S, C, params, entries)
        if ev.violations:
            raise RecordError(f"안 {label}: 지금 평가기로 다시 채점하면 제약 위반이 {len(ev.violations)}건이다 "
                              f"(예: {ev.violations[0].message}) -- 받아들이지 않는다.")
        recorded_score = raw.get("eval_objective")
        if recorded_score is None or not _close(ev.objective.total, float(recorded_score)):
            score_changed.append({"label": label,
                                  "recorded": round(float(recorded_score), 4) if recorded_score is not None else None,
                                  "current": round(ev.objective.total, 4)})
        # 화면·서명에 나가는 값은 이 서버가 계산한 것만(위 docstring 4)
        plans.append(PlanAssignment(entries=entries, objective=ev.objective.total, label=label, violations=[],
                                    unfilled=[f"{s.project_id}:{s.grade}:{s.missing}명 미충원" for s in ev.shortfalls],
                                    time_limited=False, termination="record", best_bound=None, gap_used=None))
    version = active.info.version
    pre = Precomputed(preset=rec["title"], computed_at=str(rec["optimize"].get("computed_at")
                                                           or rec.get("exported_at") or ""), label=rec["title"])
    pre.plans = plans
    stop = rec["optimize"].get("stop_reason")
    pre.stop_reason = stop if stop in ("time_limit", "user_stopped") else None
    pre.optimize_key = ResultCache.key(weights, params, n_alt, version)
    op = rec.get("operating")
    if op:
        try:
            oparams = MilpParams(**op["milp_params"])
            S0 = eng.skill_matrix({})
            ks = sorted(int(k) for k in op["ks"])
            if sorted(int(r["k"]) for r in op["rows"]) != ks:
                raise ValueError("K 행이 맞지 않다")
            from core.evaluate.operating import _gains, _row
            rows = []
            for row in op["rows"]:
                clean = {k: row[k] for k in ("k", "accepted", "elapsed_s", "carried_from_k") if k in row}
                if row.get("accepted"):
                    if not row.get("entries"):
                        raise ValueError(f"K={row['k']} 채택 행인데 배치 기록이 없다")
                    entries = [AssignEntry(**e) for e in row["entries"]]
                    ev = evaluate_plan(graph, S0, C, oparams, entries)
                    if (sorted(v.code for v in ev.violations) != sorted(row.get("violations", []))
                            or not _close(ev.objective.total, row["objective"], abs_=1e-3)):
                        raise ValueError(f"K={row['k']} 다시 채점 불일치")
                    try:
                        _check_operating_row(graph, active.current, entries, int(row["k"]))
                    except ValueError as exc:
                        raise ValueError(f"K={row['k']} {exc}") from None
                    # 화면에 보이는 값(목적·품질·빈자리·항목별·사업별·이동)은 모두 이 서버가 명단으로 다시 계산한다(Codex 리뷰 P2)
                    clean.update(_row(graph, S0, C, oparams, active.current, entries,
                                      [f"{s.project_id}:{s.grade}:{s.missing}명 미충원" for s in ev.shortfalls]))
                else:
                    clean["violations"] = [str(v)[:40] for v in row.get("violations", [])][:20]
                # 증명 값은 이 서버가 확인할 수 없다(docstring 4) -- 서명 경로(routes/operating._signed)가 세탁하지 않게
                clean.update({"termination": "record", "best_bound": None, "proven_optimal": False})
                rows.append(clean)
            base = next((r for r in rows if r.get("k") == 0 and r.get("accepted")), None)
            for r in rows:
                if r.get("accepted"):
                    _gains(r, base)
            pre.operating = {"key": operating_key(oparams, ks, version), "ks": ks, "rows": rows,
                             "computed_at": str(op.get("computed_at") or pre.computed_at),
                             "milp_params": request_fields(oparams)}
        except (KeyError, TypeError, ValueError) as exc:
            pre.skipped.append(f"운영 중 비교는 쓰지 않는다: {str(exc)[:120]}")
    facts = {"people": active.info.people, "projects": active.info.projects, "synthetic": active.info.synthetic,
             "dataset_id": active.info.dataset_id, "horizon_start": manifest.get("horizon_start"),
             "horizon_months": manifest.get("horizon_months"),
             "kind": "operating" if (active.scenario or {}).get("scenario") == "operating" else "planning"}
    check = {"plans": len(plans), "score_changed": score_changed, "operating": pre.operating is not None,
             "skipped": pre.skipped, "dataset_version": version, "facts": facts}
    return active, pre, check


# --- 내보내기 ---------------------------------------------------------------

def files_of(app, active) -> dict[str, str]:
    """지금 작업 데이터의 원천 CSV 묶음(원문). 만들 수 없으면 RecordError."""
    files = getattr(active, "record_files", None)
    if files:
        return dict(files)
    from api.datasets import _ALLOWED, extract_bundle_zip
    src = active.info.source
    root_files: dict[str, str] = {}

    def read_dir(root: Path) -> None:
        for p in sorted(root.iterdir()):
            if p.is_file() and (p.name in _ALLOWED or p.name == "manifest.json"):
                root_files[p.name] = p.read_text("utf-8")
    if src == "upload":
        saved = app.state.dataset_store.load()
        if saved is None or saved[0].get("version") != active.info.content_version:
            raise RecordError("지금 업로드 데이터의 저장본이 없어 내보낼 수 없다(다시 올릴 것).")
        with tempfile.TemporaryDirectory(prefix="teamweaver-export-") as tmp:
            read_dir(extract_bundle_zip(saved[1], Path(tmp) / "bundle"))
    elif src == "demo-bundle":
        from api.demos import current_demo_root
        root = getattr(app.state, "demo_active_root", None) or current_demo_root()
        if root is None:
            raise RecordError("시작 데이터 묶음 위치를 찾지 못했다.")
        root = Path(root)
        if root.suffix.lower() == ".zip":
            with tempfile.TemporaryDirectory(prefix="teamweaver-export-") as tmp:
                read_dir(extract_bundle_zip(root.read_bytes(), Path(tmp) / "bundle"))
        else:
            read_dir(root)
    else:
        raise RecordError("기본 고정 데이터는 CSV 원천이 없어 편성 기록으로 내보낼 수 없다.")
    return root_files


def export_record(app, active, **kw) -> dict:
    """화면이 보낸 안(서버 서명 포함)을 확인하고 기록을 만든다. 서명이 하나라도 맞지 않으면 RecordError.
    형이 틀린 값은 500이 아니라 RecordError(폴백 리뷰 S1)."""
    try:
        return _export_record(app, active, **kw)
    except RecordError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordError(f"내보낼 안의 모양이 맞지 않다({type(exc).__name__}: {str(exc)[:160]})") from None


def _export_record(app, active, *, title: str, weights: dict, milp_params: dict, n_alternatives: int,
                   plans: list[dict], stop_reason: str | None = None) -> dict:
    from api.plan_token import verify_evidence, verify_plan
    from core.evaluate.plan_eval import evaluate_plan
    from core.scoring.engine import ScoringEngine
    title = title.strip()
    if not title or len(title) > TITLE_MAX:
        raise RecordError("제목을 1~120자로 적는다.")
    params = _params_from_request(milp_params)
    version = active.info.version
    eng = ScoringEngine(active.graph)
    S, C = eng.skill_matrix(weights), eng.synergy_matrix()
    out_plans = []
    for p in plans:
        entries = [AssignEntry(**e).model_dump() for e in p.get("entries", [])]
        if not verify_plan(p.get("plan_token", ""), version, str(p.get("label")), entries, weights, params):
            raise RecordError(f"안 {p.get('label')}: 이 서버가 지금 데이터로 계산한 결과가 아니다(서명 불일치).")
        evidence = p.get("evidence") or {}
        if not verify_evidence(p.get("evidence_token", ""), p["plan_token"], evidence):
            raise RecordError(f"안 {p.get('label')}: 계산 신뢰도 서명이 맞지 않다.")
        ev = evaluate_plan(active.graph, S, C, params, [AssignEntry(**e) for e in entries])
        out_plans.append({"label": p["label"], "entries": entries, "objective": evidence.get("objective", p.get("objective")),
                          "unfilled": [f"{s.project_id}:{s.grade}:{s.missing}명 미충원" for s in ev.shortfalls],
                          "violations": [],
                          "time_limited": bool(evidence.get("time_limited")), "termination": evidence.get("termination"),
                          "best_bound": evidence.get("best_bound"), "gap_used": evidence.get("gap_allowed"),
                          "eval_objective": ev.objective.total})
    if not out_plans:
        raise RecordError("내보낼 편성안이 없다 -- 먼저 계산하거나 기록을 연다.")
    parsed = active.parsed_reviews
    judgments = None
    if parsed is not None and active.info.review_judge in ("llm", "record"):
        judgments = {"model": active.info.judge_model, "polarity": [float(p.text_polarity) for p in parsed]}
    files = files_of(app, active)
    manifest = json.loads(files["manifest.json"])
    op_state = getattr(app.state, "precomputed_operating", None)
    operating = None
    if op_state and op_state.get("milp_params"):
        operating = {"milp_params": _params_from_request(op_state["milp_params"]).model_dump(),
                     "ks": op_state["ks"], "rows": op_state["rows"], "computed_at": op_state.get("computed_at")}
    return {
        "format": FORMAT, "version": VERSION, "title": title,
        "kind": (active.scenario or {}).get("scenario") or "planning",
        "exported_at": _now(),
        "dataset": {"dataset_id": active.info.dataset_id, "content_version": active.info.content_version,
                    "synthetic": active.info.synthetic, "people": active.info.people, "projects": active.info.projects,
                    "horizon_start": manifest.get("horizon_start"), "horizon_months": manifest.get("horizon_months"),
                    "files": files},
        "review_judgments": judgments,
        "request": {"weights": weights, "milp_params": milp_params, "n_alternatives": n_alternatives},
        "optimize": {"plans": out_plans, "stop_reason": stop_reason if stop_reason in ("time_limit", "user_stopped") else None,
                     "computed_at": _now()},
        "operating": operating,
    }


# --- 저장소 -----------------------------------------------------------------

class RecordStore:
    """`<data>/records/<id>.json`(0600) + `index.json`. 열어 둔 기록은 `active.json`(재기동해도 그 기록으로 뜬다).
    목록 읽고-고치고-쓰기는 잠금 안에서(폴백 리뷰 N1: 동시에 불러오면 항목이 사라지고 상한을 넘었다)."""

    _lock = threading.Lock()

    def __init__(self, root: Path):
        self.dir = root / "records"

    def _index_path(self) -> Path:
        return self.dir / "index.json"

    def _read_index(self) -> list[dict]:
        try:
            data = json.loads(self._index_path().read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write_index(self, items: list[dict]) -> None:
        from api.storage import atomic_write, ensure_private_dir
        ensure_private_dir(self.dir)
        atomic_write(self._index_path(), json.dumps(items, ensure_ascii=False).encode("utf-8"))

    def list(self) -> list[dict]:
        active = self.active_id()
        return [{**i, "open": i["id"] == active} for i in self._read_index()]

    def save(self, rec: dict, check: dict) -> dict:
        from api.storage import atomic_write, ensure_private_dir
        with self._lock:
            items = self._read_index()
            if len(items) >= MAX_RECORDS:
                raise RecordError(f"편성 기록은 {MAX_RECORDS}개까지 둔다 -- 오래된 기록을 지운 뒤 다시 불러온다.")
            ensure_private_dir(self.dir)
            rid = secrets.token_hex(8)
            atomic_write(self.dir / f"{rid}.json", json.dumps(rec, ensure_ascii=False).encode("utf-8"))
            item = summary(rec, check, id=rid, imported_at=_now(), score_changed=check.get("score_changed", []))
            self._write_index([item, *items])
            return item

    def load(self, rid: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{16}", rid or ""):
            raise KeyError(rid)
        try:
            return json.loads((self.dir / f"{rid}.json").read_text("utf-8"))
        except FileNotFoundError:
            raise KeyError(rid) from None

    def raw(self, rid: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{16}", rid or ""):
            raise KeyError(rid)
        try:
            return (self.dir / f"{rid}.json").read_bytes()
        except FileNotFoundError:
            raise KeyError(rid) from None

    def delete(self, rid: str) -> None:
        with self._lock:
            items = self._read_index()
            if not any(i["id"] == rid for i in items):
                raise KeyError(rid)
            if self.active_id() == rid:
                raise RecordError("지금 열려 있는 기록은 지울 수 없다 -- 다른 데이터로 바꾼 뒤 지운다.")
            self._write_index([i for i in items if i["id"] != rid])
            (self.dir / f"{rid}.json").unlink(missing_ok=True)

    def active_id(self) -> str | None:
        try:
            rid = json.loads((self.dir / "active.json").read_text("utf-8")).get("id")
            return rid if isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{16}", rid) else None
        except (OSError, ValueError, AttributeError):
            return None

    def set_active(self, rid: str) -> None:
        from api.storage import atomic_write, ensure_private_dir
        ensure_private_dir(self.dir)
        atomic_write(self.dir / "active.json", json.dumps({"id": rid}).encode("utf-8"))

    def clear_active(self) -> None:
        (self.dir / "active.json").unlink(missing_ok=True)
