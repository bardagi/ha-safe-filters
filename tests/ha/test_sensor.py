"""Tests for the Safe Filters sensor entity."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    UnitOfEnergy,
    UnitOfTemperature,
)
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from .conftest import ENTITY_ID, SOURCES, get_state, set_temp, setup_entry


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def _mock_restored(hass: HomeAssistant, value: float, age_s: float) -> None:
    """Pretend the sensor showed ``value`` (°C), last good ``age_s`` seconds ago."""
    now = dt_util.utcnow().timestamp()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ENTITY_ID, str(value)),
                {
                    "native_value": value,
                    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
                    "device_class": "temperature",
                    "pipeline": {
                        "version": 1,
                        "fingerprint": "does-not-match",
                        "value": value,
                        "last_good": now - age_s,
                        "stages": {},
                    },
                },
            )
        ],
    )


async def test_combines_sources(hass: HomeAssistant) -> None:
    for entity_id, value in zip(SOURCES, (10.0, 12.0, 11.0), strict=True):
        set_temp(hass, entity_id, value)
    await setup_entry(hass)

    state = get_state(hass)
    assert state.state == "11.0"
    assert state.attributes["status"] == "ok"
    assert state.attributes["valid_sources"] == 3
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS
    assert state.attributes[ATTR_DEVICE_CLASS] == "temperature"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["sources"]["sensor.temp_a"] == {"value": 10.0, "status": "ok"}


async def test_follows_source_changes_and_skips_unavailable(hass: HomeAssistant) -> None:
    for entity_id, value in zip(SOURCES, (10.0, 12.0, 11.0), strict=True):
        set_temp(hass, entity_id, value)
    await setup_entry(hass, method="min")
    assert get_state(hass).state == "10.0"

    hass.states.async_set("sensor.temp_a", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    state = get_state(hass)
    assert state.state == "11.0"
    assert state.attributes["valid_sources"] == 2
    assert state.attributes["sources"]["sensor.temp_a"]["status"] == "unavailable"

    set_temp(hass, "sensor.temp_b", 9.0)
    await hass.async_block_till_done()
    assert get_state(hass).state == "9.0"


async def test_out_of_range_and_non_numeric(hass: HomeAssistant) -> None:
    set_temp(hass, "sensor.temp_a", -999)
    set_temp(hass, "sensor.temp_b", "garbage")
    set_temp(hass, "sensor.temp_c", 4.2)
    await setup_entry(hass, method="min", min_value=-60.0, max_value=60.0)

    state = get_state(hass)
    assert state.state == "4.2"
    assert state.attributes["sources"]["sensor.temp_a"]["status"] == "out_of_range"
    assert state.attributes["sources"]["sensor.temp_b"]["status"] == "non_numeric"


async def test_stale_source_is_dropped(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    for entity_id, value in zip(SOURCES, (3.0, 5.0, 1.0), strict=True):
        set_temp(hass, entity_id, value)
    await setup_entry(hass, method="min", stale_after=60.0)
    assert get_state(hass).state == "1.0"

    for _ in range(4):
        await _tick(hass, freezer, 20)
        # temp_a and temp_b keep reporting (same value); temp_c went silent.
        set_temp(hass, "sensor.temp_a", 3.0)
        set_temp(hass, "sensor.temp_b", 5.0)
    await _tick(hass, freezer, 1)

    state = get_state(hass)
    assert state.state == "3.0"
    assert state.attributes["sources"]["sensor.temp_c"]["status"] == "stale"


async def test_hold_then_unavailable(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    for entity_id in SOURCES:
        set_temp(hass, entity_id, 7.0)
    await setup_entry(hass, hold_time=10.0)

    for entity_id in SOURCES:
        hass.states.async_set(entity_id, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    state = get_state(hass)
    assert state.state == "7.0"
    assert state.attributes["status"] == "holding"

    await _tick(hass, freezer, 9)
    assert get_state(hass).state == "7.0"

    await _tick(hass, freezer, 2)
    assert get_state(hass).state == STATE_UNAVAILABLE

    set_temp(hass, "sensor.temp_b", 8.0)
    await hass.async_block_till_done()
    state = get_state(hass)
    assert state.state == "8.0"
    assert state.attributes["status"] == "ok"


async def test_unit_conversion(hass: HomeAssistant) -> None:
    set_temp(hass, "sensor.temp_a", 10.0)
    set_temp(hass, "sensor.temp_b", 50.0, unit_of_measurement=UnitOfTemperature.FAHRENHEIT)
    await setup_entry(hass, entity_ids=["sensor.temp_a", "sensor.temp_b"], method="mean")

    state = get_state(hass)
    assert state.state == "10.0"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS


async def test_incompatible_unit_is_rejected(hass: HomeAssistant) -> None:
    set_temp(hass, "sensor.temp_a", 10.0)
    hass.states.async_set("sensor.temp_b", "55", {ATTR_UNIT_OF_MEASUREMENT: "%"})
    await setup_entry(hass, entity_ids=["sensor.temp_a", "sensor.temp_b"], method="max")

    state = get_state(hass)
    assert state.state == "10.0"
    assert state.attributes["sources"]["sensor.temp_b"]["status"] == "unit_mismatch"


async def test_lowpass_smooths_over_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    for entity_id in SOURCES:
        set_temp(hass, entity_id, 0.0)
    await setup_entry(hass, lowpass={"enabled": True, "time_constant": 5.0}, precision=2.0)
    for entity_id in SOURCES:
        set_temp(hass, entity_id, 10.0)
    await hass.async_block_till_done()
    assert float(get_state(hass).state) < 1.0

    await _tick(hass, freezer, 5)
    # One time constant later: ~63% of the way there, even with no new reports.
    assert 5.5 < float(get_state(hass).state) < 7.0


async def test_diagnostic_changes_wait_for_tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A source change that doesn't change the output isn't written right away."""
    for entity_id, value in zip(SOURCES, (10.0, 12.0, 11.0), strict=True):
        set_temp(hass, entity_id, value)
    await setup_entry(hass)
    assert get_state(hass).state == "11.0"

    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_temp(hass, "sensor.temp_b", 12.5)  # not the median
    await hass.async_block_till_done()
    assert [event for event in events if event.data["entity_id"] == ENTITY_ID] == []
    assert get_state(hass).attributes["sources"]["sensor.temp_b"]["value"] == 12.0

    await _tick(hass, freezer, 1)
    state = get_state(hass)
    assert state.state == "11.0"
    assert state.attributes["sources"]["sensor.temp_b"]["value"] == 12.5


async def test_energy_sources_use_total_state_class(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    for entity_id, value in zip(SOURCES, (100.0, 101.0, 102.0), strict=True):
        hass.states.async_set(
            entity_id,
            str(value),
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR, ATTR_DEVICE_CLASS: "energy"},
        )
    await setup_entry(hass, method="max")

    state = get_state(hass)
    assert state.state == "102.0"
    assert state.attributes[ATTR_DEVICE_CLASS] == "energy"
    assert state.attributes["state_class"] == "total"
    assert "impossible" not in caplog.text


async def test_restores_value_during_startup(hass: HomeAssistant) -> None:
    _mock_restored(hass, 9.5, age_s=120)
    hass.set_state(CoreState.not_running)
    # Only one (misleading) source has loaded so far.
    set_temp(hass, "sensor.temp_a", 25.0)
    await setup_entry(hass, method="median")

    state = get_state(hass)
    assert state.state == "9.5"
    assert state.attributes["status"] == "restored"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS

    # Source updates during startup are ignored...
    set_temp(hass, "sensor.temp_a", 26.0)
    await hass.async_block_till_done()
    assert get_state(hass).state == "9.5"

    # ...until Home Assistant has started and the other sources are in.
    set_temp(hass, "sensor.temp_b", 10.0)
    set_temp(hass, "sensor.temp_c", 10.4)
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    state = get_state(hass)
    assert state.state == "10.4"
    assert state.attributes["status"] == "ok"


async def test_old_restored_value_is_not_used(hass: HomeAssistant) -> None:
    _mock_restored(hass, 9.5, age_s=3 * 3600)
    hass.set_state(CoreState.not_running)
    await setup_entry(hass)
    assert get_state(hass).state == STATE_UNAVAILABLE


async def test_restored_value_expires_during_slow_startup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _mock_restored(hass, 9.5, age_s=25 * 60)
    hass.set_state(CoreState.not_running)
    await setup_entry(hass, hold_time=30.0)
    assert get_state(hass).attributes["status"] == "restored"

    # Home Assistant is still starting when the 30 minute hold time runs out.
    await _tick(hass, freezer, 6)
    assert get_state(hass).state == STATE_UNAVAILABLE


async def test_options_change_reloads(hass: HomeAssistant) -> None:
    for entity_id, value in zip(SOURCES, (10.0, 12.0, 11.0), strict=True):
        set_temp(hass, entity_id, value)
    entry = await setup_entry(hass)
    assert get_state(hass).state == "11.0"

    hass.config_entries.async_update_entry(entry, options={**entry.options, "method": "max"})
    await hass.async_block_till_done()
    assert get_state(hass).state == "12.0"


async def test_unload(hass: HomeAssistant) -> None:
    set_temp(hass, "sensor.temp_a", 1.0)
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_state(hass).state == STATE_UNAVAILABLE
