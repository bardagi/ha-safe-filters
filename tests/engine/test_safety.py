"""Regression tests for rejected samples, damaged state and finite arithmetic."""

import math
from copy import deepcopy

import pytest

from custom_components.safe_filters.engine import (
    CombineMethod,
    OutlierConfig,
    Pipeline,
    PipelineConfig,
    PipelineStatus,
    SourceReading,
)
from custom_components.safe_filters.engine.numeric import average, blend, weighted_average
from custom_components.safe_filters.engine.stages import (
    EmaStage,
    LowPassStage,
    MovingAverageStage,
    RateLimitStage,
)


def sample(pipeline: Pipeline, value: float, now: float, *, new: bool = True):
    return pipeline.evaluate([SourceReading("a", value)], now, new)


def spike_pipeline(**kwargs) -> Pipeline:
    return Pipeline(PipelineConfig(sources=("a",), outlier=OutlierConfig(5, 5), **kwargs))


def warm(pipeline: Pipeline) -> None:
    for now, value in enumerate([0, 0, 0, 4, 4]):
        sample(pipeline, value, now)


def test_rejected_reading_does_not_advance_ema_or_refresh_hold() -> None:
    pipeline = spike_pipeline(ema_readings=3, hold_time_s=60)
    warm(pipeline)
    before = pipeline.dump()
    rejected = sample(pipeline, 100, 5)
    assert rejected.value == before["value"] == 3
    assert rejected.status is PipelineStatus.HOLDING
    assert pipeline.dump()["last_good"] == 4
    assert pipeline.dump()["input"] == 4
    assert sample(pipeline, 100, 65, new=False).status is PipelineStatus.NO_DATA


def test_rejected_stream_expires_and_does_not_reset_its_gate() -> None:
    pipeline = spike_pipeline(hold_time_s=60)
    warm(pipeline)
    for now, value in enumerate([100, -100] * 10, start=1):
        result = sample(pipeline, value, now * 60)
        assert result.status is (PipelineStatus.HOLDING if now == 1 else PipelineStatus.NO_DATA)
    # A genuine sustained change is eventually accepted, with fresh downstream history.
    for now in range(1201, 1210):
        result = sample(pipeline, 50, now)
    assert result.status is PipelineStatus.OK
    assert result.value == 50


def test_held_time_filters_settle_toward_accepted_input_only() -> None:
    pipeline = spike_pipeline(lowpass_time_constant_s=60, hold_time_s=120)
    warm(pipeline)
    rejected = sample(pipeline, 100, 60)
    held = sample(pipeline, 100, 100, new=False)
    assert rejected.status is held.status is PipelineStatus.HOLDING
    assert 0 < rejected.value < held.value < 4


@pytest.mark.parametrize("restart_at", [10, 100])
def test_restart_preserves_rejection_before_and_after_expiry(restart_at: float) -> None:
    pipeline = spike_pipeline(ema_readings=3, hold_time_s=60)
    warm(pipeline)
    sample(pipeline, 100, 5)
    if restart_at > 60:
        sample(pipeline, 100, 65, new=False)
    saved = pipeline.dump()
    restored = spike_pipeline(ema_readings=3, hold_time_s=60)
    assert restored.restore(saved, restart_at) is (restart_at < 60)
    # The HA restore path evaluates empty sources before startup.
    restored.evaluate([], restart_at, False)
    assert not restored.dump()["stages"]["outlier"]["accepted"]
    # Repeated restarts must not lose an expired rejection window either.
    again = Pipeline(restored.config)
    again.restore(restored.dump(), restart_at)
    restored = again
    assert not restored.dump()["stages"]["outlier"]["accepted"]
    result = sample(restored, -100, restart_at + 1)
    assert result.status is not PipelineStatus.OK
    for now in range(int(restart_at) + 2, int(restart_at) + 10):
        result = sample(restored, 20, now)
    assert result.status is PipelineStatus.OK


def test_old_spike_snapshot_restores_only_value() -> None:
    pipeline = spike_pipeline(ema_readings=3)
    warm(pipeline)
    saved = pipeline.dump()
    saved["version"] = 1
    saved["stages"]["outlier"].pop("accepted")
    restored = spike_pipeline(ema_readings=3)
    assert restored.restore(saved, 10)
    assert restored.evaluate([], 11, False).value == saved["value"]
    assert sample(restored, 20, 12).value == 20


@pytest.mark.parametrize("data", [[], ["bad"], "bad", 42, {"version": 2, "value": []}])
def test_malformed_snapshot_is_ignored(data) -> None:
    assert not Pipeline(PipelineConfig(sources=("a",))).restore(data, 10)


@pytest.mark.parametrize("field", ["value", "last_good", "last_evaluated"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_top_level_numbers_are_not_restored(field, value) -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a",)))
    sample(pipeline, 10, 0)
    saved = pipeline.dump()
    saved[field] = value
    restored = Pipeline(pipeline.config)
    assert not restored.restore(saved, 10)
    assert sample(restored, 20, 11).value == 20


@pytest.mark.parametrize("field", ["value", "last_good", "last_evaluated"])
def test_future_snapshot_times_are_not_restored(field) -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a",)))
    sample(pipeline, 10, 100)
    # A future measurement value is fine; future timestamps are not.
    saved = pipeline.dump()
    saved[field] = 200
    assert Pipeline(pipeline.config).restore(saved, 150) is (field == "value")


def test_corrupt_stage_fields_are_discarded_without_poisoning_output() -> None:
    pipeline = spike_pipeline(
        max_rate_per_min=5,
        lowpass_time_constant_s=60,
        ema_readings=3,
        moving_average_window_s=60,
    )
    warm(pipeline)
    saved = pipeline.dump()
    corruptions = [
        ("outlier", "window", [float("nan")]),
        ("outlier", "output", float("inf")),
        ("outlier", "accepted", "yes"),
        ("rate_limit", "time", 1000),
        ("lowpass", "input", float("nan")),
        ("lowpass", "output", float("inf")),
        ("ema", "output", float("nan")),
        ("moving_average", "samples", [[0, float("inf")]]),
        ("moving_average", "samples", [[1000, 1]]),
        ("moving_average", "samples", [[2, 1], [1, 2]]),
    ]
    for stage, key, value in corruptions:
        corrupt = deepcopy(saved)
        corrupt["stages"][stage][key] = value
        restored = Pipeline(pipeline.config)
        assert restored.restore(corrupt, 10)
        assert restored.evaluate([], 11, False).value == saved["value"]
        assert sample(restored, 20, 12).value == 20


def test_corrupt_input_and_stage_container_are_discarded() -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a",), ema_readings=3))
    sample(pipeline, 10, 0)
    for key, value in [("input", float("nan")), ("stages", []), ("stages", {"ema": []})]:
        saved = pipeline.dump()
        saved[key] = value
        restored = Pipeline(pipeline.config)
        assert restored.restore(saved, 10)
        assert sample(restored, 20, 11).value == 20


def test_backward_clock_invalidates_held_value_and_recovers() -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a",), hold_time_s=60, ema_readings=3))
    sample(pipeline, 10, 1000)
    assert pipeline.evaluate([], 400, False).status is PipelineStatus.NO_DATA
    assert sample(pipeline, 20, 401).value == 20


@pytest.mark.parametrize("method", list(CombineMethod))
def test_large_finite_inputs_never_overflow_average(method) -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a", "b"), method=method))
    result = pipeline.evaluate([SourceReading("a", 1e308), SourceReading("b", 1e308)], 0, True)
    assert result.value == pytest.approx(1e308)
    assert result.value is not None
    assert math.isfinite(result.value)
    recovered = pipeline.evaluate([SourceReading("a", 10), SourceReading("b", 10)], 1, True)
    assert recovered.value == 10


@pytest.mark.parametrize("stage", [EmaStage(3), LowPassStage(60), MovingAverageStage(60)])
def test_smoothing_opposite_extreme_values_stays_finite(stage) -> None:
    for now, value in [(0, -1e308), (30, 1e308), (60, 1e308), (120, 10)]:
        assert math.isfinite(stage.process(value, now, True))


def test_invalid_calculation_holds_finite_output_then_recovers() -> None:
    pipeline = Pipeline(PipelineConfig(sources=("a",), max_rate_per_min=1e308, hold_time_s=60))
    assert sample(pipeline, 10, 0).value == 10
    # The rate * elapsed time overflows, though both operands are finite.
    assert sample(pipeline, -1e308, 120).status is PipelineStatus.NO_DATA
    assert sample(pipeline, 20, 121).value == 20


def test_corrupt_learned_offsets_are_discarded() -> None:
    config = PipelineConfig(
        sources=("a", "b"),
        method=CombineMethod.WEIGHTED_MEAN,
        offset_time_constant_s=60,
    )
    pipeline = Pipeline(config)
    pipeline.evaluate([SourceReading("a", 10), SourceReading("b", 20)], 0, True)
    for offsets in [{"a": float("nan"), "b": 5}, {"a": 1}, []]:
        saved = pipeline.dump()
        saved["offsets"] = offsets
        restored = Pipeline(config)
        assert restored.restore(saved, 10)
        assert sample(restored, 10, 11).value == 10


def test_rate_limit_extreme_delta_is_bounded() -> None:
    stage = RateLimitStage(1e308)
    stage.process(-1e308, 0, True)
    assert stage.process(1e308, 60, True) == 0


def test_averaging_preserves_small_values_and_zero_weight_semantics() -> None:
    assert average([5e-324, 5e-324]) == 5e-324
    assert average([1e308, -1e308, 3e-300]) == pytest.approx(1e-300, rel=1e-14, abs=0)
    assert weighted_average([1e308, 1e-300], [0, 1]) == 1e-300
    assert blend(1e-300, 1e308, 0) == 1e-300
    assert blend(1e308, 1e-300, 1) == 1e-300
