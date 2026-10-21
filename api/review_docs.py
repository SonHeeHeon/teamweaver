"""인사팀 검토 자료(사업별 소명 자료) -- 만들기·저장(2026-10-19, claude-a, 사용자 요청. 설계 .omc/plan/2026-10-19-review-doc.md).

신규 사업 편성·긴급 투입 **확정 한 건 × 그 확정으로 사람이 새로 들어간 사업 하나**마다 한 편.
- 시스템 사실(잠김): 지금 소명 사실(core.kg.justify) + 옮겨 온 인력의 득실(core.evaluate.review_moves). 처음 만들 때 계산해 자료에 저장한다
  -- 나중 판에서 옛 판을 다시 만들려면 묶음 전체를 다시 변환·판정해야 해서(설계 4절). 그래서 **그 확정이 지금 켜진 판일 때만** 만든다.
- 본문: AI 초안(부서장이 누를 때만) → 부서장이 고친 글. AI 원본은 남긴다.
- 부서장 의견: 사실마다 해명·이의(사실 지문 `fact_key`에 붙인다), 옮겨 온 사람마다 판단·합의, 긴급 투입 상황 메모.
결론("얻는 것이 더 크다")은 서버가 만들지 않는다(사용자 결정 -- 판단은 부서장 몫, 숫자는 계산상 비교일 뿐).

저장: `<데이터 폴더>/review_docs/<확정 판 버전>/<사업 ID>.json`. 데이터가 바뀌어도 지우지 않는다(결재 자료). 실데이터라면 사번·평가 사실이
들어가므로 업로드 데이터와 같은 비공개 폴더 규칙(ensure_private_dir·atomic_write)을 따른다.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from api.storage import atomic_write, data_dir, ensure_private_dir

DISCLAIMER = "계산 숫자는 같은 평가 기준의 계산상 비교이며 실제 성과를 보장하지 않는다(NOT_CALIBRATED)."
CONFIRM_KINDS = {"apply_auto": "new", "apply_manual": "new", "apply_emergency": "emergency"}
AGREEMENT = ("agreed", "requested", "hr_assigned")
_SAFE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")      # 점으로 시작하는 이름(..) 금지(폴백 리뷰 P3)
_LOCK = threading.Lock()


class ReviewDocError(Exception):
    """사용자에게 그대로 보일 수 있는 거절 사유(라우트가 409/404로 바꾼다)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- 저장 ---------------------------------------------------------------------------------------------------------

class ReviewDocStore:
    def __init__(self, root: Path | None = None):
        self.dir = (root if root is not None else data_dir()) / "review_docs"

    def _path(self, version: str, project_id: str) -> Path:
        if not _SAFE.match(version) or not _SAFE.match(project_id):
            raise ReviewDocError("잘못된 자료 이름")
        return self.dir / version / f"{project_id}.json"

    def get(self, version: str, project_id: str) -> dict | None:
        try:
            return json.loads(self._path(version, project_id).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, doc: dict) -> None:
        p = self._path(doc["confirm"]["version"], doc["project_id"])
        ensure_private_dir(p.parent)
        atomic_write(p, json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))

    def all(self) -> list[dict]:
        out = []
        if not self.dir.exists():
            return out
        for p in sorted(self.dir.glob("*/*.json")):
            try:
                out.append(json.loads(p.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def create(self, doc: dict) -> dict:
        """없을 때만 쓴다(한 번에 하나) -- 동시에 두 번 만들면 늦은 쪽이 그 사이 편집을 덮던 경쟁(폴백 리뷰 P3)."""
        with _LOCK:
            existing = self.get(doc["confirm"]["version"], doc["project_id"])
            if existing is not None:
                return existing
            self.put(doc)
            return doc

    def update(self, version: str, project_id: str, fn) -> dict:
        """읽고 fn(doc)로 고쳐 쓴다(한 번에 하나). fn이 ReviewDocError를 올리면 쓰지 않는다."""
        with _LOCK:
            doc = self.get(version, project_id)
            if doc is None:
                raise KeyError(project_id)
            fn(doc)
            self.put(doc)
            return doc


# --- 확정 이력 ----------------------------------------------------------------------------------------------------

def _rows_to_entries(files: dict) -> list:
    from api.workspace_edits import current_table
    from core.optimize.types import AssignEntry
    _, rows = current_table(files)
    return [AssignEntry(person_id=r["person_id"], project_id=r["project_id"], alloc=float(r["alloc"])) for r in rows]


def confirms(history_entries: list[dict]) -> list[dict]:
    """확정 이력(신규 사업 편성·긴급 투입)을 오래된 것부터: {version, content_version, before_content_version, kind, at, note}."""
    out = []
    for i, e in enumerate(history_entries):
        kind = CONFIRM_KINDS.get(e.get("kind", ""))
        if kind and i > 0:
            out.append({"version": e["version"], "content_version": e["content_version"],
                        "before_version": history_entries[i - 1].get("version"),
                        "before_content_version": history_entries[i - 1]["content_version"],
                        "kind": kind, "at": e.get("at"), "note": e.get("note", ""),
                        "situation": (e.get("summary") or {}).get("situation", ""),
                        # 등급 대체와 사유(2026-10-21) -- 그 사업 자료의 MOVE_GRADE 사실
                        "grade_swaps": (e.get("summary") or {}).get("grade_swaps") or []})
    return out


_ENTRIES_CACHE: dict[str, list] = {}       # 판 content_version → 현재 배치(판은 바뀌지 않는다 -- 목록을 열 때마다 zip을 풀지 않게)


def _entries_of_version(history, content_version: str) -> list:
    from api.workspace import _read_zip
    if content_version not in _ENTRIES_CACHE:
        blob = history.load_zip(content_version)
        if blob is None:
            raise ReviewDocError("이 확정의 보관 판이 없다(최근 20판만 보관한다).")
        if len(_ENTRIES_CACHE) > 64:
            _ENTRIES_CACHE.clear()
        _ENTRIES_CACHE[content_version] = _rows_to_entries(_read_zip(blob))
    return list(_ENTRIES_CACHE[content_version])


def confirm_entries(history, confirm: dict, graph=None) -> tuple[list, list]:
    """확정 직전·확정한 판의 현재 배치(보관된 판 zip에서). 보관본이 없으면 ReviewDocError.
    graph를 주면 그 그래프에 있는 사람·사업 행만 남긴다 -- 원천 CSV에는 변환이 버리는 행(계획 기간 밖 사업 등)이 남아 있을 수 있다(폴백 리뷰 P2)."""
    before = _entries_of_version(history, confirm["before_content_version"])
    after = _entries_of_version(history, confirm["content_version"])
    if graph is not None:
        ok = lambda e: e.person_id in graph.pid_index and e.project_id in graph.project_index   # noqa: E731
        before, after = [e for e in before if ok(e)], [e for e in after if ok(e)]
    return before, after


def _undone_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / "review_docs" / "undone.json"


def undone_versions(root: Path | None = None) -> set[str]:
    try:
        data = json.loads(_undone_path(root).read_text("utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(v) for v in data} if isinstance(data, list) else set()


def record_undone(version: str, root: Path | None = None) -> None:
    """되돌리기(api.workspace.undo)가 꺼낸 판 버전을 덧붙인다(지우지 않는다). 쓰기 실패는 되돌리기를 막지 않게 올리지 않는다."""
    with _LOCK:
        seen = undone_versions(root)
        if version in seen:
            return
        p = _undone_path(root)
        try:
            ensure_private_dir(p.parent)
            atomic_write(p, json.dumps(sorted(seen | {version}), ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass


def _cancelled_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / "review_docs" / "cancelled.json"


def cancelled_records(root: Path | None = None) -> set[tuple[str, str]]:
    """확정 취소한 확정 (판 버전, 확정 시각) -- 이력 20판 밖으로 밀려도 남는 기록(2026-10-21 폴백 리뷰 S3)."""
    try:
        data = json.loads(_cancelled_path(root).read_text("utf-8"))
    except (OSError, ValueError):
        return set()
    return {(str(v), str(a)) for v, a in data if isinstance(v, str)} if isinstance(data, list) else set()


def record_cancelled(version: str, at: str, cancelled: bool, root: Path | None = None) -> None:
    """확정 취소를 남기거나(취소) 지운다(그 취소 판을 되돌리기로 꺼냄). 쓰기 실패는 판 변경을 막지 않게 올리지 않는다."""
    with _LOCK:
        seen = cancelled_records(root)
        key = (str(version), str(at))
        if (key in seen) == cancelled:
            return
        seen = seen | {key} if cancelled else seen - {key}
        p = _cancelled_path(root)
        try:
            ensure_private_dir(p.parent)
            atomic_write(p, json.dumps(sorted(seen), ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass


def is_cancelled(history_entries: list[dict], entry: dict) -> bool:
    """이 확정 항목이 취소됐는가: 이력의 확정 취소 판(api.workspace.cancelled_keys -- 판 버전 + 확정 시각) 또는 오래 남는 기록."""
    from api.workspace import cancelled_keys
    key = (str(entry.get("version")), str(entry.get("at")))
    return key in cancelled_keys(history_entries) or key in cancelled_records()


def confirm_cancelled(history_entries: list[dict], version: str) -> bool:
    """그 판 버전의 확정이 이력에 있고 모두 취소됐는가(화면 꼬리표 "확정 취소됨")."""
    live = [e for e in history_entries if e.get("kind") in CONFIRM_KINDS and e.get("version") == version]
    if live:
        return all(is_cancelled(history_entries, e) for e in live)
    return any(v == version for v, _ in cancelled_records())          # 이력 밖으로 밀린 취소된 확정(2차 리뷰 NIT)


def history_state(history_entries: list[dict], confirm: dict) -> str:
    """확정의 지금 상태: "in_history"(이력에 있음) · "reverted"(되돌리기 기록에 있고 지금 이력에는 없음) ·
    "out_of_history"(다른 데이터로 바꿨거나 20판 밖으로 밀려남 -- 되돌린 것이 아니므로 열고 고치고 내보낼 수 있다).
    이력에 남은 판으로 되돌림을 짐작하면 두 방향으로 틀렸다(폴백 리뷰 2라운드) -- 실제 되돌리기 기록으로만 판정한다.
    같은 명단을 다시 확정하면 같은 판 버전이 이력에 돌아오므로 "in_history"가 된다."""
    # 확정 항목(apply_*)으로 있을 때만 "이력에 있음" -- 되돌린 뒤 같은 데이터를 다시 열어 생긴 시작 판(base)은 재확정이 아니다(Codex Stop 훅)
    live = [e for e in history_entries if e.get("kind") in CONFIRM_KINDS and e.get("version") == confirm["version"]]
    if live:
        # 확정 취소(2026-10-21)는 판을 꺼내지 않고 위에 쌓는다 -- 이력에 남아 있어도 취소됐으면 되돌린 것과 같이 잠근다
        # (같은 명단을 다시 확정해 취소되지 않은 확정이 하나라도 있으면 살아 있다)
        return "in_history" if any(not is_cancelled(history_entries, e) for e in live) else "reverted"
    if (str(confirm.get("version")), str(confirm.get("at"))) in cancelled_records():
        return "reverted"                       # 이력 밖으로 밀린 취소된 확정도 잠근다
    return "reverted" if confirm["version"] in undone_versions() else "out_of_history"


# --- 사실 ---------------------------------------------------------------------------------------------------------

def _fact(kind: str, text: str, owners: tuple = (), *, adverse: bool, section: str, skill: str | None = None) -> dict:
    from core.kg.justify import Fact, fact_key
    f = Fact("F0", kind, text, owners=owners, skill=skill)
    return {"key": fact_key(f), "kind": kind, "text": text, "adverse": bool(adverse), "owner": owners[0] if owners else None,
            "section": section}


def _skills_text(skills: list[dict]) -> str:
    return ", ".join(f"{s['skill']} 충족 {s['met_before']}→{s['met_after']}명(필요 {s['headcount']}명)" for s in skills)


def _num(x: float) -> str:
    return f"{x:.2f}"


def move_facts(moves: dict, evidence: dict, grants: dict | None = None) -> list[dict]:
    """옮겨 온 인력 카드의 잠긴 사실(설계 2.2). 원래 사업이 잃는 줄은 불리한 사실이다."""
    out: list[dict] = []
    target = moves["target"]
    g = moves["target_gain"]
    line = f"이 확정으로 {target} 사업 점수 {_num(g['score_before'])} → {_num(g['score_after'])}"
    if g["skills"]:
        line += f", 요구 기술 {_skills_text(g['skills'])}"
    line += f", 등급 정원 빈자리 {g['unfilled_before']} → {g['unfilled_after']}석(확정 뒤 정원·요구 기준)"
    out.append(_fact("MOVE_TARGET", line, adverse=g["score_after"] < g["score_before"] or g["unfilled_after"] > g["unfilled_before"],
                     section="move"))
    met_by = {m["person_id"]: [c["skill"] for c in m.get("requirements", []) if c.get("status") == "met"]
              for m in evidence.get("members", [])}
    for j in moves["joined"]:
        p = j["person_id"]
        mine = met_by.get(p) or []
        grant = (grants or {}).get(p) if j["origin"] == "partly_free" else None
        origin = ("프로젝트 동시 수행(원래 사업 유지)" if grant else
                  {"bench": "대기 인력", "partly_free": "일부 여유(다른 배치는 그대로)", "moved": "진행 사업에서 옮겨 옴",
                   "follow_on": "지금 사업이 끝난 뒤 이어서 투입"}[j["origin"]])
        out.append(_fact("MOVE_WHO", f"{p}({j['grade']}): {origin}, {target} 투입률 {j['alloc']:g}"
                         + (f", 이 사업 요구 기술 중 충족: {', '.join(mine)}" if mine else ", 이 사업 요구 기술 중 충족하는 것 없음"),
                         (p,), adverse=not mine, section="move"))
        if grant:
            # 부서장이 사람 단위로 허용한 동시 수행(2026-10-19) -- 사유·메모·시각을 잠긴 사실로(검토자가 근거를 본다)
            memo = f" · {grant['memo']}" if grant.get("memo") else ""
            when = f" ({grant['granted_at']})" if grant.get("granted_at") else ""
            out.append(_fact("MOVE_CONCURRENT", f"{p}: 프로젝트 동시 수행 허용 — 사유 {grant.get('reason')}{memo}{when}, "
                             f"같은 달 최대 {grant.get('max_projects')}개 사업(사업마다 100%)", (p,), adverse=False, section="move"))
        for s in j["sources"]:
            src = s["project_id"]
            how = "그 사업에서 빠짐" if s["left"] else f"투입률만 줄어듦({s['alloc_before']:g} → {s['alloc_after']:g})"
            out.append(_fact("MOVE_FROM", f"{p}: {s['project_name']}({src})에서 옮겨 옴 — {how}", (p, src),
                             adverse=False, section="move"))
            loss = f"{src} 사업(이 확정 전체로 인한 변화): 점수 {_num(s['score_before'])} → {_num(s['score_after'])}"
            if s["skills"]:
                loss += f", 요구 기술 {_skills_text(s['skills'])}"
            loss += f", 등급 정원 빈자리 {s['unfilled_before']} → {s['unfilled_after']}석"
            worse = (s["score_after"] < s["score_before"] - 1e-9 or s["unfilled_after"] > s["unfilled_before"]
                     or any(k["met_after"] < k["met_before"] for k in s["skills"]))
            out.append(_fact("MOVE_LOSS", loss, (p, src), adverse=worse, section="move"))
            if s["backfill"]:
                fill = ", ".join(f"{b['person_id']}({b['grade']}, {'대기 인력' if b['from_bench'] else '다른 사업에서'})"
                                 for b in s["backfill"])
                fill_text, bad = f"{src}의 자리를 같은 확정에서 {fill}이(가) 채움", False
            elif s["unfilled_after"] > s["unfilled_before"]:
                fill_text, bad = f"{src}의 자리는 채우지 않아 빈자리로 남음", True
            else:
                fill_text, bad = f"{src}에 새로 생긴 빈자리 없음(이 확정에서 메운 사람 없음)", False
            out.append(_fact("MOVE_FILL", fill_text, (p, src), adverse=bad, section="move"))
    return out


def grade_swap_facts(confirm: dict, project_id: str) -> list[dict]:
    """이 확정의 등급 대체(2026-10-21 사용자 결정 "위 아래 모두 허용하고, 사유는 전부 받고") -- 잠긴 사실. 아래 등급으로 채운 것은
    검토자가 볼 "확인할 사실"(불리), 위 등급은 비용만 늘어 참고. 결론은 쓰지 않는다(부서장 판단 칸)."""
    from core.evaluate.grade_swap import ORDER
    out = []
    for s in confirm.get("grade_swaps") or []:
        if s.get("project_id") != project_id:
            continue
        down = ORDER.index(s["grade"]) > ORDER.index(s["seat"])
        out.append(_fact("MOVE_GRADE", f"{project_id}: 등급 대체 — {s['seat']} 자리 {s['count']}개를 {s['grade']}으로 채움"
                         f"({'아래' if down else '위'} 등급), 사유 {s['reason']}. 이 확정으로 정원 {s['seat']} −{s['count']} · "
                         f"{s['grade']} +{s['count']}", adverse=down, section="move"))
    return out


def base_facts(inp, *, focus_people: set[str] | None) -> list[dict]:
    """지금 소명 사실(core.kg.justify). 불리한 사실은 "확인할 사실", 나머지는 부록. 긴급 투입은 새로 들어온 사람 사실만 자세히,
    기존 팀원은 부록에 등급·투입률(MEM)만."""
    from core.kg.justify import GROUP_KINDS, fact_key
    out = []
    for f in inp.facts:
        owner = f.owners[0] if f.owners else None
        if focus_people is not None and f.kind not in GROUP_KINDS and owner not in focus_people and f.kind != "MEM" \
                and not (f.kind == "CW" and any(o in focus_people for o in f.owners)):
            continue
        out.append({"key": fact_key(f), "kind": f.kind, "text": f.text, "adverse": bool(f.adverse), "owner": owner,
                    "section": "adverse" if f.adverse else "appendix"})
    return out


# --- 만들기 -------------------------------------------------------------------------------------------------------

def build_doc(*, dataset, kg, params, confirm: dict, project_id: str, before: list, after: list) -> dict:
    """확정 한 건 × 사업 하나의 새 자료(편집 없음). dataset은 확정한 판이어야 한다(지식 그래프·이름)."""
    from api.routes.justification import build_input, display_names
    from core.evaluate.review_moves import mover_effects
    from core.scoring.engine import ScoringEngine
    eng = ScoringEngine(dataset.graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    inp = build_input(kg, project_id, after, dataset.graph, S, C, params)
    moves = mover_effects(dataset.graph, S, C, params, kg, before, after, project_id)
    joined = {j["person_id"] for j in moves["joined"]}
    grants = dataset.scenario.get("concurrency") or {}
    for j in moves["joined"]:                  # 화면 표시용: 동시 수행 허용으로 들어온 사람
        if j["origin"] == "partly_free" and j["person_id"] in grants:
            j["concurrency"] = grants[j["person_id"]]
    facts = (move_facts(moves, inp.evidence, grants) + grade_swap_facts(confirm, project_id)
             + base_facts(inp, focus_people=joined if confirm["kind"] == "emergency" else None))
    names = display_names(dataset)             # 사실·본문에 나오는 모든 사람(대안 후보 포함)을 같은 이름으로(폴백 리뷰 P3)
    project = dataset.graph.projects[dataset.graph.project_index[project_id]]
    # 검토 자료 안에서는 상황 글을 상황 메모(부서장이 고치는 칸) 한 곳에만 둔다 -- 고치거나 지운 뒤에도 confirm에 원문이 따로
    # 남지 않게(폴백 리뷰 S1). 확정 note(머리말+잘린 글)와 이력 summary.situation(원천)은 변경 기록이라 그대로다
    return {"confirm": {k: v for k, v in confirm.items() if k != "situation"}, "project_id": project_id, "project_name": project.name, "revision": 0, "created_at": now(),
            "facts": facts, "moves": moves, "team": list(inp.team),
            # 만들 때의 평가 설정 -- AI 초안도 같은 기준으로 쓴다(설정을 바꾼 뒤 초안이 잠긴 사실과 다르게 쓰였다, Codex 리뷰)
            "milp_params": params.model_dump(mode="json"), "names": dict(sorted(names.items())),
            "disclaimer": DISCLAIMER, "body_ai": None, "body_ai_method": None, "body": None, "notes": [], "judgments": {},
            # 긴급 투입 화면에 적은 상황 글을 처음 값으로(부서장 점검 f6 -- 같은 말을 두 번 적었다). 부서장이 고칠 수 있고 AI에는 보내지 않는다
            "situation_memo": confirm.get("situation", "") if confirm["kind"] == "emergency" else "",
            "status": "draft", "exported_at": None, "exported_facts": None}


def view(doc: dict, *, reverted: bool, active: bool, history: str = "in_history", cancelled: bool = False) -> dict:
    """화면·PDF로 보내는 모양: 해명·이의마다 근거 사실이 지금 자료에 있는지(orphan), 확정이 되돌려졌는지."""
    keys = {f["key"] for f in doc["facts"]}
    notes = [{**n, "orphan": n["key"] not in keys} for n in doc.get("notes", [])]
    return {**doc, "notes": notes, "reverted": reverted, "active": active, "history": history, "cancelled": cancelled,
            "body_edited": doc.get("body") is not None and doc.get("body") != doc.get("body_ai")}
