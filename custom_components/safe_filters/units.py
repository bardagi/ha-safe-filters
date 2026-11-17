"""Unit handling: convert source values into the output unit."""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import Any, TypeGuard

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    NON_NUMERIC_DEVICE_CLASSES,
    UNIT_CONVERTERS,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import State
from homeassistant.util.unit_conversion import BaseUnitConverter

CONF_MEASUREMENT = "measurement"
"""Persisted metadata; absence means unresolved, a null unit means unitless."""


def measurement_from_states(states: Iterable[State | None]) -> dict[str, Any] | None:
    """Resolve metadata only after every configured source is known."""
    states = list(states)
    if not states or any(not pick_unit_and_device_class([state])[0] for state in states):
        return None
    _, unit, device_class = pick_unit_and_device_class(states)
    return {"unit": unit, "device_class": device_class}


def valid_measurement(value: Any) -> TypeGuard[Mapping[str, Any]]:
    """Distinguish valid persisted metadata from absent or malformed data."""
    return (
        isinstance(value, Mapping)
        and "unit" in value
        and (value["unit"] is None or isinstance(value["unit"], str))
        and "device_class" in value
        and (
            value["device_class"] is None or numeric_device_class(value["device_class"]) is not None
        )
    )


def measurement_supported(measurement: Mapping[str, Any]) -> bool:
    """Check that Home Assistant accepts the selected numeric class and unit."""
    device_class = measurement["device_class"]
    return device_class is None or measurement["unit"] in DEVICE_CLASS_UNITS.get(
        device_class, {measurement["unit"]}
    )


def matches_measurement(state: State | None, measurement: Mapping[str, Any]) -> bool:
    """Check known source metadata against a helper's fixed measurement."""
    if state is None:
        return True
    source_class = state.attributes.get(ATTR_DEVICE_CLASS)
    target_class = measurement["device_class"]
    if source_class is not None and target_class is not None and source_class != target_class:
        return False
    return (
        convert_value(
            1.0,
            state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
            measurement["unit"],
            source_class or target_class,
        )
        is not None
    )


# Converters whose result depends on *what* is measured (a specific gas, glucose, or
# a dimensionless ratio): ppm → µg/m³ is only right for the gas it was made for, and
# % ↔ ppm would happily turn 400 ppm CO₂ into 0.04 % humidity. They are only used
# when the source's device class selects them, never as a guess.
_QUANTITY_SPECIFIC_UNIT_CLASSES = frozenset(
    {
        "unitless",
        "carbon_monoxide",
        "nitrogen_dioxide",
        "nitrogen_monoxide",
        "ozone",
        "sulphur_dioxide",
        "blood_glucose_concentration",
    }
)


@functools.cache
def _find_converter(
    from_unit: str, to_unit: str, device_class: str | None
) -> type[BaseUnitConverter] | None:
    if device_class is not None and (preferred := UNIT_CONVERTERS.get(device_class)):
        # The device class says what is measured; no other converter applies.
        if from_unit in preferred.VALID_UNITS and to_unit in preferred.VALID_UNITS:
            return preferred
        return None
    for converter in UNIT_CONVERTERS.values():
        if converter.UNIT_CLASS in _QUANTITY_SPECIFIC_UNIT_CLASSES:
            continue
        if from_unit in converter.VALID_UNITS and to_unit in converter.VALID_UNITS:
            return converter
    return None


def convert_value(
    value: float, from_unit: str | None, to_unit: str | None, device_class: str | None
) -> float | None:
    """Convert ``value`` to ``to_unit``; ``None`` if the units are incompatible.

    A source without a unit is assumed to already be in the output unit.
    """
    if not math.isfinite(value) or any(
        item is not None and not isinstance(item, str)
        for item in (from_unit, to_unit, device_class)
    ):
        return None
    if from_unit == to_unit or from_unit is None:
        return value
    if to_unit is None:
        return None
    if (converter := _find_converter(from_unit, to_unit, device_class)) is None:
        return None
    try:
        converted = converter.convert(value, from_unit, to_unit)
    except (ArithmeticError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def units_compatible(states: Iterable[State | None]) -> bool:
    """Return True if every known source unit can be converted to the first one."""
    target: str | None = None
    for state in states:
        if state is None or (unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)) is None:
            continue
        if target is None:
            target = unit
            continue
        if convert_value(1.0, unit, target, state.attributes.get(ATTR_DEVICE_CLASS)) is None:
            return False
    return True


def pick_unit_and_device_class(
    states: Iterable[State | None],
) -> tuple[bool, str | None, SensorDeviceClass | None]:
    """Choose the output unit and device class from the current source states.

    Returns ``(known, unit, device_class)``. ``known`` is False when no source
    has a unit or a real value yet (e.g. during startup), in which case the
    caller should fall back to restored values. The unit is the first source's
    unit; the device class is used only if all sources that have one agree.
    """
    known = False
    unit: str | None = None
    device_classes: set[str] = set()
    for state in states:
        if state is None:
            continue
        state_unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if state_unit is not None:
            known = True
        elif state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            with suppress(ValueError):
                known = known or math.isfinite(float(state.state))
        if unit is None:
            unit = state_unit
        if (device_class := state.attributes.get(ATTR_DEVICE_CLASS)) is not None:
            device_classes.add(device_class)
    if len(device_classes) != 1:
        return known, unit, None
    return known, unit, numeric_device_class(device_classes.pop())


def numeric_device_class(value: Any) -> SensorDeviceClass | None:
    """Return ``value`` as a numeric sensor device class, or None.

    Unknown values and non-numeric classes (timestamp, date, enum) give None:
    the output is always a number.
    """
    if value is None:
        return None
    try:
        device_class = SensorDeviceClass(value)
    except ValueError:
        return None
    return None if device_class in NON_NUMERIC_DEVICE_CLASSES else device_class


def state_class_for(device_class: SensorDeviceClass | None) -> SensorStateClass | None:
    """The state class to use with ``device_class``.

    ``measurement`` where Home Assistant allows it. Otherwise (energy, gas, water,
    monetary, …) ``total``: not ``total_increasing``, because switching sources or
    filtering can make the value go down, which would count as a meter reset.
    """
    allowed = None if device_class is None else DEVICE_CLASS_STATE_CLASSES.get(device_class)
    if allowed is None or SensorStateClass.MEASUREMENT in allowed:
        return SensorStateClass.MEASUREMENT
    if SensorStateClass.TOTAL in allowed:
        return SensorStateClass.TOTAL
    return None
