"""Unit handling: convert source values into the output unit."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor.const import UNIT_CONVERTERS
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import State
from homeassistant.util.unit_conversion import BaseUnitConverter


def _find_converter(
    from_unit: str, to_unit: str, device_class: str | None
) -> type[BaseUnitConverter] | None:
    candidates: list[type[BaseUnitConverter]] = []
    if device_class is not None and (preferred := UNIT_CONVERTERS.get(device_class)):
        candidates.append(preferred)
    candidates.extend(UNIT_CONVERTERS.values())
    for converter in candidates:
        if from_unit in converter.VALID_UNITS and to_unit in converter.VALID_UNITS:
            return converter
    return None


def convert_value(
    value: float, from_unit: str | None, to_unit: str | None, device_class: str | None
) -> float | None:
    """Convert ``value`` to ``to_unit``; ``None`` if the units are incompatible.

    A source without a unit is assumed to already be in the output unit.
    """
    if from_unit == to_unit or from_unit is None or to_unit is None:
        return value
    if (converter := _find_converter(from_unit, to_unit, device_class)) is None:
        return None
    return converter.convert(value, from_unit, to_unit)


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
        if state_unit is not None or state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            known = True
        if unit is None:
            unit = state_unit
        if (device_class := state.attributes.get(ATTR_DEVICE_CLASS)) is not None:
            device_classes.add(device_class)
    return known, unit, _sensor_device_class(device_classes)


def _sensor_device_class(device_classes: set[str]) -> SensorDeviceClass | None:
    if len(device_classes) != 1:
        return None
    try:
        return SensorDeviceClass(next(iter(device_classes)))
    except ValueError:
        return None
