"""Combine the values of several valid sources into one number."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from enum import StrEnum


class CombineMethod(StrEnum):
    """How valid source values are combined."""

    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    WEIGHTED_MEAN = "weighted_mean"
    PRIORITY = "priority"


def combine(
    values: Sequence[tuple[str, float]],
    method: CombineMethod,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Combine ``(entity_id, value)`` pairs, given in source (priority) order.

    ``values`` must be non-empty and contain only valid, finite numbers.
    """
    if not values:
        raise ValueError("combine() needs at least one value")

    numbers = [value for _, value in values]

    match method:
        case CombineMethod.MIN:
            return min(numbers)
        case CombineMethod.MAX:
            return max(numbers)
        case CombineMethod.MEAN:
            return statistics.fmean(numbers)
        case CombineMethod.MEDIAN:
            return statistics.median(numbers)
        case CombineMethod.PRIORITY:
            return numbers[0]
        case CombineMethod.WEIGHTED_MEAN:
            return _weighted_mean(values, weights or {})

    raise ValueError(f"Unknown combine method: {method}")


def _weighted_mean(values: Sequence[tuple[str, float]], weights: Mapping[str, float]) -> float:
    """Weighted mean; weights are renormalised over the sources that are valid now."""
    pairs = [(value, max(0.0, float(weights.get(entity_id, 1.0)))) for entity_id, value in values]
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        # Every valid source has weight 0: fall back to a plain mean rather than failing.
        return statistics.fmean(value for value, _ in pairs)
    return sum(value * weight for value, weight in pairs) / total
