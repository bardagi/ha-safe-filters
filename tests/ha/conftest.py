"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.safe_filters.const import DOMAIN

TEMP_ATTRS = {
    ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS,
    ATTR_DEVICE_CLASS: "temperature",
}
SOURCES = ["sensor.temp_a", "sensor.temp_b", "sensor.temp_c"]
ENTITY_ID = "sensor.outdoor"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


def base_options(**overrides: Any) -> dict[str, Any]:
    """Options as the config flow would store them."""
    options: dict[str, Any] = {
        "name": "Outdoor",
        "entity_ids": list(SOURCES),
        "method": "median",
        "stale_after": 0.0,
        "min_sources": 1.0,
        "hold_time": 30.0,
        "outlier": {"enabled": False, "window_size": 5.0, "radius": 2.0},
        "rate_limit": {"enabled": False, "max_rate": 1.0},
        "lowpass": {"enabled": False, "time_constant": 5.0},
        "moving_average": {"enabled": False, "window": 10.0},
        "precision": 1.0,
    }
    options.update(overrides)
    return options


def single_options(entity_id: str = SOURCES[0], **overrides: Any) -> dict[str, Any]:
    """Options for a single-sensor filter, as the config flow would store them."""
    options = base_options(**overrides)
    for key in ("entity_ids", "method", "min_sources"):
        options.pop(key)
    options["entity_id"] = entity_id
    return options


def get_state(hass: HomeAssistant, entity_id: str = ENTITY_ID) -> State:
    """Return an entity's state, which must exist."""
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state


def set_temp(hass: HomeAssistant, entity_id: str, value: Any, **attrs: Any) -> None:
    """Set a source temperature sensor state."""
    hass.states.async_set(entity_id, str(value), {**TEMP_ATTRS, **attrs})


async def setup_entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None, **overrides: Any
) -> MockConfigEntry:
    """Create and set up a Safe Filters config entry.

    Uses ``options`` as given, or else :func:`base_options` with ``overrides``.
    """
    if options is None:
        options = base_options(**overrides)
    entry = MockConfigEntry(domain=DOMAIN, title="Outdoor", options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
