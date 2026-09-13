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
   │
   ├─ segmentTrack            → proposes orbitframes entries, then runs setupSeveralTopsImages
   │                            over the proposal (check only unless -commit; links with
   │                            -refreshLinks)
   └─ setupSeveralTopsImages  → track-N/<orbit>_<firstBurst>/   (reads frames + orbitframes)
```

### Reframing (`segmentTrack` → `setupSeveralTopsImages`)

The reframed directory is `<orbit>_<firstBurst>` and pairing (`mosaicworkflow.grepdate.findPair`)
matches that name **exactly**. So `firstBurst` is the pairing key and `nBursts` is free: shorten a
piece as much as the data requires and it still pairs, but shift its start and it needs matching
pieces in the neighbouring acquisitions before any pair can form. That asymmetry is what makes
`orbitframes` hard to maintain, and it is what `segmentTrack` automates — see
`Documents/segmentTrack.md` for the rules. `segmentTrack` never modifies `frames`.

The framing of a new acquisition is **carried forward from the last built acquisition whose burst
coverage matches it** — matched rather than merely latest, because the satellites interleave and
do not cover the same bursts (track-31: S1C 398-495, S1A 413-494). What is maximised is pairs:
the shared key gives the 6-day nearest-neighbour pair and the satellite-specific keys give the
12-day alternating pairs, which is why a piece nested inside another is kept where it is a key in
its own right. Nothing is invented outside the ground the reference acquisitions were cut over, no
piece exceeds `-maxBursts` (76), and pieces cut here are broken to sit inside the velocityStats
boxes. The reference is chosen by **how much of this acquisition's data its framing would cut**
(`carriedCoverage`), most recent first among equals; only where the neighbours frame less than
`REACHBACKFRACTION` of the data does it reach back for an older framing that covers more —
track-163's 63410 takes the 2022 framing, which trimmed is exactly what it was built as. Lengths
are reconciled only across pieces that stopped short of their **own** data; one that ran to the end
of its acquisition was trimmed there and says nothing about how the key is framed. `pairable()`
decides what counts as a neighbour (12 days normally, 24 or 36 where a cycle was missed and nothing
lies between, and pairs a day apart once S1A/S1C/S1D all operate). A key that falls before the
data — the coverage having stepped later — is **moved onto the first burst there is** rather than
abandoned, snapping to a start the track already uses where one is within `snapTol`; track-45's
coverage stepped 419→421 and everything since is cut at 422. Scored over all 30 Greenland tracks
the rules reproduce 92.0% of built acquisitions exactly.

`segmentTrack -commit` merges its proposal into `orbitframes` **by orbit** — the lines of the
not-yet-built orbits it proposes for are replaced and every other line, hand comments included, is
kept. **An acquisition already built is never rewritten**: its lines describe directories that
exist and pair, so the only edit it takes is an extra piece appended after them, indexed above
everything it already uses, where a key introduced further along needs a partner. An orbit that is
written states every `frames` index, using `nBursts` 0 for frames it cannot build:
`determineFraming` falls back to the `frames` default for any index left unlisted, and
`checkRange` then aborts the whole track if the data cannot cover it. The
processing track dir reaches the assembly tree through per-orbit `<orbit>-<seq>` symlinks that
nothing else creates, so an acquisition is invisible to `segmentTrack` (and silently absent from
the proposal) until `-refreshLinks` links it. `-refreshLinks` skips assembled dirs older than
`-maxLinkAgeDays` (365) because a track cut short years ago has old assembled dirs that were
deliberately never linked. A track moved between volumes has links into more than one assembly
tree; the `project.yaml` `defaultAssemblyDirectory` key (joined with the track name) says which is
current, and without it the most recently assembled one is taken — reported, never fatal.

**Burst numbering.** A burst number counts 2.759 s periods from the ascending node.
`computeBurstTimes` anchors once (`round(fitted t at offset 0 / 2.759)`) and then *counts*,
stepping by `round(dt/period)` between bursts. It used to round every burst on its own, which
slipped whenever a swath's times sat near a half-period boundary — track-171's iw1 ratios read
640.5016, 641.5013, … and crossed `.5` partway down the file, numbering two bursts the same, which
`checkBurstTimes` reports as a "gap in burst times" and aborts the track on. The same old rule
numbered by row index, so a genuinely dropped burst was hidden inside a contiguous count; counting
by time leaves the hole visible. **Each swath keeps its own anchor** — the three swaths of a burst
are ~T/3 apart so their numbers legitimately differ by ±1, and forcing them onto one grid would
renumber most of the archive (measured: 988/1593 files in track-141). Cross-swath agreement is
checked, not enforced. `.btimes` is derived from `*.tops_par` + `ascendingNodeTime` alone, both of
which survive SLC cleaning, so rerunning `computeBurstTimes.py` in a directory repairs it;
`--check` reports without writing, and `segmentTrack` does it automatically, before it reads the
coverage, for the directories it is actively segmenting — so a slipped acquisition is repaired and
framed in the same run. Never a sweep, never a stale (SLC-cleaned) directory, and never one where
the first burst number would move, since that is the piece key.

**Stale links.** Cleaning an assembled directory deletes the SLCs but leaves the metadata
(`.slc.par`, `.tops_par`, `.btimes`, `ascendingNodeTime`), so it still frames even though nothing
can be cut from it. `hasSwathSLCs()` is the test: `-refreshLinks` will not link such a directory,
and an existing link to one is marked `noData` — still framed, since that is what the comparison
table is read against, but never written for, never given a matching piece, and skipped by the
setup step (`setupSeveralImages(requireSLCs=True)`).
On a mature track this is most of the record: track-126 has 77 of 80 linked directories cleaned. `-undo`/`-undoLinks` work off `orbitframesBackup/` in the track dir.

`segmentTrack` runs `setupSeveralTopsImages` itself, over the proposal merged into `orbitframes`
in memory, so the framing is checked whether or not it is committed. That pass writes nothing and
collects every failure (`SetupError`, `strict=False`) instead of aborting the track at the first
one; **if it reports anything, nothing is written at all** — no `orbitframes`, no scripts — and
the run exits 1. Fixing the framing is left to the user. A clean pass under `-commit` writes the
entries and then the `setup_` scripts, clearing out the superseded ones (`cleanStale`: setup
scripts for never-built pieces among the orbits covered, and any earlier `runSetup.*` whose every
entry is regenerated now or already built — a run file still listing unbuilt work this run does
not cover is kept, since it is the only record of it), so repeat runs leave one current set; `-noSetup` stops after `orbitframes`. The setup step covers the last
`SETUPWINDOWDAYS` (90) of the record, back from the last acquisition built, so a decade of old
framings — cut against SLCs re-downloaded since — does not report problems that would block the
commit; `-firstdate`/`-lastdate` override the window. An interactive `-plot` run without
`-commit` offers **commit**/**exit** buttons that run that same write step. Standalone
`setupSeveralTopsImages` is unchanged (`strict=True`).

- Steps 1–2 run in `track-N/` against the raw orbit dir; step 3 runs inside the orbit dir; step 4 produces the merged output dir `<orbit>-<seq>/`; step 5 writes into that output dir.
- If `frameRange` doesn't exist in the track dir, step 3 is skipped.
- If only one `.SAFE` is present, step 4 is skipped — SLCs are renamed (`YYYYMMDDtHHMMSS_iwN_hh.* → YYYYMMDD-<seq>_iwN_hh.*`) and moved directly; `setupTrack` then writes `SLC_tab_YYYYMMDD-<seq>` and copies `absolutegain` + `ascendingNodeTime` into the output dir (what `catMultipleTops` does for multi-SAFE units — the `setup_<orbit>_<burst>` scripts start from `ls SLC_tab*-<seq>`, so a unit without the tab hangs them), and `computeBurstTimes` is still run.
- Sentinel files `Completed`/`Failed` mark orbit-dir state; `Ignore` skips an orbit entirely.

### Downstream (pair setup, not orchestrated by setupTrack)

```
track-N/<orbit1>-<seq>/  +  track-N/<orbit2>-<seq>/
   │
   └─ setupStrackReg (per pair, per frame)
        registration mode (default): coarsereg → Gamma offset_* (CW offsets) → simoffsets.py → strack (sub-SLC scales)
        offset-tracking mode (--strackOffsets): strack (full res) → cullst (via `cleanoff` script)
```

`setupStrackReg` is usually reached via `setuppairs` (this package) → `setupSARpair.py` (still in `insarScripts/bin`) → the generated `runboth`, rather than called directly.

Its offset outputs are **GeoTIFF + tiff-backed VRT by default**; `--noTiff` reverts to raw binary,
and the older `--tiff` is still accepted (it now asks for what already happens), so the
`runboth`/`dofast` scripts `setupSARpair.py` wrote with it keep working. The choice is passed
through to `simoffsets.py --tiff`, `strack -tiff` and the generated `cleanoff`/`cleanoffmerge.py`,
so the whole chain stays in one format.

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
| `segmentTrack` | Proposes `orbitframes` entries from the `.btimes` burst coverage and drives `setupSeveralTopsImages` over the proposal; writes nothing without `-commit` |
| `setupSeveralTopsImages` | Creates per-orbit/frame `setup_orbit_frame` scripts via Gamma's `setuptopsimage`, reading `frames`/`orbitframes` control files (or lists passed by `segmentTrack`) |
| `setuppairs` | Runs `grepdate` per frame and calls `setupSARpair.py` (still in `insarScripts/bin`) for every `o--` acquisition whose gap to the next same-frame acquisition is 0 < nDays <= sensor `maxDays` — consecutive pairs only, any separation (1, 5, 6, 7, 12, 13 ... days); `--check` dry-runs |
| `setupStrackReg` | Per-pair registration (`coarsereg` + Gamma offset_* + `simoffsets.py` + `strack`) or full offset tracking + culling (`strack` + `cullst` via `cleanoff`) |
| `cloneSLCdir` | Clones an SLC directory for a sensor/date range — copies small metadata, symlinks large `.slc`/`.pow` files |
| `cullSLCclones` | Companion to `cloneSLCdir` — removes duplicate `runboth` files from clones whose `setupStrackReg.py --frame` params match the original |
| `spinner` | `Spinner` class — small terminal busy-indicator used by `setupTrack` during long steps |
| `updateOffsetsToTiff` | Migrates an already-processed frame dir from raw flat binary to GeoTIFF + tiff-backed VRTs, and regenerates its post-processing scripts in tiff form. See "Migrating old frame dirs" below |
| `reprocessS1` | Queue-driven rebuild of frames over a date window: refile archive zips → `setupTrack --overWrite` → `setup_<orbit>_<burst>` → strip TIFFs and subswath SLCs. Uses `asfsearchdownload.queueS1` with its own `queueDir`; see [Documents/reprocessS1.md](Documents/reprocessS1.md) |

## External tool dependencies

- **Gamma SAR processor** (binaries must be on PATH): `S1_TOPS_preproc`, `SLC_cat_S1_TOPS`, `SLC_copy_S1_TOPS`, `S1_OPOD_vec`, `setuptopsimage`, `create_offset`/`offset_pwr`/`offset_fit` (Gamma ISP, referenced via `ISPpath` in `setupStrackReg`).
- **GrIMP/GIT64 binaries** (via `s1setup` → `setupStrackReg`): `coarsereg`, `simoffsets.py`, `strack` (speckleSource), `cullst` (invoked indirectly through the generated `cleanoff`/`cleanoffmerge.py` scripts).
- **OPOD orbit archive**: `updateS1State` looks in `/Volumes/insar9/ian/Data/SentinelGreenland/OPOD/` for `.EOF` precise orbit files.
- **Calibration XML parsing**: `radcalcoeffs` uses `s1setup.s1XMLreader.S1XMLreader` (BeautifulSoup
  + lxml, both now declared dependencies). It is a copy of `myDev.S1XMLheader` renamed so `myDev`
  stays untouched for its other users. Do not reintroduce the `myDev` import: that module is a
  loose directory in `~/PycharmProjects`, importable only when `PYTHONPATH` includes it, which an
  interactive shell provides and cron does not — `radcalcoeffs` failed with `ModuleNotFoundError`
  the first time the assemble stage ran outside an interactive shell (2026-08-15). Note that
  `runAutoupdateS1.sh`'s `conda activate base` **wipes** `PYTHONPATH` regardless of the caller:
  `qgis-activate.sh` prepends to it, and re-activating an already-active base first runs
  `qgis-deactivate.sh`, which unsets it. So no s1setup module may rely on `PYTHONPATH`.

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
- **`setupTrack --queue`** takes its work from `asfSearchAndDownload`'s `toProcess.yaml` (see `Documents/setupTrack.md`), **consuming** entries as they finish and routing failures to `problem.yaml` with a `comment` naming the failing step. This is the only place s1setup depends on asfSearchAndDownload: `queueS1` is imported **lazily, inside `resolveQueueUnits`**, so the classic directory path keeps its ~0.1 s start and still runs where that package is absent. Do not hoist that import to module scope.
- Internal contracts queue mode relies on, easy to break by accident: `classifyOrbits` returns `(runList, skips)` with a machine-readable skip *code* per entry (the `'retrying'` code is a message only — those units are in `runList`); `processOrbit` returns `('ok'|'skipped'|'failed', detail)` — its early returns used to be `True`, which conflated "did nothing" with "processed"; `_runSteps` returns `(ok, detail)` where detail names the failing step and is what ends up in the problem comment.

## Migrating old frame dirs to tiff (`updateOffsetsToTiff`)

`setupStrackReg` defaults `tiff: True`, so new pairs are GeoTIFF-backed throughout, but ~6000
already-processed frame dirs are still raw flat binary. `updateOffsetsToTiff` converts them in
place. It takes frame dirs directly, or sweeps whole tracks:

```
updateOffsetsToTiff track-26/5622_573                       # one dir
updateOffsetsToTiff --tracks track-26 track-74 --dryRun     # named tracks, no writes
updateOffsetsToTiff --allTracks --nThreads 8 --debug
updateOffsetsToTiff --allTracks -firstdate 2020-01-01       # date-windowed
```

`--project` names the root holding `track-*` (default cwd). A sweep runs `--nThreads` (4) dirs at
a time, keeps going past a dir that fails, and prints a summary with per-vintage counts and any
failures repeated at the end. `--firstdate`/`--lastdate` (either `YYYY-MM-DD` or `YYYY:MM:DD`)
filter on the frame's acquisition date read from the `pairinfo`, falling back to the geodat;
default is all time, and a frame whose date cannot be read is skipped rather than converted
whenever a window is given. `--debug` moves every original (rasters, the VRTs it rewrites, the
scripts it regenerates) into a **`debug/` subdirectory of the frame directory itself**
(`azimuth.offsets` → `<frame>/debug/azimuth.offsets`), so the rollback copy travels with the
frame it came from and two frames can never collide. `--debug NAME` overrides the subdirectory
name; it takes a name, not a path. Without `--debug` the originals are deleted. Re-running is a
no-op — `isSkippedSubdir()` keeps the scans out of `debug/` (and `velocity*/`), so the saved
originals are never re-converted.

**Threading caveat:** conversion works on absolute paths and is thread-safe, but
`makeCullFile`/`makeCleanOff` write into the *current* directory, so script regeneration has to
`chdir` and is serialised behind `CHDIR_LOCK`. Anything added to this module that uses a relative
path must account for that.

**Vintages.** Directories are not uniform — in track-26, 402 of 517 have no VRTs at all, 145 use
the pre-2020 `<root>.cull.dr.interp` name order, and a few predate `runcull`. The tool picks a
manifest accordingly:
- **modern** (`range.offsets.vrt` exists) — the VRT set is the manifest; every raw band it
  references is converted, masks included, and each VRT is rewritten in place.
- **legacy** (no VRTs) — a product table drives it (downstream-read products + the cull chain),
  grids come from the `.dat` sidecars, and the modern VRT set is written over the result. Old
  interp names are normalised. Any stray raw-backed VRT (`offsets.SECorrection.vrt` is the
  common one) is then swept with the modern path so both vintages end with no raw bands.

**The naming rule matters and is not uniform.** `strack`/`strackw`/`cullst` name their tiffs by
band role — `deriveTif` strips the source's last extension and appends `.<Description>.tif`
(`<pair>.offsets.cull.dr` → `<pair>.offsets.cull.RangeOffsets.tif`) — while the Python writers
(`cleanoff`, `simoffsets`, `regOffsetsMerge`, `mergeoff`, `mergefast`, `processfast`) append
`.tif` to the source name. The discriminator is the pair prefix: role naming applies iff the
product starts with `<orbit1>_<frame>.<orbit2>_<frame>.` and its root contains neither `.interp`
nor `.merge`. Dataset metadata cannot be used — the Python writers clone the cull VRT's metadata
verbatim, so `azimuth.offsets.vrt` and `<pair>.offsets.cull.vrt` carry identical keys. The prefix
test is load-bearing: `offsets.dr` (simoffsets) and `<pair>.offsets.dr` (strack) both carry a
`RangeOffsets` description but must resolve to different names.

**Type/grid table** — `.mt`/`.mask` are Byte (nodata 0), `.lat`/`.lon` are Float64, everything
else Float32 (nodata -2e9); raw data is big-endian. Every product's size is checked against
`nr*na*itemSize` from a `.dat` before conversion, and skipped with a warning on mismatch — that
check is what keeps a wrong table entry from silently producing a garbage raster across 6000 dirs.

**Scripts.** Only the post-processing ones are regenerated, via the real generators
(`sarfunc.makeCullFile`, `sarfunc.makeCleanOff`) so the output matches a native run: `runcull`,
`runCullReg<N>` (replacing the pre-2020 `runcullReg`), `cleanoff`, and `fast/runFastCull`. The
long-running drivers — `runboth`, `fast/dofast`, `runAll`, `runSimll`, `setupStrackReg` — are
deliberately left alone. Two gotchas: `makeCullFile` opens the script before reading the sidecar
for NR/NA, so a missing `.dat` truncates it *and* takes the process down via `u.myerror`
(SystemExit, not Exception) — hence the pre-check and the `BaseException` catch;
`fast/runFastCull` usually hits that because the `.speckle` products it culls are deleted once
`mergefast` has run.

**Regenerating adopts current parameters.** A 2016 `runcull` had `intfloat -thresh 40` baked in;
the regenerated one takes `normalInterp.thresh: 20` from today's `sensors/S1.yaml`. Re-running a
converted legacy dir therefore fills fewer holes (~0.05% of pixels on the frame tested). Values
where both runs produce data are bit-identical — this is a parameter change, not a format one.

**Left raw by design**, and reported per directory: the interferogram (`*.int.flatc.reflat`), the
power image (`*.pow`), the `.azd` azimuth-defocus rasters (referenced by no VRT, so no dimensions
and no tiff-mode consumer), and `fast/initialGuess.mask` (the fast tracker owns it, and
`fast/dofast` is out of scope).
