"""Tests for the live preview of the source values under the source forms."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    MockHAClientWebSocket,
    WebSocketGenerator,
)

from custom_components.safe_filters.const import DOMAIN

from .conftest import SOURCES, base_options, set_temp, single_options

WEATHER = "weather.home"
ICON = "mdi:filter-variant"


@pytest.fixture(autouse=True)
def sources(hass: HomeAssistant) -> None:
    for entity_id, value, name in zip(SOURCES, (10.0, 12.0, 11.0), "ABC", strict=True):
        set_temp(hass, entity_id, value, friendly_name=f"Temp {name}")
    hass.states.async_set(
        WEATHER,
        "cloudy",
        {
            "friendly_name": "Home",
            "temperature": 5.0,
            "temperature_unit": "°C",
            "humidity": 80,
            "pressure": 1012.0,
            "pressure_unit": "hPa",
        },
    )


async def _config_flow(hass: HomeAssistant, kind: str) -> str:
    """Open the ``kind`` source form of a config flow; return the flow id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": kind}
    )
    assert result["step_id"] == kind
    assert result.get("preview") == DOMAIN
    return result["flow_id"]


async def _options_flow(hass: HomeAssistant, options: dict[str, Any]) -> str:
    """Open the options flow of a new entry; return the flow id."""
    entry = MockConfigEntry(domain=DOMAIN, title="Outdoor", options=options)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result.get("preview") == DOMAIN
    return result["flow_id"]


async def _start_preview(
    client: MockHAClientWebSocket,
    flow_id: str,
    user_input: dict[str, Any],
    flow_type: str = "config_flow",
) -> dict[str, Any]:
    """Subscribe to the preview; return the first preview it sends."""
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/start_preview",
            "flow_id": flow_id,
            "flow_type": flow_type,
            "user_input": user_input,
        }
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    return await _next_preview(client)


async def _next_preview(client: MockHAClientWebSocket) -> dict[str, Any]:
    msg = await client.receive_json()
    assert msg["type"] == "event"
    return msg["event"]


async def test_single_preview_follows_the_source(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    flow_id = await _config_flow(hass, "single")
    client = await hass_ws_client(hass)
    preview = await _start_preview(client, flow_id, {"name": "Outdoor", "entity_id": SOURCES[0]})
    assert preview == {
        "state": "10.0 °C",
        "attributes": {"friendly_name": "Outdoor", "icon": ICON},
    }

    set_temp(hass, SOURCES[0], 10.5)
    assert (await _next_preview(client))["state"] == "10.5 °C"
    hass.states.async_set(SOURCES[0], STATE_UNAVAILABLE)
    assert (await _next_preview(client))["state"] == "unavailable"


async def test_combine_preview_lists_each_source(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    flow_id = await _config_flow(hass, "combine")
    client = await hass_ws_client(hass)
    preview = await _start_preview(
        client, flow_id, {"entity_ids": [SOURCES[1], WEATHER, "sensor.missing"]}
    )
    # The weather entity shows the value that measures the same as the others.
    assert preview["state"] == "Temp B: 12.0 °C · Home: 5.0 °C · sensor.missing: —"
    assert preview["attributes"] == {"friendly_name": "", "icon": ICON}


@pytest.mark.parametrize(
    ("kind", "user_input"),
    [("single", {"name": "Outdoor"}), ("combine", {"entity_ids": []})],
)
async def test_preview_without_sources(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    kind: str,
    user_input: dict[str, Any],
) -> None:
    flow_id = await _config_flow(hass, kind)
    client = await hass_ws_client(hass)
    assert (await _start_preview(client, flow_id, user_input))["state"] == "unknown"


async def test_options_flow_preview(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    flow_id = await _options_flow(hass, base_options())
    client = await hass_ws_client(hass)
    preview = await _start_preview(
        client, flow_id, {"entity_ids": SOURCES[:2], "method": "median"}, "options_flow"
    )
    assert preview == {
        "state": "Temp A: 10.0 °C · Temp B: 12.0 °C",
        "attributes": {"friendly_name": "Outdoor", "icon": ICON},
    }


async def test_options_flow_preview_shows_the_saved_attribute(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    flow_id = await _options_flow(hass, single_options(WEATHER, attributes={WEATHER: "humidity"}))
    client = await hass_ws_client(hass)
    preview = await _start_preview(client, flow_id, {"entity_id": WEATHER}, "options_flow")
    assert preview["state"] == "80 %"
