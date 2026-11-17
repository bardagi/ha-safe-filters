"""Reject cycles between helpers without restricting ordinary filter chains."""

from __future__ import annotations

from collections.abc import Sequence

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .engine import source_ids


def has_dependency_cycle(hass: HomeAssistant, entry_id: str | None, sources: Sequence[str]) -> bool:
    """Check the proposed entry and all reachable Safe Filters dependencies."""
    registry = er.async_get(hass)
    owners = {
        entity.entity_id: entity.config_entry_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN and entity.config_entry_id is not None
    }
    root = entry_id or "__new_safe_filter__"
    graph = {
        entry.entry_id: source_ids(entry.options)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    graph[root] = tuple(sources)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for source in graph.get(node, ()):
            if (owner := owners.get(source)) is not None and visit(owner):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return visit(root)
