# cullSLCclones

Removes duplicate `runboth` files from cloned Sentinel-1 SLC directories, then the clone directories that no clone pair needs. For each cloned acquisition, it compares the `setupStrackReg.py --frame` parameters in the clone's `runboth` against the original's; if they match, the clone's `runboth` is deleted (the SLC data is not touched).

A clone directory is then kept only if it has offsets (a processed clone pair), still has a `runboth` (it starts a clone pair), is the second image of any clone pair in the track (from the start's `runboth` or `*.pairinfo`, regardless of the date window; processed or not — every tie rerun's azest reads the partner's `geodat10x2.in`; removing it broke 62 insar10 S1A/S1C fits, 2026-10-01), or is not paired same-sensor by the prime (a start waiting for its partner). Everything else is a copy of a frame the prime already pairs the same way and is removed (`--check` lists them). Only plain clones are removed: symlinks into the prime track and no offsets.

Companion to [cloneSLCdir](cloneSLCdir.md), which creates the clones in the first place.

---

## Usage

```
cullSLCclones [options] track sensor
```

| Argument | Description |
|----------|-------------|
| `track` | Track directory name (must contain `track` and `-`, e.g., `track-90`) |
| `sensor` | Sensor to process: `S1A`, `S1B`, `S1C` or `S1D` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--sourcePath DIR` | `Sentinel1` | Path to the original source directory (relative paths resolved against parent of CWD) |
| `--firstdate YYYY-MM-DD` | `2015-01-01` | Only process acquisitions on or after this date |
| `--lastdate YYYY-MM-DD` | `2050-01-01` | Only process acquisitions on or before this date |
| `--check` | False | Dry run — report duplicates without deleting |
| `--noRemoveRedundant` | False | Cull duplicate `runboth` files only; keep every clone directory (skip the redundant-clone removal). `prepareS1Pairs` passes it. |

---

## Part of the s1setup package.
