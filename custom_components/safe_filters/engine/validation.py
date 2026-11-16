"""Per-source validation: decide which source readings can be trusted."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum


class SourceStatus(StrEnum):
    """Why a source is (not) used."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    NON_NUMERIC = "non_numeric"
    UNIT_MISMATCH = "unit_mismatch"
    OUT_OF_RANGE = "out_of_range"
    STALE = "stale"
    DEVIATION = "deviation"


@dataclass(frozen=True, slots=True)
class SourceReading:
    """One source's current reading, already converted to the output unit.

    The Home Assistant layer pre-fills ``status`` for problems it detects while
    reading the state (unavailable, non-numeric, unit mismatch). ``value`` must be
    set whenever ``status`` is ``OK``.
    """

    entity_id: str
    value: float | None
    last_reported: float | None = None
    status: SourceStatus = SourceStatus.OK


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Thresholds for trusting a source."""

    min_value: float | None = None
    max_value: float | None = None
    stale_after_s: float = 0.0
    """Seconds without a report before a source is stale. 0 disables the check."""
    max_deviation: float | None = None
    """Max distance from the median of all valid sources (needs >= 3 valid sources)."""


MIN_SOURCES_FOR_DEVIATION_CHECK = 3


def validate(
    readings: Sequence[SourceReading], config: ValidationConfig, now: float
) -> list[SourceReading]:
    """Return the readings with ``status`` updated; order is preserved."""
    checked = [_check_single(reading, config, now) for reading in readings]
    return _check_deviation(checked, config.max_deviation)


def _check_single(reading: SourceReading, config: ValidationConfig, now: float) -> SourceReading:
    if reading.status is not SourceStatus.OK:
        return reading
    value = reading.value
    if value is None or not math.isfinite(value):
        return replace(reading, status=SourceStatus.NON_NUMERIC)
    if (config.min_value is not None and value < config.min_value) or (
        config.max_value is not None and value > config.max_value
    ):
        return replace(reading, status=SourceStatus.OUT_OF_RANGE)
    if (
        config.stale_after_s > 0
        and reading.last_reported is not None
        and now - reading.last_reported > config.stale_after_s
    ):
        return replace(reading, status=SourceStatus.STALE)
    return reading


def _check_deviation(
    readings: list[SourceReading], max_deviation: float | None
) -> list[SourceReading]:
    if max_deviation is None:
        return readings
    valid = [r.value for r in readings if r.status is SourceStatus.OK and r.value is not None]
    if len(valid) < MIN_SOURCES_FOR_DEVIATION_CHECK:
        # With only two sources there is no way to tell which one is wrong.
        return readings
    median = statistics.median(valid)
    return [
        replace(r, status=SourceStatus.DEVIATION)
        if r.status is SourceStatus.OK
        and r.value is not None
        and abs(r.value - median) > max_deviation
        else r
        for r in readings
    ]
