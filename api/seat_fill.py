"""빼 온 자리를 메울 후보(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야할거같네"): 확정으로 새로 생긴 빈자리 사업마다 지금 바로 넣을 수
있는 사람(대기 인력 등 -- 다른 사업에서 또 빼 오지 않는다: 빈자리가 사슬로 옮겨 간다, 허용이 필요한 동시 수행도 아님)을 위반 없는 것만,
그 빈자리 등급이 먼저. 화면이 "메움"으로 고르면 그 사람을 같은 확정에 넣어 다시 미리 계산한다(다른 등급이면 등급 대체가 걸린다)."""
from __future__ import annotations


def staffing_ids(app, dataset) -> set[str]:
    """아직 편성 중인 신규 사업(manifest pipeline·proposals -- 작업 데이터 화면의 "신규 사업"). 시작 전이라 빈자리가 정상이다 --
    "메우지 않음"(정원 줄이기)을 받지 않고 빈자리 결정에서 뺀다. 원천 묶음이 없는 데이터(fixture)는 manifest proposals만."""
    try:
        from api.workspace import current_files
        from api.workspace_edits import pipeline_of, read_manifest
        m = read_manifest(current_files(app, dataset))
    except Exception:                                   # noqa: BLE001
        return set(dataset.scenario.get("proposals") or [])
    return set(pipeline_of(m)) | {p for p in (m.get("proposals") or []) if isinstance(p, str)}


def fill_options(graph, S, C, params, entries, holes: dict[str, list[dict]], *, locked, names: dict[str, str],
                 top: int = 5) -> dict[str, list[dict]]:
    from core.evaluate.staffing_sim import rank_candidates
    out: dict[str, list[dict]] = {}
    for jid, hs in holes.items():
        grades = {h["grade"] for h in hs}
        # 보이는 것은 몇 명뿐이라 정확 채점은 15명까지(기본 40 -- 고를 때마다 다시 부르는 미리 계산이 빼 온 사업 수만큼 느려지지 않게)
        rows = rank_candidates(graph, S, C, params, entries, jid, include_pull=False, top=15, screen=15, locked=locked)
        ok = [r for r in rows if not r["pulled_from"] and not r["needs_grant"] and r["source"] != "concurrent"
              and not r["new_violations"]]
        ok.sort(key=lambda r: (r["grade"] not in grades, -r["delta_total"], r["person_id"]))
        out[jid] = [{"person_id": r["person_id"], "name": names.get(r["person_id"], r["person_id"]), "grade": r["grade"],
                     "alloc": r["alloc"], "skill_fit": r["skill_fit"], "delta_total": r["delta_total"]} for r in ok[:top]]
    return out
