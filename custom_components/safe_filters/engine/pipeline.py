"""The full processing pipeline: validate → quorum → combine → filter → round."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .combine import CombineMethod, combine, effective_weights, weight_of
from .compensation import OffsetLearner
from .stages import (
    EmaStage,
    LowPassStage,
    MovingAverageStage,
    OutlierStage,
    RateLimitStage,
    Stage,
)
from .validation import SourceReading, SourceStatus, ValidationConfig, validate

_LOGGER = logging.getLogger(__name__)

STATE_VERSION = 1

# Tolerance for comparing weight shares, so e.g. 30 % + 20 % counts as 50 %.
_SHARE_EPSILON = 1e-9


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
    min_weight_share: float = 0.0
    """Weighted mean only: share (0..1) of the total weight that must be valid."""
    offset_time_constant_s: float | None = None
    """Weighted mean only: learn source offsets over this time to bridge dropouts."""
    hold_time_s: float = 1800.0
    outlier: OutlierConfig | None = None
    max_rate_per_min: float | None = None
    lowpass_time_constant_s: float | None = None
    ema_readings: int | None = None
    moving_average_window_s: float | None = None
    precision: int | None = None

    def state_fingerprint(self) -> str:
        """Identify the settings that filter state depends on.

        Saved stage state is only reused when this matches, so changing e.g. the
        sources or the low-pass time constant starts the filters fresh, while
        changing a range limit or the hold time keeps them.
        """
        relevant: dict[str, Any] = {
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
        if self.ema_readings is not None:
            # Only when set, so entries without it keep their saved filter state.
            relevant["ema_readings"] = self.ema_readings
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
    shares: Mapping[str, float] = field(default_factory=dict)
    """Weighted mean only: each source's share (0..1) of ``combined``; 0 when not used."""
    offsets: Mapping[str, float] = field(default_factory=dict)
    """Offset learning only: how far each source usually is from the full weighted mean."""

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
    if config.ema_readings is not None:
        stages.append(EmaStage(config.ema_readings))
    if config.moving_average_window_s is not None:
        stages.append(MovingAverageStage(config.moving_average_window_s))
    return stages


class Pipeline:
    """Stateful processor for one safe-filter sensor."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.fingerprint = config.state_fingerprint()
        self.stages = build_stages(config)
        self._weighted = config.method is CombineMethod.WEIGHTED_MEAN
        self._learner = (
            OffsetLearner(config.sources, config.weights, config.offset_time_constant_s)
            if self._weighted and config.offset_time_constant_s is not None
            else None
        )
        self._value: float | None = None
        """Unrounded filter output (also what is held during a dropout)."""
        self._input: float | None = None
        """The last combined value fed to the filters, which keep settling toward it
        while it is held."""
        self._last_good: float | None = None
        """When quorum was last met."""
        self._restored = False

    def evaluate(
        self,
        readings: Sequence[SourceReading],
        now: float,
        new_sample: bool,
        reported_by: str | None = None,
    ) -> PipelineResult:
        """Run the pipeline for the current source readings.

        ``new_sample`` is True when a source reported, False for periodic ticks.
        ``reported_by`` is the source that reported, if known: a report only counts
        as a new sample if that source is valid.
        """
        config = self.config
        checked = tuple(validate(readings, config.validation, now))
        valid = [
            (r.entity_id, r.value)
            for r in checked
            if r.status is SourceStatus.OK and r.value is not None
        ]
        if new_sample and reported_by is not None:
            new_sample = any(entity_id == reported_by for entity_id, _ in valid)

        if valid and len(valid) >= config.min_sources and self._enough_weight(valid):
            if self._learner is not None:
                combined = self._learner.combine(valid, now)
            else:
                combined = combine(valid, config.method, config.weights)
            if self._value is None:
                # Starting up or recovering from "no data": old history is irrelevant.
                self._reset_stages()
            self._value = self._filter(combined, now, new_sample)
            self._input = combined
            self._last_good = now
            self._restored = False
            return self._result(
                self._round(self._value), PipelineStatus.OK, combined, checked, valid
            )

        if self._learner is not None:
            self._learner.advance(now)

        if (
            self._value is not None
            and self._last_good is not None
            and now - self._last_good <= config.hold_time_s
        ):
            if self._input is not None:
                # No new value, but the time-based filters keep settling toward
                # the last one, however old it is.
                self._value = self._filter(self._input, now, new_sample=False)
            status = PipelineStatus.RESTORED if self._restored else PipelineStatus.HOLDING
            return self._result(self._round(self._value), status, None, checked, [])

        self._value = None
        self._input = None
        self._restored = False
        return self._result(None, PipelineStatus.NO_DATA, None, checked, [])

    def _filter(self, value: float, now: float, new_sample: bool) -> float:
        for stage in self.stages:
            value = stage.process(value, now, new_sample)
        return value

    def _enough_weight(self, valid: Sequence[tuple[str, float]]) -> bool:
        """Whether the valid sources carry the required share of the total weight."""
        config = self.config
        if not self._weighted or config.min_weight_share <= 0:
            return True
        total = math.fsum(weight_of(config.weights, source) for source in config.sources)
        if total <= 0:
            # Every weight is 0, so they all count equally: no share to require.
            return True
        share = math.fsum(weight_of(config.weights, entity_id) for entity_id, _ in valid) / total
        return share >= config.min_weight_share - _SHARE_EPSILON

    def _result(
        self,
        value: float | None,
        status: PipelineStatus,
        combined: float | None,
        checked: tuple[SourceReading, ...],
        used: Sequence[tuple[str, float]],
    ) -> PipelineResult:
        shares: dict[str, float] = {}
        if self._weighted:
            used_shares = effective_weights(used, self.config.weights) if used else {}
            shares = {r.entity_id: used_shares.get(r.entity_id, 0.0) for r in checked}
        offsets = {} if self._learner is None else self._learner.offsets
        return PipelineResult(value, status, combined, checked, shares, offsets)

    def dump(self) -> dict[str, Any]:
        """Serialise everything needed to resume after a restart."""
        return {
            "version": STATE_VERSION,
            "fingerprint": self.fingerprint,
            "value": self._value,
            "input": self._input,
            "last_good": self._last_good,
            "stages": {stage.kind: stage.dump() for stage in self.stages},
            "offsets": None if self._learner is None else self._learner.dump(),
        }

    def restore(self, data: Mapping[str, Any] | None, now: float) -> bool:
        """Resume from :meth:`dump` output. Returns True if a value was restored.

        The value is only restored if it is not older than the hold time; it is
        then shown (status ``restored``) until fresh data arrives or the hold time,
        counted from when the value was last good, runs out. Filter state and
        learned source offsets are only reused when the settings they depend on are
        unchanged. The offsets are kept even when the value is too old to restore.
        With the filter state, the filters keep settling toward the last input while
        the restored value is shown; without it, the value is shown as is.
        """
        if not data or data.get("version") != STATE_VERSION:
            return False
        same_settings = data.get("fingerprint") == self.fingerprint
        if same_settings and self._learner is not None:
            try:
                self._learner.load(data.get("offsets"))
            except (AttributeError, TypeError, ValueError) as err:
                _LOGGER.debug("Discarding saved source offsets: %s", err)
                self._learner.reset()
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

        if same_settings:
            try:
                saved = data["stages"]
                for stage in self.stages:
                    stage.load(saved[stage.kind])
                # Saved before "input" existed: just show the value.
                saved_input = data.get("input")
                self._input = None if saved_input is None else float(saved_input)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Discarding saved filter state: %s", err)
                self._reset_stages()
        return True

    def _reset_stages(self) -> None:
        for stage in self.stages:
            stage.reset()
        self._input = None

    def _round(self, value: float) -> float:
        if self.config.precision is None:
            return value
        return round(value, self.config.precision)
