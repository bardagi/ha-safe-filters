"""Combine the values of several valid sources into one number."""

from __future__ import annotations

import math
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


def weight_of(weights: Mapping[str, float], entity_id: str) -> float:
    """The configured weight of a source: missing means 1, negative means 0."""
    return max(0.0, float(weights.get(entity_id, 1.0)))


def effective_weights(
    values: Sequence[tuple[str, float]], weights: Mapping[str, float]
) -> dict[str, float]:
    """Each valid source's share of a weighted mean; the shares sum to 1.

    Weights are renormalised over the sources in ``values`` (the valid ones). If
    every one of them has weight 0, they share equally rather than failing.
    """
    raw = {entity_id: weight_of(weights, entity_id) for entity_id, _ in values}
    total = sum(raw.values())
    if total <= 0:
        return {entity_id: 1.0 / len(raw) for entity_id in raw}
    return {entity_id: weight / total for entity_id, weight in raw.items()}


def _weighted_mean(values: Sequence[tuple[str, float]], weights: Mapping[str, float]) -> float:
    """Weighted mean; weights are renormalised over the sources that are valid now."""
    shares = effective_weights(values, weights)
    return math.fsum(value * shares[entity_id] for entity_id, value in values)
