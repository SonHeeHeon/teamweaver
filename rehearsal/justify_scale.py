"""E7b: 인사팀 소명 글(GraphRAG) 확대 검증 -- 서비스 모듈(core/kg/justify.py + api/rag/justification.py)로 잰다.

사전 등록(이 docstring과 아래 상수가 기준이다 -- 측정 전에 커밋해 Git에 남긴다. 로컬 계획 .omc/plan/2026-10-09-graphrag-justification.md는 참고):
- G1(필수): 판별 가능한(만들었고 우연히 참이 아닌) 주입 오류 100% 탐지 **그리고** 채운 글 무결성 실패 0(중괄호·칩 수·칩 앞 문구·짧은 문구의 귀속을
  render와 따로 확인 -- attribution_problems). 하나라도 어기면 고친 뒤 다시 잰다.
- G2(목표): GraphRAG 채택률 ≥95% 달성, 90~95% 조건부, <90% 미달. 분모는 계획한 호출 전부(AI 호출 오류 포함 — 호출 오류 제외 채택률은 따로 보고).
  비용 상한으로 중단되면 "미완". 측정 전 조율 시험(아래 CALIBRATION, 같은 묶음 사업 일부)으로 문법·지시문을 다듬었다 -- 처음 보는 데이터의 수치가 아니다.
- G3(보고, 기준 없음): 비용·지연·3회 일관성·필수 요소 포함률·**불리한 사실 인용률**(Fact.adverse)·길이·팀 크기별 채택률·연결 말 조각 전부.
- 대상: 시연 묶음 6개(100·200·300명 × 연초 계획·운영 중)의 배치 있는 사업 전부 × RUNS회. 연초 계획 = 미리 계산 안 A, 운영 중 = 미리 계산 K 비교의 가장 큰 K 배치.
  데이터는 서비스와 같이 만든다: 리뷰 글 판정값은 판정 캐시(~/.teamweaver/review_judgments_synthetic.json)의 **임시 복사본**에서 읽는다(실제 폴더는 건드리지 않는다).
- 모델: fixtures/pricing.json briefing_model(gpt-6-luna, 추론 low) -- 서비스와 같다. 사내 GLM은 Z.ai 잔액 소진으로 제외.
- 자리표시 + 허용 목록 방식(측정 전 설계 변경 -- core/kg/justify.py): AI는 정해진 문법(주어·주제 라벨·연결어·맺음말)으로 {F#}를 엮기만 한다.
  오류 주입(AI 호출 없음)은 AI 원문에 직접 심는다: M1~M9 규칙별 구조 변형(규칙을 보고 만든 것) + H1~H7 보류 묶음(폴백 리뷰 3회째가 허용 목록
  설계 전에 쓴 거짓 통과 예문 유형 중 7개. 같은 리뷰의 "문장을 넘어 주어 잇기", "팀원 아닌 사람에게 지어낸 이유", "반면" 접두는 문법 밖이라 S2로
  구성상 잡혀 따로 넣지 않았다). M8(팀원 빠짐)·H6 소문자는 거짓 주장이 아니라 규칙·형식 위반이다.
  채운 글 무결성 = 중괄호·근거 칩 수·칩 앞 문구가 그 사실 문구·짧은 문구의 귀속(attribution_problems, render와 따로 판단).
  만들지 못한 경우는 사유를 나눈다(no_target·noop·coincidentally_true). 채운 글 무결성(중괄호·칩 수·칩 앞 문구·귀속)도 검사와 따로 확인한다.
  G1 = 판별 가능한 변형 100% 탐지 + 무결성 실패 0.
- 연결 말 감사: 채택된 글의 AI 연결 말 조각 전부를 빈도순으로 보고서에 싣는다.

  uv run python -m rehearsal.justify_scale             → rehearsal/results/justify-scale.{json,html}
  uv run python -m rehearsal.justify_scale --render-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_JSON, OUT_HTML = ROOT / "rehearsal" / "results" / "justify-scale.json", ROOT / "rehearsal" / "results" / "justify-scale.html"
BUNDLES = ("org-n100", "org-n200", "org-n300", "org-n100-operating", "org-n200-operating", "org-n300-operating")
RUNS, WORKERS, COST_CAP_USD, SEED = 3, 6, 3.0, 20261009
# 사전 판정 기준(측정 전 고정)
G1_DETECTION = 1.0                 # 판별 가능한 주입 오류 탐지율
G2_TARGET, G2_FLOOR = 0.95, 0.90   # GraphRAG 채택률: ≥0.95 달성, 0.90~0.95 조건부, <0.90 미달
TEAM_BINS = ((1, 5), (6, 10), (11, 20), (21, 999))
# 측정 전 조율 시험(사전 등록 측정 아님): 같은 시연 묶음의 사업 일부로 문법·지시문을 다듬었다 -- 채택률에 다소 낙관 편향이 있을 수 있다.
CALIBRATION = ("자리표시 + 허용 목록 문법으로 바꾼 뒤 조율 시험 60건(같은 묶음의 사업 일부, 각 1회): 100명 8/8, 300명 운영 중 12/12, 200명 10/10, "
               "200명 운영 중 7/10 채택. 탈락 3건(일본어 글자 섞임, '기술 경력' 뒤 등급 사실, 라벨 변형) 중 라벨 변형만 문법에 추가했다.")
KNOWN_LIMITS: set[str] = set()       # 자리표시 + 허용 목록: 숫자·상태는 서버가 채우므로 AI가 바꿀 수 없다


# ---------------------------------------------------------------- 재료(서비스와 같은 데이터)
def _bundle_root(name: str, tmp: Path) -> Path:
    from api.datasets import extract_bundle_zip
    d = ROOT / "demo" / name
    return d if d.exists() else extract_bundle_zip((ROOT / "demo" / f"{name}.zip").read_bytes(), tmp / name)


def inputs_for(name: str, tmp: Path, judge_cache: Path | None) -> tuple[list, dict]:
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    from core.kg.justify import justification_input
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(_bundle_root(name, tmp))
    ds, parsed = to_dataset(b, rep)
    judge = "rule"
    if judge_cache is not None:
        from api import review_judge as rj
        parsed = rj.judge_reviews(ds, parsed, cache_path=judge_cache, trim=not (b.manifest.get("synthetic") is True))
        judge = "llm(cache)"
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    kg = build_kg(b, ds, parsed)
    pre = json.loads((ROOT / "demo" / "precomputed" / f"{name}.json").read_text("utf-8"))
    if name.endswith("-operating"):
        rows = [r for r in pre["operating"]["rows"] if r.get("accepted", True) and r.get("entries")]
        row = max(rows, key=lambda r: r["k"])
        entries, source = [AssignEntry(**e) for e in row["entries"]], f"운영 중 K={row['k']} 배치"
    else:
        entries, source = [AssignEntry(**e) for e in pre["optimize"]["plans"][0]["entries"]], "안 A"
    projects = sorted({e.project_id for e in entries})
    out = [justification_input(kg, j, entries, graph=g, S=S, C=C, params=params) for j in projects]
    return out, {"bundle": name, "plan": source, "people": len(ds.people), "projects": len(projects), "review_judge": judge,
                 "precomputed_at": pre.get("computed_at")}


# ---------------------------------------------------------------- 오류 주입(AI 원문에 직접 심는다)
# M: 규칙마다 만든 구조 변형(검사 규칙을 보고 만든 것이라 동어반복 성격이 있다 -- 구현이 실제로 거르는지 확인하는 용도).
# H: 보류 묶음 -- 폴백 리뷰(3회째)가 허용 목록 설계 **전에** 쓴 거짓 통과 예문을 그대로 옮겼다. 검사기 문법을 보고 만든 것이 아니다(측정 전 고정).
JUDGMENT_BANK = (
    "팀원 모두 요구 수준을 만족합니다.", "요구 기술을 빠짐없이 갖췄습니다.", "이 팀은 요건을 모두 채웁니다.", "해당 기술 요건에 미치지 못합니다.",
    "규칙에 저촉되는 부분이 없습니다.", "이 배치는 최선의 선택입니다.", "다른 후보보다 낫습니다.", "충분한 역량을 보유하고 있습니다.",
    "관련 경험이 풍부합니다.", "협업 실적이 탄탄합니다.", "동료 평가가 좋습니다.", "요구 기준을 넘어섭니다.", "흠잡을 데 없는 구성입니다.",
    "이 사업의 적임자입니다.", "기술 수준이 높습니다.", "경력이 짧아 아쉽습니다.", "예산 안에서 문제없이 배치됐습니다.", "모든 제약을 지켰습니다.",
    "검증을 마쳤습니다.", "가장 알맞은 인력입니다.",
)
H_PREFIX = ("고급 인력인 ", "핵심 인력인 ", "십여 년 경력의 ", "동료들에게 칭찬받는 ", "{skill} 전문가인 ", "다년간 경험을 쌓은 ", "Top3 인재인 ",
            "S급 인재인 ", "오랜 경력의 ", "수십 건의 사업을 수행한 ")
H_INFIX = ("절반만 투입되며 ", "PM 역할을 전담하며 ", "{skill} 역량이 미흡하지만 ", "요구 레벨 이상으로 ", "{skill} 충족 결과 ", "{skill} 미달 상황에서도 ",
           "수년간 손발을 맞춘 동료와 함께 ", "{skill} 부족 측면이 있으나 ")
H_TAIL = ("이나 {skill}은 못 맡습니다.", "이므로 적절한 배치입니다.", "입니다, {other}도 마찬가지입니다.", "이며 {other}과 함께한 반년 넘는 경험이 있습니다.")
H_SENTENCE = ("나머지 팀원도 같은 수준입니다.", "다른 팀원도 그렇습니다.", "둘 다 경력이 길며 손발이 잘 맞습니다.", "적절한 배치입니다.",
              "반면 일부 팀원은 경험이 부족합니다.")


def _bound(raw: str, inp) -> list[tuple[str, re.Match]]:
    """짧은 문구로 채워지는(주어 = 주인 한 명) 사람별 자리표시: (문장, 문장 안 일치). 이 자리의 사실은 글에 주인 ID 없이 나온다."""
    from core.kg.justify import SLOT, _analyze, split_sentences
    short = iter(_analyze(raw, inp)["short"])
    out = []
    for s in split_sentences(raw):
        for m in SLOT.finditer(s):
            if next(short, False):
                out.append((s, m))
    return out


def mutations(raw: str, inp, rng: random.Random) -> dict:
    """값: ("made", 바꾼 원문) 또는 ("no_target"|"noop"|"coincidentally_true", None). 원문은 자리표시가 든 AI 글."""
    from core.kg.justify import LABELS, LEAD, SLOT, SUBJECT_KINDS, split_sentences
    facts = {f.id: f for f in inp.facts}
    sents = [s for s in split_sentences(raw) if s in raw]
    bound = [(s, m) for s, m in _bound(raw, inp) if s in raw]
    skills = sorted({f.skill for f in inp.facts if f.kind == "SKL" and f.skill}) or ["ETL"]
    out = {}

    def swap(s, new):
        t = raw.replace(s, new, 1)
        return ("made", t) if t != raw else ("noop", None)

    def subj_pos(s, m):                         # 짧은 문구 자리의 주어 ID 위치(문장 안 그 앞의 마지막 주인 ID)
        owner = facts[m.group(1)].owners[0]
        return s.rfind(owner, 0, m.start()), owner

    def pick_bound():
        return rng.choice(bound) if bound else None
    others_team = lambda p: [q for q in inp.team if q != p]          # noqa: E731
    outsiders = sorted({o for f in inp.facts for o in f.owners[1:] if o not in inp.team})

    # ---- M: 규칙별 구조 변형
    b = pick_bound()
    if b:
        s, m = b
        pos, _ = subj_pos(s, m)
        out["M1_number_in_text"] = swap(s, s[:pos] + f"{rng.choice((7, 12, 48, 96))}개월 경력의 " + s[pos:])
    else:
        out["M1_number_in_text"] = ("no_target", None)
    ms = list(SLOT.finditer(raw))
    out["M2_bad_slot"] = (("made", raw[:(m := rng.choice(ms)).start()] + f"{{F{len(inp.facts) + 50}}}" + raw[m.end():]) if ms else ("no_target", None))
    res = None
    for s, m in rng.sample(bound, len(bound)):
        f = facts[m.group(1)]
        cand = [g for g in inp.facts if g.kind == f.kind and g.owners and g.owners[0] != f.owners[0]]
        if cand:
            g = rng.choice(cand)
            res = ("coincidentally_true", None) if g.phrase == f.phrase else swap(s, s[:m.start()] + f"{{{g.id}}}" + s[m.end():])
            break
    out["M3_other_person_fact"] = res or ("no_target", None)

    def replace_subject(new_id_of):
        for s, m in rng.sample(bound, len(bound)):
            pos, owner = subj_pos(s, m)
            new = new_id_of(owner)
            if pos >= 0 and new:
                return swap(s, s[:pos] + new + s[pos + len(owner):])
        return ("no_target", None)
    out["M4_wrong_subject"] = replace_subject(lambda o: rng.choice(others_team(o)) if others_team(o) else None)
    at = rng.randrange(len(sents) + 1) if sents else 0
    out["M5_judgment_sentence"] = ("made", " ".join(sents[:at] + [rng.choice(JUDGMENT_BANK)] + sents[at:]))
    shape = re.fullmatch(r"([A-Za-z]+)(\d+)", inp.team[0]) if inp.team else None
    fake = shape.group(1) + "9" * len(shape.group(2)) if shape else None
    out["M6_fake_id"] = (("coincidentally_true", None) if fake in inp.known_ids else replace_subject(lambda o: fake)) if fake else ("no_target", None)
    out["M7_outsider_id"] = replace_subject(lambda o: rng.choice(outsiders)) if outsiders else ("no_target", None)
    if len(inp.team) >= 2 and sents:
        p = rng.choice(inp.team)
        kept = [s for s in sents if p not in re.findall(r"[A-Za-z]+\d+", SLOT.sub(" ", s))
                and not any(facts.get(x) and facts[x].kind in SUBJECT_KINDS and facts[x].owners[0] == p for x in SLOT.findall(s))]
        out["M8_drop_member"] = ("made", " ".join(kept)) if kept and len(kept) < len(sents) else ("noop", None)
    else:
        out["M8_drop_member"] = ("no_target", None)
    b = pick_bound()
    if b and len(inp.team) >= 2:
        s, m = b
        y = rng.choice(others_team(facts[m.group(1)].owners[0]))
        body = s[:-1] if s.endswith(".") else s
        out["M9_claim_by_reference"] = swap(s, body + f", {y}{rng.choice(('도 같은 근거를 가집니다', '도 그렇습니다', '의 경우도 같습니다'))}.")
    else:
        out["M9_claim_by_reference"] = ("no_target", None)

    # ---- H: 보류 묶음(독립 리뷰 예문)
    res = None                                   # H1 다른 사람을 사이에 두고 그 사람의 사실을 짧게 붙이기
    for s, m in rng.sample(bound, len(bound)):
        p = facts[m.group(1)].owners[0]
        cws = [f for f in inp.facts if f.kind == "CW" and p in f.owners]
        if not cws:
            continue
        cw = rng.choice(cws)
        q = next(o for o in cw.owners if o != p)
        qf = [f for f in inp.facts if f.kind in SUBJECT_KINDS and f.owners[0] == q]
        if qf:
            res = swap(s, s[:m.end()] + f"이며 {q}과 {{{cw.id}}}이고, {{{rng.choice(qf).id}}}" + s[m.end():])
            break
    out["H1_interposed_person"] = res or ("no_target", None)
    b = pick_bound()
    if b:
        s, m = b
        pos, _ = subj_pos(s, m)
        out["H2_prefix_claim"] = swap(s, s[:pos] + rng.choice(H_PREFIX).format(skill=rng.choice(skills)) + s[pos:])
        out["H3_infix_claim"] = swap(s, s[:m.start()] + rng.choice(H_INFIX).format(skill=rng.choice(skills)) + s[m.start():])
        tail = rng.choice(H_TAIL).format(skill=rng.choice(skills), other=rng.choice(others_team(facts[m.group(1)].owners[0]) or ["팀원"]))
        out["H4_tail_claim"] = swap(s, s[:m.end()] + tail)
    else:
        out["H2_prefix_claim"] = out["H3_infix_claim"] = out["H4_tail_claim"] = ("no_target", None)
    at = rng.randrange(len(sents) + 1) if sents else 0
    out["H5_sentence_claim"] = ("made", " ".join(sents[:at] + [rng.choice(H_SENTENCE)] + sents[at:]))
    pair = re.search(r"\{(F\d+)\},\s*\{(F\d+)\}", raw)
    kind = rng.choice(("bundle", "lower", "chip")) if pair else rng.choice(("lower", "chip"))
    if kind == "bundle":
        out["H6_slot_format"] = ("made", raw[:pair.start()] + f"{{{pair.group(1)}, {pair.group(2)}}}" + raw[pair.end():])
    elif ms and kind == "lower":
        m = rng.choice(ms)
        out["H6_slot_format"] = ("made", raw[:m.start()] + m.group(0).replace("F", "f") + raw[m.end():])
    elif sents:
        s = rng.choice(sents)
        body = s[:-1] if s.endswith(".") else s
        out["H6_slot_format"] = swap(s, body + f" 팀 차원에서 해결됐습니다[{inp.facts[0].id}].")
    else:
        out["H6_slot_format"] = ("no_target", None)
    res = None                                   # H7 주제 라벨을 뒤 사실과 안 맞는 라벨로
    for s in rng.sample(sents, len(sents)):
        m0 = SLOT.search(s)
        if not m0:
            continue
        lm = LEAD.fullmatch(s[:m0.start()])
        f = facts.get(m0.group(1))
        if lm and lm.group("label") and f:
            wrong = [lb for lb, ks in LABELS.items() if f.kind not in ks and lb != "이 사업"]
            new_label = rng.choice(wrong)
            res = swap(s, s[:lm.start("label")] + new_label + s[lm.end("label"):])
            break
    out["H7_label_mismatch"] = res or ("no_target", None)
    return out


def attribution_problems(text: str, inp, person_ids: set) -> list[str]:
    """채운 글만 보고(render의 판단과 따로) 사람별 사실의 귀속을 확인한다(폴백 리뷰 4회째 MUST-2).
    근거 칩 앞이 주인 ID가 붙은 문구(phrase_full)면 통과. 짧은 문구(phrase)면 그 앞쪽(같은 문장, 칩 지움)에서 거슬러 처음 만나는 사람 ID가
    주인이어야 한다 -- 사실 문구 속 다른 사람 ID(함께 일한 사람·비교 후보·평가자)도 센다. 사람 ID는 known 사람 ID만(사업 이름 속 영문+숫자 제외)."""
    from core.kg.justify import SUBJECT_KINDS
    facts = {f.id: f for f in inp.facts}
    probs = []
    for sent in re.split(r"(?<=[.!?。])\s+|\n", text):
        for m in re.finditer(r"\[(F\d+)\]", sent):
            f = facts.get(m.group(1))
            if f is None or f.kind not in SUBJECT_KINDS:
                continue
            before = sent[:m.start()]
            if before.endswith(f.phrase_full):
                continue
            if not before.endswith(f.phrase):
                probs.append(f"{f.id} 칩 앞 문구가 사실 문구가 아니다")
                continue
            head = re.sub(r"\[F\d+\]", " ", before[:len(before) - len(f.phrase)])
            hits = [m2 for m2 in re.finditer(r"[A-Za-z]+\d+", head) if m2.group(0) in person_ids]
            if not hits or hits[-1].group(0) != f.owners[0]:
                probs.append(f"{f.id}: 짧은 문구 바로 앞 사람 {hits[-1].group(0) if hits else '없음'} ≠ 주인 {f.owners[0]}")
            elif re.search(r"[A-Za-z]+\d+\s*(?:과|와|,|및)\s*$", head[:hits[-1].start()]):      # "A와 B" 목록의 끝이면 한 사람 주어가 아니다
                probs.append(f"{f.id}: 짧은 문구 앞 사람이 여러 명 목록의 끝({hits[-1].group(0)})")
    return probs


# ---------------------------------------------------------------- 실행
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def run() -> dict:
    from openai import OpenAI
    from api.rag.justification import generate_justification
    from core.config import load_env, load_pricing
    from core.kg.justify import Fact, JustificationInput, template_text, verify
    load_env()
    model = load_pricing()["briefing_model"]
    if model not in load_pricing().get("models", {}):
        raise SystemExit(f"{model}의 단가가 pricing.json에 없어 비용 상한이 작동하지 않는다")
    client = OpenAI(timeout=180, max_retries=1)
    files = ("rehearsal/justify_scale.py", "core/kg/justify.py", "api/rag/justification.py", "core/kg/views.py", "core/kg/graph.py",
             "core/ingest/skills.py", "core/ingest/skill_dictionary.json", "core/ingest/convert.py")
    res = {"when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *files], capture_output=True, text=True).stdout.strip()),
           "sha256": {f: _sha(ROOT / f) for f in files}, "model": model, "runs": RUNS,
           "criteria": {"G1_detection": G1_DETECTION, "G2_target": G2_TARGET, "G2_floor": G2_FLOOR},
           "bundles": {}, "calls": [], "inputs": {}}
    spent, lock = 0.0, threading.Lock()
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="tw-e7b-") as td:
        src = Path.home() / ".teamweaver" / "review_judgments_synthetic.json"
        cache = None
        if src.exists():                                   # 실제 데이터 폴더는 읽기만 -- 임시 복사본을 쓴다
            cache = Path(td) / "judge.json"
            shutil.copy(src, cache)
        jobs = []
        for name in BUNDLES:
            inps, info = inputs_for(name, Path(td), cache)
            res["bundles"][name] = info
            for inp in inps:
                key = f"{name}/{inp.project_id}"
                res["inputs"][key] = {"team": inp.team, "facts": [asdict(f) for f in inp.facts],
                                      "template_chars": len(template_text(inp.facts))}
                jobs += [(name, inp, r) for r in range(RUNS)]
            print(f"{name}: {info}", flush=True)
        res["planned_calls"] = len(jobs)

        def one(job):
            name, inp, r = job
            if stop.is_set():
                return None
            out = generate_justification(client, model, inp)
            ver = out["verification"] or {}
            return {"bundle": name, "project_id": inp.project_id, "run": r, "team": len(inp.team), "method": out["method"],
                    "fallback_reason": out["fallback_reason"], "rules": sorted({v["rule"] for v in ver.get("violations", [])}),
                    "violations": ver.get("violations", [])[:6], "coverage": ver.get("coverage"), "sentences": ver.get("sentences"),
                    "adverse": ver.get("adverse"),
                    "chars": ver.get("chars") or 0, "usage": out["usage"], "llm_text": out["llm_text"],
                    "rendered": out["text"] if out["method"] == "graphrag" else None}
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = [ex.submit(one, j) for j in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                row = f.result()
                if row is None:
                    continue
                with lock:
                    res["calls"].append(row)
                    spent += (row["usage"] or {}).get("cost_usd", 0)
                    if spent > COST_CAP_USD:
                        stop.set()
                if i % 50 == 0:
                    acc = sum(1 for c in res["calls"] if c["method"] == "graphrag") / len(res["calls"])
                    print(f"  {i}/{len(jobs)} accepted {acc:.1%} spent ${spent:.3f}", flush=True)
    res["spent_usd"] = round(spent, 4)
    res["stopped_by_cost_cap"] = stop.is_set()
    res["mutations"] = inject(res)
    res["free_text_audit"] = audit(res)
    res["summary"] = summarize(res)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res


def inject(res: dict) -> dict:
    from core.kg.justify import SLOT, Fact, JustificationInput, render_ok, verify
    from core.kg.justify import render as render_fill
    rng = random.Random(SEED)
    mut: dict = {}
    for c in res["calls"]:
        if c["method"] != "graphrag":
            continue
        meta = res["inputs"][f"{c['bundle']}/{c['project_id']}"]
        inp = JustificationInput(c["project_id"], "", meta["team"],
                                 [Fact(f["id"], f["kind"], f["text"], f["phrase"], tuple(f["owners"]), f["skill"], f["status"], f["phrase_full"], f["adverse"])
                                  for f in meta["facts"]],
                                 _known_ids(c["bundle"]))
        text = render_fill(c["llm_text"], inp)                # 채운 글 무결성(검사와 따로): 중괄호·칩 수·칩 앞 문구·짧은 문구의 귀속
        bad = [] if render_ok(c["llm_text"], text) else ["중괄호 또는 칩 수"]
        for fid in SLOT.findall(c["llm_text"]):
            f = next((x for x in inp.facts if x.id == fid), None)
            if f is None or (f"{f.phrase}[{fid}]" not in text and f"{f.phrase_full}[{fid}]" not in text):
                bad.append(f"{fid} 문구")
        bad += attribution_problems(text, inp, _person_ids(c["bundle"]) | set(inp.team))
        integ = mut.setdefault("_render_integrity", {"checked": 0, "failed": 0, "examples": []})
        integ["checked"] += 1
        if bad:
            integ["failed"] += 1
            if len(integ["examples"]) < 5:
                integ["examples"].append({"call": f"{c['bundle']}/{c['project_id']}#{c['run']}", "problems": bad[:5]})
        for mname, (state, mtext) in mutations(c["llm_text"], inp, rng).items():
            m = mut.setdefault(mname, {"made": 0, "detected": 0, "no_target": 0, "noop": 0, "coincidentally_true": 0, "missed": [], "rules": {}})
            if state != "made":
                m[state] += 1
                continue
            m["made"] += 1
            v = verify(mtext, inp)
            if not v["ok"]:
                m["detected"] += 1
                for r in {x["rule"] for x in v["violations"]}:
                    m["rules"][r] = m["rules"].get(r, 0) + 1
            elif len(m["missed"]) < 5:
                m["missed"].append({"call": f"{c['bundle']}/{c['project_id']}#{c['run']}", "text": mtext[:600]})
    integ = mut.pop("_render_integrity", {"checked": 0, "failed": 0, "examples": []})
    return {"render_integrity": integ, **dict(sorted(mut.items()))}


def audit(res: dict) -> dict:
    """채택된 글의 AI 연결 말 조각 전부(빈도순, ID는 <ID>). 허용 목록 방식이라 모두 문법 안의 말이어야 한다 -- 사람이 훑어보는 용도."""
    from core.kg.justify import fragments
    cnt: dict[str, int] = {}
    for c in res["calls"]:
        if c["method"] != "graphrag" or not c["llm_text"]:
            continue
        for frag in fragments(c["llm_text"]):
            f = re.sub(r"\s+", " ", re.sub(r"[A-Za-z]+\d+", "<ID>", frag))
            cnt[f] = cnt.get(f, 0) + 1
    items = sorted(cnt.items(), key=lambda kv: -kv[1])
    return {"distinct": len(items), "all": items}


_ID_CACHE: dict[str, set] = {}


def _known_ids(bundle: str) -> set:
    """검사기에 넘길 ID: 그 묶음의 모든 사람·사업(서비스는 그래프에서 같은 집합을 만든다). JSON에 넣지 않고 다시 읽는다."""
    if bundle not in _ID_CACHE:
        import csv
        with tempfile.TemporaryDirectory() as td:
            root = _bundle_root(bundle, Path(td))
            ids = {r["person_id"] for r in csv.DictReader(open(root / "people.csv", encoding="utf-8"))}
            ids |= {r["project_id"] for r in csv.DictReader(open(root / "projects.csv", encoding="utf-8"))}
        _ID_CACHE[bundle] = ids
    return _ID_CACHE[bundle]


_PERSON_CACHE: dict[str, set] = {}


def _person_ids(bundle: str) -> set:
    if bundle not in _PERSON_CACHE:
        import csv
        with tempfile.TemporaryDirectory() as td:
            root = _bundle_root(bundle, Path(td))
            _PERSON_CACHE[bundle] = {r["person_id"] for r in csv.DictReader(open(root / "people.csv", encoding="utf-8"))}
    return _PERSON_CACHE[bundle]


def _rate(cs) -> float | None:
    return round(sum(1 for c in cs if c["method"] == "graphrag") / len(cs), 4) if cs else None


def summarize(res: dict) -> dict:
    calls = res["calls"]
    cov_keys = ("요구 기술 충족", "사람별 기술 근거", "같은 산업 경험", "같은 고객사 경험", "함께 일한 이력", "동료 평가", "다른 후보 비교", "규칙 준수")
    by = {}
    for name in res["bundles"]:
        cs = [c for c in calls if c["bundle"] == name]
        if not cs:
            continue
        lat = sorted((c["usage"] or {}).get("latency_s", 0) for c in cs if c["usage"])
        projs = {c["project_id"] for c in cs}
        all3 = sum(1 for p in projs if sum(1 for c in cs if c["project_id"] == p and c["method"] == "graphrag") == res["runs"])
        acc = [c for c in cs if c["method"] == "graphrag"]
        cov = {}
        for k in cov_keys:
            have = [c for c in acc if (c["coverage"] or {}).get(k) is not None]
            cov[k] = round(sum(1 for c in have if c["coverage"][k]) / len(have), 3) if have else None
        adv = [c["adverse"] for c in acc if c.get("adverse") and c["adverse"]["available"]]
        by[name] = {"calls": len(cs), "accepted": len(acc), "rate": _rate(cs),
                    "adverse_cited_rate": round(sum(a["cited"] for a in adv) / sum(a["available"] for a in adv), 3) if adv else None,
                    "adverse_all_cited_share": round(sum(1 for a in adv if a["cited"] == a["available"]) / len(adv), 3) if adv else None,
                    "rate_excl_llm_error": _rate([c for c in cs if not (c["fallback_reason"] or "").startswith("llm_error")]),
                    "reasons": _count(c["fallback_reason"] or "accepted" for c in cs), "rules": _count(r for c in cs for r in c["rules"]),
                    "latency_p50": statistics.median(lat) if lat else None, "latency_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
                    "cost_usd": round(sum((c["usage"] or {}).get("cost_usd", 0) for c in cs), 4),
                    "projects_all_runs_accepted": f"{all3}/{len(projs)}",
                    "chars_p50": statistics.median([c["chars"] for c in acc]) if acc else None,
                    "template_chars_p50": statistics.median([res["inputs"][f"{name}/{p}"]["template_chars"] for p in projs]),
                    "coverage": cov}
    bins = {f"{a}~{b if b < 999 else ''}명": _rate([c for c in calls if a <= c["team"] <= b]) for a, b in TEAM_BINS}
    bins_n = {f"{a}~{b if b < 999 else ''}명": sum(1 for c in calls if a <= c["team"] <= b) for a, b in TEAM_BINS}
    total = len(calls)
    rate = _rate(calls) or 0.0
    counted = {k: m for k, m in res["mutations"].items() if k != "render_integrity" and k not in KNOWN_LIMITS}
    made = sum(m["made"] for m in counted.values())
    det = sum(m["detected"] for m in counted.values())
    g1 = (det / made) if made else None
    integ = res["mutations"].get("render_integrity", {"checked": 0, "failed": 0})
    by_group = {grp: {"made": sum(m["made"] for k, m in counted.items() if k.startswith(grp)),
                      "detected": sum(m["detected"] for k, m in counted.items() if k.startswith(grp))} for grp in ("M", "H")}
    complete = not res.get("stopped_by_cost_cap") and total == res.get("planned_calls", total)
    g2 = ("미완(비용 상한 중단)" if not complete else "달성" if rate >= G2_TARGET else "조건부" if rate >= G2_FLOOR else "미달")
    return {"by_bundle": by, "by_team_size": bins, "by_team_size_n": bins_n, "total_calls": total, "planned_calls": res.get("planned_calls"),
            "accepted": sum(1 for c in calls if c["method"] == "graphrag"), "acceptance_rate": rate,
            "acceptance_rate_excl_llm_error": _rate([c for c in calls if not (c["fallback_reason"] or "").startswith("llm_error")]),
            "G1": {"made": made, "detected": det, "rate": g1, "by_group": by_group, "render_integrity": integ,
                   "pass": g1 is not None and g1 >= G1_DETECTION and integ["failed"] == 0, "excluded_known_limits": sorted(KNOWN_LIMITS)},
            "G2": {"rate": rate, "verdict": g2, "complete": complete},
            "reasons": _count(c["fallback_reason"] or "accepted" for c in calls), "rules": _count(r for c in calls for r in c["rules"])}


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _f(x, fmt="{:.1f}"):
    return "—" if x is None else fmt.format(x)


def render(res: dict) -> None:
    e = html.escape
    s = res["summary"]
    rows = "".join(
        f"<tr><td>{e(n)}</td><td>{e(res['bundles'][n]['plan'])}</td><td class='num'>{b['calls']}</td><td class='num'>{_f(b['rate'], '{:.1%}')}</td>"
        f"<td class='num'>{e(b['projects_all_runs_accepted'])}</td><td>{e(', '.join(f'{k} {v}' for k, v in b['rules'].items()) or '—')}</td>"
        f"<td class='num'>{_f(b['latency_p50'])} / {_f(b['latency_p95'])}</td><td class='num'>${b['cost_usd']:.3f}</td>"
        f"<td class='num'>{_f(b['chars_p50'], '{:,.0f}')} / {_f(b['template_chars_p50'], '{:,.0f}')}</td></tr>" for n, b in s["by_bundle"].items())
    mrows = "".join(f"<tr><td>{e(k)}</td><td class='num'>{m['made']}</td><td class='num'>{m['detected']}</td>"
                    f"<td class='num'>{m['no_target']} / {m['noop']} / {m['coincidentally_true']}</td>"
                    f"<td>{e(', '.join(f'{r} {c}' for r, c in sorted(m['rules'].items())))}</td><td class='num'>{m['made'] - m['detected']}</td></tr>"
                    for k, m in res["mutations"].items() if k != "render_integrity")
    missed = "".join(f"<li><b>{e(k)}</b> {e(x['call'])}: {e(x['text'][:300])}</li>" for k, m in res["mutations"].items()
                     if k != "render_integrity" for x in m["missed"])
    integ = s["G1"]["render_integrity"]
    cov_keys = ("요구 기술 충족", "사람별 기술 근거", "같은 산업 경험", "같은 고객사 경험", "함께 일한 이력", "동료 평가", "다른 후보 비교", "규칙 준수")
    crows = "".join(f"<tr><td>{e(n)}</td>" + "".join(f"<td class='num'>{_f(b['coverage'][k], '{:.0%}')}</td>" for k in cov_keys)
                    + f"<td class='num'>{_f(b.get('adverse_cited_rate'), '{:.0%}')}</td><td class='num'>{_f(b.get('adverse_all_cited_share'), '{:.0%}')}</td></tr>"
                    for n, b in s["by_bundle"].items())
    trows = "".join(f"<tr><td>{e(k)}</td><td class='num'>{s['by_team_size_n'][k]}</td><td class='num'>{_f(v, '{:.1%}')}</td></tr>" for k, v in s["by_team_size"].items())
    ex = next((c for c in res["calls"] if c["method"] == "graphrag" and c["bundle"] == "org-n300"), None)
    rej = [c for c in res["calls"] if c["method"] != "graphrag"][:8]
    rej_html = "".join(f"<li><b>{e(c['bundle'])}/{e(c['project_id'])}</b>(팀 {c['team']}명) {e(c['fallback_reason'] or '')}: "
                       + e(" / ".join(f"{v['rule']} {v['detail']}" for v in c["violations"][:2])) + "</li>" for c in rej)
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>소명 글 확대 검증</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--box:#edf3f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#a8a8a8;--acc:#8ab8e0;--line:#3a3a3a;--box:#222b33}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1100px;margin:auto}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.88em}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}}th{{color:var(--muted)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}.sub{{color:var(--muted);font-size:.85em}}.w{{overflow-x:auto}}.box{{background:var(--box);border-radius:10px;padding:10px 16px}}
pre{{white-space:pre-wrap;background:var(--box);padding:10px;border-radius:8px;font-size:.85em}}
</style></head><body><main>
<h1>인사팀 소명 글(GraphRAG) 확대 검증 (E7b)</h1>
<p class="sub">TeamWeaver · {e(res['when'])} · 커밋 {e(res['commit'])}{' (측정 당시 커밋 안 된 변경 있음)' if res.get('dirty') else ''} · 모델 {e(res['model'])} · 사업마다 {res['runs']}회 ·
호출 {s['total_calls']}/{s.get('planned_calls')}{' · 비용 상한으로 중단' if res.get('stopped_by_cost_cap') else ''} · 비용 ${res['spent_usd']:.3f} · 가상 데이터</p>
<div class="box"><p><b>G1 오류 주입 탐지</b>: {s['G1']['detected']}/{s['G1']['made']}({_f(s['G1']['rate'], '{:.1%}')}; 구조 변형 M {s['G1']['by_group']['M']['detected']}/{s['G1']['by_group']['M']['made']},
보류 묶음 H {s['G1']['by_group']['H']['detected']}/{s['G1']['by_group']['H']['made']}) + 채운 글 무결성 {integ['checked'] - integ['failed']}/{integ['checked']}
→ <b>{'통과' if s['G1']['pass'] else '실패'}</b>(기준 100%) ·
<b>G2 GraphRAG 채택률</b>: {s['acceptance_rate']:.1%}({s['accepted']}/{s['total_calls']}) → <b>{e(s['G2']['verdict'])}</b>(달성 ≥95%, 조건부 90~95%) ·
AI 호출 오류 제외 {_f(s['acceptance_rate_excl_llm_error'], '{:.1%}')}</p>
<p class="sub">판정 기준은 측정 전에 고정했다(<code>rehearsal/justify_scale.py</code> docstring·상수와 <code>docs/requests/2026-10-09-hr-justification.md</code>, 측정 전 커밋).
G2 분모는 계획한 호출 전부(AI 호출 오류 포함). 데이터는 서비스와 같이 리뷰 글 판정 캐시(임시 복사본)를 썼다.
미리 계산 배치: 100명은 지금 서비스 기준과 같고, 200·300명은 하위 기술 부분 인정 전에 만든 배치다(배치 자체는 유효한 입력 — 병합 뒤 다시 만든다).
측정 전 조율: {e(CALIBRATION)} 채택률에 다소 낙관 편향이 있을 수 있다.</p></div>
<h2>1. 묶음별 결과</h2>
<div class="w"><table><tr><th>묶음</th><th>배치</th><th>호출</th><th>채택률</th><th>3회 모두 채택 사업</th><th>탈락 규칙</th><th>지연 p50/p95 초</th><th>비용</th><th>글 길이(채택 p50 / 템플릿)</th></tr>{rows}</table></div>
<h3>팀 크기별 채택률</h3><div class="w"><table><tr><th>팀 크기</th><th>호출</th><th>채택률</th></tr>{trows}</table></div>
<p class="sub">탈락 이유 전체: {e(', '.join(f'{k} {v}' for k, v in s['reasons'].items()))}. 규칙(자리표시 + 허용 목록): S1 없는 자리표시 · S2 문법 밖 연결 말·자리표시 없는 문장 ·
S3 주어·라벨 자리의 팀원 아닌·지어낸 ID · S4 사실과 머리(주어·라벨)의 짝 · S5 사람별 사실이 없는 팀원 · S6 빈 글.</p>
<h3>탈락 예(앞 8건)</h3><ul>{rej_html or '<li>없음</li>'}</ul>
<h2>2. 오류 주입(검사기가 틀린 글을 잡는가)</h2>
<div class="w"><table><tr><th>변형</th><th>만든 수</th><th>잡음</th><th>못 만듦: 대상 없음 / 변화 없음 / 우연히 참</th><th>잡은 규칙</th><th>놓침</th></tr>{mrows}</table></div>
<p class="sub">AI 원문(자리표시 포함)에 직접 심는다. 채택된 글마다 각 변형 하나씩.
<b>M(구조 변형, 규칙을 보고 만든 것 — 구현이 거르는지 확인)</b>: M1 연결 말 숫자 · M2 없는 자리표시 · M3 짧게 채워지는 사람 사실을 다른 사람 것으로 · M4 그 주어를 다른 팀원으로 ·
M5 근거 없는 판단 문장 · M6 지어낸 ID 주어 · M7 팀원 아닌 사람(다른 후보·평가자) 주어 · M8 팀원 한 명 모두 삭제 · M9 사실 없이 다른 팀원에게 넘기기.
<b>H(보류 묶음 — 독립 리뷰가 허용 목록 설계 전에 쓴 예문)</b>: H1 다른 사람을 사이에 두고 그 사람 사실 붙이기 · H2 주어 앞 수식("고급 인력인", "십여 년 경력의") ·
H3 주어 뒤 주장("절반만 투입되며", "ETL 충족 결과") · H4 꼬리 주장 · H5 자리표시 없는 판단 문장 · H6 묶음 자리표시·소문자·위조 근거 칩 · H7 주제 라벨과 사실 종류 불일치.
허용 목록 방식이라 문법 밖 말은 구성상 탈락한다 — G1은 그 구현이 실제 실수 유형을 거르는지와, 채운 글이 사실 문구 그대로인지(무결성)를 확인한다.</p>
<h3>놓친 예</h3><ul>{missed or '<li>없음</li>'}</ul>
<h2>3. 필수 요소 포함률·불리한 사실 인용률(채택된 글, 그 사실이 있는 경우만)</h2>
<div class="w"><table><tr><th>묶음</th>{''.join(f'<th>{e(k)}</th>' for k in cov_keys)}<th>불리한 사실 인용률</th><th>불리한 사실 모두 인용한 글</th></tr>{crows}</table></div>
<p class="sub">불리한 사실 = 요구 기술 부족 · 사람별 기술 미달 · 규칙 위반·빈자리가 있는 규칙 사실 · 전체 점수가 오르는 다른 후보 · 부정(0 미만) 동료 평가(Fact.adverse).
기준 없이 보고한다(G3). AI가 고르는 방식이라 빼고 쓸 수 있다 — 구조적 보완(반드시 인용하게 하는 규칙, 또는 서버가 덧붙이기)은 사용자 결정.</p>
<h2>4. AI 연결 말 감사(채택된 글의 조각 전부, 빈도순 — ID는 &lt;ID&gt;)</h2>
<p class="sub">허용 목록 문법 안의 말만 남는다. 서로 다른 조각 {res.get('free_text_audit', {}).get('distinct', 0)}개.</p>
<div class="w"><table><tr><th>조각</th><th>횟수</th></tr>{''.join(f"<tr><td>{e(t)}</td><td class='num'>{n}</td></tr>" for t, n in res.get('free_text_audit', {}).get('all', []))}</table></div>
<h2>5. 채택된 글 예(300명) — AI 원문과 서버가 채운 글</h2>
<pre>{e((ex or {}).get('llm_text') or '')}</pre>
<pre>{e((ex or {}).get('rendered') or '')}</pre>
<p class="sub">알려진 한계: 어떤 사실을 고르고 뺄지는 AI 몫이다(불리한 사실을 뺄 수 있다 — 3절 필수 요소 포함률). 주제 라벨은 뜻을 바꾸지 않는 말로 골랐지만 사람이 고른 목록이다.
사내 GLM은 잔액 소진으로 재지 않았다.</p>
</main></body></html>"""
    OUT_HTML.write_text(page, "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()
    res = json.loads(OUT_JSON.read_text("utf-8")) if a.render_only else run()
    render(res)
    print(json.dumps(res["summary"]["G1"], ensure_ascii=False), json.dumps(res["summary"]["G2"], ensure_ascii=False))


if __name__ == "__main__":
    main()
