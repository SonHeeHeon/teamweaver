"""낯선 기술 이름 정리(2026-10-22 사용자 "4,5번은 개발해보자" -- 5번 "기술 이름 정리", claude-a, 설계 루트 `.omc/plan/2026-10-22-ai-value.md`).

사전(`skills.py`, 228개념)에 없는 이름은 지금 합치지 않고 경고만 한다(같은 기술이 다른 기술로 셈해진다 -- 사전이 없으면 100명 쌍의 86% 점수가 바뀜).
여기서는 그런 이름마다 후보를 만들고(문자열 유사도 + AI 판정), **관리자가 승인한 것만** 데이터 판의 manifest `skill_names`에 남긴다.
로더·지식 그래프는 `dictionary_for(manifest)`로 기본 사전을 그 결정만큼 넓혀 쓴다 -- 데이터가 새 판이 되니 캐시·미리 계산은 옛 값으로 쓰이지 않고, 되돌리기로 무른다.

결정(manifest `skill_names` 항목 {name, decision, concept?, reason, at}):
- alias: 그 개념의 다른 이름(같은 기술) · narrower: 그 개념의 하위 기술(새 개념, 상위 경력에 사전의 부분 인정 비율 -- 지금 70%)
- new: 사전에 없는 별개 기술(이름 그대로 둔다) · not_skill: 기술이 아니다(이름 그대로 둔다 -- 데이터를 지우지 않는다). 둘 다 정리 목록에서만 빠진다.
자동 합치기는 없다(조사: LLM이 스스로 매긴 신뢰는 과신, 업계도 사람이 결정 -- `.omc/research/reports/자연어 인력 요청과 기술명 정규화.md`).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import time
from collections import Counter, defaultdict
from functools import lru_cache

from core.ingest.skills import SkillDictionary, from_data, load_dictionary, norm_key

log = logging.getLogger(__name__)

DECISIONS = ("alias", "narrower", "new", "not_skill")
MAP_DECISIONS = ("alias", "narrower")
# 이름이 비슷해 잘못 합치기 쉬운 개념 쌍(조사: Java/JavaScript·React/React Native·Oracle DB/Oracle Cloud 같은 오병합) -- 고르면 "확인 필요"
CONFUSABLE = {frozenset(p) for p in [
    ("java", "javascript"), ("java", "java_ee"), ("react", "react_native"), ("angular", "angularjs"), ("mysql", "mariadb"),
    ("sql", "nosql"), ("sql", "sql_tuning"), ("sql", "plsql"), ("spring", "spring_boot"), ("spring", "spring_batch"),
    ("spring", "spring_cloud"), ("kubernetes", "eks"), ("kubernetes", "kubeflow"), ("aws", "aws_lambda"), ("aws", "eks"),
    ("git", "gitlab_ci"), ("oracle_db", "goldengate"), ("oracle_db", "plsql"), ("cpp", "csharp"), ("go", "rust")]}
CONTEXT_SKILLS = 5


def decisions_of(manifest: dict | None) -> list[dict]:
    """manifest의 승인된 결정(모양이 맞는 것만). 이름 키가 같으면 나중 것."""
    out: dict[str, dict] = {}
    for e in (manifest or {}).get("skill_names") or []:
        if not isinstance(e, dict) or e.get("decision") not in DECISIONS or not str(e.get("name") or "").strip():
            continue
        if e["decision"] in MAP_DECISIONS and not e.get("concept"):
            continue
        out[norm_key(e["name"])] = e
    return list(out.values())


def check(manifest: dict | None, report) -> None:
    """로더 검사: 모양이 틀렸거나 사전에 없는 개념을 가리키면 경고(그 항목은 쓰지 않는다)."""
    raw = (manifest or {}).get("skill_names")
    if raw is None:
        return
    if not isinstance(raw, list):
        report.warn("manifest.json", "skill_names는 목록이어야 한다 -- 무시했다")
        return
    base = load_dictionary()
    for e in raw:
        if not isinstance(e, dict) or e.get("decision") not in DECISIONS or not str(e.get("name") or "").strip():
            report.warn("manifest.json", f"기술 이름 결정 모양이 틀렸다 -- 무시했다: {str(e)[:80]}")
        elif e["decision"] in MAP_DECISIONS and e.get("concept") not in base.concepts:
            report.warn("manifest.json", f"기술 이름 결정 '{e['name']}'이 사전에 없는 개념({e.get('concept')})을 가리킨다 -- 무시했다")


def _narrower_id(name: str) -> str:
    return "x_" + hashlib.sha1(norm_key(name).encode("utf-8")).hexdigest()[:10]


def extend(base: SkillDictionary, decisions: list[dict]) -> SkillDictionary:
    """기본 사전 + 승인된 alias(그 개념 별칭)·narrower(새 하위 개념). 이미 다른 개념이 쓰는 이름은 건너뛴다(사전 검사가 막는 충돌)."""
    concepts = {cid: {**c, "aliases": list(c.get("aliases") or [])} for cid, c in base.concepts.items()}
    owner = dict(base.by_key)
    applied = []
    for d in decisions:
        key = norm_key(d["name"])
        if d["decision"] not in MAP_DECISIONS or d.get("concept") not in concepts or not key:
            continue
        if key in owner:
            continue
        if d["decision"] == "alias":
            concepts[d["concept"]]["aliases"].append(str(d["name"]).strip())
            owner[key] = d["concept"]
        else:
            nid = _narrower_id(d["name"])
            parent = concepts[d["concept"]]
            concepts[nid] = {"id": nid, "label": str(d["name"]).strip(), "aliases": [], "broader": [d["concept"]], "related": [],
                             "category": parent.get("category"), "kind": "product"}
            owner[key] = nid
        applied.append(f"{key}>{d['decision']}:{d['concept']}")
    if not applied:
        return base
    h = hashlib.sha256("|".join(sorted(applied)).encode("utf-8")).hexdigest()[:12]
    return from_data({"version": f"{base.version}+{h}", "partial_credit": {"narrower_to_broader": base.narrower_credit},
                      "concepts": list(concepts.values())})


@lru_cache(maxsize=16)
def _extended(key: str) -> SkillDictionary:
    return extend(load_dictionary(), json.loads(key))


def dictionary_for(manifest: dict | None) -> SkillDictionary:
    """그 데이터의 사전 = 기본 사전 + manifest의 승인된 결정. 결정이 없으면 기본 사전 그대로(같은 객체)."""
    ds = [d for d in decisions_of(manifest) if d["decision"] in MAP_DECISIONS]
    if not ds:
        return load_dictionary()
    return _extended(json.dumps(sorted(({"name": d["name"], "decision": d["decision"], "concept": d["concept"]} for d in ds),
                                       key=lambda x: x["name"]), ensure_ascii=False))


def dictionary_of_active(dataset) -> SkillDictionary:
    """켜진 데이터(api.datasets.ActiveDataset)의 사전 -- scenario에 옮겨 둔 승인 결정으로 넓힌다."""
    return dictionary_for({"skill_names": (getattr(dataset, "scenario", None) or {}).get("skill_names") or []})


# --- 정리할 이름 모으기 -----------------------------------------------------------------------------------------

def unknown_names(tables: dict, sd: SkillDictionary, decided: set[str] | None = None) -> list[dict]:
    """사전(넓힌 사전)으로도 못 맞춘 이름과 맥락: 쓰는 사람·사업·업무 수, 그 이름을 가진 사람들이 함께 가진(사전에 있는) 기술 상위 5개, 요구한 사업.
    이미 결정한 이름(new·not_skill 포함, decided = 이름 키)은 뺀다. 많이 쓰는 이름 먼저."""
    decided = decided or set()
    holders: dict[str, set] = defaultdict(set)
    known_of: dict[str, set] = defaultdict(set)
    for r in tables.get("person_skills.csv") or []:
        name = str(r.get("skill_name") or "").strip()
        c = sd.lookup(name)
        if c is None:
            holders[name].add(r.get("person_id"))
        else:
            known_of[r.get("person_id")].add(c["label"])
    projects: dict[str, set] = defaultdict(set)
    for r in tables.get("project_skill_requirements.csv") or []:
        name = str(r.get("skill_name") or "").strip()
        if sd.lookup(name) is None:
            projects[name].add(r.get("project_id"))
    works: Counter = Counter()
    for r in tables.get("work_skills.csv") or []:
        name = str(r.get("skill_name") or "").strip()
        if sd.lookup(name) is None:
            works[name] += 1
    out = []
    for name in set(holders) | set(projects) | set(works):
        if not name or norm_key(name) in decided:
            continue
        co = Counter(s for p in holders.get(name, ()) for s in known_of.get(p, ()))
        out.append({"name": name, "people": len(holders.get(name, ())), "projects": sorted(projects.get(name, ())),
                    "works": works.get(name, 0), "co_skills": [s for s, _ in co.most_common(CONTEXT_SKILLS)]})
    out.sort(key=lambda x: (-(x["people"] + len(x["projects"]) + x["works"]), x["name"]))
    return out


# --- 후보 만들기 ---------------------------------------------------------------------------------------------------

_VERSIONISH = re.compile(r"\d+(?:\.\d+)*[a-z]?$")


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and short in long_:                     # "springboot3" ⊃ "springboot", "쿠버네티스운영" ⊃ "쿠버네티스"
        r = max(r, 0.6 + 0.4 * len(short) / len(long_))
    return r


def string_candidates(sd: SkillDictionary, name: str, k: int = 5) -> list[dict]:
    """정규화한 이름과 사전 이름(대표·별칭)의 문자열 유사도 상위 k개(개념마다 가장 비슷한 이름 하나). AI 없이도 낸다."""
    key = _VERSIONISH.sub("", norm_key(name)) or norm_key(name)
    best: dict[str, tuple[float, str]] = {}
    for cid, c in sd.concepts.items():
        for n in [c["label"], *(c.get("aliases") or [])]:
            s = _sim(key, norm_key(n))
            if s > best.get(cid, (0.0, ""))[0]:
                best[cid] = (s, n)
    top = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))[:k]
    return [{"id": cid, "label": sd.concepts[cid]["label"], "score": round(s, 3), "matched": n} for cid, (s, n) in top]


SYSTEM = ("너는 IT 기술 이름 사전 관리자를 돕는다. 인사 데이터에 나온, 사전에 없는 기술 이름마다 하나를 고른다: "
          "alias(사전의 그 기술과 같은 기술의 다른 표기·약어·오타·한글 음차), narrower(사전 그 기술의 하위·특정 제품·버전 계열), "
          "new(사전에 없는 별개 기술), not_skill(기술 이름이 아님). 개념 id는 주어진 사전 목록에서만 쓴다. 비슷해 보여도 다른 기술"
          "(Java와 JavaScript, React와 React Native 등)은 합치지 않는다. JSON 하나만 답한다.")

TASK = """정리할 이름(맥락: 이 이름을 가진 사람 수, 그 사람들이 함께 가진 기술, 요구한 사업 수, 문자열이 비슷한 사전 후보):
{items}

사전(id | 대표 이름 | 다른 이름):
{catalog}

답 형식: {{"items": [{{"name": 위 이름 그대로, "decision": "alias"|"narrower"|"new"|"not_skill", "concept": 사전 id 또는 null(new·not_skill),
"alternatives": [다른 후보 id 0~2개], "reason": 한 문장}}]}}"""


def _catalog(sd: SkillDictionary) -> str:
    return "\n".join(f"{cid} | {c['label']}" + (f" | {', '.join((c.get('aliases') or [])[:4])}" if c.get("aliases") else "")
                     for cid, c in sd.concepts.items() if not cid.startswith("x_"))


def _rule_suggestion(sd: SkillDictionary, item: dict, cands: list[dict]) -> dict:
    top = cands[0] if cands else None
    if top and top["score"] >= 0.8:
        return {"decision": "alias", "concept": top["id"], "alternatives": [c["id"] for c in cands[1:3]],
                "reason": f"문자열이 '{top['matched']}'와 비슷하다({top['score']:.2f})"}
    return {"decision": "new", "concept": None, "alternatives": [c["id"] for c in cands[:3]],
            "reason": "사전 이름과 충분히 비슷한 것이 없다"}


def _confidence(sd: SkillDictionary, s: dict, cands: list[dict], method: str) -> tuple[str, list[str]]:
    """높음 = AI가 alias로 고른 개념이 문자열 1위와 같고 헷갈리는 쌍이 아님. 그 밖은 확인. 높음도 자동 승인하지 않는다(한 번에 승인 버튼만)."""
    flags = []
    cid = s.get("concept")
    pool = {cid, *(s.get("alternatives") or []), *(c["id"] for c in cands[:2])} - {None}
    for pair in CONFUSABLE:                                     # 고른 개념의 짝이 다른 후보에 있으면 확인
        other = next(iter(pair - {cid}), None) if cid in pair else None
        if other in pool and other in sd.concepts:
            flags.append(f"헷갈리기 쉬운 기술 — {sd.concepts[cid]['label']} ↔ {sd.concepts[other]['label']}")
    if s["decision"] == "narrower":
        flags.append(f"하위 기술 — 상위 기술 경력의 {round(sd.narrower_credit * 100)}%로 인정된다")
    high = (method == "ai" and s["decision"] == "alias" and cands and cands[0]["id"] == cid and cands[0]["score"] >= 0.5 and not flags)
    return ("high" if high else "check"), flags


def suggest(sd: SkillDictionary, items: list[dict], *, client=None, model: str | None = None, reasoning_effort: str | None = None,
            price: dict | None = None, batch: int = 25) -> dict:
    """이름마다 제안: {name, decision, concept, concept_label, alternatives[{id,label}], reason, confidence, flags, candidates, method}.
    AI 판정은 사전 id 밖이면 버리고 문자열 제안으로 대신한다. 반환 {items, method, usage, fallback_reason}."""
    cands = {it["name"]: string_candidates(sd, it["name"]) for it in items}
    ai_out: dict[str, dict] = {}
    usage = {"calls": 0, "in": 0, "out": 0, "cost_usd": 0.0, "latency_s": 0.0, "model": model}
    fallback = None if client is not None and model else "no_client"
    if fallback is None:
        pr = price or {}
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            lines = "\n".join(
                f"- {it['name']} | 사람 {it.get('people', 0)} | 함께 가진 기술: {', '.join(it.get('co_skills') or []) or '-'} | 요구 사업 "
                f"{len(it.get('projects') or [])} | 문자열 후보: {', '.join(c['id'] for c in cands[it['name']][:3])}" for it in chunk)
            t0 = time.monotonic()
            try:
                resp = client.chat.completions.create(
                    model=model, response_format={"type": "json_object"},
                    **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": TASK.format(items=lines, catalog=_catalog(sd))}])
                data = json.loads(resp.choices[0].message.content or "{}")
            except Exception as exc:                            # noqa: BLE001 -- 네트워크·API·JSON 오류는 문자열 제안으로
                log.warning("기술 이름 AI 판정 실패: %s", exc)
                fallback = f"llm_error:{type(exc).__name__}"
                break
            u = getattr(resp, "usage", None)
            pin, pout = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
            usage.update(calls=usage["calls"] + 1, **{"in": usage["in"] + pin, "out": usage["out"] + pout},
                         latency_s=round(usage["latency_s"] + time.monotonic() - t0, 2),
                         cost_usd=round(usage["cost_usd"] + (pin * pr.get("input_per_1m", 0) + pout * pr.get("output_per_1m", 0)) / 1e6, 6))
            for e in (data.get("items") or []) if isinstance(data, dict) else []:
                if isinstance(e, dict) and isinstance(e.get("name"), str):
                    ai_out[e["name"].strip()] = e
    out = []
    for it in items:
        name, cs = it["name"], cands[it["name"]]
        e = ai_out.get(name)
        method = "ai"
        s = None
        if e is not None:
            dec, cid = e.get("decision"), e.get("concept")
            ok = dec in DECISIONS and ((dec in MAP_DECISIONS and cid in sd.concepts and not str(cid).startswith("x_"))
                                       or (dec not in MAP_DECISIONS))
            if ok:
                alts = [a for a in (e.get("alternatives") or []) if isinstance(a, str) and a in sd.concepts and a != cid][:2]
                s = {"decision": dec, "concept": cid if dec in MAP_DECISIONS else None, "alternatives": alts,
                     "reason": str(e.get("reason") or "")[:200]}
        if s is None:
            method = "string"
            s = _rule_suggestion(sd, it, cs)
        conf, flags = _confidence(sd, s, cs, method)
        out.append({**it, **s, "concept_label": sd.concepts[s["concept"]]["label"] if s.get("concept") else None,
                    "alternatives": [{"id": a, "label": sd.concepts[a]["label"]} for a in s["alternatives"] if a in sd.concepts],
                    "confidence": conf, "flags": flags, "candidates": cs, "method": method})
    return {"items": out, "method": "ai" if fallback is None and ai_out else "string",
            "usage": usage if usage["calls"] else None, "fallback_reason": fallback}
