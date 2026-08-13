# setupTrack

Orchestrates the 5-step Sentinel-1 preprocessing pipeline for each unprocessed orbit under a track directory. Steps: `findgain` → `runPreProcTops` → `trimTopsSLCsToFit` → `catMultipleTops` → `radcalcoeffs`. Uses a RAM-backed scratch directory (`/dev/shm`) when available, falling back to disk (`/tmp`). Replaces the legacy `setupTrack` and `setupTrack1` csh scripts.

Run from any directory; pass the track directory (or explicit orbit dirs) as arguments.

---

## Usage

```
setupTrack.py [options] track-N/
setupTrack.py [options] track-N/4547 track-N/4722
```

| Argument | Description |
|----------|-------------|
| `dirs` | Track directory (e.g. `track-1/`) or one or more explicit orbit directories |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--check` | False | Dry run: show what would be processed, no execution |
| `--firstdate YYYY-MM-DD` | ~50 years ago | Skip orbits with ascending node time before this date |
| `--lastdate YYYY-MM-DD` | 2100-01-01 | Skip orbits with ascending node time after this date |
| `--scratch PATH` | `/dev/shm/<user>/scratch` | Base scratch directory for intermediate SLCs |
| `--diskScratch PATH` | `/tmp/<user>/scratch` | Fallback scratch on disk if `/dev/shm` runs out of space |
| `--overWrite` | False | Reprocess orbits whose output already exists (only if TIFFs still present); prompts before deleting |
| `--noPrompt` | False | Skip confirmation prompt when combined with `--overWrite` |
| `--quiet` | False | Suppress subprocess output; show only step names and timing |

---

## What it does

For each orbit directory in the given track:

1. Reads `ascendingNodeTime` to filter by date range
2. Skips orbits that already have processed output (unless `--overWrite`)
3. Runs the 5-step pipeline in sequence, with a `Spinner` animation:
   - `findgain` — gain calibration
   - `runPreProcTops` — preprocessing
   - `trimTopsSLCsToFit` — trim bursts to frame bounds (Gamma)
   - `catMultipleTops` — concatenate multi-sequence SLCs (Gamma)
   - `radcalcoeffs` — radiometric calibration coefficients
4. Intermediate SLCs are written to scratch; the scratch directory is cleaned up after each orbit

---

## Part of the s1setup package.
