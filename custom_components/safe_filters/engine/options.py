"""Config entry option keys, defaults, and conversion into a PipelineConfig.

Durations are entered in minutes in the UI and converted to seconds here.
This module has no Home Assistant imports so it can be unit tested anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .combine import CombineMethod
from .pipeline import OutlierConfig, PipelineConfig
from .validation import ValidationConfig

CONF_NAME = "name"
CONF_ENTITY_ID = "entity_id"
"""The source of a single-sensor filter. Its presence marks the entry as single."""
CONF_ENTITY_IDS = "entity_ids"
"""The sources of a filter that combines several sensors."""
CONF_METHOD = "method"
CONF_WEIGHTS = "weights"
CONF_MIN_WEIGHT = "min_weight"
"""Weighted mean: % of the total weight that must be valid."""
CONF_OFFSET_LEARNING = "offset_learning"
"""Weighted mean: minutes over which source offsets are learned; 0 = off."""

CONF_MIN_VALUE = "min_value"
CONF_MAX_VALUE = "max_value"
CONF_STALE_AFTER = "stale_after"
CONF_MAX_DEVIATION = "max_deviation"
CONF_MIN_SOURCES = "min_sources"
CONF_HOLD_TIME = "hold_time"

CONF_ENABLED = "enabled"
CONF_OUTLIER = "outlier"
CONF_WINDOW_SIZE = "window_size"
CONF_RADIUS = "radius"
CONF_RATE_LIMIT = "rate_limit"
CONF_MAX_RATE = "max_rate"
CONF_LOWPASS = "lowpass"
CONF_TIME_CONSTANT = "time_constant"
CONF_MOVING_AVERAGE = "moving_average"
CONF_WINDOW = "window"
CONF_PRECISION = "precision"

DEFAULT_METHOD = CombineMethod.MEDIAN
DEFAULT_WEIGHT = 1.0
DEFAULT_MIN_WEIGHT = 0.0  # percent; off
DEFAULT_OFFSET_LEARNING = 0.0  # minutes; off
DEFAULT_STALE_AFTER = 0.0  # minutes; off, because many sources only report changes
DEFAULT_MIN_SOURCES = 1
DEFAULT_HOLD_TIME = 30.0  # minutes
DEFAULT_OUTLIER_WINDOW = 5
DEFAULT_OUTLIER_RADIUS = 2.0
DEFAULT_MAX_RATE = 1.0  # units per minute
DEFAULT_TIME_CONSTANT = 5.0  # minutes
DEFAULT_MA_WINDOW = 10.0  # minutes
DEFAULT_PRECISION = 1

SECONDS_PER_MINUTE = 60.0
PERCENT = 100.0


def is_single(options: Mapping[str, Any]) -> bool:
    """Return True if the options describe a filter for one sensor."""
    return CONF_ENTITY_ID in options


def source_ids(options: Mapping[str, Any]) -> tuple[str, ...]:
    """The source entity ids, in priority order, for either kind of entry."""
    if is_single(options):
        return (options[CONF_ENTITY_ID],)
    return tuple(options.get(CONF_ENTITY_IDS, ()))


def build_config(options: Mapping[str, Any]) -> PipelineConfig:
    """Turn config entry options into a :class:`PipelineConfig`."""
    sources = source_ids(options)
    method = (
        CombineMethod.PRIORITY
        if is_single(options)
        else CombineMethod(options.get(CONF_METHOD, DEFAULT_METHOD))
    )

    weights: dict[str, float] = {}
    min_weight_share = 0.0
    offset_time_constant_s: float | None = None
    if method is CombineMethod.WEIGHTED_MEAN:
        saved = options.get(CONF_WEIGHTS) or {}
        weights = {entity_id: float(saved.get(entity_id, DEFAULT_WEIGHT)) for entity_id in sources}
        min_weight_pct = float(options.get(CONF_MIN_WEIGHT, DEFAULT_MIN_WEIGHT) or 0)
        min_weight_share = min(max(min_weight_pct, 0.0), PERCENT) / PERCENT
        learning_min = float(options.get(CONF_OFFSET_LEARNING, DEFAULT_OFFSET_LEARNING) or 0)
        if learning_min > 0:
            offset_time_constant_s = learning_min * SECONDS_PER_MINUTE

    stale_after_min = float(options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER) or 0)
    validation = ValidationConfig(
        min_value=_optional_float(options.get(CONF_MIN_VALUE)),
        max_value=_optional_float(options.get(CONF_MAX_VALUE)),
        stale_after_s=max(0.0, stale_after_min) * SECONDS_PER_MINUTE,
        max_deviation=_optional_float(options.get(CONF_MAX_DEVIATION)),
    )

    outlier_opts = _enabled_section(options, CONF_OUTLIER)
    rate_opts = _enabled_section(options, CONF_RATE_LIMIT)
    lowpass_opts = _enabled_section(options, CONF_LOWPASS)
    ma_opts = _enabled_section(options, CONF_MOVING_AVERAGE)

    max_rate = None if rate_opts is None else float(rate_opts.get(CONF_MAX_RATE, DEFAULT_MAX_RATE))
    if max_rate is not None and max_rate <= 0:
        # A limit of 0 would freeze the value forever; the config flow rejects it now,
        # but older entries may still have it saved.
        max_rate = None

    precision = options.get(CONF_PRECISION, DEFAULT_PRECISION)

    return PipelineConfig(
        sources=sources,
        method=method,
        weights=weights,
        validation=validation,
        min_sources=max(1, int(options.get(CONF_MIN_SOURCES, DEFAULT_MIN_SOURCES))),
        min_weight_share=min_weight_share,
        offset_time_constant_s=offset_time_constant_s,
        hold_time_s=max(0.0, float(options.get(CONF_HOLD_TIME, DEFAULT_HOLD_TIME)))
        * SECONDS_PER_MINUTE,
        outlier=None
        if outlier_opts is None
        else OutlierConfig(
            window_size=int(outlier_opts.get(CONF_WINDOW_SIZE, DEFAULT_OUTLIER_WINDOW)),
            radius=float(outlier_opts.get(CONF_RADIUS, DEFAULT_OUTLIER_RADIUS)),
        ),
        max_rate_per_min=max_rate,
        lowpass_time_constant_s=None
        if lowpass_opts is None
        else float(lowpass_opts.get(CONF_TIME_CONSTANT, DEFAULT_TIME_CONSTANT))
        * SECONDS_PER_MINUTE,
        moving_average_window_s=None
        if ma_opts is None
        else float(ma_opts.get(CONF_WINDOW, DEFAULT_MA_WINDOW)) * SECONDS_PER_MINUTE,
        precision=None if precision is None else int(precision),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _enabled_section(options: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    section = options.get(key)
    if isinstance(section, Mapping) and section.get(CONF_ENABLED):
        return section
    return None
