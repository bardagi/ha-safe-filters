"""Tests for config entry migration."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.safe_filters.const import DOMAIN

from .conftest import SOURCES, base_options, set_temp


async def _setup(
    hass: HomeAssistant, *, minor_version: int, stale_after: float, version: int = 1
) -> MockConfigEntry:
    for entity_id in SOURCES:
        set_temp(hass, entity_id, 7.0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Outdoor",
        options=base_options(stale_after=stale_after),
        version=version,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_old_default_stale_after_is_turned_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass, minor_version=1, stale_after=120.0)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.options["stale_after"] == 0.0


@pytest.mark.parametrize(("minor_version", "stale_after"), [(1, 90.0), (1, 0.0), (2, 120.0)])
async def test_chosen_stale_after_is_kept(
    hass: HomeAssistant, minor_version: int, stale_after: float
) -> None:
    entry = await _setup(hass, minor_version=minor_version, stale_after=stale_after)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.options["stale_after"] == stale_after


async def test_newer_major_version_is_not_loaded(hass: HomeAssistant) -> None:
    entry = await _setup(hass, version=2, minor_version=1, stale_after=120.0)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
