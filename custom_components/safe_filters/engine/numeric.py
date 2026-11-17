"""Finite arithmetic at persistence and processing boundaries."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def finite(value: Any) -> float:
    """Parse a finite number, rejecting booleans and non-numeric state."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a measurement")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite number")
    return result


def optional_finite(value: Any) -> float | None:
    """Parse an optional persisted number."""
    return None if value is None else finite(value)


def weighted_average(values: Sequence[float], weights: Sequence[float]) -> float:
    """Average finite values without overflowing intermediate sums/products."""
    total = math.fsum(weights)
    terms = [value * (weight / total) for value, weight in zip(values, weights, strict=True)]
    try:
        result = math.fsum(terms)
    except OverflowError:
        pass
    else:
        # Preserve small residuals when very large opposite values cancel. Only
        # rescale if all products underflowed (or the sum overflowed above).
        if result != 0 or any(term != 0 for term in terms):
            return finite(result)
    scale = max(abs(value) for value, weight in zip(values, weights, strict=True) if weight > 0)
    if scale == 0:
        return 0.0
    normalized = math.fsum(
        (value / scale) * (weight / total)
        for value, weight in zip(values, weights, strict=True)
        if weight > 0
    )
    # Rounding must not push an average outside the finite input range.
    return finite(min(1.0, max(-1.0, normalized)) * scale)


def average(values: Sequence[float]) -> float:
    """Overflow-resistant arithmetic mean."""
    return weighted_average(values, [1.0] * len(values))


def median(values: Sequence[float]) -> float:
    """Median with an overflow-resistant midpoint for an even count."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else average(ordered[middle - 1 : middle + 1])


def blend(previous: float, target: float, alpha: float) -> float:
    """Interpolate without subtracting potentially opposite extreme values."""
    return weighted_average([previous, target], [1.0 - alpha, alpha])
