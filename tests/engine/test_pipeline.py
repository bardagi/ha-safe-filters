"""Tests for the full pipeline: quorum, hold, filters, persistence."""

import pytest

from custom_components.safe_filters.engine import (
    CombineMethod,
    OutlierConfig,
    Pipeline,
    PipelineConfig,
    PipelineResult,
    PipelineStatus,
    SourceReading,
    SourceStatus,
    ValidationConfig,
)

SOURCES = ("sensor.a", "sensor.b", "sensor.c")


def readings(*values: float | None, reported: float | None = None) -> list[SourceReading]:
    return [
        SourceReading(entity_id, value, reported)
        if value is not None
        else SourceReading(entity_id, None, status=SourceStatus.UNAVAILABLE)
        for entity_id, value in zip(SOURCES, values, strict=True)
    ]


def make(**kwargs) -> Pipeline:
    kwargs.setdefault("sources", SOURCES)
    return Pipeline(PipelineConfig(**kwargs))


def value_of(result: PipelineResult) -> float:
    """The output value, which must exist."""
    assert result.value is not None
    return result.value


def test_combines_and_rounds() -> None:
    pipeline = make(method=CombineMethod.MEAN, precision=1)
    result = pipeline.evaluate(readings(10.0, 10.1, 10.25), 0, True)
    assert result.status is PipelineStatus.OK
    assert result.value == 10.1
    assert result.combined == pytest.approx(10.116666, abs=1e-5)
    assert result.valid_count == 3


def test_unavailable_source_is_skipped() -> None:
    pipeline = make(method=CombineMethod.MIN)
    result = pipeline.evaluate(readings(None, 5.0, 7.0), 0, True)
    assert result.value == 5.0
    assert result.valid_count == 2
    assert result.sources[0].status is SourceStatus.UNAVAILABLE


def test_glitching_sensor_does_not_win_min() -> None:
    """The outdoor min-temperature case: one sensor reports -40 on a bad battery."""
    pipeline = make(
        method=CombineMethod.MIN,
        validation=ValidationConfig(max_deviation=5),
    )
    assert pipeline.evaluate(readings(4.0, 4.6, -40.0), 0, True).value == 4.0


def test_stale_source_is_skipped() -> None:
    pipeline = make(method=CombineMethod.MIN, validation=ValidationConfig(stale_after_s=3600))
    stale = [
        SourceReading("sensor.a", -5.0, last_reported=0),
        SourceReading("sensor.b", 3.0, last_reported=9000),
        SourceReading("sensor.c", 4.0, last_reported=9000),
    ]
    assert pipeline.evaluate(stale, 10_000, True).value == 3.0


def test_hold_then_unavailable_then_recover() -> None:
    pipeline = make(min_sources=2, hold_time_s=600, lowpass_time_constant_s=300)
    assert pipeline.evaluate(readings(10.0, 10.0, 10.0), 0, True).value == 10.0

    held = pipeline.evaluate(readings(None, None, 99.0), 60, True)
    assert held.status is PipelineStatus.HOLDING
    assert held.value == 10.0
    assert held.combined is None

    assert pipeline.evaluate(readings(None, None, 99.0), 600, False).status is (
        PipelineStatus.HOLDING
    )
    gone = pipeline.evaluate(readings(None, None, 99.0), 601, False)
    assert gone.status is PipelineStatus.NO_DATA
    assert gone.value is None

    # Recovery: filters restart instead of dragging the old 10.0 along.
    recovered = pipeline.evaluate(readings(20.0, 20.0, None), 5000, True)
    assert recovered.status is PipelineStatus.OK
    assert recovered.value == 20.0


def test_short_dropout_keeps_filter_state() -> None:
    pipeline = make(hold_time_s=600, lowpass_time_constant_s=300)
    pipeline.evaluate(readings(10.0, 10.0, 10.0), 0, True)
    pipeline.evaluate(readings(None, None, None), 60, True)
    result = pipeline.evaluate(readings(20.0, 20.0, 20.0), 120, True)
    assert 10.0 < value_of(result) < 20.0  # smoothed, not reset


def test_spike_never_reaches_the_smoother() -> None:
    pipeline = make(
        method=CombineMethod.MEDIAN,
        outlier=OutlierConfig(window_size=5, radius=2),
        lowpass_time_constant_s=300,
    )
    for t in range(5):
        pipeline.evaluate(readings(10.0, 10.0, 10.0), t * 60, True)
    spike = pipeline.evaluate(readings(80.0, 80.0, 80.0), 300, True)
    assert spike.value == 10.0
    assert spike.combined == 80.0


def test_rounds_only_at_output() -> None:
    pipeline = make(precision=0, lowpass_time_constant_s=600)
    pipeline.evaluate(readings(0.0, 0.0, 0.0), 0, True)
    values = []
    for minute in range(1, 6):
        result = pipeline.evaluate(readings(1.0, 1.0, 1.0), minute * 60, True)
        values.append(result.value)
    # Each step is far below 0.5, but the unrounded state keeps integrating.
    assert values[-1] == 0.0
    assert 0.3 < pipeline.dump()["value"] < 0.5
    for minute in range(6, 30):
        result = pipeline.evaluate(readings(1.0, 1.0, 1.0), minute * 60, False)
    assert result.value == 1.0


class TestRestore:
    def _warm(self) -> Pipeline:
        pipeline = make(hold_time_s=1800, lowpass_time_constant_s=600, precision=2)
        pipeline.evaluate(readings(10.0, 10.0, 10.0), 0, True)
        pipeline.evaluate(readings(12.0, 12.0, 12.0), 300, True)
        return pipeline

    def test_restores_value_and_filter_state(self) -> None:
        saved = self._warm()
        state = saved.dump()

        restored = make(hold_time_s=1800, lowpass_time_constant_s=600, precision=2)
        assert restored.restore(state, now=420)
        snapshot = restored.evaluate([], 420, False)
        assert snapshot.status is PipelineStatus.RESTORED
        assert snapshot.value == round(state["value"], 2)

        # Continues smoothing from the restored state instead of jumping to 12.0.
        expected = value_of(saved.evaluate(readings(12.0, 12.0, 12.0), 480, True))
        assert restored.evaluate(readings(12.0, 12.0, 12.0), 480, True).value == expected
        assert expected < 12.0

    def test_restored_value_expires_with_hold_time(self) -> None:
        state = self._warm().dump()
        restored = make(hold_time_s=1800, lowpass_time_constant_s=600)
        assert restored.restore(state, now=1000)
        assert restored.evaluate([], 2101, False).status is PipelineStatus.NO_DATA

    def test_too_old_is_not_restored(self) -> None:
        state = self._warm().dump()
        restored = make(hold_time_s=1800, lowpass_time_constant_s=600)
        assert not restored.restore(state, now=300 + 1801)
        assert restored.evaluate([], 2200, False).status is PipelineStatus.NO_DATA

    def test_changed_filter_settings_start_fresh(self) -> None:
        state = self._warm().dump()
        restored = make(hold_time_s=1800, lowpass_time_constant_s=60)
        assert restored.restore(state, now=420)
        # Value is shown, but the new low-pass has no history: first sample passes through.
        assert restored.evaluate(readings(15.0, 15.0, 15.0), 480, True).value == 15.0

    def test_changed_hold_time_keeps_filter_state(self) -> None:
        saved = self._warm()
        restored = make(hold_time_s=3600, lowpass_time_constant_s=600, precision=2)
        assert restored.restore(saved.dump(), now=420)
        assert value_of(restored.evaluate(readings(12.0, 12.0, 12.0), 480, True)) < 12.0

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"version": 999},
            {"version": 1, "value": "x", "last_good": 0},
            {"version": 1, "value": None, "last_good": 0},
        ],
    )
    def test_bad_data_is_ignored(self, data) -> None:
        assert not make().restore(data, now=0)

    def test_corrupt_stage_state_resets_stages(self) -> None:
        state = self._warm().dump()
        state["stages"]["lowpass"] = {"bogus": 1}
        restored = make(hold_time_s=1800, lowpass_time_constant_s=600, precision=2)
        assert restored.restore(state, now=420)
        assert restored.evaluate(readings(15.0, 15.0, 15.0), 480, True).value == 15.0
