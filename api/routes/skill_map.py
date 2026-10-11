"""조직 기술 지도 API -- 지식 그래프의 첫 번째 보기(claude-a `core.kg.views.skill_map`, 2026-10-07).

"우리 조직에 이 기술을 실무 수준으로 가진 사람이 몇 명이고, 지금 비어 있는 사람은? 진행·신규 제안 사업이 몇 명을 요구하나?"
계산은 claude-a 함수 그대로이고, 여기서는 (1) 사람 ID를 화면용으로(가상 데이터면 이름), (2) 하위 기술에서 인정받은 경력을
따로 센다(사용자 결정 2026-10-11 "인정 경력은 구분해서 표시" -- 그래프의 HAS_SKILL 간선 `implied_from`).
"""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset
from api.routes.justification import display_names

router = APIRouter()

NOTE = ("실무 가능 = 해당 기술 경력 12개월 이상, 숙련 = 36개월 이상(입력 단계 레벨 경계). 수요는 사업의 요구 기술·필요 인원이다. "
        "투입률·가용률·예산은 보지 않는다 -- 배치 가능 여부는 최적화가 판단한다. 계산상 지표이며 사업 효과는 NOT_CALIBRATED.")


def build_skill_map(dataset: ActiveDataset) -> dict:
    kg = dataset.knowledge_graph()
    if kg is None:
        raise HTTPException(status_code=409, detail="조직 기술 지도는 CSV 묶음 데이터(업로드·시연 데이터)에서만 만들 수 있다 "
                                                    "-- 지금 데이터는 고정 데모 데이터라 원 표가 없다.")
    from core.kg.views import PRACTICAL_MONTHS, skill_map
    rows = skill_map(kg)
    names = display_names(dataset)
    credited: dict[str, set[str]] = {}               # 기술 이름 → 하위 기술에서 인정받아 실무 가능이 된 사람
    for sk in kg.nodes_of("skill"):
        # 인정분 덕분에 실무 가능이 된 사람만(본인 경력만으로 이미 12개월 이상이면 인정 없이도 실무 가능 -- Codex 리뷰 P2)
        credited[sk["label"]] = {e["src"].split(":", 1)[1] for e in kg.inc(sk["id"], "HAS_SKILL")
                                 if e.get("implied_from") and (e.get("months") or 0) >= PRACTICAL_MONTHS
                                 and (e.get("own_months") or 0) < PRACTICAL_MONTHS}
    out = []
    for r in rows:
        cr = credited.get(r["skill"], set())
        out.append({**{k: v for k, v in r.items() if k != "practical_people"},
                    "practical_credited": len(cr & set(r["practical_people"])),
                    "practical_people": [{"id": p, "name": names.get(p), "credited": p in cr} for p in r["practical_people"]]})
    return {"dataset_version": dataset.info.version, "rows": out, "note": NOTE,
            "summary": {"skills": len(out), "scarce": sum(r["scarce"] for r in out),
                        "proposal_gap_skills": sum(1 for r in out if r["proposal_gap"] > 0),
                        "demanded": sum(1 for r in out if r["demand_headcount"] > 0)}}


@router.get("/api/skill-map")
async def skill_map_route(dataset_version: str | None = None, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    # 화면이 본 데이터와 다르면 409(다른 사용자가 전환한 직후 옛 화면에 새 조직의 사람이 섞이지 않게 -- Codex 리뷰 P1)
    check_dataset_version(dataset_version, dataset)
    return await anyio.to_thread.run_sync(build_skill_map, dataset)
