"""Safe Filters: filter one sensor, or combine several, into one trustworthy value."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_STALE_AFTER
from .dependencies import has_dependency_cycle
from .engine import source_ids

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# The "stale after" default of entries with config entry version 1.1. Sources that
# only report changes (ESPHome, MQTT) were marked stale whenever their value held
# steady for that long.
_OLD_DEFAULT_STALE_AFTER = 120.0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Safe Filters helper from a config entry."""
    if has_dependency_cycle(hass, entry.entry_id, source_ids(entry.options)):
        raise ConfigEntryError("Safe Filters sources contain a dependency cycle")
    entry.runtime_data = dict(entry.options)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry created by an older version."""
    if entry.version > 1:
        # Created by a newer version; can't be read by this one.
        return False

    if entry.minor_version < 2:
        options = dict(entry.options)
        if options.get(CONF_STALE_AFTER) == _OLD_DEFAULT_STALE_AFTER:
            # Still the old default: turn the check off like the new default.
            options[CONF_STALE_AFTER] = 0.0
            _LOGGER.info(
                "%s: 'Stale after' was the old default of 120 min and is now off",
                entry.title,
            )
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)

    if entry.minor_version < 3:
        # Measurement metadata is resolved by the sensor, where restore data is
        # available. Never guess a legacy unit from a partially loaded source set.
        hass.config_entries.async_update_entry(entry, minor_version=3)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    if entry.runtime_data == dict(entry.options):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
