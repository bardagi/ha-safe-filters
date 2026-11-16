"""Shared pytest configuration."""

import importlib.util

# The Home Assistant test harness is not installable on native Windows; run only the
# pure engine tests there. Linux, WSL and CI run everything.
collect_ignore = [] if importlib.util.find_spec("pytest_homeassistant_custom_component") else ["ha"]
