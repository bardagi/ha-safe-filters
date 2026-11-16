"""The full processing pipeline: validate → quorum → combine → filter → round."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .combine import CombineMethod, combine
from .stages import LowPassStage, MovingAverageStage, OutlierStage, RateLimitStage, Stage
from .validation import SourceReading, SourceStatus, ValidationConfig, validate

_LOGGER = logging.getLogger(__name__)

STATE_VERSION = 1


class PipelineStatus(StrEnum):
    """Overall health of the output value."""

    OK = "ok"
    """Enough valid sources; the value is fresh."""
    HOLDING = "holding"
    """Too few valid sources; showing the last good value until the hold time runs out."""
    RESTORED = "restored"
    """Showing the value restored after a restart; waiting for fresh source data."""
    NO_DATA = "no_data"
    """No trustworthy value; the entity should be unavailable."""


@dataclass(frozen=True, slots=True)
class OutlierConfig:
    """Settings for the spike/outlier stage."""

    window_size: int
    radius: float


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Everything the pipeline needs, in engine units (seconds, units/minute)."""

    sources: tuple[str, ...]
    method: CombineMethod = CombineMethod.MEAN
    weights: Mapping[str, float] = field(default_factory=dict)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    min_sources: int = 1
    hold_time_s: float = 1800.0
    outlier: OutlierConfig | None = None
    max_rate_per_min: float | None = None
    lowpass_time_constant_s: float | None = None
    moving_average_window_s: float | None = None
    precision: int | None = None

    def state_fingerprint(self) -> str:
        """Identify the settings that filter state depends on.

        Saved stage state is only reused when this matches, so changing e.g. the
        sources or the low-pass time constant starts the filters fresh, while
        changing a range limit or the hold time keeps them.
        """
        relevant = {
            "sources": list(self.sources),
            "method": str(self.method),
            "weights": {key: float(value) for key, value in sorted(self.weights.items())},
            "outlier": None
            if self.outlier is None
            else [self.outlier.window_size, self.outlier.radius],
            "max_rate_per_min": self.max_rate_per_min,
            "lowpass_time_constant_s": self.lowpass_time_constant_s,
            "moving_average_window_s": self.moving_average_window_s,
        }
        encoded = json.dumps(relevant, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of one evaluation."""

    value: float | None
    """Rounded output, or ``None`` when the entity should be unavailable."""
    status: PipelineStatus
    combined: float | None
    """Combined value before filtering; ``None`` without quorum."""
    sources: tuple[SourceReading, ...]

    @property
    def valid_count(self) -> int:
        """Number of sources that passed validation."""
        return sum(1 for source in self.sources if source.status is SourceStatus.OK)


def build_stages(config: PipelineConfig) -> list[Stage]:
    """Create the enabled stages in their fixed order."""
    stages: list[Stage] = []
    if config.outlier is not None:
        stages.append(OutlierStage(config.outlier.window_size, config.outlier.radius))
    if config.max_rate_per_min is not None:
        stages.append(RateLimitStage(config.max_rate_per_min))
    if config.lowpass_time_constant_s is not None:
        stages.append(LowPassStage(config.lowpass_time_constant_s))
    if config.moving_average_window_s is not None:
        stages.append(MovingAverageStage(config.moving_average_window_s))
    return stages


class Pipeline:
    """Stateful processor for one safe-filter sensor."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.fingerprint = config.state_fingerprint()
        self.stages = build_stages(config)
        self._value: float | None = None
        """Unrounded filter output (also what is held during a dropout)."""
        self._last_good: float | None = None
        """When quorum was last met."""
        self._restored = False

    def evaluate(
        self, readings: Sequence[SourceReading], now: float, new_sample: bool
    ) -> PipelineResult:
        """Run the pipeline for the current source readings.

        ``new_sample`` is True when a source reported, False for periodic ticks.
        """
        config = self.config
        checked = tuple(validate(readings, config.validation, now))
        valid = [
            (r.entity_id, r.value)
            for r in checked
            if r.status is SourceStatus.OK and r.value is not None
        ]

        if valid and len(valid) >= config.min_sources:
            combined = combine(valid, config.method, config.weights)
            if self._value is None:
                # Starting up or recovering from "no data": old history is irrelevant.
                self._reset_stages()
            value = combined
            for stage in self.stages:
                value = stage.process(value, now, new_sample)
            self._value = value
            self._last_good = now
            self._restored = False
            return PipelineResult(self._round(value), PipelineStatus.OK, combined, checked)

        if (
            self._value is not None
            and self._last_good is not None
            and now - self._last_good <= config.hold_time_s
        ):
            status = PipelineStatus.RESTORED if self._restored else PipelineStatus.HOLDING
            return PipelineResult(self._round(self._value), status, None, checked)

        self._value = None
        self._restored = False
        return PipelineResult(None, PipelineStatus.NO_DATA, None, checked)

    def dump(self) -> dict[str, Any]:
        """Serialise everything needed to resume after a restart."""
        return {
            "version": STATE_VERSION,
            "fingerprint": self.fingerprint,
            "value": self._value,
            "last_good": self._last_good,
            "stages": {stage.kind: stage.dump() for stage in self.stages},
        }

    def restore(self, data: Mapping[str, Any] | None, now: float) -> bool:
        """Resume from :meth:`dump` output. Returns True if a value was restored.

        The value is only restored if it is not older than the hold time; it is
        then shown (status ``restored``) until fresh data arrives or the hold time,
        counted from when the value was last good, runs out. Filter state is only
        reused when the filter settings are unchanged.
        """
        if not data or data.get("version") != STATE_VERSION:
            return False
        try:
            value = data["value"]
            last_good = data["last_good"]
            if value is None or last_good is None:
                return False
            value, last_good = float(value), float(last_good)
        except (KeyError, TypeError, ValueError):
            return False
        if now - last_good > self.config.hold_time_s:
            return False

        self._value = value
        self._last_good = last_good
        self._restored = True

        if data.get("fingerprint") == self.fingerprint:
            try:
                saved = data["stages"]
                for stage in self.stages:
                    stage.load(saved[stage.kind])
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Discarding saved filter state: %s", err)
                self._reset_stages()
        return True

    def _reset_stages(self) -> None:
        for stage in self.stages:
            stage.reset()

    def _round(self, value: float) -> float:
        if self.config.precision is None:
            return value
        return round(value, self.config.precision)
