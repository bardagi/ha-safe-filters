"""Tests for combining source values."""

import pytest

from custom_components.safe_filters.engine import CombineMethod, combine, effective_weights

VALUES = [("sensor.a", 10.0), ("sensor.b", 14.0), ("sensor.c", 12.0)]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (CombineMethod.MIN, 10.0),
        (CombineMethod.MAX, 14.0),
        (CombineMethod.MEAN, 12.0),
        (CombineMethod.MEDIAN, 12.0),
        (CombineMethod.PRIORITY, 10.0),
    ],
)
def test_basic_methods(method: CombineMethod, expected: float) -> None:
    assert combine(VALUES, method) == pytest.approx(expected)


def test_median_of_two_is_mean() -> None:
    """Documents why median of two sensors rejects nothing."""
    assert combine([("a", 10.0), ("b", 40.0)], CombineMethod.MEDIAN) == 25.0


def test_priority_uses_first_valid_in_order() -> None:
    assert combine([("sensor.b", 14.0), ("sensor.c", 12.0)], CombineMethod.PRIORITY) == 14.0


def test_weighted_mean() -> None:
    weights = {"sensor.a": 3.0, "sensor.b": 1.0, "sensor.c": 0.0}
    assert combine(VALUES, CombineMethod.WEIGHTED_MEAN, weights) == pytest.approx(11.0)


def test_weighted_mean_renormalises_over_valid_sources() -> None:
    weights = {"sensor.a": 3.0, "sensor.b": 1.0}
    # sensor.a is missing (invalid): only sensor.b and sensor.c (default weight 1) remain.
    values = [("sensor.b", 14.0), ("sensor.c", 12.0)]
    assert combine(values, CombineMethod.WEIGHTED_MEAN, weights) == pytest.approx(13.0)


def test_weighted_mean_all_zero_weights_falls_back_to_mean() -> None:
    weights = {"sensor.a": 0.0, "sensor.b": 0.0}
    values = [("sensor.a", 10.0), ("sensor.b", 20.0)]
    assert combine(values, CombineMethod.WEIGHTED_MEAN, weights) == pytest.approx(15.0)


def test_effective_weights_redistribute_a_missing_source() -> None:
    """50/30/20: when the 50 drops out, the others count 60 % and 40 %."""
    weights = {"sensor.a": 50.0, "sensor.b": 30.0, "sensor.c": 20.0}
    shares = effective_weights([("sensor.b", 21.0), ("sensor.c", 19.0)], weights)
    assert shares == pytest.approx({"sensor.b": 0.6, "sensor.c": 0.4})


def test_effective_weights_all_zero_share_equally() -> None:
    shares = effective_weights([("a", 1.0), ("b", 2.0)], {"a": 0.0, "b": 0.0})
    assert shares == pytest.approx({"a": 0.5, "b": 0.5})


def test_weighted_mean_matches_effective_weights() -> None:
    weights = {"sensor.a": 50.0, "sensor.b": 30.0, "sensor.c": 20.0}
    values = [("sensor.a", 22.0), ("sensor.b", 21.0), ("sensor.c", 19.0)]
    assert combine(values, CombineMethod.WEIGHTED_MEAN, weights) == pytest.approx(21.1)
    assert combine(values[1:], CombineMethod.WEIGHTED_MEAN, weights) == pytest.approx(20.2)


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        combine([], CombineMethod.MEAN)
