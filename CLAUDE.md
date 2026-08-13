# CLAUDE.md — s1setup

Sentinel-1 TOPS SLC preprocessing: unpacks SAFE archives via the **Gamma SAR processor**, concatenates/trims frames to track boundaries, computes calibration coefficients, then prepares image pairs for `strack`/`strackw`/`cullst` in GIT64 `speckleSource/`. Detailed per-script docs already exist in `Documents/*.md` — this file adds the pipeline-order and cross-module picture. See the [packages CLAUDE.md](../CLAUDE.md) for dependency-graph context (`s1setup` depends on `utilities`, `sarfunc`).

## Pipeline order

`setupTrack` orchestrates **steps 1–5** below for every unprocessed orbit directory under a track directory (`track-N/<orbit>/`):

```
track-N/<orbit>/   (raw SAFE dirs)
   │
   ├─ 1. findgain            → absolutegain                (median calib gain per IW swath)
   ├─ 2. runPreProcTops       → per-beam SLC + SLC_tab_*    (S1_TOPS_preproc, updateS1State)
   ├─ 3. trimTopsSLCsToFit    → trims SLCs in place         (SLC_copy_S1_TOPS, uses ../frameRange)
   ├─ 4. catMultipleTops      → track-N/<orbit>-<seq>/      (SLC_cat_S1_TOPS merge rounds,
   │                                                          OR if only 1 SAFE: rename+move SLCs)
   │        └─ computeBurstTimes  → *.btimes, frames.FIRST.LAST, missing.N
   └─ 5. radcalcoeffs         → track-N/<orbit>-<seq>/betaNought   (beta-nought from calib XML)
   │
   ▼
track-N/<orbit>-<seq>/   (merged SLC ready for pairing)
```

- Steps 1–2 run in `track-N/` against the raw orbit dir; step 3 runs inside the orbit dir; step 4 produces the merged output dir `<orbit>-<seq>/`; step 5 writes into that output dir.
- If `frameRange` doesn't exist in the track dir, step 3 is skipped.
- If only one `.SAFE` is present, step 4 is skipped — SLCs are renamed (`YYYYMMDDtHHMMSS_iwN_hh.* → YYYYMMDD-<seq>_iwN_hh.*`) and moved directly, and `computeBurstTimes` is still run.
- Sentinel files `Completed`/`Failed` mark orbit-dir state; `Ignore` skips an orbit entirely.

### Downstream (pair setup, not orchestrated by setupTrack)

```
track-N/<orbit1>-<seq>/  +  track-N/<orbit2>-<seq>/
   │
   └─ setupStrackReg (per pair, per frame)
        registration mode (default): coarsereg → Gamma offset_* (CW offsets) → simoffsets.py → strack (sub-SLC scales)
        offset-tracking mode (--strackOffsets): strack (full res) → cullst (via `cleanoff` script)
```

`setupStrackReg` is "usually called from `setuppairs.py`" (outside this package) rather than directly.

## Module table

| Module | One-line purpose |
|---|---|
| `setupTrack` | Orchestrates the 5-step pipeline (findgain → runPreProcTops → trimTopsSLCsToFit → catMultipleTops → radcalcoeffs) for all unprocessed orbits under a track dir |
| `findgain` | Step 1 — median absolute calibration gain per IW swath from SAFE annotation XML → `absolutegain` |
| `runPreProcTops` | Step 2 — unpacks SAFEs via `S1_TOPS_preproc`, extracts `ascendingNodeTime`, calls `updateS1State` per SLC, writes `SLC_tab_*` |
| `trimTopsSLCsToFit` | Step 3 — trims SLC burst coverage to `../frameRange` via `SLC_copy_S1_TOPS` |
| `catMultipleTops` | Step 4 — merges multiple TOPS SLC frames pairwise via `SLC_cat_S1_TOPS`, moves result to `<orbit>-<seq>/`, runs `computeBurstTimes` |
| `computeBurstTimes` | Computes burst times/frame numbers from `.tops_par` files; called by `catMultipleTops` (and the single-SAFE path in `setupTrack`) |
| `radcalcoeffs` | Step 5 — computes beta-nought calibration coefficient from SAFE calibration XML → `betaNought` |
| `updateS1State` | Updates a Gamma SLC `.par` with precise OPOD state vectors via `S1_OPOD_vec`; called by `runPreProcTops` |
| `checkframes` | Scans orbit dirs for burst/frame coverage gaps and early/late frames; can move/split problematic SAFEs |
| `setupSeveralTopsImages` | Creates per-orbit/frame `setup_orbit_frame` scripts via Gamma's `setuptopsimage`, reading `frames`/`orbitframes` control files |
| `setupStrackReg` | Per-pair registration (`coarsereg` + Gamma offset_* + `simoffsets.py` + `strack`) or full offset tracking + culling (`strack` + `cullst` via `cleanoff`) |
| `cloneSLCdir` | Clones an SLC directory for a sensor/date range — copies small metadata, symlinks large `.slc`/`.pow` files |
| `cullSLCclones` | Companion to `cloneSLCdir` — removes duplicate `runboth` files from clones whose `setupStrackReg.py --frame` params match the original |
| `spinner` | `Spinner` class — small terminal busy-indicator used by `setupTrack` during long steps |

## External tool dependencies

- **Gamma SAR processor** (binaries must be on PATH): `S1_TOPS_preproc`, `SLC_cat_S1_TOPS`, `SLC_copy_S1_TOPS`, `S1_OPOD_vec`, `setuptopsimage`, `create_offset`/`offset_pwr`/`offset_fit` (Gamma ISP, referenced via `ISPpath` in `setupStrackReg`).
- **GrIMP/GIT64 binaries** (via `s1setup` → `setupStrackReg`): `coarsereg`, `simoffsets.py`, `strack` (speckleSource), `cullst` (invoked indirectly through the generated `cleanoff`/`cleanoffmerge.py` scripts).
- **OPOD orbit archive**: `updateS1State` looks in `/Volumes/insar9/ian/Data/SentinelGreenland/OPOD/` for `.EOF` precise orbit files.
- **`myDev` module** (`ss.S1XMLheader`): required by `radcalcoeffs` to parse calibration XML.

## Relationship to GIT64 / speckleSource

- `s1setup` is the **pre-tracking** stage: it produces co-registered, calibrated Sentinel-1 SLCs (`<orbit>-<seq>/`) with `betaNought`, `absolutegain`, and burst/frame metadata (`.btimes`, `frames.FIRST.LAST`).
- `setupStrackReg` is the bridge into `speckleSource`: it builds the `strack`/`strackw` input files (`setupStrackInput`), runs `strack` for both registration and full offset tracking, and runs `cullst`-based culling (`cullls`-style `cleanoff`/`cleanoffmerge.py`) on the resulting offset maps.
- Output naming (`*.cull.interp.da`, offset files) feeds the same conventions documented in the top-level CLAUDE.md's "Naming conventions for offset files".

## Notes

- `setupTrack --scratch` defaults to `/dev/shm/<user>/scratch` (tmpfs) with automatic fallback to `--diskScratch` (`/tmp/<user>/scratch`) if `/dev/shm` is low on space (`_shmLow`, 85% threshold) or a step fails due to `ENOSPC`.
- `setupTrack --overWrite` deletes and reprocesses an orbit's output dir only if the source `.SAFE/measurement/*.tiff` files are still present (otherwise the orbit can't be reprocessed and is skipped).
- Orbit directory naming: raw source dirs are `NNNNN` or `NNNNN_SEQ` (`isSourceOrbitDir`/`parseOrbitSeq`); merged output dirs are `NNNNN-SEQ`.
- `setupTrack` groups all orbits processed in one run by track directory and writes a single log file per track: `log.<trackName>.<pid>.<YYYYMMDD>`.
- `checkframes`' default valid burst range is `[300, 750]`, overridable via a `frameRange` file (also consumed by `trimTopsSLCsToFit`).
