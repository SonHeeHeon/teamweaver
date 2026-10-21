"""낯선 기술 이름 정리 API(2026-10-22): 사전에 없는 이름 목록 → AI 제안(가짜) → 관리자 승인이 새 판이 되고 기술 경력이 대표 이름으로 합쳐진다.
되돌리기로 무른다. 시연 묶음 `demo/org-n100-messy-skills`(scripts/make_messy_skill_demo.py -- 14개 표기를 사전에 없는 꼴로)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
MESSY = ROOT / "demo" / "org-n100-messy-skills"


@pytest.fixture
def messy(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(MESSY.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(MESSY))
    return MESSY


class FakeAI:
    def __init__(self, answer):
        self.calls, self.answer = [], answer
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=json.dumps(self.answer, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))

    def with_options(self, **kw):
        return self


def _holders(c, skill):
    g = c.app.state.dataset.graph
    return {p.id for p in g.people if skill in p.skills}


def test_unknown_names_suggest_approve_and_undo(messy, ai_client):
    from api.main import app
    manifest = json.loads((MESSY / "manifest.json").read_text("utf-8"))
    variants = manifest["messy_skill_names"]["variants"]
    with TestClient(app) as c:
        v = c.get("/api/meta").json()["dataset_version"]
        q = c.get("/api/skills/unknown", params={"dataset_version": v}).json()
        names = {i["name"] for i in q["items"]}
        assert names == set(variants.values())
        k = next(i for i in q["items"] if i["name"] == "Kuberentes")
        assert k["people"] > 0 and k["candidates"][0]["id"] == "kubernetes"
        ai = FakeAI({"items": [{"name": "Kuberentes", "decision": "alias", "concept": "kubernetes", "reason": "오타"}]})
        with ai_client(ai):
            s = c.post("/api/skills/unknown/suggest", json={"dataset_version": v, "names": ["Kuberentes", "이티엘"]}).json()
        assert s["method"] == "ai" and {i["name"] for i in s["items"]} == {"Kuberentes", "이티엘"}
        sent = json.dumps(ai.calls[0]["messages"], ensure_ascii=False)
        assert not any(p.id in sent for p in c.app.state.dataset.graph.people)     # 사람 id는 AI에 가지 않는다(수만 간다)
        before_k8s, typo = _holders(c, "Kubernetes"), _holders(c, "Kuberentes")
        assert typo and not typo <= before_k8s                                # 낯선 이름으로만 가진 사람이 있다
        r = c.post("/api/skills/names", json={"dataset_version": v, "decisions": [
            {"name": "Kuberentes", "decision": "alias", "concept": "kubernetes", "reason": "오타", "method": "ai"}]})
        assert r.status_code == 200, r.text
        v2 = r.json()["workspace"]["dataset_version"]
        assert v2 != v and r.json()["workspace"]["history"][0]["kind"] == "skill_names"
        after_k8s = _holders(c, "Kubernetes")
        assert after_k8s == before_k8s | typo and not _holders(c, "Kuberentes")   # 낯선 이름을 가진 사람이 Kubernetes로 합쳐졌다
        # (k["people"]는 원천 행 수 -- 10년 넘게 안 쓴 행은 보유 기술이 아니라 typo보다 많을 수 있다)
        q2 = c.get("/api/skills/unknown", params={"dataset_version": v2}).json()
        assert "Kuberentes" not in {i["name"] for i in q2["items"]} and q2["decided"][0]["name"] == "Kuberentes"
        # 정리할 이름이 아닌 것(이미 사전에 있음)은 거절
        assert c.post("/api/skills/names", json={"dataset_version": v2, "decisions": [
            {"name": "Kubernetes", "decision": "alias", "concept": "kubernetes"}]}).status_code == 422
        u = c.post("/api/workspace/undo", json={"dataset_version": v2})
        assert u.status_code == 200, u.text
        assert _holders(c, "Kubernetes") == before_k8s
