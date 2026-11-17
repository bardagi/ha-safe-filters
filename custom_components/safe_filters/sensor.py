"""Safe Filters sensor: the Home Assistant side of the processing pipeline."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
    split_entity_id,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_COMBINED_VALUE,
    ATTR_SOURCES,
    ATTR_STATUS,
    ATTR_VALID_SOURCES,
    TICK_INTERVAL,
)
from .dependencies import has_dependency_cycle
from .engine import (
    Pipeline,
    PipelineResult,
    PipelineStatus,
    SourceReading,
    SourceStatus,
    build_config,
)
from .units import (
    CONF_MEASUREMENT,
    convert_value,
    matches_measurement,
    measurement_from_states,
    numeric_device_class,
    state_class_for,
    valid_measurement,
)

_LOGGER = logging.getLogger(__name__)

# Sources whose value is set by a person, not reported by a device: never "stale".
_NO_STALE_CHECK_DOMAINS = {INPUT_NUMBER_DOMAIN, NUMBER_DOMAIN}
_ATTRIBUTE_DIGITS = 4
_SHARE_DIGITS = 1
_RESTORE_UNIT = "native_unit_of_measurement"
_RESTORE_DEVICE_CLASS = "device_class"
_RESTORE_PIPELINE = "pipeline"
_RESTORE_MEASUREMENT_KNOWN = "measurement_known"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Safe Filters sensor from a config entry."""
    async_add_entities([SafeFilterSensor(entry)])


@dataclass
class SafeFilterExtraStoredData(SensorExtraStoredData):
    """Sensor restore data plus the pipeline (filter) state."""

    device_class: str | None
    pipeline: dict[str, Any] | None
    measurement_known: bool = True

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the stored data."""
        data = super().as_dict()
        data[_RESTORE_DEVICE_CLASS] = self.device_class
        data[_RESTORE_PIPELINE] = self.pipeline
        data[_RESTORE_MEASUREMENT_KNOWN] = self.measurement_known
        return data


class SafeFilterSensor(RestoreSensor):
    """Validates, combines and filters one or more source sensors into one trustworthy value."""

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({ATTR_VALID_SOURCES, ATTR_COMBINED_VALUE, ATTR_SOURCES})

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self._entry = entry
        self._config = build_config(entry.options)
        self._pipeline = Pipeline(self._config)
        self._source_ids = list(self._config.sources)
        self._no_stale_check = {
            entity_id
            for entity_id in self._source_ids
            if split_entity_id(entity_id)[0] in _NO_STALE_CHECK_DOMAINS
        }
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._attr_suggested_display_precision = self._config.precision
        self._attr_available = False
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._started = False
        self._can_write = False
        self._unit_pinned = False
        measurement = entry.options.get(CONF_MEASUREMENT)
        if valid_measurement(measurement):
            self._set_unit(measurement["unit"], numeric_device_class(measurement["device_class"]))
        self._last_primary: tuple[Any, ...] | None = None
        self._last_attributes: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore state and start listening to the sources."""
        await super().async_added_to_hass()

        if self.entity_id in self._source_ids or has_dependency_cycle(
            self.hass, self._entry.entry_id, self._source_ids
        ):
            raise HomeAssistantError("Safe Filters sources contain a dependency cycle")

        await self._async_restore()

        self.async_on_remove(
            async_track_state_change_event(self.hass, self._source_ids, self._async_source_changed)
        )
        self.async_on_remove(
            async_track_state_report_event(self.hass, self._source_ids, self._async_source_reported)
        )
        self.async_on_remove(async_track_time_interval(self.hass, self._async_tick, TICK_INTERVAL))
        # Sources load in arbitrary order during startup. Until Home Assistant has
        # started, keep showing the restored value instead of computing from a
        # partial set of sources. If HA is already running this runs immediately.
        self.async_on_remove(async_at_started(self.hass, self._async_on_started))
        self._can_write = True

    @property
    def extra_restore_state_data(self) -> SafeFilterExtraStoredData:
        """Return data to persist across restarts, including filter state."""
        return SafeFilterExtraStoredData(
            self.native_value,
            self.native_unit_of_measurement,
            self.device_class,
            self._pipeline.dump(),
            self._unit_pinned,
        )

    async def _async_restore(self) -> None:
        last = await self.async_get_last_extra_data()
        raw = last.as_dict() if last is not None else {}
        data = raw if isinstance(raw, Mapping) else {}
        restored_unit = data.get(_RESTORE_UNIT)

        measurement = self._entry.options.get(CONF_MEASUREMENT)
        if not valid_measurement(measurement):
            restored_measurement = {
                "unit": restored_unit,
                "device_class": data.get(_RESTORE_DEVICE_CLASS),
            }
            # A null unit is meaningful only if it was actually saved.
            restored_known = (
                data.get(
                    _RESTORE_MEASUREMENT_KNOWN,
                    restored_unit is not None
                    or data.get(_RESTORE_DEVICE_CLASS) is not None
                    or data.get("native_value") is not None,
                )
                is True
            )
            measurement = (
                restored_measurement
                if restored_known
                and _RESTORE_UNIT in data
                and valid_measurement(restored_measurement)
                else measurement_from_states(self._source_states())
            )
        if measurement is not None and valid_measurement(measurement):
            self._pin_measurement(measurement)
        else:
            self._set_unit(None, None)

        if (
            not self._unit_pinned
            or restored_unit != self._attr_native_unit_of_measurement
            or numeric_device_class(data.get(_RESTORE_DEVICE_CLASS)) != self._attr_device_class
        ):
            return
        now = dt_util.utcnow().timestamp()
        if self._pipeline.restore(data.get(_RESTORE_PIPELINE), now):
            self._apply(self._pipeline.evaluate([], now, new_sample=False), flush_details=True)

    def _pin_measurement(self, measurement: Mapping[str, Any]) -> None:
        self._set_unit(measurement["unit"], numeric_device_class(measurement["device_class"]))
        self._unit_pinned = True
        if self.registry_entry is not None:
            options = dict(self.registry_entry.options.get("sensor.private", {}))
            if options.get("suggested_unit_of_measurement") != measurement["unit"]:
                if measurement["unit"] is None:
                    options.pop("suggested_unit_of_measurement", None)
                else:
                    options["suggested_unit_of_measurement"] = measurement["unit"]
                # Keep any explicit per-entity display preference. The suggestion
                # follows our output unit when the user has not set that override.
                er.async_get(self.hass).async_update_entity_options(
                    self.entity_id, "sensor.private", options
                )
        if self._entry.options.get(CONF_MEASUREMENT) != measurement:
            # This sensor has already applied a newly resolved measurement. Only
            # user edits need a reload; recording this metadata must not loop.
            self._entry.runtime_data = {
                **self._entry.runtime_data,
                CONF_MEASUREMENT: dict(measurement),
            }
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._entry.options, CONF_MEASUREMENT: dict(measurement)}
            )

    def _set_unit(self, unit: str | None, device_class: SensorDeviceClass | None) -> None:
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class_for(device_class)

    def _source_states(self) -> list[State | None]:
        return [self.hass.states.get(entity_id) for entity_id in self._source_ids]

    @callback
    def _async_on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._async_evaluate(new_sample=True, flush_details=True)

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._started:
            return
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        # Attribute-only changes are not new measurements. The same state with the
        # same attributes is a forced update: the source measured the same value again.
        new_sample = (
            new_state is None
            or old_state is None
            or old_state.state != new_state.state
            or old_state.attributes == new_state.attributes
        )
        self._async_evaluate(new_sample=new_sample, reported_by=event.data["entity_id"])

    @callback
    def _async_source_reported(self, event: Event[EventStateReportedData]) -> None:
        """A source reported its unchanged value again: that is a new measurement too."""
        if not self._started:
            return
        self._async_evaluate(new_sample=True, reported_by=event.data["entity_id"])

    @callback
    def _async_tick(self, _now: datetime) -> None:
        if self._started:
            self._async_evaluate(new_sample=False, flush_details=True)
            return
        # Still starting up: don't read the sources yet, but let a restored value
        # expire with the hold time.
        now = dt_util.utcnow().timestamp()
        self._apply(self._pipeline.evaluate([], now, new_sample=False), flush_details=True)

    @callback
    def _async_evaluate(
        self, new_sample: bool, flush_details: bool = False, reported_by: str | None = None
    ) -> None:
        if not self._unit_pinned:
            # Nothing was known at startup; pin unit and device class now.
            measurement = measurement_from_states(self._source_states())
            if measurement is not None:
                self._pin_measurement(measurement)
        now = dt_util.utcnow().timestamp()
        readings = (
            [self._read_source(entity_id) for entity_id in self._source_ids]
            if self._unit_pinned
            else []
        )
        self._apply(self._pipeline.evaluate(readings, now, new_sample, reported_by), flush_details)

    def _read_source(self, entity_id: str) -> SourceReading:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return SourceReading(entity_id, None, status=SourceStatus.UNAVAILABLE)
        try:
            value = float(state.state)
        except ValueError:
            return SourceReading(entity_id, None, status=SourceStatus.NON_NUMERIC)
        if not math.isfinite(value):
            return SourceReading(entity_id, None, status=SourceStatus.NON_NUMERIC)

        if not matches_measurement(
            state,
            {
                "unit": self._attr_native_unit_of_measurement,
                "device_class": self._attr_device_class,
            },
        ):
            return SourceReading(entity_id, None, status=SourceStatus.UNIT_MISMATCH)

        converted = convert_value(
            value,
            state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
            self._attr_native_unit_of_measurement,
            state.attributes.get(ATTR_DEVICE_CLASS) or self._attr_device_class,
        )
        if converted is None:
            return SourceReading(entity_id, None, status=SourceStatus.UNIT_MISMATCH)

        last_reported = (
            None if entity_id in self._no_stale_check else state.last_reported.timestamp()
        )
        return SourceReading(entity_id, converted, last_reported)

    @callback
    def _apply(self, result: PipelineResult, flush_details: bool = False) -> None:
        """Update the entity from ``result`` and write the state if needed.

        Every write is a recorder row and a state_changed event (which also fires
        automations on this entity), so a write happens right away only when
        something that matters changed: the value, availability, unit, status, or
        a source's status. Changes to the diagnostic values alone (``combined_value``
        and per-source values) are written on the next ``flush_details`` call,
        i.e. the periodic tick.
        """
        self._attr_native_value = result.value
        self._attr_available = result.status is not PipelineStatus.NO_DATA
        self._attr_extra_state_attributes = {
            ATTR_STATUS: result.status.value,
            ATTR_VALID_SOURCES: result.valid_count,
            ATTR_COMBINED_VALUE: _rounded(result.combined),
            ATTR_SOURCES: {
                source.entity_id: _source_attributes(source, result) for source in result.sources
            },
        }
        primary = (
            self._attr_available,
            self._attr_native_value,
            self._attr_native_unit_of_measurement,
            self._attr_device_class,
            result.status,
            tuple(source.status for source in result.sources),
        )
        if primary == self._last_primary and (
            not flush_details or self._attr_extra_state_attributes == self._last_attributes
        ):
            return
        self._last_primary = primary
        self._last_attributes = self._attr_extra_state_attributes
        if self._can_write:
            self.async_write_ha_state()


def _source_attributes(source: SourceReading, result: PipelineResult) -> dict[str, Any]:
    """One source's entry in the ``sources`` attribute."""
    attributes: dict[str, Any] = {
        "value": _rounded(source.value),
        "status": source.status.value,
    }
    if (share := result.shares.get(source.entity_id)) is not None:
        # Percent of the combined value this source makes up right now.
        attributes["share"] = round(share * 100, _SHARE_DIGITS)
    if (offset := result.offsets.get(source.entity_id)) is not None:
        attributes["offset"] = _rounded(offset)
    return attributes


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, _ATTRIBUTE_DIGITS)
