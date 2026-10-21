"""등급 대체 짝짓기(core/evaluate/grade_swap, 2026-10-21 사용자 결정)."""
import pytest

from core.evaluate.grade_swap import plan_swaps, same_swaps, swapped_need


def test_upward_and_downward_fill_the_short_seat():
    need = {"고급": 0, "중급": 2, "초급": 1}
    # 중급 자리 하나에 고급을 넣었다(중급이 없어서)
    assert plan_swaps(need, {"중급": 1, "초급": 1}, {"고급": 1, "중급": 1, "초급": 1}) == [{"seat": "중급", "grade": "고급", "count": 1}]
    # 중급 자리 하나에 초급을 넣었다(사업 사정상)
    assert plan_swaps(need, {"중급": 1, "초급": 1}, {"중급": 1, "초급": 2}) == [{"seat": "중급", "grade": "초급", "count": 1}]


def test_nearest_grade_first_then_upper_seat():
    need = {"특급": 1, "고급": 0, "중급": 1, "초급": 0}
    # 넘친 고급 2명: 특급 자리와 중급 자리 모두 한 단계 -- 위 자리(특급) 먼저, 다음 중급
    assert plan_swaps(need, {}, {"고급": 2}) == [{"seat": "특급", "grade": "고급", "count": 1},
                                               {"seat": "중급", "grade": "고급", "count": 1}]
    # 넘친 초급 1명, 빈자리 특급·중급: 가까운 중급 먼저
    assert plan_swaps(need, {}, {"초급": 1}) == [{"seat": "중급", "grade": "초급", "count": 1}]


def test_no_short_seat_means_no_swap_and_unlisted_grades_are_free():
    need = {"중급": 1}
    assert plan_swaps(need, {"중급": 1}, {"중급": 1, "고급": 1}) == []          # 고급은 정원 표 밖 -- 원래 자유
    assert plan_swaps({"중급": 1, "고급": 0}, {"중급": 1}, {"중급": 1, "고급": 1}) == []   # 빈자리가 없다 -- 대체가 아니라 정원 초과


def test_overflow_that_existed_before_is_not_this_confirms_swap():
    need = {"고급": 0, "중급": 2}
    before = {"고급": 1, "중급": 1}                  # 이미 고급 1명이 넘쳐 있었다(예전 교체 적용 등)
    assert plan_swaps(need, before, {"고급": 1, "중급": 1}) == []
    assert plan_swaps(need, before, {"고급": 2, "중급": 0}) == [{"seat": "중급", "grade": "고급", "count": 1}]


def test_swapped_need_and_compare():
    need = {"고급": 0, "중급": 2}
    s = [{"seat": "중급", "grade": "고급", "count": 1}]
    assert swapped_need(need, s) == {"고급": 1, "중급": 1}
    with pytest.raises(ValueError):
        swapped_need(need, [{"seat": "중급", "grade": "고급", "count": 3}])
    assert same_swaps(s, [{"seat": "중급", "grade": "고급", "count": 1, "reason": "중급 전원 투입 중"}])
    assert not same_swaps(s, [{"seat": "중급", "grade": "초급", "count": 1}])
