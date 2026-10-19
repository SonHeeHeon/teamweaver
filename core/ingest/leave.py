"""휴직·휴가 가정(2026-10-19 사용자 가정, claude-b `scripts/make_annual_demo.py`에서 옮김 -- 생성기 v3의 기준).

사용자: "1년간 3%는 육아휴직으로 1년간 휴직(그런데 1월부터 12월까지 말고 현실적으로 지난년도 8월부터 이번년도 8월까지 이런 식으로),
2%는 한달 휴가(신행 혹은 리프레쉬 > 그런데 보통 이건 플젝에서 자체 감당하긴함) 정도만 하자. 나머지는 전부 100% 가능하다는 가정".
- 육아휴직: 12개월 연속, 계획 시작 11개월 전 ~ 11개월 뒤 사이에 시작(해를 걸친다) -- 그 달 가용률 0.
- 한 달 휴가: 계획 12개월 중 한 달. 사업이 자체 감당하므로 가용률은 깎지 않고 manifest `leave`에만 남긴다.
달력 달 기준이라 같은 사람·같은 시드면 6개월 묶음과 12개월 묶음이 같은 창을 쓴다. 근거 없는 가정값(NOT_CALIBRATED).
"""
from __future__ import annotations

import random

PARENTAL_SHARE, PARENTAL_MONTHS = 0.03, 12
SHORT_LEAVE_SHARE = 0.02
MODEL = ("대부분 100% · 육아휴직 3%(12개월, 해를 걸침, 그 달 0%) · 한 달 휴가 2%(사업이 자체 감당 -- 가용률 그대로)")


def leave_seed(seed: int) -> int:
    """묶음 시드 → 휴직 난수열 시드(다른 표의 난수열과 섞이지 않는다). 시드 2026이면 b 스크립트의 예전 상수와 같다."""
    return seed * 2750159 + 13


def month_label(first: str, k: int) -> str:
    y, m = int(first[:4]), int(first[5:]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def offset(first: str, month: str) -> int:
    return (int(month[:4]) - int(first[:4])) * 12 + int(month[5:]) - int(first[5:])


def leave_windows(person_ids: list[str], first: str, seed: int) -> dict[str, dict]:
    """사람별 휴직·휴가 창 {person_id: {kind, start(YYYY-MM), months}}. person_ids 순서와 seed가 같으면 결과가 같다."""
    rng = random.Random(seed)
    ids = list(person_ids)
    n_par, n_short = round(len(ids) * PARENTAL_SHARE), round(len(ids) * SHORT_LEAVE_SHARE)
    chosen = rng.sample(ids, n_par + n_short)
    out = {pid: {"kind": "육아휴직", "start": month_label(first, rng.randint(-11, 11)), "months": PARENTAL_MONTHS}
           for pid in chosen[:n_par]}
    out.update({pid: {"kind": "한 달 휴가", "start": month_label(first, rng.randint(0, 11)), "months": 1}
                for pid in chosen[n_par:]})
    return out


def available_mm(window: dict | None, month: str) -> float:
    """그 달 가용률: 육아휴직 달만 0 -- 한 달 휴가는 사업이 자체 감당한다고 보고(사용자 설명) 깎지 않는다."""
    if not window or window["kind"] != "육아휴직":
        return 1.0
    k = offset(window["start"], month)
    return 0.0 if 0 <= k < window["months"] else 1.0


def manifest_section(windows: dict[str, dict]) -> dict:
    return {"model": MODEL, "calibrated": False,
            "people": [{"person_id": pid, **w} for pid, w in sorted(windows.items())]}
