"""Tests for combining source values."""

import pytest

from custom_components.safe_filters.engine import CombineMethod, combine

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


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        combine([], CombineMethod.MEAN)
