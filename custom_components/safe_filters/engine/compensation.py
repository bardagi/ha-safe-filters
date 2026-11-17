"""Bridge a source dropout without a jump: learn how far each source usually is off."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .combine import effective_weights, weight_of


class OffsetLearner:
    """Weighted mean that stays continuous when sources drop out or come back.

    Renormalising the weights over the valid sources removes a dead source's value,
    but not its influence: if it read 1 degree above the others, the result drops
    when it goes away. So while every source with a weight above 0 is valid, this
    learns each source's *offset* from their full weighted mean, smoothed over real
    time. When some are missing, each remaining source's value minus its offset is
    an estimate of the full mean, and those estimates are averaged with the
    renormalised weights.

    Until the offsets have been learned once, it is a plain weighted mean.
    """

    def __init__(
        self, sources: Sequence[str], weights: Mapping[str, float], time_constant_s: float
    ) -> None:
        self.weights = weights
        self.time_constant_s = max(0.0, float(time_constant_s))
        self._weighted = frozenset(s for s in sources if weight_of(weights, s) > 0)
        self._offsets: dict[str, float] | None = None
        self._time: float | None = None

    @property
    def offsets(self) -> Mapping[str, float]:
        """The learned offset of each source; empty until learned."""
        return {} if self._offsets is None else dict(self._offsets)

    def combine(self, values: Sequence[tuple[str, float]], now: float) -> float:
        """Combine the valid ``(entity_id, value)`` pairs, learning when all are valid."""
        shares = effective_weights(values, self.weights)
        mean = math.fsum(value * shares[entity_id] for entity_id, value in values)
        dt = self._advance(now)

        if self._weighted and self._weighted <= {entity_id for entity_id, _ in values}:
            self._learn(values, mean, dt)
            return mean

        offsets = self._offsets
        if offsets is None or any(
            entity_id not in offsets for entity_id, _ in values if shares[entity_id] > 0
        ):
            return mean
        return math.fsum(
            (value - offsets.get(entity_id, 0.0)) * shares[entity_id] for entity_id, value in values
        )

    def advance(self, now: float) -> None:
        """Let time pass without combining (too few valid sources).

        Keeps the learning rate normal when the sources come back, instead of
        treating the whole gap as learning time.
        """
        self._advance(now)

    def _advance(self, now: float) -> float | None:
        dt = None if self._time is None else max(0.0, now - self._time)
        self._time = now
        return dt

    def _learn(self, values: Sequence[tuple[str, float]], mean: float, dt: float | None) -> None:
        targets = {entity_id: value - mean for entity_id, value in values}
        if self._offsets is None:
            self._offsets = targets
            return
        if dt is None:
            # First call after a start or restore: no elapsed time to learn over.
            alpha = 0.0
        elif self.time_constant_s == 0:
            alpha = 1.0
        else:
            alpha = 1.0 - math.exp(-dt / self.time_constant_s)
        for entity_id, target in targets.items():
            old = self._offsets.get(entity_id)
            self._offsets[entity_id] = target if old is None else old + alpha * (target - old)

    def reset(self) -> None:
        """Forget the learned offsets."""
        self._offsets = None
        self._time = None

    def dump(self) -> dict[str, float] | None:
        """Return the learned offsets (JSON-serialisable)."""
        return None if self._offsets is None else dict(self._offsets)

    def load(self, data: Any) -> None:
        """Restore offsets from :meth:`dump`. May raise on malformed data.

        The time is not restored, so time spent stopped doesn't count as learning.
        """
        self._offsets = (
            None if data is None else {str(key): float(value) for key, value in data.items()}
        )
        self._time = None
