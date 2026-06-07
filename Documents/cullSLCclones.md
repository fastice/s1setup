# cullSLCclones

Removes duplicate `runboth` files from cloned Sentinel-1 SLC directories. For each cloned acquisition, it compares the `setupStrackReg.py --frame` parameters in the clone's `runboth` against the original's; if they match, the clone's `runboth` is deleted (the SLC data is not touched).

Companion to [cloneSLCdir](cloneSLCdir.md), which creates the clones in the first place.

---

## Usage

```
cullSLCclones [options] track sensor
```

| Argument | Description |
|----------|-------------|
| `track` | Track directory name (must contain `track` and `-`, e.g., `track-90`) |
| `sensor` | Sensor to process: `S1A`, `S1B`, or `S1C` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--sourcePath DIR` | `Sentinel1` | Path to the original source directory (relative paths resolved against parent of CWD) |
| `--firstdate YYYY-MM-DD` | `2015-01-01` | Only process acquisitions on or after this date |
| `--lastdate YYYY-MM-DD` | `2050-01-01` | Only process acquisitions on or before this date |
| `--check` | False | Dry run — report duplicates without deleting |

---

## Part of the s1setup package.
