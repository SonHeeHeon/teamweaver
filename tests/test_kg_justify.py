"""인사팀 소명 글(GraphRAG, 자리표시 + 허용 목록 방식) -- 사실·템플릿·검사(core/kg/justify.py)와 AI 호출·대체(api/rag/justification.py).

AI는 정해진 문법(주어·주제 라벨·연결어·맺음말)으로 자리표시 {F#}를 엮기만 한다. 폴백 리뷰 3회에서 나온 거짓 통과·거짓 탈락 예문을 회귀 시험으로 둔다
(자유 문장 시절의 판단어 활용형, 판단어 목록 밖 표현, 다른 사람 사실 붙이기, 묶음 자리표시·위조 근거 칩 등)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.kg.justify import Fact, JustificationInput, josa, justification_input, render, render_ok, template_text, verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inp():
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(ROOT / "demo" / "org-n100")
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    entries = [AssignEntry(**e) for e in json.loads((ROOT / "demo" / "precomputed" / "org-n100.json").read_text("utf-8"))["optimize"]["plans"][0]["entries"]]
    return justification_input(build_kg(b, ds, parsed), "J003", entries, graph=g, S=S, C=C, params=params)


def _small():
    facts = [Fact("F1", "PRJ", "사업 J1 데이터 마트", "J1 데이터 마트(고객사 미상, 산업 금융, 팀 2명: P1, P2)"),
             Fact("F2", "REQ", "요구 기술 ETL", "ETL 요구 2명(레벨 4 이상) 대비 해당자 2명으로 충족", (), "ETL", "met"),
             Fact("F3", "MEM", "P1(중급)", "중급 등급, 투입률 0.3, 기술 적합 0.7", ("P1",)),
             Fact("F4", "SKL", "P1의 ETL", "ETL 경력 79개월로 레벨 4(요구 레벨 4) 충족", ("P1",), "ETL", "met"),
             Fact("F5", "SKL", "P1의 Spark", "Spark 경력 8개월로 레벨 1(요구 레벨 2) 미달", ("P1",), "Spark", "short"),
             Fact("F6", "MEM", "P2(고급)", "고급 등급, 투입률 0.5, 기술 적합 0.9", ("P2",)),
             Fact("F7", "CW", "P1과 P2", "P1과 P2가 함께 일한 기간 19개월(최근 3년 4개월)", ("P1", "P2")),
             Fact("F8", "REV", "P2가 P1을 평가", "동료 P2의 평가 판정 0.667(-1 부정 ~ +1 긍정), 항목 전문성", ("P1", "P2"),
                  phrase_full="P1에 대한 동료 P2의 평가 판정 0.667(-1 부정 ~ +1 긍정), 항목 전문성"),
             Fact("F9", "ALT", "P1 대신 P9", "P1 대신 P9(기술 적합 0.5)로 바꾸면 전체 점수 -0.19(기술 -0.02, 협업 -0.17), 새 위반 없음", ("P1", "P9")),
             Fact("F10", "CON", "규칙 위반 0건", "배치 전체 규칙 위반 0건, 등급 정원 빈자리 0석"),
             Fact("F11", "SKL", "P2의 ETL", "ETL 경력 60개월로 레벨 4(요구 레벨 4) 충족", ("P2",), "ETL", "met"),
             Fact("F12", "IND", "P2 같은 산업", "같은 산업 과거 사업 3건·20개월 경험", ("P2",))]
    return JustificationInput("J1", "데이터 마트", ["P1", "P2"], facts, {"P1", "P2", "P7", "P9", "J1"} - {"P7"})


GOOD = ("이 사업은 {F1}이며 요구 기술은 {F2}입니다. P1은 {F3}이고 {F4}이며, {F5}입니다. 동료 평가는 {F8}입니다. "
        "P2는 {F6}이며 {F11}입니다. 함께 일한 이력은 {F7}입니다. 후보 비교 결과는 {F9}입니다. 마지막으로 규칙 준수 현황은 {F10}입니다. "
        "이상의 근거로 이 배치를 제안합니다.")


def test_good_text_passes_and_renders():
    s = _small()
    v = verify(GOOD, s)
    assert v["ok"], v["violations"]
    t = render(GOOD, s)
    assert "P1은 중급 등급, 투입률 0.3, 기술 적합 0.7[F3]이고 ETL 경력 79개월로 레벨 4(요구 레벨 4) 충족[F4]" in t
    assert "동료 평가는 P1에 대한 동료 P2의 평가" in t and "{" not in t and render_ok(GOOD, t)


@pytest.mark.parametrize("text, rule", [
    (GOOD.replace("{F10}", "{F99}"), "S1"),                                                          # 없는 자리표시
    (GOOD.replace("P2는 {F6}", "P2는 79개월 경력으로 {F6}"), "S2"),                                     # 연결 말에 숫자
    (GOOD + " 팀원 모두 요구 수준을 만족합니다.", "S2"),                                                  # 자리표시 없는 단정(리뷰 2회째)
    (GOOD + " P1의 Spark는 요구에 못 미칩니다.", "S2"),
    (GOOD.replace("P1은 {F3}", "고급 인력인 P1은 {F3}"), "S2"),                                        # 판단어 목록 밖 표현(리뷰 3회째 MUST-3)
    (GOOD.replace("P1은 {F3}", "P1은 ETL 전문가로 {F3}"), "S2"),
    (GOOD.replace("P1은 {F3}", "P1은 ETL 역량이 미흡하지만 {F3}"), "S2"),
    (GOOD.replace("P1은 {F3}", "십여 년 경력의 P1은 {F3}"), "S2"),
    (GOOD.replace("P2는 {F6}", "동료들에게 칭찬받는 P2는 {F6}"), "S2"),
    (GOOD.replace("P1은 {F3}", "Top3 인재인 P1은 {F3}"), "S2"),
    (GOOD.replace("P1은 {F3}", "P1은 절반만 투입되며 {F3}"), "S2"),
    (GOOD.replace("P1은 {F3}", "다른 후보보다 P1이 {F3}"), "S2"),
    (GOOD.replace("P1은 {F3}이고 {F4}", "P1은 ETL 충족 결과 {F4}"), "S2"),                            # 주제 표현으로 가린 판정(MUST-4)
    (GOOD.replace("P1은 {F3}이고 {F4}", "P1은 요구 레벨 이상으로 {F4}"), "S2"),
    (GOOD + " 나머지 팀원도 같은 수준입니다.", "S2"),                                                    # ID 없는 넘기기(MUST-5)
    (GOOD.replace("P2는 {F6}이며 {F11}입니다.", "P2는 {F6}이며 {F11}이나 Spark는 못 맡습니다."), "S2"),
    (GOOD.replace("P2는 {F6}이며 {F11}입니다.", "P2는 {F6}이며 {F11}입니다, P1도 마찬가지입니다."), "S2"),
    (GOOD.replace("P2는 {F6}", "반면 P2는 {F6}"), "S2"),
    (GOOD.replace("요구 기술은 {F2}", "요구 기술은 {F2, F3}"), "S2"),                                    # 묶음 자리표시(MUST-2)
    (GOOD.replace("{F11}", "{f11}"), "S2"),
    (GOOD.replace("요구 기술은 {F2}입니다.", "요구 기술은 {F2}이며 팀 차원에서 해결됐습니다[F2]."), "S2"),   # 위조 근거 칩
    (GOOD.replace("P1은 {F3}이고 {F4}이며, {F5}", "P1은 {F3}이며 P2와 {F7}이고, {F11}"), "S2"),       # 다른 사람을 사이에 두고 그 사람 사실(MUST-1)
    (GOOD.replace("P2는 {F6}", "P7은 {F6}"), "S3"),                                                    # 지어낸 ID
    (GOOD.replace("P2는 {F6}", "P9는 {F6}"), "S3"),                                                    # 팀원이 아닌 사람
    (GOOD.replace("P2는 {F6}이며 {F11}", "P2는 {F6}이며 {F4}"), "S4"),                                  # 다른 사람(P1)의 사실을 P2에
    (GOOD.replace("P2는 {F6}이며 {F11}", "P2의 기술 근거는 {F4}"), "S4"),
    (GOOD + " P1은 {F2}입니다.", "S4"),                                                                 # 사업 전체 사실을 사람에게
    (GOOD.replace("함께 일한 이력은 {F7}", "같은 고객사 경험은 {F12}"), "S4"),                            # 라벨과 사실 종류가 다름
    (GOOD.replace("동료 평가는 {F8}", "동료 평가는 {F7}"), "S4"),
    (GOOD.replace("P2는 {F6}이며 {F11}", "P2의 기술 경력은 {F6}, {F11}"), "S4"),                         # 등급·투입 사실은 '기술 경력'이 아니다
    (GOOD.replace("함께 일한 이력은 {F7}", "산업 경험は {F12}"), "S2"),                                  # 다른 언어 글자(실제 모델 출력)
    ("이 사업은 {F1}입니다. P1은 {F3}입니다.", "S5"),                                                  # P2 빠짐
    ("소명합니다.", "S6"),
    ("", "S6"),
])
def test_each_rule_catches_its_error(text, rule):
    v = verify(text, _small())
    assert not v["ok"] and rule in {x["rule"] for x in v["violations"]}, v["violations"]


@pytest.mark.parametrize("text", [
    GOOD,
    GOOD.replace("동료 평가는 {F8}", "그리고 {F8}"),                                                   # 머리 없는 자리는 주인 ID를 붙여 채운다
    GOOD.replace("함께 일한 이력은 {F7}", "{F7}"),
    GOOD.replace("마지막으로 규칙 준수 현황은 {F10}", "규칙 검증 결과는 {F10}"),
    GOOD.replace("함께 일한 이력은 {F7}입니다.", "함께 일한 이력으로는 {F7}이 있습니다."),
    GOOD.replace("함께 일한 이력은 {F7}", "P1은 {F7}"),                                                # 함께 일한 이력은 그 두 사람 주어 뒤에 둘 수 있다
    GOOD.replace("P2는 {F6}이며 {F11}입니다.", "또한, P2는 {F6}이며 동일 산업 경험은 {F12}입니다. P2의 기술 근거는 {F11}입니다."),
    GOOD.replace("이상의 근거로 이 배치를 제안합니다.", "이상의 근거로 P1, P2를 배치합니다."),
    "이 사업은 {F1}입니다. P1과 P2는 각각 {F3}, {F6}입니다. 기술 근거는 {F4}, {F11}이며 {F5}입니다.",   # 리뷰 SHOULD-1 거짓 탈락 예
])
def test_correct_texts_are_not_rejected(text):
    v = verify(text, _small())
    assert v["ok"], v["violations"]
    assert render_ok(text, render(text, _small()))


def test_short_phrase_only_under_its_owner():
    s = _small()
    t = render("이 사업은 {F1}입니다. 기술 근거는 {F4}, {F11}입니다. P1과 P2는 각각 {F3}, {F6}입니다. P1은 {F5}이며, P2는 {F11}입니다.", s)
    assert "기술 근거는 P1의 ETL 경력 79개월" in t and "P2의 ETL 경력 60개월" in t                     # 머리가 라벨이면 주인 ID를 붙인다
    assert "각각 P1의 중급 등급" in t and "P2의 고급 등급" in t                                           # 주어가 여러 명이어도 붙인다
    assert "P1은 Spark 경력 8개월" in t and "P2는 ETL 경력 60개월" in t                                  # 주어 한 명 = 주인일 때만 짧은 문구
    t2 = render("P1은 {F3}입니다. 또한 {F4}입니다.", s)
    assert "또한 P1의 ETL" in t2                                                                         # 주어는 다음 문장으로 잇지 않는다


def test_render_fixes_particles_after_ids():
    s = _small()
    t = render("이 사업은 {F1}입니다. P1는 {F3}입니다. P2은 {F6}입니다. P1과 P2은 각각 {F4}, {F11}입니다.", s)
    assert "P1은 중급" in t and "P2는 고급" in t and "P1과 P2는 각각" in t


def test_render_ok_catches_leftover_braces_and_forged_chips():
    assert render_ok("가 {F1}", "가 x[F1]")
    assert not render_ok("가 {F1} [F2]", "가 x[F1] [F2]") and not render_ok("가 {F1}", "가 {x}[F1]")


@pytest.mark.parametrize("word, pair, want", [
    ("DP0006", "과/와", "과"), ("DP0032", "으로/로", "로"), ("DP0081", "이/가", "이"), ("DP0002", "이/가", "가"),
    ("DP0010", "으로/로", "으로"), ("데이터 마트(가나)", "은/는", "는"), ("서울", "으로/로", "로"), ("팀", "을/를", "을")])
def test_josa(word, pair, want):
    assert josa(word, pair) == want


def test_template_and_real_facts(inp):
    t = template_text(inp.facts)
    assert inp.facts[0].kind == "PRJ" and inp.facts[-1].kind == "CON" and all(f"[{f.id}]" in t for f in inp.facts)
    assert all(f.phrase for f in inp.facts)
    subj = [f for f in inp.facts if f.kind in {"MEM", "SKL", "IND", "CLI", "REV"}]
    assert subj and all(f.owners and f.owners[0] in inp.team and f.owners[0] not in f.phrase for f in subj)   # 짧은 문구에 주인 ID가 없다
    assert all(f.phrase_full.startswith(f.owners[0]) for f in subj)                                              # 긴 문구엔 있다
    con = inp.facts[-1].phrase
    assert "빈자리" in con and "독립 검증" not in con                                                           # 근거는 현행 평가기(리뷰 SHOULD-2)
    assert all("부정 ~ +1 긍정" in f.phrase or "판정 없음" in f.phrase for f in inp.facts if f.kind == "REV")      # 평가 점수 척도
    cw = [f for f in inp.facts if f.kind == "CW"]
    assert cw and all(f.phrase.startswith(f"{f.owners[0]}{josa(f.owners[0], '과/와')} {f.owners[1]}{josa(f.owners[1], '이/가')} ") for f in cw)
    raw = "이 사업은 {F1}입니다. " + " ".join(f"{p}는 " + ", ".join(f"{{{f.id}}}" for f in subj if f.owners[0] == p) + "입니다." for p in inp.team)
    v = verify(raw, inp)
    assert v["ok"], v["violations"]


def test_skill_phrase_carries_partial_credit_provenance():
    from core.kg.justify import _skill_bits
    text, phrase = _skill_bits({"skill": "Java", "months": 30, "level": 2, "min_level": 2, "status": "met",
                                "implied_from": {"skill": "Spring", "months": 60, "depth": 1, "credit": 0.5}})
    assert "하위 기술 Spring 60개월에서 일부 인정" in text and "하위 기술 Spring 60개월에서 일부 인정" in phrase and phrase.endswith("충족")


# ---------------------------------------------------------------- AI 호출·대체(가짜 클라이언트)
class _Client:
    def __init__(self, content=None, exc=None):
        self.content, self.exc, self.calls = content, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))


def test_accepted_text_is_rendered():
    from api.rag.justification import SYSTEM, generate_justification
    c = _Client(json.dumps({"text": GOOD}, ensure_ascii=False))
    out = generate_justification(c, "gpt-6-luna", _small())
    assert out["method"] == "graphrag" and out["fallback_reason"] is None
    assert out["text"] == render(GOOD, _small()) and out["llm_text"] == GOOD
    assert out["usage"]["cost_usd"] > 0 and c.calls[0]["reasoning_effort"] == "low"
    user = c.calls[0]["messages"][1]["content"]
    assert "P1, P2" in user and "{F4} [SKL, 사람: P1]" in user                  # 팀원 명단과 사실 종류·사람 표시를 알려 준다
    assert "'다만 {F7}입니다'" in user                                            # 지시문의 예시 중괄호가 format에 먹히지 않는다
    assert "'동료 평가'" in SYSTEM and "'규칙 준수 현황'" in SYSTEM                # 프롬프트와 검사기가 같은 라벨 목록을 쓴다


@pytest.mark.parametrize("client, reason", [
    (_Client(json.dumps({"text": GOOD.replace("{F11}", "{F4}")}, ensure_ascii=False)), "verify:S4"),
    (_Client("not json"), "llm_bad_json"),
    (_Client(json.dumps({"text": ["x"]})), "llm_bad_json"),
    (_Client(exc=TimeoutError("slow")), "llm_error:TimeoutError"),
    (None, "no_client"),
])
def test_fallback_to_template(client, reason):
    from api.rag.justification import generate_justification
    out = generate_justification(client, "gpt-6-luna", _small())
    assert out["method"] == "template" and out["fallback_reason"].startswith(reason)
    assert out["text"] == template_text(_small().facts)


def test_render_check_failure_falls_back(monkeypatch):
    import api.rag.justification as J
    monkeypatch.setattr(J, "render_ok", lambda raw, text: False)
    out = J.generate_justification(_Client(json.dumps({"text": GOOD}, ensure_ascii=False)), "gpt-6-luna", _small())
    assert out["method"] == "template" and out["fallback_reason"] == "render_check"


# ---------------------------------------------------------------- 폴백 리뷰 4회째 회귀: 다른 사람이 나오는 사실 뒤·"각각"·문법 추가·불리한 사실
def test_person_fact_after_a_fact_naming_others_gets_its_owner():
    s = _small()
    for raw, want in (("P1은 {F9}이며 {F4}입니다.", "P1의 ETL 경력 79개월"),            # 후보 비교(P9) 뒤 -- P9 것으로 읽히지 않게
                      ("P1은 {F7}이며 {F8}입니다.", "P1에 대한 동료 P2의 평가"),          # 함께 일한 이력(P2) 뒤
                      ("P1은 {F8}이며 {F4}입니다.", "P1의 ETL 경력 79개월")):              # 동료 평가(평가자 P2) 뒤
        assert want in render(raw, s), raw
    assert "P1은 중급 등급" in render("P1은 {F3}이며 {F9}입니다.", s)                    # 앞에 없으면 그대로 짧은 문구


def test_each_and_multi_subjects_must_match():
    s = _small()
    bad = ["P1과 P2는 각각 {F4}, {F5}입니다.", "P1과 P2는 각각 {F6}, {F3}입니다.", "P1과 P2는 각각 {F3}입니다.", "P1과 P2는 {F4}입니다."]
    for raw in bad:
        v = verify(raw, s)
        assert any(x["rule"] == "S4" for x in v["violations"]), raw
    ok = verify("이 사업은 {F1}입니다. P1과 P2는 각각 {F3}, {F6}입니다. P1과 P2는 {F4}, {F11}입니다.", s)
    assert ok["ok"], ok["violations"]


@pytest.mark.parametrize("text", [
    "이 사업은 {F1}입니다. P1은, {F3}입니다. P2의 경우 {F6}입니다.",                     # 머리 뒤 쉼표, "ID의 경우"(리뷰 SHOULD-3)
    "이 사업의 요구 기술은 {F2}입니다. 기술 근거는 각각 {F4}, {F11}입니다. 다만 {F5}입니다. P1과 P2가 함께 일한 이력은 {F7}입니다. P2는 {F6}입니다.",
    "P1의 근거는 {F3}, {F4}, {F9}입니다. 다른 후보와의 비교 결과는 {F9}입니다. P2의 배치 근거는 {F6}입니다.",
    "동료 평가 결과는 {F8}입니다. 협업 경험은 {F7}입니다. 후보 비교는 {F9}입니다. 규칙 준수 결과는 {F10}입니다. P1은 {F3}이며, P2는 {F6}입니다.",
])
def test_meaning_preserving_grammar_additions(text):
    v = verify(text, _small())
    assert v["ok"], v["violations"]


def test_adverse_facts_are_flagged_and_counted(inp):
    s = _small()
    v = verify(GOOD, s)
    assert v["adverse"] == {"available": 0, "cited": 0}                                       # 손으로 만든 사실엔 표시가 없다
    adv = [f for f in inp.facts if f.adverse]
    assert adv and all(f.status == "short" for f in adv if f.kind in ("REQ", "SKL"))
    assert all(f.adverse == (f.status == "short") for f in inp.facts if f.kind in ("REQ", "SKL"))


def _fuzz_texts(inp, n, seed):
    """문법 안에서 무작위로 엮은 글(머리 종류·자리표시·연결어를 섞는다)."""
    import random
    from core.kg.justify import CONNECTIVES, LABELS
    rng = random.Random(seed)
    ids = [f.id for f in inp.facts]
    labels = list(LABELS)
    out = []
    for _ in range(n):
        sents = []
        for _ in range(rng.randint(1, 6)):
            kind = rng.random()
            if kind < 0.4:
                head = f"{rng.choice(inp.team)}는 "
            elif kind < 0.55 and len(inp.team) > 1:
                a, b = rng.sample(inp.team, 2)
                head = f"{a}와 {b}는 " + ("각각 " if rng.random() < 0.5 else "")
            elif kind < 0.8:
                head = (f"{rng.choice(inp.team)}의 " if rng.random() < 0.3 else "") + f"{rng.choice(labels)}는 "
            else:
                head = ""
            if rng.random() < 0.3:
                head = rng.choice(CONNECTIVES) + " " + head
            subj = [t for t in inp.team if head.endswith((f"{t}는 ", f"{t}는 각각 "))]
            pool = [f.id for f in inp.facts if subj and subj[-1] in f.owners] if rng.random() < 0.6 else []
            parts = [head + "{%s}" % rng.choice(pool or ids)]
            for _ in range(rng.randint(0, 3)):
                sep = rng.choice((", ", "이며 ", "이고 ", " 및 "))
                if rng.random() < 0.3:
                    sep += f"{rng.choice(inp.team)}는 "
                parts.append(sep + "{%s}" % rng.choice(ids))
            sents.append("".join(parts) + "입니다.")
        out.append(" ".join(sents))
    return out


def test_grammar_fuzz_rendered_attribution_is_correct(inp):
    """문법 안의 무작위 글 중 검사를 통과한 것은, 채운 글만 보는 독립 귀속 확인(rehearsal 오라클)도 통과해야 한다(리뷰 4회째 MUST-2)."""
    from rehearsal.justify_scale import attribution_problems
    people = set(inp.team) | {o for f in inp.facts for o in f.owners}
    passed = 0
    for raw in _fuzz_texts(inp, 8000, 7):
        v = verify(raw, inp)
        if not v["violations"] or all(x["rule"] == "S5" for x in v["violations"]):      # 팀원 빠짐(S5)만 걸린 글도 귀속은 맞아야 한다
            passed += 1
            text = render(raw, inp)
            assert render_ok(raw, text)
            assert attribution_problems(text, inp, people) == [], raw
    assert passed > 300


def test_attribution_oracle_flags_the_old_short_phrase_bug():
    """독립 귀속 확인이 실제로 잡는지: 예전 채우기(머리만 보고 짧은 문구)로 만든 글은 걸려야 한다."""
    from rehearsal.justify_scale import attribution_problems
    s = _small()
    f = {x.id: x for x in s.facts}
    old = f"P1은 {f['F9'].phrase}[F9]이며 {f['F4'].phrase}[F4]입니다."               # 비교 후보 P9 뒤의 짧은 문구
    assert attribution_problems(old, s, {"P1", "P2", "P9"}) == ["F4: 짧은 문구 바로 앞 사람 P9 ≠ 주인 P1"]
    assert attribution_problems(render("P1은 {F9}이며 {F4}입니다.", s), s, {"P1", "P2", "P9"}) == []
    for bad in (f"P1과 P2는 {f['F11'].phrase}[F11]입니다.",                                # 여러 명 목록의 끝(리뷰 5회째 SHOULD-1)
                f"P2는 {f['F7'].phrase}[F7]이며 {f['F11'].phrase}[F11]입니다."):
        assert attribution_problems(bad, s, {"P1", "P2", "P9"}), bad
