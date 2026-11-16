"""Tests for turning config entry options into a pipeline config."""

from custom_components.safe_filters.engine import CombineMethod, build_config


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
    assert config.validation.stale_after_s == 7200.0
    assert config.min_sources == 1
    assert config.hold_time_s == 1800.0
    assert config.outlier is None
    assert config.lowpass_time_constant_s is None
    assert config.precision == 1


def test_stale_zero_disables() -> None:
    config = build_config({"entity_ids": ["sensor.a"], "method": "min", "stale_after": 0})
    assert config.validation.stale_after_s == 0.0
