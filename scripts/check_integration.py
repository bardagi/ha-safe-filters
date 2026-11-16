"""Consistency checks that hassfest does not cover.

- Every translation file has exactly the same keys and ``{placeholders}`` as en.json.
- The version in manifest.json matches pyproject.toml.
- The brand icon exists (required by HACS).

Usage: uv run python scripts/check_integration.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tomllib
from collections.abc import Iterator
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "safe_filters"
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _flatten(node: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix, str(node)


def check_translations() -> list[str]:
    """Compare every translation file against en.json."""
    errors: list[str] = []
    translations = INTEGRATION / "translations"
    reference = dict(_flatten(json.loads((translations / "en.json").read_text("utf-8"))))
    for path in sorted(translations.glob("*.json")):
        if path.name == "en.json":
            continue
        other = dict(_flatten(json.loads(path.read_text("utf-8"))))
        missing = sorted(reference.keys() - other.keys())
        unknown = sorted(other.keys() - reference.keys())
        errors += [f"{path.name}: missing key {key}" for key in missing]
        errors += [f"{path.name}: unknown key {key}" for key in unknown]
        for key in sorted(reference.keys() & other.keys()):
            expected = set(PLACEHOLDER_RE.findall(reference[key]))
            found = set(PLACEHOLDER_RE.findall(other[key]))
            if expected != found:
                errors.append(f"{path.name}: {key} has placeholders {found}, expected {expected}")
    return errors


def check_versions() -> list[str]:
    """The integration version must match the project version."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    if manifest["version"] != project["version"]:
        return [
            f"manifest.json version {manifest['version']} != "
            f"pyproject.toml version {project['version']}"
        ]
    return []


def check_brand() -> list[str]:
    """HACS looks for a local brand icon before falling back to home-assistant/brands."""
    return [
        f"missing brand/{name} (run scripts/render_brand.py)"
        for name in ("icon.png", "icon@2x.png")
        if not (INTEGRATION / "brand" / name).is_file()
    ]


def main() -> int:
    """Run all checks and print problems."""
    errors = check_translations() + check_versions() + check_brand()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
