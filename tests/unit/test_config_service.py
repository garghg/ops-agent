from src.services.config_services import deep_merge


def test_flat_override():
    result = deep_merge({"a": 1}, {"a": 2})
    assert result == {"a": 2}


def test_nested_merge():
    base = {"a": {"b": 1, "c": 2}}
    overrides = {"a": {"b": 99}}
    result = deep_merge(base, overrides)
    assert result == {"a": {"b": 99, "c": 2}}


def test_override_adds_new_key():
    result = deep_merge({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_base_not_mutated():
    base = {"a": {"b": 1}}
    deep_merge(base, {"a": {"b": 99}})
    assert base == {"a": {"b": 1}}
