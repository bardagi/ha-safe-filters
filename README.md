<img src="assets/icon.svg" alt="Safe Filters icon" width="128" align="right">

# Safe Filters for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/bardagi/ha-safe-filters/actions/workflows/validate.yml/badge.svg)](https://github.com/bardagi/ha-safe-filters/actions/workflows/validate.yml)
[![Tests](https://github.com/bardagi/ha-safe-filters/actions/workflows/tests.yml/badge.svg)](https://github.com/bardagi/ha-safe-filters/actions/workflows/tests.yml)

A helper that turns **one sensor, or several combined**, into **one trustworthy value**. It ignores readings that are unavailable, stale, out of range or obviously wrong. It smooths the result with time-based filters and survives restarts without garbage readings.

It replaces a YAML `filter` sensor for a single noisy sensor, and chains like `min_max` → `filter` → `statistics` for several, with one helper you set up in the UI.

```
sources ──► validate each ──► enough valid? ──► combine ──► spike     ──► rate  ──► low-pass ──► moving  ──► round
            (unavailable,     (else hold last   (min/max/    rejection     limit                 average
             range, stale,     value, then       mean/median/
             deviation, unit)  unavailable)      weighted/priority)
```

## Why

| Problem with the usual YAML chain | What Safe Filters does |
|---|---|
| `min_max` keeps using a dead sensor's last value forever. | Sources that are `unavailable` are always ignored. Optionally, *Stale after* also ignores a source that has stopped reporting but kept its last value. |
| One glitching sensor (e.g. -40 °C on a low battery) wins `min`. | Range limits, plus *max deviation* from the median of the other sources. |
| `median` of two sensors is just the average, so it rejects nothing. | Validation works per source, whatever the combine method. |
| The `lowpass` filter's time constant counts *samples*, not minutes. | Low-pass and moving average use real elapsed time. |
| `precision` on every filter step rounds intermediate values (stair-steps). | Rounding happens only on the final value. |
| The order of chained filters is easy to get wrong. | Fixed, safe order; every stage is optional. |
| After a restart the chain starts empty and shows wrong values. | The value **and the filter state** are restored. During startup the restored value is shown until Home Assistant has started and the sources are back. |

## Installation

### HACS (custom repository)

Safe Filters is not in the HACS default store. Add it as a custom repository:

1. HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/bardagi/ha-safe-filters`, category **Integration**.
3. Install **Safe Filters** and restart Home Assistant.

### Manual

Copy `custom_components/safe_filters` into your `config/custom_components/` folder and restart.

Requires Home Assistant 2025.8 or newer. The integration icon is shipped with the integration and is shown from Home Assistant 2026.3.

## Setup

**Settings → Devices & services → Helpers → Create helper → Safe Filters**, then choose:

- **Filter one sensor**: validate and smooth a single sensor. A bad reading is ignored and the last good value is held instead.
- **Combine several sensors**: merge several sensors into one value, leaving out the ones that can't be trusted.

Every option except this choice can be changed later from the helper's **Configure** button.

### 1. Sources

| Option | Description |
|---|---|
| Source sensor(s) | `sensor`, `number` or `input_number` entities. When combining, drag to reorder; with *Priority*, the first valid one is used. Units are converted automatically when possible (°F → °C, mbar → hPa, …). |
| Combine method | Combine only: `min`, `max`, `mean`, `median`, `weighted_mean`, `priority` (first valid source). |

With `weighted_mean`, an extra step asks for a weight per source. Weights are renormalised over the sources that are valid at the time.

### 2. Data quality

| Option | Default | Description |
|---|---|---|
| Lowest / highest plausible value | – | Readings outside this range are ignored. |
| Stale after | 0 (off) | A `sensor` that hasn't reported for this long is ignored. Only turn this on for sources that send their value regularly even when it doesn't change. Many integrations, such as ESPHome and MQTT, only send changes, so a steady value would look like a dead device (in ESPHome, `force_update: true` makes a sensor re-send unchanged values). Not applied to `number`/`input_number`. |
| Max deviation | – | Combine only. With ≥ 3 valid sources, ignore a source further than this from their median. |
| Minimum valid sources | 1 | Combine only. Fewer valid sources than this = no new value. |
| Hold last value for | 30 min | When there is no valid reading (or too few valid sources), keep showing the last good value (status `holding`), then become `unavailable`. Also limits how old a value restored after a restart may be. |

### 3. Filters (applied in this order)

| Stage | Settings | Notes |
|---|---|---|
| 1. Spike rejection | window (samples), max distance from median | A sample further than this from the window's median is ignored. A real, lasting change gets through after about half a window. |
| 2. Rate limit | max change per minute | Clamps how fast the value may move. Must be greater than 0. |
| 3. Low-pass | time constant (min) | Exponential smoothing on real time: after one time constant the output has moved ~63 % toward a new level. |
| 4. Moving average | window (min) | Time-weighted average. |
| Decimals | default 1 | Rounding of the final value only. |

Each smoother adds delay. Usually *one* of low-pass or moving average is enough.

## Attributes

| Attribute | Meaning |
|---|---|
| `status` | `ok`, `holding` (no valid reading or too few valid sources, showing the last good value) or `restored` (value from before a restart, waiting for fresh data). |
| `valid_sources` | Number of sources used right now (0 or 1 when filtering one sensor). |
| `combined_value` | Value before filtering: the combined value, or the source's own value when filtering one sensor. |
| `sources` | Per source: converted value and status (`ok`, `unavailable`, `non_numeric`, `unit_mismatch`, `out_of_range`, `stale`, `deviation`). |

`valid_sources`, `combined_value` and `sources` are excluded from the recorder. To avoid a new state (and recorder row) on every source report, the state is written right away only when the value, `status`, availability or a source's status changes. Changes to `combined_value` and the per-source values alone are written at most once a minute.

The sensor has `state_class: measurement` (`total` for energy, gas, water and monetary sources, which Home Assistant doesn't allow as `measurement`), so long-term statistics work. For values like "min/max last 24 h" or "change last hour", add a built-in **Statistics** helper on top of the safe filter.

## Recipes

These are starting points. Adjust them to how often your sensors report.

**Outdoor temperature from several sensors, some of which get sun**
- Method `min` (the shaded sensor wins); range -55 to 45 °C; minimum valid sources 2.
- If the sensors send their value regularly even when it doesn't change: stale after 90 min.
- Spike rejection: window 5, radius 2.5. Low-pass 5 min. Decimals 1.
- Use *max deviation* only if all sensors are placed similarly: in strong sun, a correctly shaded sensor can be the odd one out.

**Outdoor humidity from 3–4 sensors**
- Method `median`; range 0 to 100 %; max deviation 15; minimum valid sources 2.
- Moving average 10 min. Decimals 0.

**Wind speed from one sensor**
- **Filter one sensor**; range 0 to 75 m/s; low-pass 3–5 min.

**A noisy single sensor (e.g. CO₂ or power)**
- **Filter one sensor**; set a plausible range (e.g. 300 to 5000 ppm).
- Spike rejection: window 5, radius sized to a believable jump. Low-pass 2–5 min. Decimals 0.

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
