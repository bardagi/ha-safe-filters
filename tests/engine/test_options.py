"""Tests for turning config entry options into a pipeline config."""

from custom_components.safe_filters.engine import (
    CombineMethod,
    build_config,
    is_single,
    source_ids,
)


def test_full_options() -> None:
    config = build_config(
        {
            "name": "Outdoor",
            "entity_ids": ["sensor.a", "sensor.b"],
            "method": "weighted_mean",
            "weights": {"sensor.a": 2.0, "sensor.old": 9.0},
            "min_value": -50.0,
            "max_value": 60.0,
            "stale_after": 90.0,
            "max_deviation": 3.0,
            "min_sources": 2.0,
            "hold_time": 15.0,
            "outlier": {"enabled": True, "window_size": 7.0, "radius": 2.5},
            "rate_limit": {"enabled": False, "max_rate": 1.0},
            "lowpass": {"enabled": True, "time_constant": 5.0},
            "moving_average": {"enabled": True, "window": 10.0},
            "precision": 1.0,
        }
    )
    assert config.sources == ("sensor.a", "sensor.b")
    assert config.method is CombineMethod.WEIGHTED_MEAN
    assert config.weights == {"sensor.a": 2.0, "sensor.b": 1.0}
    assert config.validation.min_value == -50.0
    assert config.validation.stale_after_s == 5400.0
    assert config.validation.max_deviation == 3.0
    assert config.min_sources == 2
    assert config.hold_time_s == 900.0
    assert config.outlier is not None
    assert config.outlier.window_size == 7
    assert config.max_rate_per_min is None
    assert config.lowpass_time_constant_s == 300.0
    assert config.moving_average_window_s == 600.0
    assert config.precision == 1


def test_minimal_options_use_defaults() -> None:
    config = build_config({"entity_ids": ["sensor.a"], "method": "min"})
    assert config.method is CombineMethod.MIN
    assert config.weights == {}
    assert config.validation.min_value is None
    assert config.validation.stale_after_s == 0.0
    assert config.min_sources == 1
    assert config.hold_time_s == 1800.0
    assert config.outlier is None
    assert config.lowpass_time_constant_s is None
    assert config.precision == 1


def test_zero_rate_limit_is_ignored() -> None:
    """A saved rate of 0 would freeze the value forever."""
    config = build_config(
        {
            "entity_ids": ["sensor.a"],
            "method": "min",
            "rate_limit": {"enabled": True, "max_rate": 0},
        }
    )
    assert config.max_rate_per_min is None


def test_weighted_extras_are_converted() -> None:
    config = build_config(
        {
            "entity_ids": ["sensor.a", "sensor.b"],
            "method": "weighted_mean",
            "weights": {"sensor.a": 5.0, "sensor.b": 3.0},
            "min_weight": 50.0,
            "offset_learning": 60.0,
        }
    )
    assert config.min_weight_share == 0.5
    assert config.offset_time_constant_s == 3600.0


def test_weighted_extras_default_off() -> None:
    config = build_config({"entity_ids": ["sensor.a"], "method": "weighted_mean"})
    assert config.min_weight_share == 0.0
    assert config.offset_time_constant_s is None

    config = build_config(
        {
            "entity_ids": ["sensor.a"],
            "method": "weighted_mean",
            "min_weight": 0,
            "offset_learning": 0,
        }
    )
    assert config.min_weight_share == 0.0
    assert config.offset_time_constant_s is None


def test_weighted_extras_ignored_for_other_methods() -> None:
    """Left over after switching away from weighted mean: must not apply."""
    config = build_config(
        {
            "entity_ids": ["sensor.a", "sensor.b"],
            "method": "median",
            "min_weight": 50.0,
            "offset_learning": 60.0,
        }
    )
    assert config.min_weight_share == 0.0
    assert config.offset_time_constant_s is None


def test_stale_zero_disables() -> None:
    config = build_config({"entity_ids": ["sensor.a"], "method": "min", "stale_after": 0})
    assert config.validation.stale_after_s == 0.0


def test_single_source_options() -> None:
    config = build_config(
        {
            "name": "Wind",
            "entity_id": "sensor.wind",
            "min_value": 0.0,
            "max_value": 75.0,
            "stale_after": 30.0,
            "hold_time": 15.0,
            "lowpass": {"enabled": True, "time_constant": 3.0},
            "precision": 1.0,
        }
    )
    assert config.sources == ("sensor.wind",)
    assert config.method is CombineMethod.PRIORITY
    assert config.weights == {}
    assert config.min_sources == 1
    assert config.validation.max_deviation is None
    assert config.validation.max_value == 75.0
    assert config.validation.stale_after_s == 1800.0
    assert config.hold_time_s == 900.0
    assert config.lowpass_time_constant_s == 180.0


def test_source_ids_and_is_single() -> None:
    single = {"entity_id": "sensor.a"}
    combine = {"entity_ids": ["sensor.b", "sensor.a"], "method": "priority"}
    assert is_single(single)
    assert not is_single(combine)
    assert source_ids(single) == ("sensor.a",)
    assert source_ids(combine) == ("sensor.b", "sensor.a")
    assert source_ids({}) == ()
