"""시험 도우미: 확정으로 새로 생긴 빈자리를 모두 "메우지 않음"(사유)으로(2026-10-21 빼 온 자리 결정 -- 메우거나 정해야 확정된다).
예전 시험들은 빼 온 자리를 비운 채 확정했다 -- 그 뜻(정해지지 않은 빈자리)은 이제 이 결정으로 적는다."""


def waivers(c, version: str, entries: list[dict], reason: str = "시험: 남은 인원으로 진행") -> list[dict]:
    p = c.post("/api/workspace/preview", json={"dataset_version": version, "entries": entries})
    assert p.status_code == 200, p.text
    return [{"project_id": j, **h, "reason": reason} for j, hs in p.json()["holes"].items() for h in hs]
