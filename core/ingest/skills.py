"""IT 기술 이름 사전(의미 레이어, 2026-10-09, claude-a) -- 같은 기술의 여러 이름을 대표 이름 하나로, 하위 기술 경력을 상위 기술에 일부 인정.

사전 파일 `skill_dictionary.json`은 SKOS 구조를 따른 JSON이다(label=prefLabel, aliases=altLabel, broader, related). 그래프 DB·트리플 저장소 없이
파일로 보관하고, 표준 교환이 필요하면 `to_turtle()`로 SKOS Turtle을 내보낸다(실험 E8 규칙 5 근거, docs/kg-technology-decision.md).

쓰는 곳은 입력 단계 한 곳(core/ingest/convert.to_dataset)과, 원 CSV를 다시 읽는 지식 그래프(core/kg/graph.build_kg)다 -- 둘 다 `person_skill_months`를
불러 같은 결과를 낸다. 점수 엔진(core/scoring)은 바꾸지 않는다: 입력 단계가 만든 레벨을 그대로 쓴다.

이름 비교 키: NFKC → casefold → 공백과 `-_./·・` 제거(`+`·`#`·`*`·`&`·괄호는 남긴다: C/C++/C#, Pro*C 구분).
사전에 그대로 없으면 두 가지 표기 규칙만 더 본다(리뷰 SHOULD -- 실데이터에 흔한 변형): (1) 끝의 버전 표기("Java 8", "Spring Boot 2.x", "Oracle 19c"),
(2) 괄호 병기("자바(Java)", "Kubernetes(K8s)") -- 괄호 안팎이 **모두 같은 개념**일 때만. 그래도 없으면 합치지 않고 이름 그대로 둔다("C/C++"처럼 둘을 묶은 이름 포함).
"""
from __future__ import annotations

import json
import math
import numbers
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DICT_PATH = Path(__file__).with_name("skill_dictionary.json")
_STRIP = re.compile(r"[\s\-_./·・]+")
_VERSION = re.compile(r"[\s\-_]*(?:\b(?:v|ver\.?|version)\s*)?\d+(?:\.(?:\d+|x))*[a-z]{0,2}\+?$", re.IGNORECASE)
_MINOR = re.compile(r"(\d+)(?:\.(?:\d+|x))+[a-z]{0,2}\+?$", re.IGNORECASE)
_PAREN = re.compile(r"^(.*?)\s*[(（]([^()（）]+)[)）]\s*$")


def norm_key(name: str) -> str:
    return _STRIP.sub("", unicodedata.normalize("NFKC", str(name)).casefold())


def validate(data: dict) -> list[str]:
    """사전 파일 검사 -- 문제 목록(비면 통과). 별칭 키가 두 개념에 걸치거나, 상위·관련 개념이 없거나, 상위 관계가 순환하면 안 된다."""
    errs: list[str] = []
    concepts = data.get("concepts") or []
    for i, c in enumerate(concepts):
        if not c.get("id"):
            errs.append(f"{i}번째 개념에 id가 없다")
    if any(not c.get("id") for c in concepts):
        return errs
    ids = [c.get("id") for c in concepts]
    for i in {x for x in ids if ids.count(x) > 1}:
        errs.append(f"id 중복: {i}")
    known = set(ids)
    owner: dict[str, str] = {}
    for c in concepts:
        if not c.get("label"):
            errs.append(f"{c.get('id')}: label이 없다")
        for name in [c.get("label", "")] + list(c.get("aliases") or []):
            k = norm_key(name)
            if not k:
                errs.append(f"{c['id']}: 빈 이름")
                continue
            if k in owner and owner[k] != c["id"]:
                errs.append(f"이름 '{name}'(키 {k})이 {owner[k]}와 {c['id']}에 함께 걸린다")
            owner.setdefault(k, c["id"])
        for rel in ("broader", "related"):
            for b in c.get(rel) or []:
                if b not in known:
                    errs.append(f"{c['id']}.{rel}: 없는 개념 {b}")
                if b == c["id"]:
                    errs.append(f"{c['id']}.{rel}: 자기 자신")
    parents = {c["id"]: [b for b in c.get("broader") or [] if b in known] for c in concepts}
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            errs.append("상위 관계 순환: " + " → ".join(path + [n]))
            return
        state[n] = 1
        for p in parents.get(n, []):
            visit(p, path + [n])
        state[n] = 2
    for n in parents:
        visit(n, [])
    credit = (data.get("partial_credit") or {}).get("narrower_to_broader", 0)
    if isinstance(credit, bool) or not isinstance(credit, numbers.Real) or not (0 <= credit <= 1):
        errs.append(f"partial_credit.narrower_to_broader는 0~1이어야 한다: {credit}")
    return errs


@dataclass(frozen=True)
class SkillDictionary:
    version: str
    narrower_credit: float
    concepts: dict[str, dict] = field(repr=False)                    # id → 개념
    by_key: dict[str, str] = field(repr=False)                       # 이름 키 → id
    by_label: dict[str, str] = field(repr=False)                     # 대표 이름 → id

    def resolve(self, raw: str) -> tuple[dict | None, str]:
        """(개념 또는 None, 방법: exact·version·paren·unknown)."""
        cid = self.by_key.get(norm_key(raw))
        if cid:
            return self.concepts[cid], "exact"
        text = str(raw).strip()
        major = _MINOR.sub(r"\1", text)             # "Angular 1.x" → "Angular 1"(AngularJS의 별칭)을 먼저 본다(리뷰 MUST)
        if major != text and major in self._raw_keys_cache():
            return self.concepts[self.by_key[norm_key(major)]], "version"
        m = _VERSION.search(text)
        stripped = text[:m.start()].strip() if m else ""
        # 버전을 떼고 남은 키가 3자 미만("R12"→R, "C4I"→C, "DL380"→딥러닝)이거나 숫자 바로 앞이 "/"("R/3")이면 맞추지 않는다 -- 다른 기술로 잘못 합친다
        if stripped and m and len(norm_key(stripped)) >= 3 and not text[:m.start()].endswith("/"):
            cid = self.by_key.get(norm_key(stripped))
            if cid:
                return self.concepts[cid], "version"
        m = _PAREN.match(str(raw))
        if m:
            parts = [x.strip() for x in m.groups() if x.strip()]
            found = set()
            for part in parts:
                c, how = self.resolve(part)
                if c is None or how == "paren":
                    return None, "unknown"
                found.add(c["id"])
            if len(found) == 1:
                return self.concepts[found.pop()], "paren"
        return None, "unknown"

    def _raw_keys_cache(self):
        return _KeyView(self.by_key)

    def lookup(self, raw: str) -> dict | None:
        return self.resolve(raw)[0]

    def canonical(self, raw: str) -> str:
        """대표 이름. 사전에 없으면 원래 이름 그대로(조용히 합치지 않는다)."""
        c = self.lookup(raw)
        return c["label"] if c else raw

    def known(self, raw: str) -> bool:
        return self.lookup(raw) is not None

    def ancestors(self, label: str) -> dict[str, int]:
        """대표 이름 → {상위 대표 이름: 가장 가까운 깊이}(1 = 바로 위). 사전에 없거나 상위가 없으면 빈 dict."""
        cid = self.by_label.get(label)
        out: dict[str, int] = {}
        frontier = [(cid, 0)] if cid else []
        while frontier:
            nxt = []
            for c, d in frontier:
                for b in self.concepts[c].get("broader") or []:
                    lb = self.concepts[b]["label"]
                    if lb not in out or out[lb] > d + 1:
                        out[lb] = d + 1
                        nxt.append((b, d + 1))
            frontier = nxt
        return out

    def to_turtle(self, base: str = "https://teamweaver.example/skill/") -> str:
        """SKOS Turtle 내보내기(외부 라이브러리 없이) -- 표준 교환이 필요할 때."""
        def lit(s: str) -> str:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        lines = ["@prefix skos: <http://www.w3.org/2004/02/skos/core#> .", f"@prefix tw: <{base}> .", "",
                 "tw:scheme a skos:ConceptScheme ;", f"  skos:prefLabel {lit('TeamWeaver 기술 이름 사전 ' + self.version)}@ko .", ""]
        for cid, c in self.concepts.items():
            parts = [f"tw:{cid} a skos:Concept", "skos:inScheme tw:scheme", f"skos:prefLabel {lit(c['label'])}"]
            parts += [f"skos:altLabel {lit(a)}" for a in c.get("aliases") or []]
            parts += [f"skos:broader tw:{b}" for b in c.get("broader") or []]
            parts += [f"skos:related tw:{r}" for r in c.get("related") or []]
            lines.append(" ;\n  ".join(parts) + " .")
        return "\n".join(lines) + "\n"


class _KeyView:
    """`이름 in 키들`을 이름 비교 키로 묻는 얇은 보기."""
    def __init__(self, by_key):
        self.by_key = by_key

    def __contains__(self, name: str) -> bool:
        return norm_key(name) in self.by_key


def from_data(data: dict) -> SkillDictionary:
    errs = validate(data)
    if errs:
        raise ValueError("기술 이름 사전 오류:\n" + "\n".join(errs[:20]))
    concepts = {c["id"]: c for c in data["concepts"]}
    by_key = {norm_key(n): c["id"] for c in data["concepts"] for n in [c["label"], *(c.get("aliases") or [])]}
    return SkillDictionary(version=str(data.get("version", "")),
                           narrower_credit=float((data.get("partial_credit") or {}).get("narrower_to_broader", 0.0)),
                           concepts=concepts, by_key=by_key, by_label={c["label"]: c["id"] for c in data["concepts"]})


@lru_cache(maxsize=4)
def load_dictionary(path: str | None = None) -> SkillDictionary:
    return from_data(json.loads(Path(path or DICT_PATH).read_text("utf-8")))


@dataclass
class SkillMonths:
    """한 사람의 기술 경력(개월). months: 대표 이름 → 쓸 경력, own: 본인 행에서 온 경력, implied: 하위 기술에서 인정된 경력과 출처."""
    months: dict[str, int] = field(default_factory=dict)
    own: dict[str, int] = field(default_factory=dict)
    implied: dict[str, dict] = field(default_factory=dict)          # 대표 이름 → {"months", "from", "from_months", "depth"}
    raw_names: dict[str, list[str]] = field(default_factory=dict)   # 대표 이름 → 원래 이름들


def person_skill_months(rows: list[tuple[str, int]], sd: SkillDictionary | None, credit: float | None = None) -> SkillMonths:
    """rows: (원래 기술 이름, 경력 개월) -- 10년 창·120개월 상한·마지막 사용 시점 규칙을 이미 적용한 행.
    1) 이름을 대표 이름으로(같은 대표 이름이 여럿이면 가장 긴 경력), 2) 하위 → 상위 부분 인정(계수^깊이 × 개월, 내림), 3) 쓸 경력 = max(본인, 인정분).
    sd가 None이면 이름 그대로·인정 없음(예전 동작, 단 겹치는 행은 가장 긴 경력)."""
    out = SkillMonths()
    for raw, m in rows:
        name = sd.canonical(raw) if sd else raw
        out.raw_names.setdefault(name, [])
        if raw not in out.raw_names[name]:
            out.raw_names[name].append(raw)
        if m > out.own.get(name, 0):
            out.own[name] = m
    out.months = dict(out.own)
    k = (sd.narrower_credit if credit is None else credit) if sd else 0.0
    if isinstance(k, bool) or not isinstance(k, numbers.Real) or not 0 <= k <= 1:
        raise ValueError(f"부분 인정 계수는 0~1이어야 한다: {k!r}")      # 1을 넘으면 인정분이 본인 경력·120개월 상한을 넘는다
    if sd and k > 0:
        for name, m in sorted(out.own.items()):
            for anc, depth in sd.ancestors(name).items():
                give = math.floor(m * (k ** depth) + 1e-9)            # 0.29 × 100 = 28.999… 같은 부동소수 내림 오차를 막는다
                if give <= 0:
                    continue
                cur = out.implied.get(anc)
                if cur is None or give > cur["months"]:
                    out.implied[anc] = {"months": give, "from": name, "from_months": m, "depth": depth}
        for anc, imp in out.implied.items():
            if imp["months"] > out.months.get(anc, 0):
                out.months[anc] = imp["months"]
    return out
