"""Advanced output measurement settings and safe changes to existing helpers."""

from copy import deepcopy
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData, section
from homeassistant.helpers import entity_registry as er

from .conftest import ENTITY_ID, SOURCES, get_state, set_temp, setup_entry, single_options
from .test_config_flow import FILTERS_INPUT, SINGLE_QUALITY_INPUT, _assert_serializable, _start


async def test_manual_output_measurement_at_creation(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[0], 20)
    flow = await _start(hass, "single")
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Outdoor",
            "entity_id": SOURCES[0],
            "advanced": {"unit": "°F", "device_class": "temperature"},
        },
    )
    assert flow["description_placeholders"] == {"unit": "°F"}
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], SINGLE_QUALITY_INPUT)
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert flow["options"]["measurement"] == {"unit": "°F", "device_class": "temperature"}
    assert "advanced" not in flow["options"]
    assert get_state(hass).attributes["unit_of_measurement"] == "°F"
    assert float(get_state(hass).state) == pytest.approx(68)


async def test_manual_measurement_allows_sources_that_have_not_loaded(hass: HomeAssistant) -> None:
    flow = await _start(hass, "single")
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Outdoor",
            "entity_id": "sensor.not_loaded",
            "advanced": {"unit": "°C", "device_class": "temperature"},
        },
    )
    assert flow["step_id"] == "quality"
    assert flow["description_placeholders"] == {"unit": "°C"}


async def test_automatic_class_does_not_lock_unknown_sources_to_no_class(
    hass: HomeAssistant,
) -> None:
    flow = await _start(hass, "single")
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Outdoor",
            "entity_id": "sensor.not_loaded",
            "advanced": {"unit": "°C", "device_class": "auto"},
        },
    )
    assert flow["errors"] == {"base": "source_metadata_unknown"}


@pytest.mark.parametrize(
    ("advanced", "error"),
    [
        ({"unit": "W", "device_class": "temperature"}, "invalid_measurement_unit"),
        ({"unit": "°C", "device_class": "timestamp"}, "invalid_device_class"),
        ({"unit": "W", "device_class": "power"}, "incompatible_units"),
    ],
)
async def test_invalid_manual_measurement_is_rejected(
    hass: HomeAssistant,
    advanced: dict[str, str],
    error: str,
) -> None:
    set_temp(hass, SOURCES[0], 20)
    flow = await _start(hass, "single")
    if error == "invalid_device_class":
        # The dropdown rejects non-numeric classes before custom validation runs.
        with pytest.raises(InvalidData) as caught:
            await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {"name": "Outdoor", "entity_id": SOURCES[0], "advanced": advanced},
            )
        assert caught.value.path == ["advanced", "device_class"]
        return
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Outdoor",
            "entity_id": SOURCES[0],
            "advanced": advanced,
        },
    )
    assert flow["errors"] == {"base": error}
    _assert_serializable(flow)


def suggested(schema: Any, key: str) -> Any:
    marker, field = next(
        (marker, field) for marker, field in schema.schema.items() if marker == key
    )
    if isinstance(field, section):
        return {str(child): suggested(field.schema, str(child)) for child in field.schema.schema}
    if marker.description and "suggested_value" in marker.description:
        return marker.description["suggested_value"]
    return marker.default()


async def test_unit_edit_converts_thresholds_and_reloads_without_old_history(
    hass: HomeAssistant,
) -> None:
    set_temp(hass, SOURCES[0], 20)
    filters: dict[str, Any] = deepcopy(FILTERS_INPUT)
    filters["rate_limit"] = {"enabled": True, "max_rate": 1}
    filters["lowpass"] = {"enabled": True, "time_constant": 5}
    entry = await setup_entry(hass, single_options(min_value=0, max_value=40, **filters))
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["description_placeholders"] == {"unit": "°C", "device_class": "temperature"}
    _assert_serializable(flow)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "entity_id": SOURCES[0],
            "advanced": {"unit": "°F", "device_class": "temperature"},
        },
    )
    assert flow["description_placeholders"] == {"unit": "°F"}
    assert suggested(flow["data_schema"], "min_value") == pytest.approx(32)
    assert suggested(flow["data_schema"], "max_value") == pytest.approx(104)
    # The pending flow must not mutate the running entry before final submission.
    assert entry.options["measurement"]["unit"] == "°C"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            **SINGLE_QUALITY_INPUT,
            "min_value": 32,
            "max_value": 104,
        },
    )
    outlier = suggested(flow["data_schema"], "outlier")
    rate = suggested(flow["data_schema"], "rate_limit")
    assert outlier["radius"] == pytest.approx(3.6)
    assert rate["max_rate"] == pytest.approx(1.8)
    filters["outlier"] = outlier
    filters["rate_limit"] = rate
    await hass.config_entries.options.async_configure(flow["flow_id"], filters)
    await hass.async_block_till_done()
    assert entry.options["measurement"]["unit"] == "°F"
    assert get_state(hass).attributes["unit_of_measurement"] == "°F"
    assert float(get_state(hass).state) == pytest.approx(68)


async def test_measurement_type_change_clears_incompatible_thresholds(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[0], 20)
    hass.states.async_set(
        "sensor.power",
        "100",
        {
            "unit_of_measurement": "W",
            "device_class": "power",
        },
    )
    entry = await setup_entry(
        hass,
        single_options(
            min_value=0,
            max_value=40,
            max_deviation=3,
            outlier={"enabled": True, "radius": 2, "window_size": 5},
            rate_limit={"enabled": True, "max_rate": 1},
        ),
    )
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "entity_id": "sensor.power",
            "advanced": {"unit": "W", "device_class": "power"},
        },
    )
    assert flow["step_id"] == "quality"
    assert flow["description_placeholders"] == {"unit": "W"}
    schema = flow["data_schema"]
    assert schema is not None
    for marker in schema.schema:
        if marker in ("min_value", "max_value"):
            assert not marker.description or "suggested_value" not in marker.description
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], SINGLE_QUALITY_INPUT)
    assert suggested(flow["data_schema"], "outlier")["enabled"] is False
    assert suggested(flow["data_schema"], "rate_limit")["enabled"] is False
    filters = deepcopy(FILTERS_INPUT)
    filters["outlier"] = suggested(flow["data_schema"], "outlier")
    filters["rate_limit"] = suggested(flow["data_schema"], "rate_limit")
    await hass.config_entries.options.async_configure(flow["flow_id"], filters)
    await hass.async_block_till_done()
    assert not {"min_value", "max_value", "max_deviation"} & entry.options.keys()
    assert get_state(hass).attributes["unit_of_measurement"] == "W"
    assert float(get_state(hass).state) == 100


async def test_measurement_only_edit_reloads_and_keeps_explicit_display_override(
    hass: HomeAssistant,
) -> None:
    set_temp(hass, SOURCES[0], 20)
    entry = await setup_entry(hass, single_options())
    registry = er.async_get(hass)
    registry.async_update_entity_options(ENTITY_ID, "sensor", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    # All ordinary settings stay identical: only the measurement metadata changes.
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "measurement": {"unit": "K", "device_class": "temperature"},
        },
    )
    await hass.async_block_till_done()
    assert float(get_state(hass).state) == pytest.approx(68, abs=0.1)
    assert get_state(hass).attributes["unit_of_measurement"] == "°F"
    registered = registry.async_get(ENTITY_ID)
    assert registered is not None
    assert registered.options["sensor.private"]["suggested_unit_of_measurement"] == "K"


async def test_none_device_class_can_be_selected(hass: HomeAssistant) -> None:
    set_temp(hass, SOURCES[0], 20)
    entry = await setup_entry(hass, single_options())
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "entity_id": SOURCES[0],
            "advanced": {"unit": "°C", "device_class": "none"},
        },
    )
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], SINGLE_QUALITY_INPUT)
    await hass.config_entries.options.async_configure(flow["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert entry.options["measurement"] == {"unit": "°C", "device_class": None}
    assert "device_class" not in get_state(hass).attributes
