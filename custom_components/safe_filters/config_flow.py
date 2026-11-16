"""Config and options flow for Safe Filters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
    SchemaFlowMenuStep,
)

from .const import (
    CONF_ENABLED,
    CONF_ENTITY_ID,
    CONF_ENTITY_IDS,
    CONF_HOLD_TIME,
    CONF_LOWPASS,
    CONF_MAX_DEVIATION,
    CONF_MAX_RATE,
    CONF_MAX_VALUE,
    CONF_METHOD,
    CONF_MIN_SOURCES,
    CONF_MIN_VALUE,
    CONF_MOVING_AVERAGE,
    CONF_NAME,
    CONF_OUTLIER,
    CONF_PRECISION,
    CONF_RADIUS,
    CONF_RATE_LIMIT,
    CONF_STALE_AFTER,
    CONF_TIME_CONSTANT,
    CONF_WEIGHTS,
    CONF_WINDOW,
    CONF_WINDOW_SIZE,
    DEFAULT_HOLD_TIME,
    DEFAULT_MA_WINDOW,
    DEFAULT_MAX_RATE,
    DEFAULT_METHOD,
    DEFAULT_MIN_SOURCES,
    DEFAULT_OUTLIER_RADIUS,
    DEFAULT_OUTLIER_WINDOW,
    DEFAULT_PRECISION,
    DEFAULT_STALE_AFTER,
    DEFAULT_TIME_CONSTANT,
    DEFAULT_WEIGHT,
    DOMAIN,
)
from .engine import CombineMethod, is_single, source_ids
from .units import units_compatible

if TYPE_CHECKING:
    import probatio as vol
else:
    try:  # Home Assistant 2026+ validates with probatio (voluptuous-compatible API)
        import probatio as vol
    except ImportError:  # older Home Assistant
        import voluptuous as vol

UNIT_MINUTES = "min"
SOURCE_DOMAINS = [SENSOR_DOMAIN, NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN]

STEP_SINGLE = "single"
STEP_COMBINE = "combine"


def _number(
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    step: float | Literal["any"] = "any",
    unit: str | None = None,
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX, step=step)
    if min_value is not None:
        config["min"] = min_value
    if max_value is not None:
        config["max"] = max_value
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


NAME_SCHEMA = vol.Schema({vol.Required(CONF_NAME): selector.TextSelector()})

SINGLE_SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=SOURCE_DOMAINS)
        ),
    }
)

SINGLE_USER_SCHEMA = NAME_SCHEMA.extend(SINGLE_SOURCE_SCHEMA.schema)

COMBINE_SOURCES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_IDS): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=SOURCE_DOMAINS, multiple=True, reorder=True)
        ),
        vol.Required(CONF_METHOD, default=DEFAULT_METHOD.value): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[method.value for method in CombineMethod],
                translation_key=CONF_METHOD,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

COMBINE_USER_SCHEMA = NAME_SCHEMA.extend(COMBINE_SOURCES_SCHEMA.schema)


def _build_quality_schema(*, multiple_sources: bool) -> vol.Schema:
    fields: dict[vol.Marker, Any] = {
        vol.Optional(CONF_MIN_VALUE): _number(),
        vol.Optional(CONF_MAX_VALUE): _number(),
        vol.Required(CONF_STALE_AFTER, default=DEFAULT_STALE_AFTER): _number(
            min_value=0, max_value=10080, step=1, unit=UNIT_MINUTES
        ),
    }
    if multiple_sources:
        # Comparing and counting sources only makes sense with more than one.
        fields[vol.Optional(CONF_MAX_DEVIATION)] = _number(min_value=0)
        fields[vol.Required(CONF_MIN_SOURCES, default=DEFAULT_MIN_SOURCES)] = _number(
            min_value=1, max_value=50, step=1
        )
    fields[vol.Required(CONF_HOLD_TIME, default=DEFAULT_HOLD_TIME)] = _number(
        min_value=0, max_value=10080, step=1, unit=UNIT_MINUTES
    )
    return vol.Schema(fields)


COMBINE_QUALITY_SCHEMA = _build_quality_schema(multiple_sources=True)
SINGLE_QUALITY_SCHEMA = _build_quality_schema(multiple_sources=False)


async def _quality_schema(handler: SchemaCommonFlowHandler) -> vol.Schema:
    return SINGLE_QUALITY_SCHEMA if is_single(handler.options) else COMBINE_QUALITY_SCHEMA


def _filter_section(options: Mapping[str, Any], key: str, fields: dict[vol.Marker, Any]) -> section:
    """A collapsible filter section that starts expanded when the filter is enabled."""
    enabled = bool((options.get(key) or {}).get(CONF_ENABLED, False))
    return section(
        vol.Schema(
            {vol.Required(CONF_ENABLED, default=False): selector.BooleanSelector(), **fields}
        ),
        {"collapsed": not enabled},
    )


async def _filters_schema(handler: SchemaCommonFlowHandler) -> vol.Schema:
    options = handler.options
    return vol.Schema(
        {
            vol.Required(CONF_OUTLIER): _filter_section(
                options,
                CONF_OUTLIER,
                {
                    vol.Required(CONF_WINDOW_SIZE, default=DEFAULT_OUTLIER_WINDOW): _number(
                        min_value=1, max_value=99, step=1
                    ),
                    vol.Required(CONF_RADIUS, default=DEFAULT_OUTLIER_RADIUS): _number(min_value=0),
                },
            ),
            vol.Required(CONF_RATE_LIMIT): _filter_section(
                options,
                CONF_RATE_LIMIT,
                {
                    vol.Required(CONF_MAX_RATE, default=DEFAULT_MAX_RATE): _number(min_value=0),
                },
            ),
            vol.Required(CONF_LOWPASS): _filter_section(
                options,
                CONF_LOWPASS,
                {
                    vol.Required(CONF_TIME_CONSTANT, default=DEFAULT_TIME_CONSTANT): _number(
                        min_value=0, max_value=1440, unit=UNIT_MINUTES
                    ),
                },
            ),
            vol.Required(CONF_MOVING_AVERAGE): _filter_section(
                options,
                CONF_MOVING_AVERAGE,
                {
                    vol.Required(CONF_WINDOW, default=DEFAULT_MA_WINDOW): _number(
                        min_value=0, max_value=1440, unit=UNIT_MINUTES
                    ),
                },
            ),
            vol.Required(CONF_PRECISION, default=DEFAULT_PRECISION): _number(
                min_value=0, max_value=6, step=1
            ),
        }
    )


async def _weights_schema(handler: SchemaCommonFlowHandler) -> vol.Schema:
    entity_ids: list[str] = handler.options.get(CONF_ENTITY_IDS, [])
    return vol.Schema(
        {
            vol.Required(CONF_WEIGHTS): section(
                vol.Schema(
                    {
                        vol.Required(entity_id, default=DEFAULT_WEIGHT): _number(
                            min_value=0, max_value=1000
                        )
                        for entity_id in entity_ids
                    }
                ),
                {"collapsed": False},
            )
        }
    )


async def _weights_placeholders(handler: SchemaCommonFlowHandler) -> dict[str, str]:
    hass = handler.parent_handler.hass
    lines = []
    for entity_id in handler.options.get(CONF_ENTITY_IDS, []):
        state = hass.states.get(entity_id)
        name = state.attributes.get(ATTR_FRIENDLY_NAME, entity_id) if state else entity_id
        lines.append(f"- `{entity_id}`: {name}")
    return {"sources": "\n".join(lines)}


async def _unit_placeholders(handler: SchemaCommonFlowHandler) -> dict[str, str]:
    hass = handler.parent_handler.hass
    for entity_id in source_ids(handler.options):
        if (state := hass.states.get(entity_id)) and (
            unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        ):
            return {"unit": unit}
    return {"unit": "-"}


async def _validate_sources(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    entity_ids: list[str] = user_input.get(CONF_ENTITY_IDS) or []
    if not entity_ids:
        raise SchemaFlowError("no_sources")
    if len(set(entity_ids)) != len(entity_ids):
        raise SchemaFlowError("duplicate_sources")
    hass = handler.parent_handler.hass
    if not units_compatible(hass.states.get(entity_id) for entity_id in entity_ids):
        raise SchemaFlowError("incompatible_units")
    return user_input


async def _validate_quality(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    min_value = user_input.get(CONF_MIN_VALUE)
    max_value = user_input.get(CONF_MAX_VALUE)
    if min_value is not None and max_value is not None and min_value >= max_value:
        raise SchemaFlowError("min_not_below_max")
    source_count = len(source_ids(handler.options))
    if user_input.get(CONF_MIN_SOURCES, DEFAULT_MIN_SOURCES) > source_count:
        raise SchemaFlowError("min_sources_too_high")
    return user_input


async def _validate_filters(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    rate_limit = user_input.get(CONF_RATE_LIMIT) or {}
    if rate_limit.get(CONF_ENABLED) and rate_limit.get(CONF_MAX_RATE, DEFAULT_MAX_RATE) <= 0:
        # A limit of 0 would freeze the value forever.
        raise SchemaFlowError("max_rate_not_positive")
    return user_input


async def _next_after_sources(options: dict[str, Any]) -> str:
    if options.get(CONF_METHOD) == CombineMethod.WEIGHTED_MEAN:
        return "weights"
    return "quality"


async def _choose_source_step(options: dict[str, Any]) -> str:
    """Open the options flow on the source form that matches the entry's kind."""
    return STEP_SINGLE if is_single(options) else STEP_COMBINE


WEIGHTS_STEP = SchemaFlowFormStep(
    _weights_schema, next_step="quality", description_placeholders=_weights_placeholders
)
QUALITY_STEP = SchemaFlowFormStep(
    _quality_schema,
    validate_user_input=_validate_quality,
    next_step="filters",
    description_placeholders=_unit_placeholders,
)
FILTERS_STEP = SchemaFlowFormStep(
    _filters_schema,
    validate_user_input=_validate_filters,
    description_placeholders=_unit_placeholders,
)

CONFIG_FLOW = {
    "user": SchemaFlowMenuStep([STEP_SINGLE, STEP_COMBINE]),
    STEP_SINGLE: SchemaFlowFormStep(SINGLE_USER_SCHEMA, next_step="quality"),
    STEP_COMBINE: SchemaFlowFormStep(
        COMBINE_USER_SCHEMA, validate_user_input=_validate_sources, next_step=_next_after_sources
    ),
    "weights": WEIGHTS_STEP,
    "quality": QUALITY_STEP,
    "filters": FILTERS_STEP,
}

OPTIONS_FLOW = {
    # No form of its own: goes straight to the single or combine source form.
    "init": SchemaFlowFormStep(next_step=_choose_source_step),
    STEP_SINGLE: SchemaFlowFormStep(SINGLE_SOURCE_SCHEMA, next_step="quality"),
    STEP_COMBINE: SchemaFlowFormStep(
        COMBINE_SOURCES_SCHEMA,
        validate_user_input=_validate_sources,
        next_step=_next_after_sources,
    ),
    "weights": WEIGHTS_STEP,
    "quality": QUALITY_STEP,
    "filters": FILTERS_STEP,
}


class SafeFiltersConfigFlow(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle a config or options flow for Safe Filters."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast(str, options[CONF_NAME])
