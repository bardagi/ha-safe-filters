"""Tests for the individual filter stages."""

import math

import pytest

from custom_components.safe_filters.engine import (
    EmaStage,
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

    def test_keeps_moving_after_clock_jumps_back(self) -> None:
        stage = RateLimitStage(max_rate_per_min=1.0)
        stage.process(0.0, 1000, True)
        assert stage.process(10.0, 400, True) == 0.0  # clock went back: no step
        assert stage.process(10.0, 460, False) == pytest.approx(1.0)


class TestLowPass:
    def test_time_aware_step_response(self) -> None:
        stage = LowPassStage(time_constant_s=60)
        stage.process(0.0, 0, True)
        # The input was 0 until now: the step only starts to count from here.
        assert stage.process(10.0, 60, True) == 0.0
        assert stage.process(10.0, 120, False) == pytest.approx(10 * (1 - math.exp(-1)))

    def test_new_sample_is_not_applied_retroactively(self) -> None:
        """The response to a step does not depend on when the previous tick was."""
        late = LowPassStage(time_constant_s=300)
        early = LowPassStage(time_constant_s=300)
        for stage in (late, early):
            stage.process(0.0, 0, True)
        late.process(0.0, 59, False)
        assert late.process(10.0, 119, True) == 0.0
        assert early.process(10.0, 119, True) == 0.0
        assert late.process(10.0, 179, False) == pytest.approx(10 * (1 - math.exp(-60 / 300)))
        assert early.process(10.0, 179, False) == pytest.approx(late.dump()["output"])

    def test_keeps_moving_after_clock_jumps_back(self) -> None:
        stage = LowPassStage(time_constant_s=60)
        stage.process(0.0, 1000, True)
        assert stage.process(10.0, 400, True) == 0.0
        assert stage.process(10.0, 460, False) == pytest.approx(10 * (1 - math.exp(-1)))

    def test_loads_state_without_input(self) -> None:
        """State saved by 0.1.0 has no "input"; the output is assumed settled."""
        stage = LowPassStage(time_constant_s=60)
        stage.load({"output": 5.0, "time": 0.0})
        assert stage.process(10.0, 60, True) == 5.0
        assert stage.process(10.0, 120, False) == pytest.approx(10 - 5 * math.exp(-1))

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


class TestEma:
    def test_step_response_per_reading(self) -> None:
        stage = EmaStage(readings=3)  # alpha = 0.5
        assert stage.process(0.0, 0, True) == 0.0
        assert stage.process(10.0, 1, True) == pytest.approx(5.0)
        assert stage.process(10.0, 2, True) == pytest.approx(7.5)

    def test_time_plays_no_part(self) -> None:
        quick = EmaStage(readings=3)
        slow = EmaStage(readings=3)
        for stage, gap in ((quick, 1), (slow, 3600)):
            stage.process(0.0, 0, True)
            stage.process(10.0, gap, True)
        assert quick.dump() == slow.dump()

    def test_ticks_do_not_move_it(self) -> None:
        stage = EmaStage(readings=3)
        stage.process(0.0, 0, True)
        stage.process(10.0, 1, True)
        for t in range(2, 100):
            assert stage.process(10.0, t, False) == pytest.approx(5.0)

    def test_one_reading_passes_through(self) -> None:
        stage = EmaStage(readings=1)
        stage.process(0.0, 0, True)
        assert stage.process(7.0, 1, True) == 7.0

    def test_first_value_seeds_even_on_a_tick(self) -> None:
        assert EmaStage(readings=5).process(4.0, 0, False) == 4.0

    def test_roundtrip_and_reset(self) -> None:
        stage = EmaStage(readings=5)
        stage.process(1.0, 0, True)
        stage.process(4.0, 1, True)
        copy = EmaStage(readings=5)
        copy.load(stage.dump())
        assert copy.process(4.0, 2, True) == stage.process(4.0, 2, True)
        stage.reset()
        assert stage.process(9.0, 3, True) == 9.0


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
