"""IT 기술 이름 사전(core/ingest/skills.py, skill_dictionary.json) -- 검사기·오합치기·부분 인정."""
import copy
import csv
import json
from pathlib import Path

import pytest

from core.ingest.skills import DICT_PATH, from_data, load_dictionary, norm_key, person_skill_months, validate

ROOT = Path(__file__).resolve().parents[2]


def _data():
    return json.loads(DICT_PATH.read_text("utf-8"))


def test_shipped_dictionary_is_valid():
    assert validate(_data()) == []
    sd = load_dictionary()
    assert len(sd.concepts) >= 200 and 0 < sd.narrower_credit <= 1


def test_no_banned_company_names():
    text = DICT_PATH.read_text("utf-8")
    for banned in ("우리은행", "삼성", "Samsung", "Woori"):
        assert banned not in text


def test_demo_names_are_their_own_labels():
    sd = load_dictionary()
    names = set()
    for f in ("person_skills.csv", "project_skill_requirements.csv"):
        names |= {r["skill_name"] for r in csv.DictReader(open(ROOT / "demo" / "org-n100" / f, encoding="utf-8"))}
    assert names and all(sd.known(n) and sd.canonical(n) == n for n in names)


@pytest.mark.parametrize("raw, label", [
    ("스프링부트", "Spring Boot"), ("spring boot", "Spring Boot"), ("SpringBoot", "Spring Boot"), ("스프링", "Spring"),
    ("k8s", "Kubernetes"), ("쿠버네티스", "Kubernetes"), ("자바", "Java"), ("MS-SQL", "Microsoft SQL Server"),
    ("오라클", "Oracle Database"), ("넥사크로N", "Nexacro"), ("eGovFrame", "전자정부 표준프레임워크"), ("MLOps", "ML Ops"),
    ("Vector DB", "RAG·벡터DB"), ("Apache Kafka", "Kafka"), ("ＪＡＶＡ", "Java"),        # 전각 문자도 NFKC로
])
def test_aliases_resolve(raw, label):
    assert load_dictionary().canonical(raw) == label


@pytest.mark.parametrize("a, b", [
    ("Java", "JavaScript"), ("React", "React Native"), ("SQL", "NoSQL"), ("SQL", "MySQL"), ("SQL", "SQL Server"),
    ("C", "C++"), ("C", "C#"), ("C++", "C#"), (".NET", "ASP.NET"), ("AWS", "AWS Lambda"), ("Spring", "Spring Boot"),
    ("Angular", "AngularJS"), ("Kubernetes", "OpenShift"), ("Spark", "Hadoop"), ("ETL", "Airflow"), ("Oracle", "Oracle GoldenGate"),
    ("자바", "자바스크립트"), ("스프링", "스프링 부트"), ("리액트", "리액트 네이티브"), ("ML Ops", "ML"),
])
def test_confusable_names_stay_apart(a, b):
    sd = load_dictionary()
    assert sd.canonical(a) != sd.canonical(b)


def test_unknown_name_is_kept_as_is():
    sd = load_dictionary()
    assert not sd.known("사내 전용 솔루션 Z") and sd.canonical("사내 전용 솔루션 Z") == "사내 전용 솔루션 Z"


def test_norm_key_keeps_language_symbols():
    assert len({norm_key(x) for x in ("C", "C++", "C#")}) == 3
    assert norm_key("Pro*C") != norm_key("Pro C")      # '*'는 남는다(별칭으로 따로 묶는다)
    assert norm_key("Spring-Boot") == norm_key("spring boot") == norm_key("Spring.Boot")


def test_validator_catches_problems():
    base = {"partial_credit": {"narrower_to_broader": 0.5}, "concepts": [
        {"id": "a", "label": "A", "aliases": ["X"], "broader": ["b"]},
        {"id": "b", "label": "B", "aliases": ["x"], "broader": ["a"]},           # 별칭 충돌 + 순환
        {"id": "c", "label": "C", "aliases": [], "broader": ["zzz"]}]}           # 없는 상위
    errs = validate(base)
    assert any("함께 걸린다" in e for e in errs) and any("순환" in e for e in errs) and any("없는 개념" in e for e in errs)
    bad = copy.deepcopy(base)
    bad["partial_credit"]["narrower_to_broader"] = 1.5
    assert any("0~1" in e for e in validate(bad))
    with pytest.raises(ValueError):
        from_data(base)


def test_ancestors_depth():
    sd = load_dictionary()
    assert sd.ancestors("Spring Boot") == {"Spring": 1, "Java": 2}
    assert sd.ancestors("Java") == {}
    assert sd.ancestors("모르는 기술") == {}


def test_partial_credit_and_merge():
    sd = load_dictionary()
    r = person_skill_months([("스프링부트", 60), ("Spring", 10), ("자바", 20), ("Spring Boot", 40)], sd)
    assert r.own == {"Spring Boot": 60, "Spring": 10, "Java": 20}          # 같은 대표 이름은 가장 긴 경력
    assert r.months["Spring"] == 30 and r.implied["Spring"]["from"] == "Spring Boot"
    assert r.months["Java"] == 20                                           # 본인 경력(20)이 인정분(60×0.25=15)보다 길다
    assert sorted(r.raw_names["Spring Boot"]) == ["Spring Boot", "스프링부트"]
    assert person_skill_months([("Spring Boot", 60)], sd, credit=0).months == {"Spring Boot": 60}
    assert person_skill_months([("Spring Boot", 60)], sd, credit=1).months == {"Spring Boot": 60, "Spring": 60, "Java": 60}
    assert person_skill_months([("스프링부트", 60), ("스프링부트", 30)], None).months == {"스프링부트": 60}   # 사전 없이: 이름 그대로, 가장 긴 경력


def test_broader_to_narrower_is_not_credited():
    r = person_skill_months([("Java", 96)], load_dictionary())
    assert r.months == {"Java": 96} and not r.implied


def test_turtle_export_parses():
    rdflib = pytest.importorskip("rdflib")
    from rdflib.namespace import RDF, SKOS
    sd = load_dictionary()
    g = rdflib.Graph().parse(data=sd.to_turtle(), format="turtle")
    assert len(set(g.subjects(RDF.type, SKOS.Concept))) == len(sd.concepts)
    assert len(list(g.triples((None, SKOS.broader, None)))) == sum(len(c.get("broader") or []) for c in sd.concepts.values())


@pytest.mark.parametrize("raw, label, how", [
    ("Java 8", "Java", "version"), ("Spring Boot 2.x", "Spring Boot", "version"), ("Oracle 19c", "Oracle Database", "version"),
    ("Java v17", "Java", "version"), ("Windows Server 2019", "Windows Server", "version"), ("SQL Server 2019", "Microsoft SQL Server", "version"),
    ("C++11", "C++", "version"), ("자바(Java)", "Java", "paren"), ("Kubernetes(K8s)", "Kubernetes", "paren"),
    ("Spring Boot(스프링부트)", "Spring Boot", "paren"), ("비정형 문서 처리(OCR)", "비정형 문서 처리(OCR)", "exact"),
])
def test_version_and_paren_variants(raw, label, how):
    c, got = load_dictionary().resolve(raw)
    assert c is not None and c["label"] == label and got == how


@pytest.mark.parametrize("raw", ["C/C++", "Spring(MVC)", "Oracle(Tibero)", "SAP R/3", "Server", "SCM"])
def test_ambiguous_variants_stay_unknown(raw):
    """괄호 안팎이 다른 개념이거나 한쪽을 모르면, 둘을 묶은 이름이면 합치지 않는다(경고로 올라간다)."""
    assert not load_dictionary().known(raw)


def test_devops_is_not_merged_into_cicd():
    sd = load_dictionary()
    assert sd.canonical("DevOps") != sd.canonical("CI/CD")


def test_credit_must_be_between_0_and_1():
    sd = load_dictionary()
    for bad in (1.5, -0.1, "0.5", True):
        with pytest.raises(ValueError):
            person_skill_months([("Spring Boot", 60)], sd, credit=bad)


def test_credit_floor_has_no_float_error():
    # 0.29 × 100 = 28.999…이지만 29개월로 인정해야 한다
    assert person_skill_months([("Spring Boot", 100)], load_dictionary(), credit=0.29).months["Spring"] == 29


@pytest.mark.parametrize("raw, label", [
    ("Angular 1.x", "AngularJS"), ("Angular 1.5", "AngularJS"), ("Angular 15", "Angular"), ("Python 3.11", "Python"),
    ("R12", None), ("R/3", None), ("C2", None), ("C4I", None), ("DL380", None), ("ML350", None), ("R 4.2", None), ("Go 1.21", None),
])
def test_version_rule_does_not_merge_into_short_or_other_concepts(raw, label):
    """버전을 떼고 남은 키가 3자 미만이거나 숫자 앞이 '/'이면 맞추지 않는다(R12→R 언어, DL380→딥러닝 같은 오합치기 방지).
    주 버전만 남긴 형태가 다른 개념의 별칭이면 그쪽을 쓴다(Angular 1.x → AngularJS). (리뷰 MUST 2026-10-09)"""
    c = load_dictionary().lookup(raw)
    assert (c["label"] if c else None) == label
