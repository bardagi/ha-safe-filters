<img src="assets/icon.svg" alt="Safe Filters icon" width="128" align="right">

# Safe Filters for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/bardagi/ha-safe-filters/actions/workflows/validate.yml/badge.svg)](https://github.com/bardagi/ha-safe-filters/actions/workflows/validate.yml)
[![Tests](https://github.com/bardagi/ha-safe-filters/actions/workflows/tests.yml/badge.svg)](https://github.com/bardagi/ha-safe-filters/actions/workflows/tests.yml)

A helper that combines several sensors into **one trustworthy value**. It ignores sources that are unavailable, stale, out of range or obviously wrong. It smooths the result with time-based filters and survives restarts without garbage readings.

It replaces chains like `min_max` → `filter` → `statistics` with a single helper you set up in the UI.

```
sources ──► validate each ──► enough valid? ──► combine ──► spike     ──► rate  ──► low-pass ──► moving  ──► round
            (unavailable,     (else hold last   (min/max/    rejection     limit                 average
             range, stale,     value, then       mean/median/
             deviation, unit)  unavailable)      weighted/priority)
```

## Why

| Problem with the usual YAML chain | What Safe Filters does |
|---|---|
| `min_max` keeps using a dead sensor's last value forever. | Sources that haven't reported within *Stale after* are ignored. Staleness is based on `last_reported`, so a sensor reporting the same value isn't treated as dead. |
| One glitching sensor (e.g. -40 °C on a low battery) wins `min`. | Range limits, plus *max deviation* from the median of the other sources. |
| `median` of two sensors is just the average, so it rejects nothing. | Validation works per source, whatever the combine method. |
| The `lowpass` filter's time constant counts *samples*, not minutes. | Low-pass and moving average use real elapsed time. |
| `precision` on every filter step rounds intermediate values (stair-steps). | Rounding happens only on the final value. |
| The order of chained filters is easy to get wrong. | Fixed, safe order; every stage is optional. |
| After a restart the chain starts empty and shows wrong values. | The value **and the filter state** are restored. During startup the restored value is shown until Home Assistant has started and the sources are back. |

## Installation

### HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bardagi&repository=ha-safe-filters&category=integration)

Or manually: HACS → ⋮ → **Custom repositories** → add `https://github.com/bardagi/ha-safe-filters`, category **Integration**. Then install **Safe Filters** and restart Home Assistant.

### Manual

Copy `custom_components/safe_filters` into your `config/custom_components/` folder and restart.

Requires Home Assistant 2025.8 or newer. The integration icon is shipped with the integration and is shown from Home Assistant 2026.3.

## Setup

**Settings → Devices & services → Helpers → Create helper → Safe Filters.**
Every option can be changed later from the helper's **Configure** button.

### 1. Sources

| Option | Description |
|---|---|
| Source sensors | `sensor`, `number` or `input_number` entities. Drag to reorder; with *Priority*, the first valid one is used. Units are converted automatically when possible (°F → °C, mbar → hPa, …). |
| Combine method | `min`, `max`, `mean`, `median`, `weighted_mean`, `priority` (first valid source). |

With `weighted_mean`, an extra step asks for a weight per source. Weights are renormalised over the sources that are valid at the time.

### 2. Data quality

| Option | Default | Description |
|---|---|---|
| Lowest / highest plausible value | – | Readings outside this range are ignored. |
| Stale after | 120 min | A `sensor` that hasn't reported for this long is ignored. `0` = off. Not applied to `number`/`input_number`. |
| Max deviation | – | With ≥ 3 valid sources, ignore a source further than this from their median. |
| Minimum valid sources | 1 | Fewer valid sources than this = no new value. |
| Hold last value for | 30 min | When there are too few valid sources, keep showing the last good value (status `holding`), then become `unavailable`. Also limits how old a value restored after a restart may be. |

### 3. Filters (applied in this order)

| Stage | Settings | Notes |
|---|---|---|
| 1. Spike rejection | window (samples), max distance from median | A sample further than this from the window's median is ignored. A real, lasting change gets through after about half a window. |
| 2. Rate limit | max change per minute | Clamps how fast the value may move. |
| 3. Low-pass | time constant (min) | Exponential smoothing on real time: after one time constant the output has moved ~63 % toward a new level. |
| 4. Moving average | window (min) | Time-weighted average. |
| Decimals | default 1 | Rounding of the final value only. |

Each smoother adds delay. Usually *one* of low-pass or moving average is enough.

## Attributes

| Attribute | Meaning |
|---|---|
| `status` | `ok`, `holding` (too few valid sources, showing the last good value) or `restored` (value from before a restart, waiting for fresh data). |
| `valid_sources` | Number of sources used right now. |
| `combined_value` | Combined value before filtering. |
| `sources` | Per source: converted value and status (`ok`, `unavailable`, `non_numeric`, `unit_mismatch`, `out_of_range`, `stale`, `deviation`). |

`valid_sources`, `combined_value` and `sources` are excluded from the recorder to keep the database small. The sensor has `state_class: measurement`, so long-term statistics work. For values like "min/max last 24 h" or "change last hour", add a built-in **Statistics** helper on top of the safe filter.

## Recipes

These are starting points. Adjust them to how often your sensors report.

**Outdoor temperature from several sensors, some of which get sun**
- Method `min` (the shaded sensor wins); range -55 to 45 °C; stale after 90 min; minimum valid sources 2.
- Spike rejection: window 5, radius 2.5. Low-pass 5 min. Decimals 1.
- Use *max deviation* only if all sensors are placed similarly: in strong sun, a correctly shaded sensor can be the odd one out.

**Outdoor humidity from 3–4 sensors**
- Method `median`; range 0 to 100 %; max deviation 15; minimum valid sources 2.
- Moving average 10 min. Decimals 0.

**Wind speed from one sensor**
- Method `priority`; range 0 to 75 m/s; low-pass 3–5 min.

**Air pressure**
- Method `median` (or `mean` with two sensors); range 930 to 1070 hPa.
- Spike rejection: window 5, radius 2 hPa. Moving average 15 min. Optional rate limit 0.5 hPa/min.

## Development

```bash
uv sync                      # Home Assistant, test harness, ruff, ty, prek
uv run prek install          # run the checks below on every commit
uv run prek run --all-files  # ruff, ty, uv lock, translations/version/brand checks, file hygiene
uv run pytest                # all tests; on native Windows only the engine tests
```

The Home Assistant test harness doesn't run on native Windows. Use WSL or CI for the integration tests in `tests/ha/`.

The processing logic lives in `custom_components/safe_filters/engine/` and has no Home Assistant imports. `sensor.py` and `config_flow.py` are thin glue around it. The code must stay compatible with Python 3.13 (Home Assistant 2025.8), which is why ruff and ty target 3.13.

**Icon:** `assets/icon.svg` is the source. After editing it, run `uv run python scripts/render_brand.py` (the pre-commit hook does this) to regenerate `custom_components/safe_filters/brand/icon.png` and `icon@2x.png`.

**Releasing:** bump `version` in both `manifest.json` and `pyproject.toml` (a hook checks they match), commit, then `git tag v0.2.0 && git push --tags`. The release workflow checks the tag against the manifest and publishes a GitHub release, which HACS offers as an update.

## License

MIT
