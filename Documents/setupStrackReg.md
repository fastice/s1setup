# setupStrackReg

Runs the Sentinel-1 speckle-tracking pipeline for an image pair. In registration mode (default) it performs coarse registration via cross-correlation (`coarsereg`), builds a Gamma offset file, runs simulated offsets (`simoffsets.py`), then runs `strack` at multiple sub-SLC scale factors to co-register the pair. In offset-tracking mode (`--strackOffsets`) it runs the full-resolution speckle tracker and culls the result. Usually called from `setuppairs.py` rather than directly.

Run from within the pair directory (e.g. `orbit1_frame/`).

---

## Usage

```
setupStrackReg.py orbit1 orbit2 sensor [options]
```

| Argument | Description |
|----------|-------------|
| `orbit1` | First orbit number |
| `orbit2` | Second orbit number |
| `sensor` | Sensor name: `S1`, `CSK`, or `TSX` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--frame N` | 1 | Frame number |
| `--region NAME` | auto | Region (`greenland`, `antarctica`, `taku`); auto-detected from geodat if omitted |
| `--info` | False | Print processing flags for this run, then exit |
| `--cwOffsets` | False | Run coarse cross-correlation offsets (and other flagged steps) |
| `--simOffsets` | False | Run simulated offsets (and other flagged steps) |
| `--strackReg` | False | Run `strack` for registration (and other flagged steps) |
| `--strackOffsets` | False | Run full speckle tracking (disables all registration steps) |
| `--setupOnly` | False | Set up `strack` input files but do not execute the tracker |
| `--cullOnly` | False | Only cull high-resolution offsets (use with `--strackOffsets`) |

Default (no flags): runs `cwOffsets` + `simOffsets` + `strackReg` (full registration pipeline).

---

## What it does

**Registration mode** (default):
1. `coarseReg` — coarse cross-correlation to get initial range/azimuth shifts
2. `callCreateOffsets` / `callRunCWOffsets` / `callOffsetFit` — Gamma CW offset estimation
3. `simOffsets` — simulate offsets for full and registration grids
4. `runStrackRegister` — iterative `strack` at sub-SLC scales to fine-register the pair

**Offset-tracking mode** (`--strackOffsets`):
1. `runStrack` — full-resolution speckle tracking using the registered baseline
2. `runCullHiRes` — cull offsets and create `cleanoff` script

---

## Part of the s1setup package.
