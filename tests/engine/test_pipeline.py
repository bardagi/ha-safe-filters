"""Tests for the full pipeline: quorum, hold, filters, persistence."""

import math

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
    assert pipeline.evaluate(readings(20.0, 20.0, 20.0), 120, True).value == 10.0
    result = pipeline.evaluate(readings(20.0, 20.0, 20.0), 180, False)
    assert 10.0 < value_of(result) < 20.0  # smoothed, not reset


def test_hold_keeps_smoothing_toward_the_last_value() -> None:
    """A source going stale mid-step doesn't freeze the low-pass halfway."""
    pipeline = make(
        hold_time_s=1800,
        lowpass_time_constant_s=300,
        validation=ValidationConfig(stale_after_s=120),
    )
    pipeline.evaluate(readings(10.0, 10.0, 10.0, reported=0), 0, True)
    assert pipeline.evaluate(readings(20.0, 20.0, 20.0, reported=60), 60, True).value == 10.0

    held = [
        pipeline.evaluate(readings(20.0, 20.0, 20.0, reported=60), t, False)
        for t in (300, 600, 1800)
    ]
    assert [r.status for r in held] == [PipelineStatus.HOLDING] * 3
    assert [r.combined for r in held] == [None] * 3
    assert value_of(held[0]) == pytest.approx(20 - 10 * math.exp(-240 / 300))
    assert value_of(held[1]) > value_of(held[0])
    assert value_of(held[2]) == pytest.approx(20.0, abs=0.05)
    # The hold time still counts from the last good value.
    gone = pipeline.evaluate(readings(20.0, 20.0, 20.0, reported=60), 1861, False)
    assert gone.status is PipelineStatus.NO_DATA


def test_only_reports_from_valid_sources_are_new_samples() -> None:
    pipeline = make(
        method=CombineMethod.MEAN,
        outlier=OutlierConfig(window_size=5, radius=100),
        ema_readings=3,  # alpha = 0.5
    )
    assert pipeline.evaluate(readings(10.0, 10.0, 40.0), 0, True).value == 20.0

    # sensor.c dropping out changes the combined value, but is not a reading.
    dropout = pipeline.evaluate(readings(10.0, 10.0, None), 60, True, reported_by="sensor.c")
    assert dropout.combined == 10.0
    assert dropout.value == 20.0
    assert pipeline.dump()["stages"]["outlier"]["window"] == [20.0]

    reading = pipeline.evaluate(readings(10.0, 10.0, None), 120, True, reported_by="sensor.a")
    assert reading.value == pytest.approx(15.0)
    assert pipeline.dump()["stages"]["outlier"]["window"] == [20.0, 10.0]


def test_fingerprint_unchanged_without_ema() -> None:
    """Filter state saved before the EMA over readings existed is still reused."""
    config = PipelineConfig(
        sources=SOURCES,
        outlier=OutlierConfig(window_size=5, radius=2.0),
        lowpass_time_constant_s=300.0,
    )
    # As computed before the EMA over readings was added.
    assert config.state_fingerprint() == "a36b9776cbab8784"
    with_ema = PipelineConfig(
        sources=SOURCES,
        outlier=OutlierConfig(window_size=5, radius=2.0),
        lowpass_time_constant_s=300.0,
        ema_readings=5,
    )
    assert with_ema.state_fingerprint() != config.state_fingerprint()


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


WEIGHTS = {"sensor.a": 50.0, "sensor.b": 30.0, "sensor.c": 20.0}


def make_weighted(**kwargs) -> Pipeline:
    kwargs.setdefault("weights", WEIGHTS)
    return make(method=CombineMethod.WEIGHTED_MEAN, **kwargs)


class TestWeighted:
    @pytest.mark.parametrize(
        "weights",
        [WEIGHTS, {"sensor.a": 0.5, "sensor.b": 0.3, "sensor.c": 0.2}],
    )
    def test_min_weight_share(self, weights) -> None:
        """50/30/20 needing 50 %: A alone or B+C is enough, C alone is not."""
        pipeline = make_weighted(weights=weights, min_weight_share=0.5, hold_time_s=600)
        assert pipeline.evaluate(readings(22.0, None, None), 0, True).status is PipelineStatus.OK
        both = pipeline.evaluate(readings(None, 21.0, 19.0), 60, True)
        assert both.status is PipelineStatus.OK

        held = pipeline.evaluate(readings(None, None, 19.0), 120, True)
        assert held.status is PipelineStatus.HOLDING
        assert held.value == both.value
        assert held.shares == {"sensor.a": 0.0, "sensor.b": 0.0, "sensor.c": 0.0}
        gone = pipeline.evaluate(readings(None, None, 19.0), 661, False)
        assert gone.status is PipelineStatus.NO_DATA

    def test_min_weight_share_and_min_sources_both_apply(self) -> None:
        pipeline = make_weighted(min_weight_share=0.5, min_sources=2, hold_time_s=0)
        assert pipeline.evaluate(readings(22.0, None, None), 0, True).status is (
            PipelineStatus.NO_DATA
        )

    def test_min_weight_share_ignored_when_all_weights_are_zero(self) -> None:
        pipeline = make_weighted(
            weights={"sensor.a": 0.0, "sensor.b": 0.0, "sensor.c": 0.0}, min_weight_share=0.9
        )
        assert pipeline.evaluate(readings(None, 21.0, 19.0), 0, True).value == 20.0

    def test_shares(self) -> None:
        result = make_weighted().evaluate(readings(None, 21.0, 19.0), 0, True)
        assert result.combined == pytest.approx(20.2)
        assert result.shares == pytest.approx({"sensor.a": 0.0, "sensor.b": 0.6, "sensor.c": 0.4})
        assert result.offsets == {}

    def test_no_shares_for_other_methods(self) -> None:
        result = make(method=CombineMethod.MEAN).evaluate(readings(None, 21.0, 19.0), 0, True)
        assert result.shares == {}

    def test_offset_learning_bridges_a_dropout(self) -> None:
        pipeline = make_weighted(offset_time_constant_s=3600)
        pipeline.evaluate(readings(22.0, 21.0, 19.0), 0, True)
        result = pipeline.evaluate(readings(None, 21.0, 19.0), 60, True)
        assert result.combined == pytest.approx(21.1)
        assert result.offsets == pytest.approx(
            {"sensor.a": 0.9, "sensor.b": -0.1, "sensor.c": -2.1}
        )

    def test_offset_learning_ignored_for_other_methods(self) -> None:
        pipeline = make(method=CombineMethod.MEAN, offset_time_constant_s=3600)
        pipeline.evaluate(readings(22.0, 21.0, 19.0), 0, True)
        result = pipeline.evaluate(readings(None, 21.0, 19.0), 60, True)
        assert result.combined == pytest.approx(20.0)
        assert result.offsets == {}

    def _learned_state(self) -> dict:
        pipeline = make_weighted(offset_time_constant_s=3600, hold_time_s=1800)
        pipeline.evaluate(readings(22.0, 21.0, 19.0), 0, True)
        return pipeline.dump()

    def test_offsets_survive_restore(self) -> None:
        restored = make_weighted(offset_time_constant_s=3600, hold_time_s=1800)
        assert restored.restore(self._learned_state(), now=60)
        result = restored.evaluate(readings(None, 21.0, 19.0), 120, True)
        assert result.combined == pytest.approx(21.1)

    def test_offsets_restored_even_when_value_is_too_old(self) -> None:
        restored = make_weighted(offset_time_constant_s=3600, hold_time_s=1800)
        assert not restored.restore(self._learned_state(), now=36_000)
        result = restored.evaluate(readings(None, 21.0, 19.0), 36_060, True)
        assert result.combined == pytest.approx(21.1)

    def test_changed_weights_discard_offsets(self) -> None:
        restored = make_weighted(
            weights={"sensor.a": 1.0, "sensor.b": 1.0, "sensor.c": 1.0},
            offset_time_constant_s=3600,
        )
        assert restored.restore(self._learned_state(), now=60)
        result = restored.evaluate(readings(None, 21.0, 19.0), 120, True)
        assert result.combined == pytest.approx(20.0)

    def test_corrupt_offsets_are_discarded(self) -> None:
        state = self._learned_state()
        state["offsets"] = "bogus"
        restored = make_weighted(offset_time_constant_s=3600, hold_time_s=1800)
        assert restored.restore(state, now=60)
        result = restored.evaluate(readings(None, 21.0, 19.0), 120, True)
        assert result.combined == pytest.approx(20.2)


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
        # Keeps settling toward the last input (12.0), as the original would have.
        assert snapshot.value == saved.evaluate([], 420, False).value
        assert state["value"] < value_of(snapshot) < 12.0

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

    def test_state_without_input_is_shown_as_is(self) -> None:
        """State saved before "input" existed: the value is held, not smoothed further."""
        state = self._warm().dump()
        del state["input"]
        restored = make(hold_time_s=1800, lowpass_time_constant_s=600)
        assert restored.restore(state, now=420)
        assert restored.evaluate([], 900, False).value == state["value"]

    def test_changed_filter_settings_start_fresh(self) -> None:
        state = self._warm().dump()
        restored = make(hold_time_s=1800, lowpass_time_constant_s=60)
        assert restored.restore(state, now=420)
        # The value is shown as is: the new low-pass has no history to settle.
        assert restored.evaluate([], 900, False).value == state["value"]
        # ... and its first sample passes through.
        assert restored.evaluate(readings(15.0, 15.0, 15.0), 960, True).value == 15.0

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
