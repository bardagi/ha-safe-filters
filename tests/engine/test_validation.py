"""Tests for per-source validation."""

from custom_components.safe_filters.engine import (
    SourceReading,
    SourceStatus,
    ValidationConfig,
    validate,
)

NOW = 10_000.0


def _statuses(readings: list[SourceReading]) -> list[SourceStatus]:
    return [reading.status for reading in readings]


def test_prefilled_status_is_kept() -> None:
    readings = [SourceReading("a", None, status=SourceStatus.UNAVAILABLE)]
    assert _statuses(validate(readings, ValidationConfig(), NOW)) == [SourceStatus.UNAVAILABLE]


def test_non_finite_is_non_numeric() -> None:
    readings = [SourceReading("a", float("nan")), SourceReading("b", float("inf"))]
    assert _statuses(validate(readings, ValidationConfig(), NOW)) == [
        SourceStatus.NON_NUMERIC,
        SourceStatus.NON_NUMERIC,
    ]


def test_range() -> None:
    config = ValidationConfig(min_value=930, max_value=1070)
    readings = [SourceReading("a", 0.0), SourceReading("b", 1013.0), SourceReading("c", 1200.0)]
    assert _statuses(validate(readings, config, NOW)) == [
        SourceStatus.OUT_OF_RANGE,
        SourceStatus.OK,
        SourceStatus.OUT_OF_RANGE,
    ]


def test_stale() -> None:
    config = ValidationConfig(stale_after_s=3600)
    readings = [
        SourceReading("fresh", 1.0, last_reported=NOW - 60),
        SourceReading("stale", 1.0, last_reported=NOW - 7200),
        SourceReading("unknown_age", 1.0, last_reported=None),
    ]
    assert _statuses(validate(readings, config, NOW)) == [
        SourceStatus.OK,
        SourceStatus.STALE,
        SourceStatus.OK,
    ]


def test_stale_check_disabled() -> None:
    readings = [SourceReading("a", 1.0, last_reported=0.0)]
    assert _statuses(validate(readings, ValidationConfig(stale_after_s=0), NOW)) == [
        SourceStatus.OK
    ]


def test_deviation_rejects_the_odd_one_out() -> None:
    config = ValidationConfig(max_deviation=3)
    readings = [SourceReading("a", 10.0), SourceReading("b", 10.5), SourceReading("c", -40.0)]
    assert _statuses(validate(readings, config, NOW)) == [
        SourceStatus.OK,
        SourceStatus.OK,
        SourceStatus.DEVIATION,
    ]


def test_deviation_needs_three_valid_sources() -> None:
    config = ValidationConfig(max_deviation=3)
    readings = [
        SourceReading("a", 10.0),
        SourceReading("b", -40.0),
        SourceReading("c", None, status=SourceStatus.UNAVAILABLE),
    ]
    assert _statuses(validate(readings, config, NOW)) == [
        SourceStatus.OK,
        SourceStatus.OK,
        SourceStatus.UNAVAILABLE,
    ]


def test_range_is_checked_before_deviation() -> None:
    """An out-of-range reading must not drag the median used for the deviation check."""
    config = ValidationConfig(min_value=-50, max_deviation=3)
    readings = [
        SourceReading("a", 10.0),
        SourceReading("b", 11.0),
        SourceReading("c", 12.0),
        SourceReading("glitch", -999.0),
    ]
    assert _statuses(validate(readings, config, NOW)) == [
        SourceStatus.OK,
        SourceStatus.OK,
        SourceStatus.OK,
        SourceStatus.OUT_OF_RANGE,
    ]
