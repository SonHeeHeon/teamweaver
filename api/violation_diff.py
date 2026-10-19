"""확정 전후 위반 비교 -- 확정(`routes/workspace.apply`)과 확정 전 미리 계산(`routes/scenarios` 후보·draft)이 같은 규칙을 쓴다(2026-10-20 Codex 리뷰:
미리 계산은 (종류, 위치)만 비교해 기존 예산 초과가 50→150으로 커져도 "위반 없음"이라 했는데 확정은 거절했다)."""
from __future__ import annotations


def new_or_worse(before, after) -> list:
    """after 위반 중 before에 없거나 더 나빠진 것. 위반 크기 = 한도와의 거리(상한 위반은 넘친 만큼, 하한 위반은 모자란 만큼) --
    actual만 보면 최소 투입률 같은 하한 위반은 방향이 반대라 악화를 통과시켰다(Codex 리뷰 2026-10-19)."""
    old = {(v.code, v.location): v for v in before}
    out = []
    for v in after:
        o = old.get((v.code, v.location))
        if o is None or abs(v.actual - v.limit) > abs(o.actual - o.limit) + 1e-9:
            out.append(v)
    return out
