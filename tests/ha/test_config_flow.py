"""Tests for the Safe Filters config and options flows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from probatio import to_field_list

from custom_components.safe_filters.const import DOMAIN

from .conftest import SOURCES, get_state, set_temp

QUALITY_INPUT = {"stale_after": 60.0, "min_sources": 2.0, "hold_time": 15.0}
FILTERS_INPUT = {
    "outlier": {"enabled": True, "window_size": 5.0, "radius": 2.0},
    "rate_limit": {"enabled": False, "max_rate": 1.0},
    "lowpass": {"enabled": False, "time_constant": 5.0},
    "moving_average": {"enabled": True, "window": 10.0},
    "precision": 1.0,
}


def _assert_serializable(result: Mapping[str, Any]) -> None:
    """The frontend must be able to render the form (same conversion as the HTTP view)."""
    fields = to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    json.dumps(fields)


def _placeholders(result: ConfigFlowResult) -> Mapping[str, str]:
    placeholders = result.get("description_placeholders")
    assert placeholders is not None
    return placeholders


@pytest.fixture(autouse=True)
def sources(hass: HomeAssistant) -> None:
    for entity_id, value in zip(SOURCES, (10.0, 12.0, 11.0), strict=True):
        set_temp(hass, entity_id, value)


async def _start(hass: HomeAssistant, kind: str = "combine") -> ConfigFlowResult:
    """Start a config flow and pick ``kind`` ("single" or "combine") from the menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result.get("menu_options") == ["single", "combine"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": kind}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == kind
    _assert_serializable(result)
    return result


def _schema_keys(result: ConfigFlowResult) -> set[str]:
    schema = result.get("data_schema")
    assert schema is not None
    return {str(key) for key in schema.schema}


async def test_full_flow(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Outdoor", "entity_ids": SOURCES, "method": "median"},
    )
    assert result["step_id"] == "quality"
    assert result["description_placeholders"] == {"unit": "°C"}
    assert {"max_deviation", "min_sources"} <= _schema_keys(result)
    _assert_serializable(result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], QUALITY_INPUT)
    assert result["step_id"] == "filters"
    _assert_serializable(result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Outdoor"
    assert result["data"] == {}
    assert result["options"] == {
        "name": "Outdoor",
        "entity_ids": SOURCES,
        "method": "median",
        **QUALITY_INPUT,
        **FILTERS_INPUT,
    }
    assert get_state(hass).state == "11.0"


async def test_weighted_flow_asks_for_weights(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Outdoor", "entity_ids": SOURCES, "method": "weighted_mean"},
    )
    assert result["step_id"] == "weights"
    assert "sensor.temp_a" in _placeholders(result)["sources"]
    _assert_serializable(result)

    weights = {"sensor.temp_a": 2.0, "sensor.temp_b": 0.0, "sensor.temp_c": 1.0}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"weights": weights})
    assert result["step_id"] == "quality"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], QUALITY_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["weights"] == weights
    # (2*10 + 0*12 + 1*11) / 3
    assert get_state(hass).state == "10.3"


async def test_incompatible_units(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.humidity", "55", {"unit_of_measurement": "%"})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Mixed", "entity_ids": ["sensor.temp_a", "sensor.humidity"], "method": "max"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "incompatible_units"}


@pytest.mark.parametrize(
    ("quality", "error"),
    [
        ({**QUALITY_INPUT, "min_value": 10.0, "max_value": 5.0}, "min_not_below_max"),
        ({**QUALITY_INPUT, "min_sources": 4.0}, "min_sources_too_high"),
    ],
)
async def test_quality_errors(hass: HomeAssistant, quality: dict[str, Any], error: str) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Outdoor", "entity_ids": SOURCES, "method": "mean"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], quality)
    assert result["step_id"] == "quality"
    assert result["errors"] == {"base": error}


async def test_zero_rate_limit_is_rejected(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Outdoor", "entity_ids": SOURCES, "method": "mean"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], QUALITY_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**FILTERS_INPUT, "rate_limit": {"enabled": True, "max_rate": 0.0}}
    )
    assert result["step_id"] == "filters"
    assert result["errors"] == {"base": "max_rate_not_positive"}


async def test_options_flow(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Outdoor", "entity_ids": SOURCES, "method": "median"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**QUALITY_INPUT, "min_value": -50.0}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    entry = result["result"]
    assert get_state(hass).state == "11.0"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "combine"
    _assert_serializable(result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_ids": SOURCES, "method": "max"}
    )
    assert result["step_id"] == "quality"
    # Clearing an optional field removes it from the options.
    result = await hass.config_entries.options.async_configure(result["flow_id"], QUALITY_INPUT)
    assert result["step_id"] == "filters"
    _assert_serializable(result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FILTERS_INPUT, "precision": 0.0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["method"] == "max"
    assert "min_value" not in entry.options
    assert entry.options["name"] == "Outdoor"

    # Entry was reloaded with the new options.
    assert get_state(hass).state == "12.0"


SINGLE_QUALITY_INPUT = {"stale_after": 60.0, "hold_time": 15.0}


async def test_single_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, "single")
    assert _schema_keys(result) == {"name", "entity_id"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Outdoor", "entity_id": "sensor.temp_b"}
    )
    assert result["step_id"] == "quality"
    assert result["description_placeholders"] == {"unit": "°C"}
    # Comparing or counting sources is meaningless with one sensor.
    assert _schema_keys(result).isdisjoint({"max_deviation", "min_sources"})
    _assert_serializable(result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SINGLE_QUALITY_INPUT)
    assert result["step_id"] == "filters"
    _assert_serializable(result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Outdoor"
    assert result["options"] == {
        "name": "Outdoor",
        "entity_id": "sensor.temp_b",
        **SINGLE_QUALITY_INPUT,
        **FILTERS_INPUT,
    }
    state = get_state(hass)
    assert state.state == "12.0"
    assert list(state.attributes["sources"]) == ["sensor.temp_b"]


async def test_single_options_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, "single")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Outdoor", "entity_id": "sensor.temp_a"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SINGLE_QUALITY_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    entry = result["result"]
    assert get_state(hass).state == "10.0"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "single"
    assert _schema_keys(result) == {"entity_id"}
    _assert_serializable(result)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id": "sensor.temp_c"}
    )
    assert result["step_id"] == "quality"
    assert _schema_keys(result).isdisjoint({"max_deviation", "min_sources"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SINGLE_QUALITY_INPUT
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], FILTERS_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["entity_id"] == "sensor.temp_c"
    assert "entity_ids" not in entry.options
    assert entry.options["name"] == "Outdoor"
    assert get_state(hass).state == "11.0"
