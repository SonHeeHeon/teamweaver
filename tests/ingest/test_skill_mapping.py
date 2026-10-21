"""낯선 기술 이름 정리(2026-10-22): 사전에 없는 이름마다 후보(문자열 + AI)를 내고, 관리자가 승인한 결정만 manifest `skill_names`로 남아
로더·지식 그래프가 같은 넓힌 사전을 쓴다. AI 제안은 사전 id 안에서만, 헷갈리는 쌍은 확인 표시, 자동 합치기는 없다."""
import json
from types import SimpleNamespace

from core.ingest.skill_mapping import (decisions_of, dictionary_for, extend, string_candidates, suggest, unknown_names)
from core.ingest.skills import load_dictionary


class FakeAI:
    def __init__(self, answer):
        self.calls, self.answer = [], answer
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=json.dumps(self.answer, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100))

    def with_options(self, **kw):
        return self


def test_extend_adds_aliases_and_narrower_concepts_only_when_approved():
    base = load_dictionary()
    assert base.lookup("Kuberentes") is None
    sd = dictionary_for({"skill_names": [
        {"name": "Kuberentes", "decision": "alias", "concept": "kubernetes"},
        {"name": "온프레미스 K8s 운영", "decision": "narrower", "concept": "kubernetes"},
        {"name": "엑셀", "decision": "not_skill"},
        {"name": "Spark", "decision": "alias", "concept": "kafka"}]})          # 이미 다른 개념의 이름 -- 건너뛴다
    assert sd.canonical("kuberentes") == "Kubernetes"
    assert sd.ancestors("온프레미스 K8s 운영") == {"Kubernetes": 1}            # 하위 기술: 상위 경력에 부분 인정
    assert sd.canonical("Spark") == "Spark" and sd.lookup("엑셀") is None
    assert sd.version.startswith(base.version + "+") and dictionary_for({}) is base
    assert extend(base, []) is base
    assert [d["name"] for d in decisions_of({"skill_names": [{"name": "x", "decision": "alias"}]})] == []   # 개념 없는 alias는 버림


def test_unknown_names_bring_context_and_skip_decided_ones():
    sd = load_dictionary()
    tables = {"person_skills.csv": [{"person_id": "p1", "skill_name": "Kuberentes"}, {"person_id": "p1", "skill_name": "Docker"},
                                    {"person_id": "p2", "skill_name": "Kuberentes"}, {"person_id": "p2", "skill_name": "Java"}],
              "project_skill_requirements.csv": [{"project_id": "J1", "skill_name": "Kuberentes"}, {"project_id": "J1", "skill_name": "성실함"}],
              "work_skills.csv": [{"work_id": "W1", "skill_name": "Kuberentes"}]}
    items = unknown_names(tables, sd)
    assert [i["name"] for i in items] == ["Kuberentes", "성실함"]
    k = items[0]
    assert (k["people"], k["projects"], k["works"]) == (2, ["J1"], 1) and set(k["co_skills"]) == {"Docker", "Java"}
    assert [i["name"] for i in unknown_names(tables, sd, {"kuberentes"})] == ["성실함"]
    assert string_candidates(sd, "Kuberentes", 1)[0]["id"] == "kubernetes"


def test_ai_suggestions_are_checked_and_never_auto_applied():
    sd = load_dictionary()
    items = [{"name": "Kuberentes"}, {"name": "자바스크립"}, {"name": "성실함"}, {"name": "Unity3D 엔진"}, {"name": "아무거나"}]
    ai = FakeAI({"items": [
        {"name": "Kuberentes", "decision": "alias", "concept": "kubernetes", "alternatives": [], "reason": "오타"},
        {"name": "자바스크립", "decision": "alias", "concept": "java", "alternatives": ["javascript"], "reason": "자바"},
        {"name": "성실함", "decision": "not_skill", "concept": None, "reason": "성격"},
        {"name": "Unity3D 엔진", "decision": "alias", "concept": "unity_no_such_id", "reason": "?"}]})   # 사전 밖 id -- 문자열 제안으로
    r = suggest(sd, items, client=ai, model="m", price={"input_per_1m": 1.0, "output_per_1m": 2.0})
    by = {i["name"]: i for i in r["items"]}
    assert r["method"] == "ai" and len(ai.calls) == 1 and r["usage"]["cost_usd"] == 0.0012
    assert by["Kuberentes"]["confidence"] == "high" and by["Kuberentes"]["concept_label"] == "Kubernetes"
    assert by["자바스크립"]["confidence"] == "check" and any("헷갈리기 쉬운" in f for f in by["자바스크립"]["flags"])
    assert by["성실함"]["decision"] == "not_skill" and by["성실함"]["concept"] is None
    assert by["Unity3D 엔진"]["method"] == "string" and by["아무거나"]["method"] == "string"
    no_ai = suggest(sd, items)
    assert no_ai["fallback_reason"] == "no_client" and all(i["confidence"] == "check" for i in no_ai["items"])
