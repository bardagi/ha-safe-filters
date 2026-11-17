"""Integration regressions for measurement identity and source dependencies."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.safe_filters.const import DOMAIN
from custom_components.safe_filters.units import (
    CONF_MEASUREMENT,
    convert_value,
    measurement_from_states,
)

from .conftest import ENTITY_ID, SOURCES, get_state, set_temp, setup_entry, single_options
from .test_sensor import _mock_restored

MEASUREMENT = {"unit": "°C", "device_class": "temperature"}


async def test_saved_unit_survives_partial_startup_reordering_and_reload(
    hass: HomeAssistant,
) -> None:
    set_temp(hass, SOURCES[1], 68, unit_of_measurement="°F")
    entry = await setup_entry(
        hass,
        entity_ids=SOURCES[:2],
        measurement=MEASUREMENT,
        min_value=0,
        max_value=40,
    )
    assert get_state(hass).state == "20.0"
    assert get_state(hass).attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    set_temp(hass, SOURCES[0], 20)
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, "entity_ids": list(reversed(SOURCES[:2]))},
    )
    await hass.async_block_till_done()
    assert get_state(hass).state == "20.0"
    assert get_state(hass).attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert entry.options[CONF_MEASUREMENT] == MEASUREMENT


async def test_legacy_entry_waits_for_metadata_and_pins_once(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[1], 68, unit_of_measurement="°F")
    entry = await setup_entry(hass, entity_ids=SOURCES[:2], min_value=0, max_value=40)
    assert get_state(hass).state == STATE_UNAVAILABLE
    assert CONF_MEASUREMENT not in entry.options
    set_temp(hass, SOURCES[0], 20)
    await hass.async_block_till_done()
    assert entry.options[CONF_MEASUREMENT] == MEASUREMENT
    assert get_state(hass).state == "20.0"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_state(hass).state == "20.0"
    assert entry.minor_version == 3


async def test_legacy_restore_unit_wins_over_first_loaded_source(hass: HomeAssistant) -> None:
    _mock_restored(hass, 20, 10)
    set_temp(hass, SOURCES[1], 68, unit_of_measurement="°F")
    entry = await setup_entry(hass, entity_ids=SOURCES[:2], min_value=0, max_value=40)
    assert entry.options[CONF_MEASUREMENT] == MEASUREMENT
    assert get_state(hass).state == "20.0"
    assert get_state(hass).attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"


async def test_unresolved_restore_metadata_is_not_mistaken_for_unitless(
    hass: HomeAssistant,
) -> None:
    entry = await setup_entry(hass, single_options())
    assert CONF_MEASUREMENT not in entry.options
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert CONF_MEASUREMENT not in entry.options
    set_temp(hass, SOURCES[0], 20)
    await hass.async_block_till_done()
    assert entry.options[CONF_MEASUREMENT] == MEASUREMENT
    assert get_state(hass).state == "20.0"


async def test_unitless_identity_is_persisted_and_rejects_later_unit(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCES[0], "10")
    entry = await setup_entry(hass, single_options())
    assert entry.options[CONF_MEASUREMENT] == {"unit": None, "device_class": None}
    set_temp(hass, SOURCES[0], 20)
    await hass.async_block_till_done()
    state = get_state(hass)
    assert state.state == "10.0"
    assert state.attributes["status"] == "holding"
    assert state.attributes["sources"][SOURCES[0]]["status"] == "unit_mismatch"


async def test_incompatible_single_source_edit_is_rejected(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[0], 20)
    hass.states.async_set(
        "sensor.power",
        "100",
        {
            "unit_of_measurement": "W",
            "device_class": "power",
        },
    )
    entry = await setup_entry(hass, single_options())
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"entity_id": "sensor.power"},
    )
    assert result["errors"] == {"base": "incompatible_measurement"}
    assert entry.options["entity_id"] == SOURCES[0]


@pytest.mark.parametrize("combined", [False, True])
async def test_options_reject_direct_self_reference(hass: HomeAssistant, combined: bool) -> None:
    set_temp(hass, SOURCES[0], 20)
    entry = (
        await setup_entry(
            hass,
            **{
                "entity_ids": [SOURCES[0]],
                "measurement": MEASUREMENT,
            },
        )
        if combined
        else await setup_entry(hass, single_options())
    )
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    data = {"entity_ids": [ENTITY_ID], "method": "mean"} if combined else {"entity_id": ENTITY_ID}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], data)
    assert result["errors"] == {"base": "dependency_cycle"}


async def test_valid_chain_loads_but_edit_cannot_close_cycle(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[0], 20)
    first = await setup_entry(hass, single_options())
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        options=single_options(ENTITY_ID, name="Second"),
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert get_state(hass, "sensor.second").state == "20.0"
    flow = await hass.config_entries.options.async_init(first.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"entity_id": "sensor.second"},
    )
    assert result["errors"] == {"base": "dependency_cycle"}


@pytest.mark.parametrize("cycle_length", [1, 2, 3])
async def test_existing_cycles_fail_setup(hass: HomeAssistant, cycle_length: int) -> None:
    registry = er.async_get(hass)
    entries = []
    for index in range(cycle_length):
        source = f"sensor.loop_{(index + 1) % cycle_length}"
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Loop {index}",
            options=single_options(source, measurement=MEASUREMENT),
        )
        entry.add_to_hass(hass)
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            entry.entry_id,
            config_entry=entry,
            suggested_object_id=f"loop_{index}",
        )
        entries.append(entry)
    assert not await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    for entry in entries:
        assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_spikes_hold_then_expire_in_home_assistant(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_temp(hass, SOURCES[0], 0)
    await setup_entry(
        hass,
        single_options(
            outlier={"enabled": True, "window_size": 5, "radius": 5},
            ema={"enabled": True, "readings": 3},
            hold_time=1,
        ),
    )
    for value in [0, 0, 4, 4]:
        set_temp(hass, SOURCES[0], value)
        await hass.async_block_till_done()
    assert get_state(hass).state == "3.0"
    set_temp(hass, SOURCES[0], 100)
    await hass.async_block_till_done()
    assert get_state(hass).state == "3.0"
    assert get_state(hass).attributes["status"] == "holding"
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert get_state(hass).state == STATE_UNAVAILABLE


def test_unit_conversion_cannot_return_nonfinite_values() -> None:
    assert convert_value(float("inf"), "°C", "°C", "temperature") is None
    assert convert_value(1e308, "km", "mm", "distance") is None
    assert convert_value(20, "°C", None, "temperature") is None


def test_missing_metadata_is_not_a_unitless_measurement() -> None:
    assert measurement_from_states([None]) is None
