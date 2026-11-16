"""Tests for the individual filter stages."""

import math

import pytest

from custom_components.safe_filters.engine import (
    LowPassStage,
    MovingAverageStage,
    OutlierStage,
    RateLimitStage,
)


class TestOutlier:
    def test_rejects_single_spike(self) -> None:
        stage = OutlierStage(window_size=5, radius=2)
        for t, value in enumerate([10.0, 10.2, 9.9]):
            assert stage.process(value, t, new_sample=True) == value
        assert stage.process(50.0, 3, new_sample=True) == 9.9  # spike ignored
        assert stage.process(10.1, 4, new_sample=True) == 10.1

    def test_accepts_lasting_step_change(self) -> None:
        stage = OutlierStage(window_size=5, radius=2)
        for t in range(5):
            stage.process(10.0, t, new_sample=True)
        outputs = [stage.process(20.0, 5 + t, new_sample=True) for t in range(5)]
        assert outputs[0] == 10.0
        assert outputs[-1] == 20.0
        assert outputs.index(20.0) <= 3  # accepted within about half the window

    def test_first_samples_pass_until_window_has_history(self) -> None:
        stage = OutlierStage(window_size=5, radius=2)
        assert stage.process(10.0, 0, new_sample=True) == 10.0
        assert stage.process(30.0, 1, new_sample=True) == 30.0

    def test_tick_does_not_add_samples(self) -> None:
        stage = OutlierStage(window_size=5, radius=2)
        for t in range(3):
            stage.process(10.0, t, new_sample=True)
        assert stage.process(50.0, 3, new_sample=True) == 10.0
        for t in range(10):
            # The source still shows the spike, but ticks must not "vote" for it.
            assert stage.process(50.0, 4 + t, new_sample=False) == 10.0
        assert stage.dump()["window"] == [10.0, 10.0, 10.0, 50.0]

    def test_roundtrip(self) -> None:
        stage = OutlierStage(window_size=3, radius=1)
        for t, value in enumerate([1.0, 2.0, 3.0, 4.0]):
            stage.process(value, t, new_sample=True)
        copy = OutlierStage(window_size=3, radius=1)
        copy.load(stage.dump())
        assert copy.dump() == stage.dump()


class TestRateLimit:
    def test_clamps_change_per_minute(self) -> None:
        stage = RateLimitStage(max_rate_per_min=1.0)
        assert stage.process(0.0, 0, True) == 0.0
        assert stage.process(10.0, 60, True) == pytest.approx(1.0)
        # Ticks let it catch up over time.
        assert stage.process(10.0, 120, False) == pytest.approx(2.0)
        assert stage.process(10.0, 720, False) == 10.0

    def test_small_change_passes_exactly(self) -> None:
        stage = RateLimitStage(max_rate_per_min=1.0)
        stage.process(0.1, 0, True)
        assert stage.process(0.3, 60, True) == 0.3

    def test_clamps_downwards(self) -> None:
        stage = RateLimitStage(max_rate_per_min=2.0)
        stage.process(1000.0, 0, True)
        assert stage.process(0.0, 30, True) == pytest.approx(999.0)


class TestLowPass:
    def test_time_aware_step_response(self) -> None:
        stage = LowPassStage(time_constant_s=60)
        stage.process(0.0, 0, True)
        assert stage.process(10.0, 60, True) == pytest.approx(10 * (1 - math.exp(-1)))

    def test_independent_of_update_rate(self) -> None:
        """Two 30 s steps give the same result as one 60 s step (constant input)."""
        slow = LowPassStage(time_constant_s=120)
        fast = LowPassStage(time_constant_s=120)
        slow.process(0.0, 0, True)
        fast.process(0.0, 0, True)
        slow.process(10.0, 1, True)
        fast.process(10.0, 1, True)
        fast.process(10.0, 31, True)
        assert fast.process(10.0, 61, True) == pytest.approx(slow.process(10.0, 61, True))

    def test_zero_dt_does_not_move(self) -> None:
        stage = LowPassStage(time_constant_s=60)
        stage.process(5.0, 100, True)
        assert stage.process(50.0, 100, True) == 5.0


class TestMovingAverage:
    def test_time_weighted(self) -> None:
        stage = MovingAverageStage(window_s=600)
        assert stage.process(0.0, 0, True) == 0.0
        stage.process(10.0, 300, True)
        assert stage.process(10.0, 600, False) == pytest.approx(5.0)
        assert stage.process(10.0, 900, False) == pytest.approx(10.0)

    def test_short_history_averages_what_exists(self) -> None:
        stage = MovingAverageStage(window_s=3600)
        stage.process(4.0, 0, True)
        stage.process(8.0, 60, True)
        assert stage.process(8.0, 120, False) == pytest.approx(6.0)

    def test_prunes_old_samples(self) -> None:
        stage = MovingAverageStage(window_s=60)
        for t in range(0, 6000, 10):
            stage.process(float(t % 20), t, True)
        assert len(stage.dump()["samples"]) <= 8

    def test_roundtrip(self) -> None:
        stage = MovingAverageStage(window_s=600)
        stage.process(1.0, 0, True)
        stage.process(2.0, 100, True)
        copy = MovingAverageStage(window_s=600)
        copy.load(stage.dump())
        assert copy.process(2.0, 200, False) == stage.process(2.0, 200, False)

    def test_load_rejects_unordered_samples(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageStage(window_s=60).load({"samples": [[10, 1.0], [5, 2.0]]})
