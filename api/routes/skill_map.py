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


# 기술 관계 그림(2026-10-21 사용자 "조직 기술지도에도 지식 그래프", 칸은 claude-a `core.kg.views.skill_map`에 요청 --
# docs/requests/2026-10-21-skill-map-graph-fields.md): 사람별 칸이 오면 그대로 넘긴다. 사람 목록이 ID여도, 칸이 담긴 사전이어도,
# 칸이 따로(`practical_detail`) 와도 받는다 -- 화면은 `free` 칸이 있으면 그림, 없으면 예전 목록을 보인다.
PERSON_FIELDS = ("months", "level", "expert", "free", "on", "credited_from")


def _pid(p) -> str | None:
    if isinstance(p, dict):
        v = p.get("id") or p.get("person_id")
        return str(v) if v else None
    return str(p) if p else None


def _credit_list(v) -> list[dict] | None:
    """인정 출처를 [{skill, months, ratio}]로. 비율 = credit^깊이(0.7² = 49% -- 소명 근거와 같은 규칙, 폴백 리뷰 SHOULD).
    사전·목록·문자열 무엇으로 와도 받는다."""
    items = v if isinstance(v, list) else [v] if v else []
    out = []
    for x in items:
        if isinstance(x, str):
            out.append({"skill": x, "months": None, "ratio": None})
        elif isinstance(x, dict) and (x.get("skill") or x.get("from")):
            credit, depth = x.get("credit"), x.get("depth") or 1
            ratio = x.get("ratio") if x.get("ratio") is not None else (
                round(credit ** depth, 4) if isinstance(credit, (int, float)) else None)
            out.append({"skill": str(x.get("skill") or x.get("from")), "months": x.get("months") or x.get("from_months"),
                        "ratio": ratio})
    return out or None


def _norm(extra: dict) -> dict:
    out = {k: extra[k] for k in PERSON_FIELDS if k in extra}
    if "on" in out:                                    # 사업 하나를 문자열로 줘도 목록으로("J001"의 첫 글자 'J'로 묶이지 않게)
        on = out["on"]
        out["on"] = [str(x) for x in on] if isinstance(on, (list, tuple)) else ([str(on)] if on else [])
    if "credited_from" in out:
        out["credited_from"] = _credit_list(out["credited_from"])
    return out


def _detail_index(detail) -> dict[str, dict]:
    """칸이 따로 올 때(`practical_detail`) 사람 → 칸 색인을 행마다 한 번 만든다(사람마다 찾으면 수천 명에서 느렸다)."""
    if isinstance(detail, dict):
        return {str(k): v for k, v in detail.items() if isinstance(v, dict)}
    if isinstance(detail, list):
        return {pid: x for x in detail if isinstance(x, dict) and (pid := _pid(x))}
    return {}


def _person(p, names: dict[str, str], credited: set[str], detail: dict[str, dict]) -> dict | None:
    pid = _pid(p)
    if pid is None:
        return None
    # 사람이 사전으로 와도 칸이 없으면 따로 온 칸(practical_detail)을 본다(폴백 리뷰 NIT)
    extra = p if isinstance(p, dict) and any(k in p for k in PERSON_FIELDS) else detail.get(pid, {})
    return {"id": pid, "name": names.get(pid), "credited": pid in credited, **_norm(extra)}


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
        detail = _detail_index(r.get("practical_detail"))
        people = [x for x in (_person(p, names, cr, detail) for p in r["practical_people"]) if x is not None]
        out.append({**{k: v for k, v in r.items() if k not in ("practical_people", "practical_detail")},
                    "practical_credited": len(cr & {p["id"] for p in people}),
                    "practical_people": people})
    return {"dataset_version": dataset.info.version, "rows": out, "note": NOTE,
            "summary": {"skills": len(out), "scarce": sum(r["scarce"] for r in out),
                        "proposal_gap_skills": sum(1 for r in out if r["proposal_gap"] > 0),
                        "demanded": sum(1 for r in out if r["demand_headcount"] > 0)}}


@router.get("/api/skill-map")
async def skill_map_route(dataset_version: str | None = None, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    # 화면이 본 데이터와 다르면 409(다른 사용자가 전환한 직후 옛 화면에 새 조직의 사람이 섞이지 않게 -- Codex 리뷰 P1)
    check_dataset_version(dataset_version, dataset)
    return await anyio.to_thread.run_sync(build_skill_map, dataset)
