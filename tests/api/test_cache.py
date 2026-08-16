from api.cache import ResultCache


def test_cache_key_is_stable_across_dict_ordering():
    a = ResultCache.key({"Java": 5, "SQL": 3}, {}, 3)
    b = ResultCache.key({"SQL": 3, "Java": 5}, {}, 3)
    assert a == b


def test_cache_key_differs_on_different_weights():
    a = ResultCache.key({"Java": 5}, {}, 3)
    b = ResultCache.key({"Java": 4}, {}, 3)
    assert a != b


def test_get_returns_none_on_miss():
    c = ResultCache()
    assert c.get(ResultCache.key({}, {}, 3)) is None


def test_put_then_get_round_trips():
    c = ResultCache()
    k = ResultCache.key({}, {}, 1)
    c.put(k, ["fake-plan-list"])
    assert c.get(k) == ["fake-plan-list"]
