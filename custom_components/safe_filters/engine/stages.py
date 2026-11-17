"""Filter stages applied to the combined value, in a fixed order.

Every stage is fed either a *new sample* (a source reported) or a *tick* (time
passed without a report). Time-based stages advance on both; sample-based stages
only on new samples. All times are POSIX timestamps in seconds, so stage state
can be persisted and stays meaningful across a Home Assistant restart.
"""

from __future__ import annotations

import itertools
import math
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping
from typing import Any, ClassVar

from .numeric import blend, finite, median, optional_finite, weighted_average


class Stage(ABC):
    """A single filter stage."""

    kind: ClassVar[str]

    @abstractmethod
    def process(self, value: float, now: float, new_sample: bool) -> float:
        """Feed a value and return the stage output."""

    @abstractmethod
    def reset(self) -> None:
        """Forget all history."""

    @abstractmethod
    def dump(self) -> dict[str, Any]:
        """Return JSON-serialisable state."""

    @abstractmethod
    def load(self, data: Mapping[str, Any]) -> None:
        """Restore state from :meth:`dump`. May raise on malformed data."""


class OutlierStage(Stage):
    """Reject spikes: a sample further than ``radius`` from the window median is ignored.

    Rejected samples are still added to the window, so a genuine step change is
    accepted once it makes up about half of the window.
    """

    kind = "outlier"
    MIN_SAMPLES = 3

    def __init__(self, window_size: int, radius: float) -> None:
        self.window_size = max(1, int(window_size))
        self.radius = float(radius)
        self._window: deque[float] = deque(maxlen=self.window_size)
        self._output: float | None = None
        self.accepted = True

    def process(self, value: float, now: float, new_sample: bool) -> float:
        if not new_sample:
            return value if self._output is None else self._output
        min_samples = min(self.MIN_SAMPLES, self.window_size)
        is_outlier = (
            len(self._window) >= min_samples
            and abs(value - median(list(self._window))) > self.radius
        )
        if not is_outlier or self._output is None:
            self._output = value
            self.accepted = True
        else:
            self.accepted = False
        self._window.append(value)
        return self._output

    def reset(self) -> None:
        self._window.clear()
        self._output = None
        self.accepted = True

    def dump(self) -> dict[str, Any]:
        return {"window": list(self._window), "output": self._output, "accepted": self.accepted}

    def load(self, data: Mapping[str, Any]) -> None:
        if not isinstance(data["window"], list):
            raise ValueError("invalid outlier window")
        window = [finite(v) for v in data["window"]]
        output = optional_finite(data["output"])
        accepted = data.get("accepted", True)
        if not isinstance(accepted, bool) or bool(window) != (output is not None):
            raise ValueError("inconsistent outlier state")
        self._window = deque(window, maxlen=self.window_size)
        self._output = output
        self.accepted = accepted


class RateLimitStage(Stage):
    """Limit how fast the value may change, in units per minute of real time."""

    kind = "rate_limit"

    def __init__(self, max_rate_per_min: float) -> None:
        self.max_rate_per_min = abs(float(max_rate_per_min))
        self._output: float | None = None
        self._time: float | None = None

    def process(self, value: float, now: float, new_sample: bool) -> float:
        if self._output is None or self._time is None:
            self._output, self._time = value, now
            return value
        # A clock that went backwards gives dt = 0 for this step only.
        max_step = finite(self.max_rate_per_min * (max(0.0, now - self._time) / 60.0))
        delta = min(max(value - self._output, -max_step), max_step)
        self._output = finite(
            value if abs(value - self._output) <= max_step else self._output + delta
        )
        self._time = now
        return self._output

    def reset(self) -> None:
        self._output = None
        self._time = None

    def dump(self) -> dict[str, Any]:
        return {"output": self._output, "time": self._time}

    def load(self, data: Mapping[str, Any]) -> None:
        output, time = data["output"], data["time"]
        if (output is None) != (time is None):
            raise ValueError("inconsistent rate limit state")
        self._output = optional_finite(output)
        self._time = optional_finite(time)


class LowPassStage(Stage):
    """Time-based exponential moving average (low-pass): ``alpha = 1 - exp(-dt / tau)``.

    Unlike Home Assistant's built-in ``lowpass`` filter (whose time constant is
    counted in *samples*), the smoothing here depends on real elapsed time, so it
    behaves the same whether a sensor reports every 10 seconds or every 5 minutes.

    The input is held at its previous value until a new one arrives (as in the
    moving average), so a new reading only affects the output from that moment on,
    and the output keeps moving toward the last reading for as long as it lasts.
    """

    kind = "lowpass"

    def __init__(self, time_constant_s: float) -> None:
        self.time_constant_s = max(0.0, float(time_constant_s))
        self._output: float | None = None
        self._input: float | None = None
        self._time: float | None = None

    def process(self, value: float, now: float, new_sample: bool) -> float:
        if (
            self._output is None
            or self._input is None
            or self._time is None
            or self.time_constant_s == 0
        ):
            self._output, self._input, self._time = value, value, now
            return value
        # A clock that went backwards gives dt = 0 for this step only.
        dt = max(0.0, now - self._time)
        self._output = blend(self._output, self._input, -math.expm1(-dt / self.time_constant_s))
        self._input = value
        self._time = now
        return self._output

    def reset(self) -> None:
        self._output = None
        self._input = None
        self._time = None

    def dump(self) -> dict[str, Any]:
        return {"output": self._output, "input": self._input, "time": self._time}

    def load(self, data: Mapping[str, Any]) -> None:
        output, time = data["output"], data["time"]
        # State saved before "input" existed: assume the output had settled.
        value_in = data.get("input", output)
        if (output is None) != (time is None) or (output is None) != (value_in is None):
            raise ValueError("inconsistent low-pass state")
        self._output = optional_finite(output)
        self._input = optional_finite(value_in)
        self._time = optional_finite(time)


class EmaStage(Stage):
    """Exponential moving average over readings: ``alpha = 2 / (readings + 1)``.

    Each new sample moves the output ``alpha`` of the way toward it, however much
    time has passed; ticks don't move it. The first sample is taken as is.
    """

    kind = "ema"

    def __init__(self, readings: int) -> None:
        self.readings = max(1, int(readings))
        self.alpha = 2.0 / (self.readings + 1)
        self._output: float | None = None

    def process(self, value: float, now: float, new_sample: bool) -> float:
        if self._output is None:
            self._output = value
        elif new_sample:
            self._output = blend(self._output, value, self.alpha)
        return self._output

    def reset(self) -> None:
        self._output = None

    def dump(self) -> dict[str, Any]:
        return {"output": self._output}

    def load(self, data: Mapping[str, Any]) -> None:
        output = data["output"]
        self._output = optional_finite(output)


class MovingAverageStage(Stage):
    """Time-weighted moving average over a window of real time.

    Each value is held until the next one arrives (step interpolation), so a
    value that lasted 9 minutes weighs nine times more than one that lasted 1.
    While less history than the window exists, the average covers what is there.
    """

    kind = "moving_average"

    def __init__(self, window_s: float) -> None:
        self.window_s = max(0.0, float(window_s))
        self._samples: list[tuple[float, float]] = []

    def process(self, value: float, now: float, new_sample: bool) -> float:
        samples = self._samples
        if samples and now < samples[-1][0]:
            # Clock went backwards; start over rather than produce nonsense.
            samples.clear()
        if not samples or samples[-1][1] != value:
            if samples and samples[-1][0] == now:
                samples[-1] = (now, value)
            else:
                samples.append((now, value))

        window_start = now - self.window_s
        # Keep the newest sample at or before the window start: it defines the
        # value at the start of the window.
        drop = 0
        while drop + 1 < len(samples) and samples[drop + 1][0] <= window_start:
            drop += 1
        if drop:
            del samples[:drop]

        start = max(window_start, samples[0][0])
        span = now - start
        if span <= 0:
            return samples[-1][1]
        values = []
        durations = []
        for index, (time, sample) in enumerate(samples):
            seg_start = max(time, start)
            seg_end = samples[index + 1][0] if index + 1 < len(samples) else now
            if seg_end > seg_start:
                values.append(sample)
                durations.append(seg_end - seg_start)
        return weighted_average(values, durations)

    def reset(self) -> None:
        self._samples.clear()

    def dump(self) -> dict[str, Any]:
        return {"samples": [[time, value] for time, value in self._samples]}

    def load(self, data: Mapping[str, Any]) -> None:
        if not isinstance(data["samples"], list):
            raise ValueError("invalid moving average samples")
        samples = [(finite(time), finite(value)) for time, value in data["samples"]]
        if any(b[0] < a[0] for a, b in itertools.pairwise(samples)):
            raise ValueError("moving average samples are not in time order")
        self._samples = samples
