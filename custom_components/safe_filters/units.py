"""Unit handling: convert source values into the output unit."""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from homeassistant.components.climate.const import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HUMIDITY,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    NON_NUMERIC_DEVICE_CLASSES,
    UNIT_CONVERTERS,
)
from homeassistant.components.weather.const import (
    ATTR_WEATHER_APPARENT_TEMPERATURE,
    ATTR_WEATHER_CLOUD_COVERAGE,
    ATTR_WEATHER_DEW_POINT,
    ATTR_WEATHER_HUMIDITY,
    ATTR_WEATHER_PRESSURE,
    ATTR_WEATHER_PRESSURE_UNIT,
    ATTR_WEATHER_TEMPERATURE,
    ATTR_WEATHER_TEMPERATURE_UNIT,
    ATTR_WEATHER_VISIBILITY,
    ATTR_WEATHER_VISIBILITY_UNIT,
    ATTR_WEATHER_WIND_GUST_SPEED,
    ATTR_WEATHER_WIND_SPEED,
    ATTR_WEATHER_WIND_SPEED_UNIT,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant, State, split_entity_id
from homeassistant.util.unit_conversion import BaseUnitConverter

CONF_MEASUREMENT = "measurement"
"""Persisted metadata; absence means unresolved, a null unit means unitless."""


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """What is known about the value a source provides: its unit and what it measures."""

    known: bool
    """A unit or a real value is available: False while the source is still loading."""
    unit: Any
    """The unit as the source reports it (only a ``str`` or ``None`` can be converted)."""
    device_class: Any = None
    """What the value measures. Always ``None`` for an attribute: the entity's own
    device class describes its state, not its attributes."""
    class_hint: str | None = None
    """Attribute only: what it is known to measure. Only used to infer the output
    device class when no state source has one, never to check a reading."""
    unit_found: bool = True
    """False when the value has a unit that couldn't be read: it can't be used."""


@dataclass(frozen=True, slots=True)
class _KnownAttribute:
    """Where to find the unit of a well-known attribute, and what it measures."""

    unit: str | None = None
    """A fixed unit."""
    unit_attribute: str | None = None
    """Another attribute of the entity that holds the unit."""
    system_temperature: bool = False
    """In Home Assistant's temperature unit (climate and water heater entities)."""
    class_hint: str | None = None


_TEMPERATURE = SensorDeviceClass.TEMPERATURE.value
_WEATHER_TEMPERATURE = _KnownAttribute(
    unit_attribute=ATTR_WEATHER_TEMPERATURE_UNIT, class_hint=_TEMPERATURE
)
_WEATHER_WIND = _KnownAttribute(
    unit_attribute=ATTR_WEATHER_WIND_SPEED_UNIT, class_hint=SensorDeviceClass.WIND_SPEED.value
)
_SYSTEM_TEMPERATURE = _KnownAttribute(system_temperature=True, class_hint=_TEMPERATURE)
_PERCENT_HUMIDITY = _KnownAttribute(unit=PERCENTAGE, class_hint=SensorDeviceClass.HUMIDITY.value)

KNOWN_ATTRIBUTES: dict[str, dict[str, _KnownAttribute]] = {
    Platform.WEATHER: {
        ATTR_WEATHER_TEMPERATURE: _WEATHER_TEMPERATURE,
        ATTR_WEATHER_HUMIDITY: _PERCENT_HUMIDITY,
        ATTR_WEATHER_PRESSURE: _KnownAttribute(
            unit_attribute=ATTR_WEATHER_PRESSURE_UNIT,
            class_hint=SensorDeviceClass.ATMOSPHERIC_PRESSURE.value,
        ),
        ATTR_WEATHER_WIND_SPEED: _WEATHER_WIND,
        ATTR_WEATHER_WIND_GUST_SPEED: _WEATHER_WIND,
        ATTR_WEATHER_DEW_POINT: _WEATHER_TEMPERATURE,
        ATTR_WEATHER_APPARENT_TEMPERATURE: _WEATHER_TEMPERATURE,
        ATTR_WEATHER_CLOUD_COVERAGE: _KnownAttribute(unit=PERCENTAGE),
        ATTR_WEATHER_VISIBILITY: _KnownAttribute(
            unit_attribute=ATTR_WEATHER_VISIBILITY_UNIT,
            class_hint=SensorDeviceClass.DISTANCE.value,
        ),
    },
    Platform.CLIMATE: {
        ATTR_CURRENT_TEMPERATURE: _SYSTEM_TEMPERATURE,
        ATTR_CURRENT_HUMIDITY: _PERCENT_HUMIDITY,
        ATTR_TEMPERATURE: _SYSTEM_TEMPERATURE,
        ATTR_TARGET_TEMP_LOW: _SYSTEM_TEMPERATURE,
        ATTR_TARGET_TEMP_HIGH: _SYSTEM_TEMPERATURE,
        ATTR_HUMIDITY: _PERCENT_HUMIDITY,
    },
    Platform.WATER_HEATER: {
        ATTR_CURRENT_TEMPERATURE: _SYSTEM_TEMPERATURE,
        ATTR_TEMPERATURE: _SYSTEM_TEMPERATURE,
        ATTR_TARGET_TEMP_LOW: _SYSTEM_TEMPERATURE,
        ATTR_TARGET_TEMP_HIGH: _SYSTEM_TEMPERATURE,
    },
    Platform.HUMIDIFIER: {
        ATTR_CURRENT_HUMIDITY: _PERCENT_HUMIDITY,
        ATTR_HUMIDITY: _PERCENT_HUMIDITY,
    },
}
"""Well-known numeric attributes of entities without a numeric state, in offer order."""


def numeric_value(raw: Any) -> float | None:
    """``raw`` as a finite number, or ``None``.

    Booleans (``True`` is an ``int`` in Python), lists, dicts and text that isn't
    a number are not numbers.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        value = float(raw)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def needs_attribute(entity_id: str) -> bool:
    """Whether a source's state is not a number, so one of its attributes is read."""
    return split_entity_id(entity_id)[0] in KNOWN_ATTRIBUTES


def state_metadata(state: State | None) -> SourceMetadata:
    """Metadata of a source that reads its state."""
    if state is None:
        return SourceMetadata(known=False, unit=None)
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    known = unit is not None or (
        state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        and numeric_value(state.state) is not None
    )
    return SourceMetadata(known, unit, state.attributes.get(ATTR_DEVICE_CLASS))


def attribute_metadata(
    state: State | None, attribute: str | None, temperature_unit: str
) -> SourceMetadata:
    """Metadata of a source that reads ``attribute``.

    The entity's ``unit_of_measurement`` and ``device_class`` describe its state,
    so they are never used here. The unit comes from the known-attribute table;
    other attributes have no unit, i.e. they are read in the output unit.
    ``temperature_unit`` is Home Assistant's, which climate entities report in.
    """
    if state is None or attribute is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        # Not loaded: the attributes of an unavailable entity are not used.
        return SourceMetadata(known=False, unit=None)
    known = KNOWN_ATTRIBUTES.get(state.domain, {}).get(attribute)
    unit: Any = None
    if known is not None:
        if known.unit_attribute is not None:
            unit = state.attributes.get(known.unit_attribute)
            if not isinstance(unit, str) or not unit:
                # The value has a unit, but it can't be read: never guess it.
                return SourceMetadata(
                    known=False, unit=None, class_hint=known.class_hint, unit_found=False
                )
        elif known.system_temperature:
            unit = temperature_unit
        else:
            unit = known.unit
    has_value = numeric_value(state.attributes.get(attribute)) is not None
    return SourceMetadata(
        known=unit is not None or has_value,
        unit=unit,
        class_hint=None if known is None else known.class_hint,
    )


def source_metadata(
    hass: HomeAssistant, entity_id: str, attributes: Mapping[str, str | None]
) -> SourceMetadata:
    """Metadata of one source; ``attributes`` maps the sources that read an attribute."""
    state = hass.states.get(entity_id)
    if entity_id in attributes:
        return attribute_metadata(state, attributes[entity_id], hass.config.units.temperature_unit)
    return state_metadata(state)


def measurement_from_sources(sources: Iterable[SourceMetadata]) -> dict[str, Any] | None:
    """Resolve metadata only after every configured source is known.

    The unit is the first source's unit. The device class is used only if all
    sources that have one agree. If none has one, the attributes' hints are used
    the same way: a filter on a weather entity's humidity measures humidity.
    """
    sources = list(sources)
    if not sources or not all(source.known for source in sources):
        return None
    unit = next((source.unit for source in sources if source.unit is not None), None)
    classes = {source.device_class for source in sources if source.device_class is not None}
    if classes:
        device_class = numeric_device_class(classes.pop()) if len(classes) == 1 else None
        return {"unit": unit, "device_class": device_class}
    hints = {source.class_hint for source in sources if source.class_hint is not None}
    hint = numeric_device_class(hints.pop()) if len(hints) == 1 else None
    if hint is not None and not measurement_supported({"unit": unit, "device_class": hint}):
        hint = None
    return {"unit": unit, "device_class": hint}


def measurement_from_states(states: Iterable[State | None]) -> dict[str, Any] | None:
    """Resolve metadata of sources that all read their state."""
    return measurement_from_sources(state_metadata(state) for state in states)


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


def matches_measurement(source: SourceMetadata, measurement: Mapping[str, Any]) -> bool:
    """Check known source metadata against a helper's fixed measurement."""
    if not source.unit_found:
        return False
    source_class = source.device_class
    target_class = measurement["device_class"]
    if source_class is not None and target_class is not None and source_class != target_class:
        return False
    return (
        convert_value(1.0, source.unit, measurement["unit"], source_class or target_class)
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
