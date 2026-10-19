"""프로젝트 동시 수행 허용(2026-10-19 사용자 결정, 설계 .omc/plan/2026-10-19-concurrency.md 3.1).

사용자: "특정 인력은 현재 상황에 사람이 부족해서 혹은 이 인력이 저기에도 잠깐 봐줘야해서 등의 사유로 플젝 여러개를 한다."
부서장이 사람마다 사유와 함께 허용한다 -- 원천 manifest `concurrency`(CSV 계약은 그대로, 단계·이전 사업의 `pipeline`과 같은 방식):

    "concurrency": [{"person_id": "DP0012", "max_projects": 2, "reason": "기술 지원", "memo": "...", "granted_at": "2026-10-19"}]

계산(전일 투입)은 `Person.max_concurrent`만 쓴다. 사유·메모는 화면·인사팀 검토 자료 표시용. 용어는 "동시 수행"(회사 규정상 다른 말을 쓰지 않는다).
"""
from __future__ import annotations

REASONS = ("인력 부족", "기술 지원", "긴급 지원", "기타")
MAX_PROJECTS = (2, 3)


def check(manifest: dict, person_ids: set[str], report) -> None:
    """manifest `concurrency` 모양 검사 -- 오류는 report에(없으면 아무것도 하지 않는다)."""
    entries = manifest.get("concurrency")
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error("manifest.json", "concurrency must be a list", column="concurrency")
        return
    seen = set()
    for k, e in enumerate(entries):
        where = f"concurrency[{k}]"
        if not isinstance(e, dict):
            report.error("manifest.json", f"{where} must be an object", column="concurrency")
            continue
        pid = e.get("person_id")
        if not isinstance(pid, str) or pid not in person_ids:      # 문자열이 아니면(목록 등) 해시 오류 대신 검사 오류로
            report.error("manifest.json", f"{where}: 없는 사람 {pid!r}", column="concurrency")
        elif pid in seen:
            report.error("manifest.json", f"{where}: {pid} 중복", column="concurrency")
        if isinstance(pid, str):
            seen.add(pid)
        if type(e.get("max_projects")) is not int or e["max_projects"] not in MAX_PROJECTS:
            report.error("manifest.json", f"{where}: max_projects는 {list(MAX_PROJECTS)} 중 하나", column="concurrency")
        if e.get("reason") not in REASONS:
            report.error("manifest.json", f"{where}: reason은 {list(REASONS)} 중 하나", column="concurrency")
        for key in ("memo", "granted_at"):
            if key in e and not isinstance(e[key], str):
                report.error("manifest.json", f"{where}: {key}는 문자열", column="concurrency")


def limits(manifest: dict | None) -> dict[str, int]:
    """사람 → 같은 달 최대 사업 수(허용된 사람만). 검사를 통과한 manifest를 받는다."""
    return {e["person_id"]: int(e["max_projects"]) for e in (manifest or {}).get("concurrency") or []
            if isinstance(e, dict) and isinstance(e.get("person_id"), str)}


def grants(manifest: dict | None) -> dict[str, dict]:
    """사람 → 허용 기록(사유·메모·시각) -- 화면·검토 자료용."""
    return {e["person_id"]: dict(e) for e in (manifest or {}).get("concurrency") or []
            if isinstance(e, dict) and isinstance(e.get("person_id"), str)}
