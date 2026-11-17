"""Tests for bridging source dropouts with learned offsets."""

import math

import pytest

from custom_components.safe_filters.engine import OffsetLearner

SOURCES = ("sensor.a", "sensor.b", "sensor.c")
WEIGHTS = {"sensor.a": 50.0, "sensor.b": 30.0, "sensor.c": 20.0}
HOUR = 3600.0

# With A = 22, B = 21, C = 19 the weighted mean is 21.1, so the offsets are:
OFFSETS = {"sensor.a": 0.9, "sensor.b": -0.1, "sensor.c": -2.1}


def values(a: float | None, b: float | None, c: float | None) -> list[tuple[str, float]]:
    """The valid ``(entity_id, value)`` pairs; ``None`` means the source is not valid."""
    return [
        (entity_id, value)
        for entity_id, value in zip(SOURCES, (a, b, c), strict=True)
        if value is not None
    ]


def make(time_constant_s: float = HOUR) -> OffsetLearner:
    return OffsetLearner(SOURCES, WEIGHTS, time_constant_s)


def test_all_valid_is_the_plain_weighted_mean() -> None:
    learner = make()
    assert learner.combine(values(22.0, 21.0, 19.0), 0) == pytest.approx(21.1)
    assert learner.offsets == pytest.approx(OFFSETS)


def test_dropout_does_not_jump() -> None:
    """Renormalising alone gives 20.2 when the 50 % source (0.9 above) drops out."""
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 0)
    assert learner.combine(values(None, 21.0, 19.0), 60) == pytest.approx(21.1)


def test_follows_real_changes_during_dropout() -> None:
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 0)
    assert learner.combine(values(None, 22.0, 20.0), 60) == pytest.approx(22.1)


def test_rejoin_is_continuous() -> None:
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 0)
    during = learner.combine(values(None, 22.0, 20.0), 60)
    # A comes back with the same offset it had: no step.
    assert learner.combine(values(23.0, 22.0, 20.0), 120) == pytest.approx(during)


def test_plain_renormalisation_until_learned() -> None:
    learner = make()
    assert learner.combine(values(None, 21.0, 19.0), 0) == pytest.approx(20.2)
    assert learner.offsets == {}


def test_learns_only_while_all_sources_are_valid() -> None:
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 0)
    learner.combine(values(None, 25.0, 19.0), 600)
    assert learner.offsets == pytest.approx(OFFSETS)


def test_offsets_follow_slow_changes() -> None:
    learner = make(HOUR)
    learner.combine(values(22.0, 21.0, 19.0), 0)
    # A now reads 1 higher: mean 21.6, A's new offset 1.4. After one time
    # constant, ~63 % of the change is learned.
    learner.combine(values(23.0, 21.0, 19.0), HOUR)
    assert learner.offsets["sensor.a"] == pytest.approx(0.9 + 0.5 * (1 - math.exp(-1)))
    # The weighted offsets still cancel out, so all-valid stays the plain mean.
    weighted = math.fsum(WEIGHTS[s] * offset for s, offset in learner.offsets.items())
    assert weighted == pytest.approx(0.0, abs=1e-9)


def test_weight_zero_source_is_not_needed_for_learning() -> None:
    learner = OffsetLearner(SOURCES, {"sensor.a": 1.0, "sensor.b": 1.0, "sensor.c": 0.0}, HOUR)
    learner.combine(values(22.0, 20.0, None), 0)
    assert set(learner.offsets) == {"sensor.a", "sensor.b"}
    assert learner.combine(values(None, 20.0, 5.0), 60) == pytest.approx(21.0)


def test_clock_going_backwards_learns_nothing() -> None:
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 1000)
    learner.combine(values(30.0, 21.0, 19.0), 500)
    assert learner.offsets == pytest.approx(OFFSETS)


def test_gap_without_quorum_is_not_learning_time() -> None:
    learner = make()
    learner.combine(values(22.0, 21.0, 19.0), 0)
    learner.advance(10 * HOUR)
    learner.combine(values(32.0, 21.0, 19.0), 10 * HOUR)
    assert learner.offsets == pytest.approx(OFFSETS)


def test_dump_and_load() -> None:
    saved = make()
    saved.combine(values(22.0, 21.0, 19.0), 0)

    restored = make()
    restored.load(saved.dump())
    assert restored.offsets == pytest.approx(OFFSETS)
    # Time spent stopped is not learning time: the first sample changes nothing.
    restored.combine(values(30.0, 21.0, 19.0), 10 * HOUR)
    assert restored.offsets == pytest.approx(OFFSETS)
    assert restored.combine(values(None, 21.0, 19.0), 10 * HOUR + 60) == pytest.approx(21.1)


def test_load_nothing_and_reset() -> None:
    learner = make()
    learner.load(None)
    assert learner.dump() is None
    learner.combine(values(22.0, 21.0, 19.0), 0)
    learner.reset()
    assert learner.offsets == {}
