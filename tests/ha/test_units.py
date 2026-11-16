"""Tests for unit conversion and device/state class selection."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfDensity,
    UnitOfRatio,
)
from homeassistant.core import State

from custom_components.safe_filters.units import (
    convert_value,
    numeric_device_class,
    state_class_for,
    units_compatible,
)


@pytest.mark.parametrize("device_class", ["temperature", None])
def test_converts_temperature(device_class: str | None) -> None:
    assert convert_value(50.0, "°F", "°C", device_class) == pytest.approx(10.0)


def test_ratio_and_gas_concentration_are_not_mixed() -> None:
    humidity = State("sensor.humidity", "55", {ATTR_UNIT_OF_MEASUREMENT: "%"})
    co2 = State(
        "sensor.co2",
        "400",
        {ATTR_UNIT_OF_MEASUREMENT: "ppm", ATTR_DEVICE_CLASS: "carbon_dioxide"},
    )
    assert convert_value(400.0, "ppm", "%", "carbon_dioxide") is None
    assert not units_compatible([humidity, co2])


def test_gas_specific_conversion_needs_device_class() -> None:
    ppb, ug_m3 = UnitOfRatio.PARTS_PER_BILLION, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    assert convert_value(10.0, ppb, ug_m3, None) is None
    assert convert_value(10.0, ppb, ug_m3, "nitrogen_dioxide") is not None


def test_device_class_converter_is_the_only_one_tried() -> None:
    # Power can't be converted to energy, whatever else knows the units.
    assert convert_value(1.0, "W", "kWh", "power") is None


def test_state_class_follows_device_class() -> None:
    assert state_class_for(None) is SensorStateClass.MEASUREMENT
    assert state_class_for(SensorDeviceClass.TEMPERATURE) is SensorStateClass.MEASUREMENT
    assert state_class_for(SensorDeviceClass.ENERGY) is SensorStateClass.TOTAL
    assert state_class_for(SensorDeviceClass.MONETARY) is SensorStateClass.TOTAL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("temperature", SensorDeviceClass.TEMPERATURE),
        ("timestamp", None),
        ("enum", None),
        ("not_a_device_class", None),
        (None, None),
    ],
)
def test_numeric_device_class(value: str | None, expected: SensorDeviceClass | None) -> None:
    assert numeric_device_class(value) == expected
