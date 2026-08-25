# setupS1Tracks

Orchestrates Sentinel-1 velocity processing across all `track-*` directories in
a project. Sentinel-1 analogue of
[`nisargrimpworkflow.setupNISARTracks`](../../nisargrimpworkflow/nisargrimpworkflow/setupNISARTracks.py).
Run from the **project root** — the directory holding `project.yaml`, a
`templates/` directory, and the `track-*` subdirectories.

Three modes are implemented: `--copyFiles` (first-time per-track file setup),
`--runVelstatsregions` (common-box velocity rebuild + `velocityStats`), and
`--runVelStats` (`velocityStats` only). The RSLC→products pipeline is still
stubbed (see [Not yet implemented](#not-yet-implemented)).

---

## Usage

```
setupS1Tracks --copyFiles
setupS1Tracks --copyFiles --tracks track-12 track-64 --overWrite
setupS1Tracks --runVelstatsregions
setupS1Tracks --runVelStats
```

Every mode runs from the **project root** and honors `--tracks` to restrict the
run, and `--nThreads` for the thread budget.

---

## Cheat sheet

### 1. Start a new project

Prerequisites, none of which this tool creates:

| Needed | What |
|---|---|
| `project.yaml` | at the project root — at minimum `sensor: Sentinel1`, `region:` (the yaml carrying `dem`/`velMap`), `tiepointFile:`, `framePattern: '???'`, `velThumbOutput: tiff`, `velocityStatsMode:`; add `tieThresh`/`excludeThresh` to override the 100/3 defaults |
| `templates/` | `tie_plan_header`, `vel_thumb_plan`, `vel_thumb_header` — the masters with `<TRACK>`, `<DEM>`, `<TIEFILE>` placeholders |
| `track-*/` | orbit-frame dirs, built beforehand by `segmentTrack`/`setupSeveralTopsImages`/`setupTrack` |
| `track-*/velocityStats/<X>-<Y>/` | must already exist — `--copyFiles` writes a `vel_thumb_header_<range>` per range but does **not** create the range dirs |

Then instantiate the per-track plan files:

```
setupS1Tracks --copyFiles                 # copy-if-absent
setupS1Tracks --copyFiles --overWrite     # replace existing headers/plans
```

For a 12-day clone project, add `secondaryDirectories:` to the *prime's*
`project.yaml` and give each clone its own `project.yaml` — see
[Secondary trees](#--fullupdate).

### 2. Full update on a given year (or years)

```
setupS1Tracks --fullUpdate                        # ties: current year
setupS1Tracks --fullUpdate --year 2025 2026       # ties: those years
setupS1Tracks --fullUpdate --year all             # ties: 2015 -> current
```

`--year` sets the tie-refresh years (steps 1/5). The velocity rebuild and
baseline check (steps 6–7) follow the **separate** lookback window, so widen
that too if you want them to match:

```
setupS1Tracks --fullUpdate --year 2025 2026 --firstDate 2025-01-01
setupS1Tracks --fullUpdate --weeks 12             # shorter window, same years
```

### 3. Resize the velocity files onto a common grid

```
setupS1Tracks --runVelstatsregions
```

Uses **all** years by default (the common box is sized from the full history).
Cascades a matching grid+velocity rebuild into every secondary clone, then
rebuilds `velocityStats`. Add `--noUpdateVelStats` to skip that last step, or
`--noSecondary` to leave the clones alone.

### 4. Rebuild the velocity files

Two routes, depending on whether the *grid* is still right:

```
setupS1Tracks --rebuildVel --firstDate 2019-01-01     # both sets, existing runOff
setupS1Tracks --runRefresh && setupS1Tracks --updateNoCull
```

- `--rebuildVel` re-runs `mosaic3d` through each velocity dir's existing
  `runOff` — `velocity/` (`-redoculled`) then `velocity_nocull/` (`-reset`).
  Fast, touches no ties, but inherits whatever grid that `runOff` encodes.
  Bounded by the window flags, so `--firstDate`/`--lastDate`/`--weeks` set the
  range.
- `--runRefresh` + `--updateNoCull` regenerates through `vel_thumbs` instead,
  writing a fresh `runOff`. Use this when the frame's `vel_thumb_header`
  changed. Order matters — `--updateNoCull` copies `velocity/runOff`.
- If the headers themselves need resizing first, use (3) instead.

### 5. Update tiepoints without touching the velocities

```
setupS1Tracks --runRefresh --tiesOnly                 # current year
setupS1Tracks --runRefresh --tiesOnly --year 2024 2025
```

`-tiesOnly` stops `refreshties` after `maketies`/`tie_script`; `makeframetie`
and `vel_thumbs` never run, so no velocity product is rewritten. **Errors if
used without `--runRefresh`.**

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--tracks track-N …` | all `track-*` | Restrict to these track dirs. Numeric-sorted on the track integer; an explicit list is validated to exist. |
| `--copyFiles` | — | Instantiate `tie_plan_header`, `vel_thumb_plan`, and `vel_thumb_header_<range>` from `templates/` into every track `tiepoints/` (substituting `<TRACK>`, `<DEM>`, `<TIEFILE>`); skip files that already exist. `velocityStats/` dirs are assumed to already exist. |
| `--runVelstatsregions` | — | Find a common bounding box per `velocityStats` range, rebuild the velocities on that grid, resize `velocity_nocull`, and rebuild `velocityStats` (steps 0–5 below). Uses **all** years (2015→current) unless `--year` says otherwise, since the box is sized from the full history. |
| `--runVelStats` | — | Run only the final step: `velocityStats` in each track's `velocityStats/` dir (RA/XY per `project.yaml` `velocityStatsMode`). |
| `--runRefresh` | — | Run `refreshties.py` (maketies + makeframetie → `tie_script`) across the selected tracks and return. Legacy-baseline flavor (no `-phase`); honors `--year`, `--overWrite`, and `--tiesOnly`. Builds `refreshties.py [-tiesOnly ][--overWrite ]-nThreads <concurrent> -toRun="[...]" <years> -noPrompt`. |
| `--tiesOnly` | — | With `--runRefresh`, pass `-tiesOnly` to `refreshties` (write `tie_script` only, skip the vel thumbs). **Errors if used without `--runRefresh`.** |
| `--updateNoCull` | — | Run `makevelnoclean.py` across the selected tracks (regenerate `velocity_nocull/`) and return. Plain form — no `--runVelstatsregions` header/reSize/date staging. Builds `makevelnoclean.py -toRun="[...]" -threads=<nThreads> -noprompt -tiff`. |
| `--rebuildVel` | — | Rebuild **both** `velocity/` and `velocity_nocull/` with `makevelnoclean` — the `--fullUpdate` step-6 pair (`-redoculled`, then `-reset`) — and return. Uses the same window resolution as `--fullUpdate`, so `--firstDate`/`--lastDate`/`--weeks` set the range instead of the default 52-week lookback. No `vel_thumbs`, no ties: it re-runs `mosaic3d` through each velocity dir's existing `runOff`. |
| `--runAutoClean` | — | Run `autoclean.py` in each selected track dir (`cd track; autoclean.py -threads=<nThreads> -sigThresh=<S>`) and return. Sequential across tracks (autoclean is itself threaded). |
| `--sigThresh S` | 3.0 | With `--runAutoClean`, sigma threshold passed to `autoclean.py -sigThresh`. |
| `--fullUpdate` | — | Run the full monthly update (MonthlyWorkflow steps 1–7) across the selected tracks and return. See [--fullUpdate](#--fullupdate). |
| `--lastDate YYYY-MM-DD` | today | With `--fullUpdate`/`--rebuildVel`, the last date in the run (end of the lookback window). |
| `--weeks N` | 52 | With `--fullUpdate`/`--rebuildVel`, lookback window length in weeks back from `--lastDate`. |
| `--firstDate YYYY-MM-DD` | `--weeks` back, 1st of month | With `--fullUpdate`/`--rebuildVel`, override the computed window start (step 6 velocity + step 7 baseline check). |
| `--year YYYY \| all` | current year | Years to process, e.g. `--year 2015 2016`, or `--year all` for the whole archive (2015 through the current year). Drives the refresh/`vel_thumbs` paths (`--runRefresh`, `--fullUpdate` steps 1/5); **`--runVelstatsregions` always uses all years** regardless. Independent of the `--weeks`/`--firstDate`/`--lastDate` window, which applies only to `--fullUpdate` steps 6–7 and `--rebuildVel`. |
| `--tieThresh N` | project.yaml `tieThresh` (fallback 100) | With `--fullUpdate`, azimuth baseline-tiepoint count below which a frame is marked `Exclude.pending`. CLI overrides project.yaml. |
| `--excludeThresh N` | project.yaml `excludeThresh` (fallback 3) | With `--fullUpdate`, azimuth baseline-tiepoint count at/below which (or no solution) a frame is hard-`Exclude`d. CLI overrides project.yaml. |
| `--nThreads N` | 48 | Total thread budget. Concurrent tracks are capped at `min(nTracks, max(1, N//4))` so total concurrency stays near `N` rather than `N × ntracks`. |
| `--noUpdateVelStats` | False | With `--runVelstatsregions`, skip the final `velocityStats` rebuild (step 5). |
| `--overWrite` | False | With `--copyFiles`, overwrite existing `tie_plan_header` / `vel_thumb_plan` instead of skipping. |
| `--applySecondary` | False | Also run on every `secondaryDirectories` project (e.g. the `Sentinel1-S1A`/`-S1C` 12-day clones). Default is prime-only, **except** `--runVelstatsregions`, which always cascades a grid+velocity rebuild to the secondaries. Ignored (with a warning) under `--fullUpdate` — run that separately per project. |
| `--noSecondary` | False | Never touch `secondaryDirectories` projects, even for `--runVelstatsregions` (escape hatch; also set internally to prevent recursion). |
| `--secondaryVelRebuild` | — | *(internal)* In a secondary project: sync the `vel_thumb_header` grid from `--primeDir` and rebuild the frame velocities on it. Set by the prime when it cascades. |
| `--primeDir DIR` | — | *(internal)* Prime project root, required with `--secondaryVelRebuild`. |
| `--check` | — | *(not yet implemented)* report S1 track product status; raises `NotImplementedError`. |

---

## `--copyFiles`

For each track: ensure `tiepoints/` exists, then instantiate from `templates/`:

| Template | Destination | Substituted |
|---|---|---|
| `tie_plan_header` | `tiepoints/tie_plan_header` | `<TRACK>`, `<DEM>`, `<TIEFILE>` |
| `vel_thumb_plan` | `tiepoints/vel_thumb_plan` | same |
| `vel_thumb_header` | `tiepoints/vel_thumb_header_<range>` for each existing `velocityStats/*-*` dir | same |

`<DEM>` is read from the `region`/`regionFile` yaml pointed at by `project.yaml`;
`<TIEFILE>` from `project.yaml`'s `tiepointFile`. Copy-if-absent unless
`--overWrite`. Unlike the NISAR variant, this does **not** create the
`velocityStats/` directories — they must already exist.

---

## `--runVelstatsregions`

Rebuilds every frame's velocity on one common grid per `velocityStats` range, so
the frame velocity maps mosaic cleanly and `velocityStats` can aggregate them.
Runs over **all** years (2015→current) unless `--year` overrides — step 2 sizes
the common box from the frames on disk, so refreshing only part of the history
would size the region from part of the history. Steps run in order (a failed
step aborts the sequence — they are dependent):

0. Ensure every `velocityStats` range has a `vel_thumb_header` (`setupTrackDirs`
   with `copyFiles=False`).
1. Bootstrap render at the current (auto-extent) headers (`refreshties`).
2. Compute the common box and write it into the headers (`makevelstatsregions`).
3. Rebuild every frame on the common grid (`refreshties --overWrite`).
4. Resize `velocity_nocull` to match (`makevelnoclean -reSize`).
4b. Cascade a grid+velocity rebuild into each `secondaryDirectories` clone
    (`--secondaryVelRebuild`), unless `--noSecondary`. The prime passes its
    resolved year list down, so the clone rebuilds exactly the years the prime
    just did.
5. Rebuild `velocityStats` from the culled `velocity/` dirs — prime plus the
   just-rebuilt secondary frames (`velocityStats.py`), unless
   `--noUpdateVelStats`.

`velocityStats.py` aggregates the `secondaryDirectories` frames itself, so
`--runVelStats` (step 5 alone) needs no secondary cascade.

---

## `--fullUpdate`

One-command monthly velocity update — the seven steps of
`Notebooks/monthlyProcessing/MonthlyWorkflow.ipynb`, chained. Run from the project root (e.g.
`/Volumes/insar9/ian/Sentinel1`); honors `--tracks`, `--nThreads`, `--sigThresh`. Each step
prints a start banner, and on completion a bold-blue `Finished step N` line carrying the step
time and the running total for the run.

**Secondary trees.** `--fullUpdate` does **not** cascade — `--applySecondary` has no effect here
and warns. Run it separately in the prime and in each `secondaryDirectories` clone. A secondary
is auto-detected by `track-N/velocityStats` being a symlink into the prime, and in that case
**step 3 (velocityStats) is skipped** (printed as `Skipped step 3`), because the clone shares the
prime's `velocityStats` directory. Steps 1, 2 and 4–7 still run there, which is what keeps
`velocity`/`velocity_nocull` current for autoclean. Each clone therefore needs its **own
`project.yaml`** at its root (`sensor`, `region`, `framePattern`, `velThumbOutput`,
`velocityStatsMode`, `tieThresh`/`excludeThresh` — mirroring the prime); without one the config
silently falls back to defaults, notably `tieThresh` 100, which over-marks `Exclude.pending` in
step 7. Clone `project.yaml`s must **not** set `secondaryDirectories`.

**Years and the lookback window are separate controls.** They govern different halves of the
run, and neither derives from the other:

- **`--year` drives the tie-refresh steps (1, 5).** `refreshties` rebuilds whole calendar years,
  so a date window is the wrong unit for it. The default is **the current year only** — new
  acquisitions land there, and older years are rebuilt only when asked for. Pass an explicit list
  (`--year 2019 2020`) or `--year all` (2015 through the current year) to widen it.
- **`--weeks`/`--firstDate`/`--lastDate` drive the velocity rebuild (step 6) and baseline check
  (step 7)**, which bind to the exact `[firstDate, lastDate]` dates. `lastDate` defaults to today
  and `firstDate` to `--weeks` (default 52) back from it, first of month.

So a default `--fullUpdate` run today refreshes ties for **2026** while rebuilding velocities over
**2025-08-01 .. 2026-08-21**. `--runVelstatsregions` is the exception that always uses *all* years
— it sizes one common box per range from the frames on disk, so a partial rebuild would size the
region from part of the history.

| Step | Action | Reuses |
|---|---|---|
| 1 | refresh ties + velocities (`refreshties.py {year}`) | `--runRefresh` |
| 2 | nocull velocities (`makevelnoclean.py`) | `--updateNoCull` |
| 3 | `velocityStats.py` per track | `--runVelStats` |
| 4 | `autoclean.py` per track | `--runAutoClean` |
| 5 | rerun ties (`refreshties.py -tiesOnly`) | `--runRefresh --tiesOnly` |
| 6 | window velocity rebuild (`makevelnoclean.py -firstdate=… -redoculled`, then `-reset`) | `--rebuildVel` |
| 7 | check baselines (`checkbasesol.py` per track) + mark low frames | new |

### Step 7: baseline check + frame marking

For each track, runs `checkbasesol.py --firstdate <firstDate> --track <N>` and marks frames by
their azimuth baseline-tiepoint count `nA` (three-way):

- `nA <= excludeThresh` **or no solution** (`sigmaA < 0`) → hard **`Exclude`**.
- `excludeThresh < nA < tieThresh` → **`Exclude.pending`** (needs operator check).
- `nA >= tieThresh` → nothing.

Both markers carry the reason inside (`<datetime>: checkbaseline: …`, the tieScript convention).
`checkbasesol.py` skips frames that already have `Exclude`/`Exclude.pending`, so step 7 only
touches frames that passed ties. A log
`fullUpdate_checkbaseline_<YYYY-MM-DD>.log` is written at the project root with `EXCLUDED (real)`
and `PENDING` sections.

**`Exclude.pending` is self-clearing.** `refreshties` (steps 1/5) removes all `Exclude.pending`
at the start of a refresh (`clearPendingExcludes`); tieScript/step 7 re-create them for frames
still failing. So a pending frame left unresolved is simply re-flagged the following month — an
improved frame silently rejoins the mosaic; hard `Exclude` is never auto-cleared. A **separate
standalone program** (not part of this tool) resolves pending frames to `checked` or a permanent
`Exclude` after operator review.

---

## Project configuration

Read from `project.yaml` in the project root:

| Key | Used for |
|---|---|
| `region` / `regionFile` | region yaml supplying `dem` (→ `<DEM>`) and `velMap`. |
| `tiepointFile` | `<TIEFILE>` substitution. |
| `sensor` | warned if not `"Sentinel1"`. |
| `framePattern` | warned if it strips a non-empty prefix (S1 frames are burst-numbered, e.g. `<orbit>_618`; use an all-`?` pattern). |
| `velocityStatsMode` | RA vs XY `velocityStats`. |
| `tieThresh` / `excludeThresh` | `--fullUpdate` step-7 baseline thresholds (defaults 100 / 3); `--tieThresh`/`--excludeThresh` override. |
| `secondaryDirectories` | 12-day clone project roots. Absolute paths (or any entry containing `/`) are used as given; bare names resolve against the parent of the project root. Set in the prime only. |
| `applySecondary` | default for `--applySecondary`. |
| `*_template` keys | override the default `templates/<name>` template paths. |

---

## Not yet implemented

The RSLC → offsets/products → ties → mosaics pipeline is stubbed. How S1 differs
from the NISAR workflow (why this is separate code, and where the stubs land):

1. S1 builds offsets/products starting from RSLCs (NISAR ingests HDF5 RUNW/ROFF).
2. No ionosphere corrections.
3. No dual/virtual-frame system — frames come from the first burst number
   (`<orbit>_<startburst>` dirs); no `framePattern` virtual-frame assembly.
4. Separate programs ([`segmentTrack`](segmentTrack.md),
   [`setupSeveralTopsImages`](setupSeveralTopsImages.md),
   [`setupTrack`](setupTrack.md)) build the orbit-frame dirs; this tool assumes
   they exist.
5. No HDF5 ingest.

`--check` is the placeholder for the eventual product check (cf.
`setupNISARTracks --check`) and currently raises `NotImplementedError`.

---

## Related

- [`setupTrack`](setupTrack.md), [`segmentTrack`](segmentTrack.md),
  [`setupSeveralTopsImages`](setupSeveralTopsImages.md) — build the orbit-frame
  directories this tool operates over.
- [`Sentinel1Phase.setupS1PhaseTracks`](../../Sentinel1Phase/Documents/setupS1PhaseTracks.md)
  — the pared-down `--copyFiles`-only analogue for the phase (unwrapped-interferogram) workflow.
- `nisargrimpworkflow.setupNISARTracks` — the NISAR workflow this mirrors.
