"""Sources read from an attribute: a bad read leaves the source out, never a guess."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import base_options, get_state, set_temp, setup_entry, single_options

HUMIDITY = {"unit": "%", "device_class": "humidity"}
HUMIDITY_SENSORS = ["sensor.hum_a", "sensor.hum_b"]
WEATHER = "weather.home"
WEATHER_ATTRS: dict[str, Any] = {
    "temperature": 5.0,
    "temperature_unit": "°C",
    "humidity": 80,
    "pressure": 1012.0,
    "pressure_unit": "hPa",
    "wind_bearing": "NW",
}


def set_humidity(hass: HomeAssistant, entity_id: str, value: Any) -> None:
    hass.states.async_set(
        entity_id, str(value), {ATTR_UNIT_OF_MEASUREMENT: "%", ATTR_DEVICE_CLASS: "humidity"}
    )


def set_weather(hass: HomeAssistant, state: str = "cloudy", **attributes: Any) -> None:
    hass.states.async_set(WEATHER, state, {**WEATHER_ATTRS, **attributes})


async def setup_humidity(hass: HomeAssistant, **overrides: Any) -> None:
    """Two humidity sensors (70 and 72 %) and the weather's humidity, averaged."""
    set_humidity(hass, "sensor.hum_a", 70)
    set_humidity(hass, "sensor.hum_b", 72)
    set_weather(hass)
    options = base_options(
        entity_ids=[*HUMIDITY_SENSORS, WEATHER],
        method="mean",
        attributes={WEATHER: "humidity"},
        measurement=HUMIDITY,
    )
    options.update(overrides)
    await setup_entry(hass, options)


def weather_source(hass: HomeAssistant) -> dict[str, Any]:
    return get_state(hass).attributes["sources"][WEATHER]


async def test_attribute_is_combined_with_sensors(hass: HomeAssistant) -> None:
    await setup_humidity(hass)
    state = get_state(hass)
    assert state.state == "74.0"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "%"
    assert state.attributes["valid_sources"] == 3
    assert weather_source(hass) == {"value": 80.0, "status": "ok", "attribute": "humidity"}
    # Sources that read their state are shown as before.
    assert state.attributes["sources"]["sensor.hum_a"] == {"value": 70.0, "status": "ok"}

    set_weather(hass, humidity=86)
    await hass.async_block_till_done()
    assert get_state(hass).state == "76.0"


async def test_unavailable_entity_is_left_out(hass: HomeAssistant) -> None:
    await setup_humidity(hass)
    # Even if an unavailable entity still had the attribute, it isn't used.
    hass.states.async_set(WEATHER, STATE_UNAVAILABLE, {"humidity": 99})
    await hass.async_block_till_done()
    assert get_state(hass).state == "71.0"
    assert weather_source(hass)["status"] == "unavailable"

    set_weather(hass)
    await hass.async_block_till_done()
    assert get_state(hass).state == "74.0"


@pytest.mark.parametrize("humidity", [None, "missing"])
async def test_missing_attribute_is_unavailable(hass: HomeAssistant, humidity: Any) -> None:
    await setup_humidity(hass)
    attributes = {key: value for key, value in WEATHER_ATTRS.items() if key != "humidity"}
    if humidity is None:
        attributes["humidity"] = None
    hass.states.async_set(WEATHER, "cloudy", attributes)
    await hass.async_block_till_done()
    assert get_state(hass).state == "71.0"
    assert weather_source(hass) == {"value": None, "status": "unavailable", "attribute": "humidity"}


@pytest.mark.parametrize("humidity", [True, "NW", "nan", "inf", [80], {"value": 80}])
async def test_non_numeric_attribute_is_left_out(hass: HomeAssistant, humidity: Any) -> None:
    await setup_humidity(hass)
    set_weather(hass, humidity=humidity)
    await hass.async_block_till_done()
    assert get_state(hass).state == "71.0"
    assert weather_source(hass)["status"] == "non_numeric"


async def test_numeric_text_is_read(hass: HomeAssistant) -> None:
    await setup_humidity(hass)
    set_weather(hass, humidity="86.0")
    await hass.async_block_till_done()
    assert get_state(hass).state == "76.0"


async def test_attribute_unit_is_converted(hass: HomeAssistant) -> None:
    for entity_id, value in (("sensor.temp_a", 10.0), ("sensor.temp_b", 12.0)):
        set_temp(hass, entity_id, value)
    set_weather(hass, temperature=50.0, temperature_unit="°F")
    await setup_entry(
        hass,
        base_options(
            entity_ids=["sensor.temp_a", "sensor.temp_b", WEATHER],
            attributes={WEATHER: "temperature"},
            measurement={"unit": "°C", "device_class": "temperature"},
        ),
    )
    assert weather_source(hass) == {"value": 10.0, "status": "ok", "attribute": "temperature"}
    assert get_state(hass).state == "10.0"

    # Without its unit attribute, the temperature's unit is unknown: never guessed.
    set_weather(hass, temperature=50.0, temperature_unit=None)
    await hass.async_block_till_done()
    assert weather_source(hass)["status"] == "unit_mismatch"
    assert get_state(hass).state == "11.0"


async def test_wrong_quantity_is_a_unit_mismatch(hass: HomeAssistant) -> None:
    """A temperature attribute can't feed a humidity helper."""
    await setup_humidity(hass, attributes={WEATHER: "temperature"})
    assert weather_source(hass)["status"] == "unit_mismatch"
    assert get_state(hass).state == "71.0"


async def test_entity_unit_is_not_applied_to_the_attribute(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "sensor.probe",
        "70",
        {ATTR_UNIT_OF_MEASUREMENT: "°F", ATTR_DEVICE_CLASS: "temperature", "celsius": 21.5},
    )
    await setup_entry(
        hass,
        single_options(
            "sensor.probe",
            attributes={"sensor.probe": "celsius"},
            measurement={"unit": "°C", "device_class": "temperature"},
        ),
    )
    # 21.5 as is, not converted as if it were °F.
    assert get_state(hass).state == "21.5"


@pytest.mark.parametrize("attribute", [5, "", None])
async def test_malformed_attribute_never_reads_the_state(
    hass: HomeAssistant, attribute: Any
) -> None:
    """The sensor's state is a different measurement: it must not be used instead."""
    await setup_humidity(hass, attributes={"sensor.hum_a": attribute})
    state = get_state(hass)
    assert state.attributes["sources"]["sensor.hum_a"]["status"] == "unavailable"
    # The weather source now reads its (non-numeric) state.
    assert weather_source(hass)["status"] == "non_numeric"
    assert state.state == "72.0"


async def test_malformed_attribute_mapping_reads_nothing(hass: HomeAssistant) -> None:
    """If it can't tell which sources read an attribute, no source is trusted."""
    await setup_humidity(hass, attributes=["humidity"])
    assert get_state(hass).state == STATE_UNAVAILABLE


async def test_stale_attribute_source(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A weather service that stays available but stops updating is caught by stale after."""
    await setup_humidity(hass, stale_after=60.0)
    assert weather_source(hass)["status"] == "ok"

    freezer.tick(timedelta(minutes=61))
    set_humidity(hass, "sensor.hum_a", 70)
    set_humidity(hass, "sensor.hum_b", 72)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert weather_source(hass)["status"] == "stale"
    assert get_state(hass).state == "71.0"

    # Reporting the same values again makes it fresh.
    set_weather(hass)
    await hass.async_block_till_done()
    assert weather_source(hass)["status"] == "ok"
    assert get_state(hass).state == "74.0"


async def test_only_the_attribute_counts_as_a_new_reading(hass: HomeAssistant) -> None:
    """For the EMA over readings, other changes of the entity are not new readings."""
    set_weather(hass)
    await setup_entry(
        hass,
        single_options(
            WEATHER,
            attributes={WEATHER: "humidity"},
            measurement=HUMIDITY,
            ema={"enabled": True, "readings": 3.0},
        ),
    )
    assert get_state(hass).state == "80.0"

    set_weather(hass, humidity=90)
    await hass.async_block_till_done()
    assert get_state(hass).state == "85.0"

    # The condition and another attribute change: not a new humidity reading.
    set_weather(hass, "rainy", humidity=90)
    await hass.async_block_till_done()
    set_weather(hass, "rainy", humidity=90, temperature=4.0)
    await hass.async_block_till_done()
    assert get_state(hass).state == "85.0"

    # The entity reported the same values again: humidity was measured again.
    set_weather(hass, "rainy", humidity=90, temperature=4.0)
    await hass.async_block_till_done()
    assert get_state(hass).state == "87.5"


async def test_measurement_is_inferred_from_the_attribute(hass: HomeAssistant) -> None:
    """An entry without a saved measurement pins it from the attribute, not the state."""
    set_weather(hass)
    options = single_options(WEATHER, attributes={WEATHER: "humidity"})
    await setup_entry(hass, options)
    state = get_state(hass)
    assert state.state == "80.0"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "%"
    assert state.attributes[ATTR_DEVICE_CLASS] == "humidity"
