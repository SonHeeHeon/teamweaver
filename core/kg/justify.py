"""인사팀 소명 글의 재료와 검사(2026-10-09, claude-a) -- 사용자 결정: 소명 글은 GraphRAG(AI가 그래프 사실로 글을 쓴다),
실패하면 정해진 틀에 사실을 채운 글(템플릿)로 대신한다. 이 모듈은 AI를 부르지 않는다(호출은 api/rag/justification.py).

**자리표시 + 허용 목록 방식(측정 전 설계 변경, 2026-10-09)**: AI가 숫자·충족/미달을 직접 쓰는 자유 문장은 규칙 검사로 다 막을 수 없었고
(폴백 리뷰 2회에서 매번 새 구멍), 판단어 금지 목록도 목록 밖 표현("고급 인력인", "십여 년 경력의", "칭찬받는")으로 샜다(폴백 리뷰 3회째).
그래서 AI의 몫을 **사실 고르기·순서·묶기**로 좁혔다:
- 사실은 자리표시 {F12}로만 끼운다. 서버가 사실 문구로 채운다(render) -- 숫자·상태·다른 사람 ID를 AI가 쓸 수 없다.
- 자리표시 사이의 연결 말은 정해진 문법 안에서만 쓴다: 문장 머리(이어 주는 말 + 주어 "DP0006은" 또는 주제 라벨 LABELS "동료 평가는",
  "DP0006의 기술 근거는"), 자리표시 사이(",·이며·이고·및" + 새 주어·라벨), 꼬리("입니다"), 자리표시 없는 문장은 맺음말(CLOSING)만.
- 사람별 사실(MEM·SKL·IND·CLI·REV)은 주어(또는 "ID의 라벨")가 한 사람이고 그 주인이며, 같은 머리 아래 앞에 다른 사람이 나오는 사실
  (PRJ·CW·ALT·REV — 문구에 다른 ID가 있다)이 없을 때만 짧은 문구(phrase)로, 그 밖에는 주인 ID를 붙인 문구(phrase_full: "DP0035의 …")로 채운다
  (폴백 리뷰 4회째 MUST-1: "DP0006은 {후보 비교}이며 {기술}"에서 기술이 비교 후보의 것으로 읽혔다). 주어는 문장마다 새로 정한다.
- 여러 주어("A와 B는")는 주어마다 자기 사실이 하나 이상, "각각"이면 사람별 사실 수·순서가 주어와 같아야 한다.

검사(verify, AI 원문 기준) -- 하나라도 어기면 채택하지 않는다(틀린 부분만 지우지 않는다, K5와 같은 원칙):
  S1 없는 자리표시 · S2 문법(허용 목록) 밖의 연결 말·자리표시 없는 문장 · S3 주어·라벨 자리의 ID가 팀원이 아님(지어낸 ID 포함) ·
  S4 사실과 머리의 짝: 사람별 사실은 주어(들) 중 주인의 것, 함께 일한 이력·후보 비교는 주어가 그 사실의 사람들 안, 사업 전체 사실(PRJ·REQ·CON)
     앞에는 사람 주어 금지, 라벨이 있으면 사실 종류가 라벨에 맞음, 여러 주어·"각각"의 대응 · S5 사람별 사실이 하나도 붙지 않은 팀원 · S6 빈 글·자리표시 없음.
보장 범위: 채운 글의 숫자·상태·사람-사실 짝은 데이터 그대로다(구성상). AI가 고른 순서·묶음·라벨은 뜻을 바꾸지 않는 말로 한정했다.
한계: 어떤 사실을 고르고 뺄지는 AI 몫이라 불리한 사실(미달·감점)을 빼고 쓸 수 있다 -- Fact.adverse로 표시해 인용률(verify의 adverse)을 보고한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.ingest.convert import level_from_months

ELEMENTS = {                      # 소명 필수 요소(보고용) -- 사실 종류로 판정
    "요구 기술 충족": "REQ", "사람별 기술 근거": "SKL", "같은 산업 경험": "IND", "같은 고객사 경험": "CLI",
    "함께 일한 이력": "CW", "동료 평가": "REV", "다른 후보 비교": "ALT", "규칙 준수": "CON"}
TEMPLATE_ORDER = (("PRJ", "대상 사업"), ("REQ", "요구 기술"), ("MEM", "배치 인원"), ("SKL", "기술 근거"),
                  ("IND", "같은 산업 경험"), ("CLI", "같은 고객사 경험"), ("CW", "함께 일한 이력"), ("REV", "동료 평가"),
                  ("ALT", "다른 후보와 비교"), ("CON", "규칙 준수"))
SUBJECT_KINDS = {"MEM", "SKL", "IND", "CLI", "REV"}       # 사람별 사실(짧은 문구에는 주인 ID가 없다)
# 주제 라벨 → 그 뒤에 올 수 있는 사실 종류. 프롬프트도 이 목록을 그대로 보여 준다.
LABELS: dict[str, set[str]] = {
    "사업 개요": {"PRJ"}, "대상 사업": {"PRJ"}, "이 사업": {"PRJ", "REQ", "CON"},
    "요구 기술": {"REQ"}, "이 사업의 요구 기술": {"REQ"}, "요구 기술 현황": {"REQ"}, "요구 기술 충족 현황": {"REQ"}, "요구 기술 충족도": {"REQ"}, "기술 요건": {"REQ"},
    "등급과 투입": {"MEM"}, "배치 정보": {"MEM"}, "기본 정보": {"MEM"},
    "기술 근거": {"SKL"}, "기술 경력": {"SKL"},
    "근거": {"MEM", "SKL", "IND", "CLI", "REV", "CW", "ALT"}, "배치 근거": {"MEM", "SKL", "IND", "CLI", "REV", "CW", "ALT"},
    "같은 산업 경험": {"IND"}, "동일 산업 경험": {"IND"}, "산업 경험": {"IND"},
    "같은 고객사 경험": {"CLI"}, "동일 고객사 경험": {"CLI"}, "고객사 경험": {"CLI"},
    "관련 경험": {"IND", "CLI"}, "과거 사업 경험": {"IND", "CLI"},
    "함께 일한 이력": {"CW"}, "함께 일한 경험": {"CW"}, "협업 이력": {"CW"}, "협업 경험": {"CW"},
    "동료 평가": {"REV"}, "동료 평가 근거": {"REV"}, "동료 평가 결과": {"REV"},
    "후보 비교": {"ALT"}, "후보 비교 결과": {"ALT"}, "다른 후보와의 비교": {"ALT"}, "다른 후보와의 비교 결과": {"ALT"},
    "다른 후보와 비교한 결과": {"ALT"}, "교체 검토 결과": {"ALT"},
    "대체 검토 결과": {"ALT"},
    "규칙 준수 현황": {"CON"}, "규칙 준수 여부": {"CON"}, "규칙 준수 상태": {"CON"}, "규칙 검토 결과": {"CON"},
    "규칙 검증 결과": {"CON"}, "규칙 위반 건수": {"CON"}, "배치 규칙 검토 결과": {"CON"}, "규칙 준수 결과": {"CON"},
}
CONNECTIVES = ("또한", "그리고", "아울러", "한편", "다만", "이어서", "다음으로", "마지막으로", "끝으로", "먼저", "우선")


@dataclass
class Fact:
    id: str
    kind: str
    text: str                      # 사실 한 줄(템플릿·화면 목록용, 주인 ID 포함)
    phrase: str = ""               # 자리표시에 들어갈 문구(사람별 사실은 주인 ID 없이 -- 주어가 그 사람일 때)
    owners: tuple = ()             # 주인(사람 ID). 첫째가 그 사실의 주어(배치된·평가받은 사람)
    skill: str | None = None
    status: str | None = None      # REQ·SKL: "met" | "short"
    phrase_full: str = ""          # 그 밖의 자리에 쓸 문구(비우면 사람별 사실은 "주인의 phrase", 나머지는 phrase)
    adverse: bool = False          # 불리한 사실(요구 부족·기술 미달·규칙 위반·빈자리·점수가 오르는 다른 후보·부정 평가) -- 인용률 보고용

    def __post_init__(self):
        if not self.phrase_full:
            self.phrase_full = f"{self.owners[0]}의 {self.phrase}" if self.kind in SUBJECT_KINDS and self.owners else self.phrase


@dataclass
class JustificationInput:
    project_id: str
    project: str
    team: list[str]
    facts: list[Fact]
    known_ids: set[str] = field(default_factory=set)      # 그래프의 모든 사람·사업 ID(지어낸 ID 판별용)
    evidence: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------- 사실 만들기
_DIGIT_SOUND = {"0": (True, False), "1": (True, True), "2": (False, False), "3": (True, False), "4": (False, False),
                "5": (False, False), "6": (True, False), "7": (True, True), "8": (True, True), "9": (False, False)}   # (받침, ㄹ받침)


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사. pair: "은/는"·"이/가"·"을/를"·"과/와"·"으로/로". 끝 괄호 묶음은 건너뛰고 본다(숫자는 읽는 소리 기준)."""
    w = word.rstrip()
    while w.endswith(")") and "(" in w:
        w = w[:w.rfind("(")].rstrip()
    ch = w[-1:] or "가"
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        bat, rieul = jong != 0, jong == 8
    elif ch in _DIGIT_SOUND:
        bat, rieul = _DIGIT_SOUND[ch]
    else:
        bat, rieul = ch.upper() in "LMNR", ch.upper() in "LR"
    a, b = pair.split("/")
    if pair == "으로/로":
        return b if (not bat or rieul) else a
    return a if bat else b


def _skill_bits(c: dict) -> tuple[str, str]:
    imp = c.get("implied_from")
    src = f"하위 기술 {imp['skill']} {imp['months']}개월에서 일부 인정" if imp else ""
    st = "충족" if c["status"] == "met" else "미달"
    text_tail = f"{c['skill']} 경력 {c['months']}개월 = 레벨 {c['level']}(요구 레벨 {c['min_level']})" + (f", {src}" if imp else "") + f" → {st}"
    phrase = f"{c['skill']} 경력 {c['months']}개월" + (f"({src})" if imp else "") + f"로 레벨 {c['level']}(요구 레벨 {c['min_level']}) {st}"
    return text_tail, phrase


def _polarity(v) -> str:
    return "판정 없음" if v is None else f"판정 {v}(-1 부정 ~ +1 긍정)"


def facts_from_evidence(ev: dict, violations: int, shortfall: int = 0) -> list[Fact]:
    """근거 → 번호 붙은 사실(F1…): 한 줄 사실(text)과 자리표시 문구(phrase·phrase_full), 검사용 주인·기술·상태.
    violations·shortfall: 배치 전체를 현행 평가기(plan_eval)로 다시 잰 규칙 위반 수·등급 정원 빈자리 수."""
    out: list[Fact] = []

    def add(kind, text, phrase, owners=(), skill=None, status=None, full="", adverse=None):
        adverse = (status == "short") if adverse is None else adverse
        out.append(Fact(f"F{len(out) + 1}", kind, text, phrase, tuple(owners), skill, status, full, adverse))
    team = [m["person_id"] for m in ev["members"]]
    head = f"{ev['project']}(고객사 {ev.get('client') or '미상'}, 산업 {ev.get('industry') or '미상'}, 팀 {len(team)}명: {', '.join(team)})"
    add("PRJ", f"사업 {ev['project_id']} {head}", f"{ev['project_id']} {head}")
    for r in ev["requirements"]:
        st = "met" if r["status"] == "met" else "short"
        word = "충족" if st == "met" else "부족"
        lv = level_from_months(r["min_months"] or 0)
        add("REQ", f"요구 기술 {r['skill']}: {r['headcount']}명 필요(최소 경력 {r['min_months']}개월 = 레벨 {lv}), 팀에서 해당자 {r['met_by']}명 → {word}",
            f"{r['skill']} 요구 {r['headcount']}명(레벨 {lv} 이상) 대비 해당자 {r['met_by']}명으로 {word}", skill=r["skill"], status=st)
    for m in ev["members"]:
        p = m["person_id"]
        add("MEM", f"{p}({m['grade']}) 투입률 {m['alloc']}, 기술 적합 {m['skill_fit']}",
            f"{m['grade']} 등급, 투입률 {m['alloc']}, 기술 적합 {m['skill_fit']}", owners=(p,))
        for c in m["requirements"]:
            if c["status"] != "missing":
                tail, phrase = _skill_bits(c)
                add("SKL", f"{p}의 {tail}", phrase, owners=(p,), skill=c["skill"], status="met" if c["status"] == "met" else "short")
        if m["same_industry_projects"]:              # None(산업 모름)·0은 사실로 넣지 않는다
            add("IND", f"{p}: 같은 산업 과거 사업 {m['same_industry_projects']}건, {m['same_industry_months']}개월",
                f"같은 산업 과거 사업 {m['same_industry_projects']}건·{m['same_industry_months']}개월 경험", owners=(p,))
        if m["same_client_projects"]:
            names = ", ".join(m["same_client_projects"][:2])
            add("CLI", f"{p}: 같은 고객사 과거 사업 {len(m['same_client_projects'])}건({names})",
                f"같은 고객사 과거 사업 {len(m['same_client_projects'])}건({names}) 경험", owners=(p,))
        for cw in m["cowork_in_team"][:3]:
            if cw["with"] > p:
                q = cw["with"]
                body = f"{p}{josa(p, '과/와')} {q}{josa(q, '이/가')} 함께 일한 기간 {cw['months_total']}개월(최근 3년 {cw['months_recent']}개월)"
                add("CW", body, body, owners=(p, q))
        for rv in m["reviews_from_team"][:2]:
            labels = ", ".join(rv["labels"][:3])
            fr = rv["from"]
            add("REV", f"{fr}{josa(fr, '이/가')} {p}{josa(p, '을/를')} 평가: {_polarity(rv['polarity'])}, 항목 {labels}",
                f"동료 {fr}의 평가 {_polarity(rv['polarity'])}, 항목 {labels}", owners=(p, fr),
                full=f"{p}에 대한 동료 {fr}의 평가 {_polarity(rv['polarity'])}, 항목 {labels}",
                adverse=rv["polarity"] is not None and rv["polarity"] < 0)
        for a in (ev.get("alternatives", {}).get(p) or [])[:1]:
            v = f", 새 위반 {len(a['new_violations'])}건" if a["new_violations"] else ", 새 위반 없음"
            q = a["person_id"]
            body = (f"{p} 대신 {q}(기술 적합 {a['skill_fit']}){josa(q, '으로/로')} 바꾸면 전체 점수 {a['delta_total']:+.2f}"
                    f"(기술 {a['delta_skill']:+.2f}, 협업 {a['delta_synergy']:+.2f}){v}")
            add("ALT", body, body, owners=(p, q), adverse=a["delta_total"] > 0)
    con = f"규칙 위반(예산·가용률·정원·동시 사업·투입률) {violations}건, 등급 정원 빈자리 {shortfall}석(현행 평가기로 다시 계산)"
    add("CON", f"이 배치 전체의 {con}", f"배치 전체 {con}", adverse=violations > 0 or shortfall > 0)
    return out


def justification_input(kg, project_id: str, entries, *, graph, S, C, params) -> JustificationInput:
    """사업 하나의 소명 재료. entries: 배치 전체(규칙 위반·빈자리는 배치 전체를 현행 평가기로 잰다)."""
    from core.evaluate.plan_eval import evaluate_plan
    from core.kg.views import project_evidence
    ev = project_evidence(kg, project_id, entries, graph=graph, S=S, C=C, params=params)
    pe = evaluate_plan(graph, S, C, params, list(entries))
    ids = {n.split(":", 1)[1] for n, v in kg.nodes.items() if v["type"] in ("person", "project")}
    return JustificationInput(project_id=project_id, project=ev["project"], team=[m["person_id"] for m in ev["members"]],
                              facts=facts_from_evidence(ev, len(pe.violations), sum(s.missing for s in pe.shortfalls)),
                              known_ids=ids, evidence=ev)


def template_text(facts: list[Fact]) -> str:
    by: dict[str, list[Fact]] = {}
    for f in facts:
        by.setdefault(f.kind, []).append(f)
    return "\n".join(f"{head}: {f.text}. [{f.id}]" for kind, head in TEMPLATE_ORDER for f in by.get(kind, []))


# ---------------------------------------------------------------- 문법(허용 목록)
SLOT = re.compile(r"\{\s*(F\d+)\s*\}")
_ID = r"[A-Za-z]+\d+"
_IDS = rf"{_ID}(?:\s*(?:,|과|와|및)\s*{_ID})*"
_LABEL_RE = "|".join(re.escape(lb).replace(r"\ ", r"\s*") for lb in sorted(LABELS, key=len, reverse=True))
_LABEL_KEY = {lb.replace(" ", ""): lb for lb in LABELS}
_HEAD = (rf"(?:(?P<sids>{_IDS})\s*(?:은|는|이|가|의\s*경우(?:에는|는)?)(?P<each>\s*각각)?"
         rf"|(?:(?P<tids>{_IDS})\s*(?:의|이|가)\s*)?(?P<label>{_LABEL_RE})\s*(?:은|는|으로는|로는|에서는)(?:\s*각각)?)(?:\s*,)?")
_CONN = "(?:" + "|".join(CONNECTIVES) + ")"
LEAD = re.compile(rf"\s*(?:{_CONN}\s*,?\s*)?(?:{_HEAD})?\s*")
MID = re.compile(rf"\s*(?:,|이며|이고|및|와|과|그리고)(?:\s*,)?(?:\s*(?:그리고|또한|및))?(?:\s*,)?\s*(?:{_HEAD})?\s*")
TAIL = re.compile(r"\s*(?:입니다|이다|(?:이|가)?\s*있습니다)?\s*[.!]?\s*")
CLOSING = re.compile(rf"\s*(?:이상(?:의|과\s*같은)\s*근거로\s*(?:(?P<cids>{_IDS})\s*(?:을|를)\s*)?(?:이\s*사업에\s*)?(?:이\s*)?"
                     rf"배치(?:를\s*제안)?합니다|이상으로\s*소명을\s*마칩니다)\s*[.]?\s*")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n", text) if s.strip()]


def _head(m: re.Match) -> tuple[list[str] | None, str | None, bool] | None:
    """문법 일치 결과에서 머리(주어 ID들, 라벨, "각각"). 머리가 없으면 None."""
    if m.group("sids"):
        return re.findall(_ID, m.group("sids")), None, bool(m.group("each"))
    if m.group("label"):
        return (re.findall(_ID, m.group("tids")) if m.group("tids") else None), _LABEL_KEY[re.sub(r"\s+", "", m.group("label"))], False
    return None


def _analyze(raw: str, inp: JustificationInput) -> dict:
    """AI 원문 분석: 위반 목록, 자리표시마다 짧은 문구를 쓸지(원문 등장 순서), 인용, 팀원별 사람 사실 수."""
    facts = {f.id: f for f in inp.facts}
    team = set(inp.team)
    viol: list[dict] = []
    short: list[bool] = []
    placed = {p: 0 for p in inp.team}
    cited: set[str] = set()

    def bad(rule, sentence, detail):
        viol.append({"rule": rule, "sentence": sentence[:200], "detail": detail})

    def check_ids(s, ids):
        for t in ids or ():
            if t not in team:
                bad("S3", s, f"주어·라벨 자리의 {t}는 " + ("이 팀원이 아니다" if t in inp.known_ids else "없는 ID다"))
    sentences = split_sentences(raw or "")
    for s in sentences:
        slots = list(SLOT.finditer(s))
        if not slots:
            m = CLOSING.fullmatch(s)
            if m is None:
                bad("S2", s, "자리표시 없는 문장(정해진 맺음말만 쓸 수 있다)")
            else:
                check_ids(s, re.findall(_ID, m.group("cids") or ""))
            continue
        frags = [s[:slots[0].start()]] + [s[a.end():b.start()] for a, b in zip(slots, slots[1:])] + [s[slots[-1].end():]]
        ids, label, each, mixed, seq, covered = None, None, False, False, [], set()

        def close_scope():                       # 여러 주어: 주어마다 자기 사실(사람별 또는 그 사람이 나오는 이력·비교)이 하나 이상,
            if ids and len(ids) > 1:             # "각각"이면 사람별 사실 수·순서가 주어와 같다
                if each and seq != list(ids):
                    bad("S4", s, f"'각각' 대응이 맞지 않다: 주어 {ids}, 사람별 사실 주인 {seq}")
                elif not each and set(ids) - covered:
                    bad("S4", s, f"주어 {sorted(set(ids) - covered)}에게 붙은 사실이 없다")
        for i, slot in enumerate(slots):
            m = (LEAD if i == 0 else MID).fullmatch(frags[i])
            if m is None:
                bad("S2", s, f"허용 목록 밖 연결 말 '{frags[i].strip()}'")
                close_scope()
                ids, label, each, mixed, seq, covered = None, None, False, False, [], set()
            else:
                h = _head(m)
                if h is not None:
                    close_scope()
                    (ids, label, each), mixed, seq, covered = h, False, [], set()
                    check_ids(s, ids)
            f = facts.get(slot.group(1))
            if f is None:
                bad("S1", s, f"없는 자리표시 {{{slot.group(1)}}}")
                short.append(False)
                continue
            cited.add(f.id)
            if label is not None and f.kind not in LABELS[label]:
                bad("S4", s, f"'{label}' 뒤에 {f.kind} 사실 {{{f.id}}}")
            ok_short = False
            if ids:
                if f.kind in SUBJECT_KINDS:
                    if f.owners[0] not in ids:
                        bad("S4", s, f"{{{f.id}}}는 {f.owners[0]}의 사실인데 주어가 {ids}")
                    else:
                        placed[f.owners[0]] += 1
                        seq.append(f.owners[0])
                        covered.add(f.owners[0])
                        ok_short = len(ids) == 1 and not mixed
                elif f.owners:
                    if not set(ids) <= set(f.owners):
                        bad("S4", s, f"{{{f.id}}}(사람 {list(f.owners)}) 앞 주어 {ids}")
                    covered |= set(f.owners)
                else:
                    bad("S4", s, f"사업 전체 사실 {{{f.id}}} 앞에 사람 주어 {ids}")
            elif f.kind in SUBJECT_KINDS:
                placed[f.owners[0]] += 1
            if f.kind == "PRJ" or len(f.owners) > 1:      # 문구에 다른 사람 ID가 있다 -- 뒤의 사람별 사실은 주인 ID를 붙인다
                mixed = True
            short.append(ok_short)
        close_scope()
        if TAIL.fullmatch(frags[-1]) is None:
            bad("S2", s, f"허용 목록 밖 꼬리 '{frags[-1].strip()}'")
    return {"violations": viol, "short": short, "placed": placed, "cited": cited, "sentences": sentences}


_PAIRS = {"은": "은/는", "는": "은/는", "이": "이/가", "가": "이/가", "과": "과/와", "와": "과/와", "을": "을/를", "를": "을/를"}
_ID_JOSA = re.compile(r"([A-Za-z]+\d+)(은|는|이|가|과|와|을|를)(?![가-힣])")


def _fix_josa(raw: str) -> str:
    """AI 연결 말의 "ID+조사"를 받침에 맞게 고친다("DP0033는" → "DP0033은"). 자리표시 안은 건드리지 않는다(문법상 ID는 머리에만 있다)."""
    return _ID_JOSA.sub(lambda m: m.group(1) + josa(m.group(1), _PAIRS[m.group(2)]), raw)


def render(raw: str, inp: JustificationInput) -> str:
    """AI 원문의 {F#}를 사실 문구로 채우고 뒤에 근거 번호 [F#]를 단다(화면 칩용). 짧은 문구는 주어가 한 사람이고 그 주인일 때만,
    그 밖에는 주인 ID가 붙은 문구. 없는 번호는 그대로 둔다(검사에서 거른다). ID 뒤 조사는 받침에 맞게 고친다."""
    facts = {f.id: f for f in inp.facts}
    short = iter(_analyze(raw, inp)["short"])
    raw = _fix_josa(raw)

    def fill(m):
        f = facts.get(m.group(1))
        use_short = next(short, False)
        if f is None:
            return m.group(0)
        return f"{f.phrase if use_short else f.phrase_full}[{f.id}]"
    return SLOT.sub(fill, raw)


def render_ok(raw: str, text: str) -> bool:
    """채운 글 안전 확인(검사와 따로): 중괄호가 남지 않고, 근거 칩 수가 자리표시 수와 같다(AI가 직접 쓴 [F#] 칩이 없다)."""
    return "{" not in text and "}" not in text and len(re.findall(r"\[F\d+\]", text)) == len(SLOT.findall(raw))


def verify(raw: str, inp: JustificationInput) -> dict:
    """AI 원문(자리표시 포함) 검사. 반환: {ok, violations: [{rule, sentence, detail}], sentences, cited, coverage, chars}."""
    if not raw or not raw.strip() or not SLOT.search(raw):
        return {"ok": False, "violations": [{"rule": "S6", "sentence": (raw or "")[:200], "detail": "빈 글이거나 자리표시가 없다"}],
                "sentences": 0, "cited": [], "coverage": {}, "chars": len(raw or "")}
    a = _analyze(raw, inp)
    viol = a["violations"]
    missing = [p for p, n in a["placed"].items() if n == 0]
    if missing:
        viol.append({"rule": "S5", "sentence": "", "detail": f"사람별 사실이 하나도 붙지 않은 팀원 {missing}"})
    kinds = {f.id: f.kind for f in inp.facts}
    have = {kinds[c] for c in a["cited"]}
    avail = {f.kind for f in inp.facts}
    cov = {name: (prefix in have) if prefix in avail else None for name, prefix in ELEMENTS.items()}
    adv = [f.id for f in inp.facts if f.adverse]
    return {"ok": not viol, "violations": viol, "sentences": len(a["sentences"]),
            "cited": sorted(a["cited"], key=lambda x: int(x[1:])), "coverage": cov, "chars": len(render(raw, inp)),
            "adverse": {"available": len(adv), "cited": sum(1 for x in adv if x in a["cited"])}}


def fragments(raw: str) -> list[str]:
    """AI가 쓴 연결 말 조각(감사용): 자리표시 사이 글과 자리표시 없는 문장."""
    out = []
    for s in split_sentences(raw or ""):
        out += [p.strip() for p in SLOT.split(s)[::2] if p.strip()]
    return out
