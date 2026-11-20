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
    SourceMetadata,
    attribute_metadata,
    convert_value,
    matches_measurement,
    measurement_from_sources,
    numeric_device_class,
    numeric_value,
    state_class_for,
    state_metadata,
    units_compatible,
)

TEMPERATURE = {"unit": "°C", "device_class": "temperature"}


def _weather(**attributes: object) -> State:
    return State(
        "weather.home",
        "cloudy",
        {
            "temperature": 41.0,
            "temperature_unit": "°F",
            "humidity": 82,
            "pressure": 1012.0,
            "pressure_unit": "hPa",
            "wind_bearing": "NW",
            **attributes,
        },
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, 5.0),
        (2.5, 2.5),
        (" 3.5 ", 3.5),
        (True, None),
        (False, None),
        ("NW", None),
        ("nan", None),
        (float("inf"), None),
        (10**400, None),
        ([1], None),
        ({"value": 1}, None),
        (None, None),
    ],
)
def test_numeric_value(raw: object, expected: float | None) -> None:
    assert numeric_value(raw) == expected


def test_weather_attribute_unit_comes_from_its_unit_attribute() -> None:
    temperature = attribute_metadata(_weather(), "temperature", "°C")
    assert temperature == SourceMetadata(
        known=True, unit="°F", device_class=None, class_hint="temperature"
    )
    assert matches_measurement(temperature, TEMPERATURE)
    assert convert_value(41.0, temperature.unit, "°C", "temperature") == pytest.approx(5.0)

    humidity = attribute_metadata(_weather(), "humidity", "°C")
    assert (humidity.unit, humidity.class_hint) == ("%", "humidity")
    assert not matches_measurement(humidity, TEMPERATURE)


@pytest.mark.parametrize("unit", [None, "", 5])
def test_missing_unit_attribute_is_never_guessed(unit: object) -> None:
    temperature = attribute_metadata(_weather(temperature_unit=unit), "temperature", "°C")
    assert not temperature.known
    assert not temperature.unit_found
    assert not matches_measurement(temperature, TEMPERATURE)
    assert not matches_measurement(temperature, {"unit": None, "device_class": None})


def test_climate_temperature_is_in_the_system_unit() -> None:
    climate = State("climate.x", "heat", {"current_temperature": 70, "current_humidity": 40})
    assert attribute_metadata(climate, "current_temperature", "°F").unit == "°F"
    assert attribute_metadata(climate, "current_humidity", "°F").unit == "%"


def test_entity_unit_and_class_are_not_applied_to_an_attribute() -> None:
    sensor = State(
        "sensor.x",
        "21",
        {ATTR_UNIT_OF_MEASUREMENT: "°F", ATTR_DEVICE_CLASS: "temperature", "celsius": -6.1},
    )
    metadata = attribute_metadata(sensor, "celsius", "°C")
    assert metadata == SourceMetadata(known=True, unit=None)
    # Read as the output unit, not converted from the entity's °F.
    assert matches_measurement(metadata, TEMPERATURE)
    assert convert_value(-6.1, metadata.unit, "°C", "temperature") == -6.1


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_attributes_of_an_unavailable_entity_are_not_loaded(state: str) -> None:
    weather = State("weather.home", state, {"humidity": 80})
    assert not attribute_metadata(weather, "humidity", "°C").known
    assert not attribute_metadata(None, "humidity", "°C").known


def test_class_hint_is_used_only_without_a_state_class() -> None:
    pressure = attribute_metadata(_weather(), "pressure", "°C")
    sensor = state_metadata(
        State("sensor.p", "1010", {ATTR_UNIT_OF_MEASUREMENT: "hPa", ATTR_DEVICE_CLASS: "pressure"})
    )
    assert measurement_from_sources([pressure]) == {
        "unit": "hPa",
        "device_class": "atmospheric_pressure",
    }
    assert measurement_from_sources([pressure, sensor]) == {
        "unit": "hPa",
        "device_class": "pressure",
    }
    # Checked by unit only, so a pressure helper accepts it.
    assert matches_measurement(pressure, {"unit": "hPa", "device_class": "pressure"})
    # A hint that doesn't fit the unit is dropped.
    odd = SourceMetadata(known=True, unit="°C", class_hint="humidity")
    assert measurement_from_sources([odd]) == {"unit": "°C", "device_class": None}
