"""가상 이름(core/ingest/names.py, 2026-10-11)."""
import re

from core.ingest.names import KNOWN_PREFIXES, MAX_NUMBER, _table, virtual_name


def test_table_covers_every_known_id_without_duplicates():
    t = _table()
    assert len(t) == len(KNOWN_PREFIXES) * MAX_NUMBER
    assert len(set(t.values())) == len(t)
    assert all(re.fullmatch(r"[가-힣]{3}", n) for n in t.values())


def test_names_are_stable_and_unknown_ids_keep_the_old_label():
    assert virtual_name("DP0001") == virtual_name("DP0001") == _table()["DP0001"]
    assert virtual_name("XX0001") == "가상XX0001"
    assert virtual_name(f"DP{MAX_NUMBER + 1:04d}") == f"가상DP{MAX_NUMBER + 1:04d}"
    assert virtual_name("DP1") == "가상DP1"


def test_common_surnames_dominate_in_every_group_actually_used():
    """묶음이 쓰는 앞번호(그룹당 1~100)마다 흔한 성이 흔하다 -- 그룹 순서대로 채우던 첫 판은 AU 그룹의 김씨가 2%였다(폴백 리뷰)."""
    for prefix in KNOWN_PREFIXES:
        first = [virtual_name(f"{prefix}{n:04d}")[0] for n in range(1, 101)]
        assert first.count("김") >= 12 and first.count("이") >= 8, (prefix, first.count("김"), first.count("이"))
