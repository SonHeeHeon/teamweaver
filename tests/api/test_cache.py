from api.cache import ResultCache


def test_cache_key_is_stable_across_dict_ordering():
    a = ResultCache.key({"Java": 5, "SQL": 3}, {}, 3, "v")
    b = ResultCache.key({"SQL": 3, "Java": 5}, {}, 3, "v")
    assert a == b


def test_cache_key_differs_on_different_weights():
    a = ResultCache.key({"Java": 5}, {}, 3, "v")
    b = ResultCache.key({"Java": 4}, {}, 3, "v")
    assert a != b


def test_get_returns_none_on_miss():
    c = ResultCache()
    assert c.get(ResultCache.key({}, {}, 3, "v")) is None


def test_put_then_get_round_trips():
    c = ResultCache()
    k = ResultCache.key({}, {}, 1, "v")
    c.put(k, ["fake-plan-list"])
    assert c.get(k) == ["fake-plan-list"]


def test_cache_evicts_least_recently_used_beyond_cap(monkeypatch):
    """데이터셋 전환마다 옛 키가 쌓이지 않게 상한을 둔다(claude-a 교차 리뷰 L2)."""
    monkeypatch.setattr(ResultCache, "MAX_ENTRIES", 2)
    c = ResultCache()
    c.put("a", [1]); c.put("b", [2])
    assert c.get("a") == [1]                 # a를 최근 사용으로
    c.put("c", [3])
    assert c.get("b") is None and c.get("a") == [1] and c.get("c") == [3]
