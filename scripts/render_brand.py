"""Render the brand PNGs (used by Home Assistant 2026.3+ and HACS) from assets/icon.svg.

Usage: uv run python scripts/render_brand.py
Only files whose content changes are rewritten.
"""

from __future__ import annotations

import pathlib

import resvg_py

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "icon.svg"
BRAND_DIR = ROOT / "custom_components" / "safe_filters" / "brand"
# Home Assistant brand image sizes: icon 256x256, hDPI icon 512x512. Logos and dark
# variants fall back to the icon, which works on both light and dark backgrounds.
OUTPUTS = {"icon.png": 256, "icon@2x.png": 512}


def main() -> int:
    """Render all outputs; return the number of files changed."""
    BRAND_DIR.mkdir(exist_ok=True)
    changed = 0
    for name, size in OUTPUTS.items():
        data = bytes(resvg_py.svg_to_bytes(svg_path=str(SOURCE), width=size, height=size))
        target = BRAND_DIR / name
        if target.exists() and target.read_bytes() == data:
            continue
        target.write_bytes(data)
        changed += 1
        print(f"updated {target.relative_to(ROOT).as_posix()}")
    return changed


if __name__ == "__main__":
    main()
