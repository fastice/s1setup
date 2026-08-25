# reprocessS1

Rebuilds Sentinel-1 frames over a date window that have already been through
the archive sweep once, from a queue, one unit at a time.

A unit that has been through [`setupTrack`](setupTrack.md) keeps its `.SAFE`
directories but not their measurement TIFFs, and each
`setup_<orbit>_<burst>` script deletes the subswath SLCs as it mosaics its
frame. So the assembly tree at rest holds only `.par`/`.tops_par`/`.btimes` —
redoing anything that needs pixels means going back to the archive zips. This
walks that back for every unit in a window:

| step | what it does | writes |
|---|---|---|
| **refile** | archive `.zip.1` → the unit's own `.SAFE` dirs | measurement TIFFs, ~25 GB/unit |
| **assemble** | `setupTrack --overWrite` | `<orbit>-<seq>/*_iw?_*.slc`, ~17 GB/unit |
| **merge** | each `setup_<orbit>_<burst>`, in burst order | `<orbit>_<burst>/<orbit>_<burst>.slc`, 2–17 GB each |
| **clean** | drop the TIFFs and the subswath SLCs again | frees ~42 GB/unit |

**"frame" means the merged product**, `<orbit>_<burst>` — what a
`setup_<orbit>_<burst>` script mosaics from the subswath SLCs — and the counts
say **merged frame(s)** throughout to keep it apart from an ASF/ESA source
frame, which is one granule and is counted as such. (The queue record's key is
still `frames:`, so an already-built queue stays readable.)

The frames on the processing volumes are the product; everything rebuilt under
`assemblyDir` is scaffolding and can come back from the archive.

**Assemble and merge are never overlapped.** Both stage through `/dev/shm`,
which does not have room for two units at once, so those two run strictly one
unit, one step at a time. `/dev/shm` is per host — run this on a machine the
nightly [`autoupdateS1`](../../asfSearchAndDownload/Documents/autoupdateS1.md)
is not using.

**Refile is the exception, and it is prefetched.** Unzipping into the assembly
tree touches no `/dev/shm` at all, so the *next* unit is refiled on a
background thread while the current one assembles and frames. It is a third of
a small unit's wall clock (3.9 of 10.9 min measured on `track-10/5781`) and
scales with granule count, so on a long run it is most of what prefetching can
hide. Depth is one: at most two units' TIFFs (~50 GB) are on disk at a time,
and the free-space guard is re-checked before each prefetch, at twice
`minFreeTB`, so a tight volume quietly falls back to refiling inline. Turn it
off with `--noPrefetch`.

The worker is a **daemon** thread — an interrupt must not leave the
interpreter waiting on a thread blocked in `unzip`. The cost is a possible
half-extracted `.SAFE`, which `fileOneZip` detects and refiles over next time,
and a staging directory of symlinks, which the next run sweeps. Its `unzip`
runs `-q` (`fileS1.fileOneZip(quiet=True)`), since per-file `inflating:` lines
from a background thread would land in the middle of the foreground unit's
progress.

---

## Usage

Two modes. `--buildQueue` takes the **config** and writes a queue;
without it the argument is the **queue** and the run works from that.

```
reprocessS1 --buildQueue [config] [--firstDate D --lastDate D --tracks "..."]
reprocessS1 [queue] [--maxOrbits N] [--tracks "..."]
```

```
reprocessS1 --buildQueue reprocess.yaml --check              # what it would queue
reprocessS1 --buildQueue reprocess.yaml                      # write toProcess.yaml
reprocessS1 --buildQueue reprocess.yaml --tracks "25 90" \
            --firstDate 2026-01-01 --lastDate 2026-01-31     # a slice of it

reprocessS1 toProcess.yaml --maxOrbits 1                     # try one
reprocessS1 toProcess.yaml --tracks 25                       # one track's worth
reprocessS1 toProcess.yaml                                   # drain the queue
reprocessS1 . --info
```

The queue argument may be either `toProcess.yaml` or the directory holding it,
and defaults to `.`. It does not need the config named again: `--buildQueue`
writes a `configPath` pointer beside the queue, the same way `autoupdateS1`
does. `--config` overrides it.

| Option | Default | Description |
|--------|---------|-------------|
| `target` | `./reprocess.yaml` / `.` | The config with `--buildQueue`, otherwise the queue |
| `--buildQueue` | off | Scan the window and write `toProcess.yaml` instead of running |
| `--info` | off | Queue paths, counts and lock state |
| `--config FILE` | the recorded `configPath` | Config to run with |
| `--firstDate D` | config | Window start `YYYY-MM-DD` |
| `--lastDate D` | config | Window end |
| `--tracks "16 25"` | all | Space- or comma-separated, `track-16` also accepted. Filters both the build and the run |
| `--maxOrbits N` | 0 (all) | Stop after N units. This is how you test with a few at a time |
| `--rebuildFrames` | off | Rebuild every frame of a unit, not only the ones whose SLC is missing or the wrong size |
| `--noPrefetch` | off | Do not refile the next unit while the current one assembles |
| `--noClean` | off | Keep the TIFFs and subswath SLCs after a unit finishes |
| `--stopOnError` | off | Stop at the first failure instead of routing it to `problem.yaml` and carrying on |
| `--check` | off | Dry run; changes nothing |

### Progress

Every unit prints its place in the run, its four steps, and what each cost,
with a running estimate for what is left:

```
=== [2/184] track-10/62557  2025-12-31  2 merged frame(s) [669, 674]
  [step 1/4] refile   6 granule(s) from the archive
    done in the background during the previous unit: 4.4 min of unzip, 0 s waited
    refile done in 0 s
  [step 2/4] assemble setupTrack --overWrite -> subswath SLCs
    assemble done in 3.5 min
  [step 3/4] merge    2 merged frame(s) from the subswath SLCs
    (1/2) setup_62557_669 -> log.62557_669.1799036.20260824
    (1/2) 62557_669.slc ok (35 s)
    (2/2) setup_62557_674 -> log.62557_674.1799036.20260824
    (2/2) 62557_674.slc ok (4.0 min)
    frames done in 4.5 min
  ...refiling track-10/62732 (6 granule(s)) in the background
  [step 4/4] clean    give back the TIFFs and subswath SLCs
    18 TIFF(s) and 3 subswath SLC(s), 48.6 GB freed
  [2/184] track-10/62557 ok in 8.1 min, ~24.6 h for the 182 left
```

"`4.4 min of unzip, 0 s waited`" is the overlap paying off: the whole refile
happened inside the previous unit's assemble. Where it has not finished in
time the wait is charged to this unit's wall clock but to none of its four
steps, which is why both numbers are reported.

Measured on `track-10`, 6-granule/2-frame units: **12.5 min** for the first
unit of a run (nothing ahead of it to overlap with) and **8.1 min** for every
one after — refile 4.4, assemble 3.5, frames 4.5, clean 4 s.

---

## Config

```yaml
archiveDir:  /Volumes/insar4/ian/Data/S1-Greenland      # the YYYY-MM zip dirs
assemblyDir: /Volumes/insar8/ian/Data/SentinelGreenland # track-N/<orbit> tree
procRoots:                                             # where setup_ scripts live
  - /Volumes/insar9/ian/Sentinel1
  - /Volumes/insar10/ian/Sentinel1
queueDir:    /Volumes/insar8/ian/Data/SentinelGreenland/reprocess
firstDate:   2025-12-19
lastDate:    2026-03-31
scratch:     /dev/shm/ian/reprocess     # [/dev/shm/<user>/reprocess]
diskScratch: /tmp/ian/scratch           # [/tmp/<user>/scratch]
minFreeTB:   1.0                        # refuse to start a unit below this [1.0]
```

`procRoots` is searched in order for `track-<n>/`; a track lives on one of them.

---

## The queue

The same queue as `autoupdateS1` —
[`asfsearchdownload.queueS1`](../../asfSearchAndDownload/Documents/autoupdateS1.md#managing-the-queues-queues1),
with its lock, its atomic writes and its processed→completed lifecycle —
pointed at a `queueDir` of its own, so nothing is shared with the nightly:

```
toProcess.yaml   problem.yaml   completed.yaml   processed.<YYYY-MM-DD>.yaml
.queueS1.lock    .reprocessS1.run.lock
```

**One runner at a time.** `.queueS1.lock` keeps the YAML consistent but not the
*choice* of units: two runners would read the same head of `toProcess` and work
the same ones, and one would `rmtree` an `<orbit>-<seq>` the other was
building. `.reprocessS1.run.lock` prevents that. It is non-blocking, so a
second terminal reports who holds it and exits 1 rather than queueing behind a
day-long drain, and it is **heartbeated as each unit starts** — the stale
window is an hour, sized to one unit rather than to a whole run, so a crashed
runner clears promptly instead of holding the queue for a day. `--info` shows
it. This is separate from, and does not replace, the deliberate absence of any
**assembly-tree** lock, which is what lets this run beside the nightly.

A unit leaves `toProcess` the moment it finishes, either into today's
`processed` file (folded into `completed.yaml` at the end of the run) or into
`problem.yaml` with the failing step as its comment — so an interrupted run
neither loses nor redoes work. `queueS1 list problem` reads them.

Each record carries what the run needs, so `run` never rescans:

```yaml
- unit: track-10/63257_4
  orbit: '63257'
  seq: 4
  date: '2026-02-17'
  procDir: /Volumes/insar9/ian/Sentinel1/track-10
  granules: [S1A_..._A648.zip, ...]   # archive basenames
  frames: [593]                       # bursts to build
  allFrames: [593]                    # every burst of this unit
```

`--buildQueue` is additive and re-runnable: a unit already in `toProcess`,
`problem` or `completed` is never queued twice, so a second pass over a wider
window or another track only adds what is new.

---

## What `--buildQueue` skips, and why

It starts from the **archive granule names** — they carry both the
acquisition date and the absolute orbit, so nothing under `assemblyDir` is
touched for a datatake outside the window. For each candidate it then looks at
the unit dirs on disk (`<orbit>` plus any `<orbit>_<seq>` split
`checkFramesS1` made) and skips:

- an `Ignore` marker — deliberately parked, usually after a failed
  `runPreProcTops`;
- no `.SAFE` dirs — never filed, or binned to `tmp/`;
- no `setup_` script for that unit — never framed;
- **every frame already built** — see below;
- an archive zip that is no longer there.

Everything but "frames already built" is reported **track by track with the
unit names**, because those are the ones that want a person — a track missing
its `setup_` scripts needs `segmentTrack` / `setupSeveralTopsImages` run on it
before this can help:

```
skipped  209 unit(s): frames already built (33 track(s))
skipped   14 unit(s): no setup_ script
    track-70      4  63317 63317_4 63667 63667_4
    track-148     3  63395_4 63395_4_4 63570_5
    ...
skipped   11 unit(s): Ignore marker
    track-17      7  5788 5963 6138 6313 6488 6663 6838
```

### Already-built frames

A frame is taken as built only if `<orbit>_<burst>.slc` is exactly the size its
`.slc.par` implies (`range_samples × azimuth_lines ×` 4 for `SCOMPLEX`, 8 for
`FCOMPLEX`). Existence alone is not enough — an interrupted mosaic leaves a
short file, and skipping on that carries the damage forward.

Frames are cleaned unevenly, so this matters: of the 781 scripts covering
2025-12-19 … 2026-03-31, 378 frames still had a sound SLC and only 409 across
208 units needed rebuilding.

### Splits

`setup_<orbit>_<burst>` carries the orbit but not the sequence, so a datatake
split into `<orbit>-0` and `<orbit>-4` has scripts for both under one orbit
number. Each script is read for its `set SOURCEDIR=` line and attached to the
unit it actually builds from. The same line supplies `SCRATCH`.

---

## Notes on the steps

**refile.** Prefetched for the next unit while this one assembles (above). The
archive holds a filed granule as `.zip.1`, which `fileS1`
neither globs nor derives a `.SAFE` name from (`os.path.splitext` on
`X.zip.1`). Each is therefore staged as a symlink named `.zip` under
`<queueDir>/.staging/`, and only that symlink is renamed — **the archive is
never modified**. `fileOneZip(..., overwrite=True)` unzips into the directory
the `.SAFE` already sits in, which is what keeps a `checkFramesS1` split
intact, and clears the stale `<date>*par` files.

**assemble.** Classic `setupTrack`, not `--queue`: `--overWrite` is rejected in
queue mode, and the queue retires an already-processed unit as `outputExists`.
Classic mode also takes **no assembly-tree lock**, so this does not contend
with a nightly `autoupdateS1` running on another host. `--overWrite` refuses a
unit whose TIFFs are absent, so the refile step is the gate.

**assemble.** Run `--quiet`: over hundreds of units the Gamma chatter buries
the progress, and setupTrack captures it all to its own
`log.<track>.<pid>.<date>` in the track dir regardless. (Its spinner writes to
stdout without checking for a tty, so a redirected log collects a long run of
`| / - \`; harmless, and invisible in a terminal.)

Each unit gets **its own scratch directory** under `scratch`, removed whatever
the outcome. `catMultipleTops` writes its intermediate `SLC_tab_<time>` into
the scratch *base* and only cleans up the SLCs, so a shared scratch collects
one tab file per unit; worse, an assemble that fails leaves its intermediate
SLCs there — GBs of tmpfs, not bytes, which is the likely origin of any large
stale `/dev/shm/<user>/scratch`. Owning the directory clears both without
deleting by pattern, which two runs sharing a scratch could not do safely.

**merge.** The generated scripts check nothing, so their exit code is only the
last command's; success is judged by the built SLC instead. Their Gamma
per-burst chatter (~7000 lines a unit — 1.3M over a full queue) is captured to
`<procDir>/log.<orbit>_<burst>.<pid>.<YYYYMMDD>` rather than dropped: the
script itself says nothing on a failure, so that log is the only account of
what went wrong, and the last 15 lines are printed when a frame does not
build.

Two directories are cleared before each run:

- `$SCRATCH/<orbit>_<burst>` from a crashed earlier run — the script's own
  `mkdir` does not check, and would build on top of it;
- `<orbit>_<burst>/iw` from the previous build — the script's merge-back is
  `mv <new>/* <existing>_save`, and `mv` will not merge a directory into a
  non-empty one of the same name. Left in place it fails with *inter-device
  move failed … Directory not empty*, keeps the **old** `iw/` pars and
  discards the ones just made, and says nothing that stops the run. The script
  repopulates `iw/` itself, so clearing it is what makes the merge complete.

Re-running over an existing `<orbit>_<burst>` is otherwise safe — the script
moves it aside and merges the new files back in, which is what preserves the
offsets and interferograms already in that directory.

**clean.** `stripMeasurementTiffs` plus `<orbit>-<seq>/*.slc` (the `.slc.par`,
`.tops_par` and `.btimes` stay). Skip it with `--noClean`, but budget ~42 GB a
unit.

A unit that **fails** keeps its TIFFs, so a retry after the fix does not have
to unzip again. Promote it back with `queueS1 promote <unit>` once the cause is
dealt with.

---

## Running it

Gamma must be on `PATH` — `run` checks for `SLC_mosaic_S1_TOPS` and refuses to
start without it, since a non-interactive wrapper does not inherit the login
environment. Start small:

```
reprocessS1 toProcess.yaml --maxOrbits 1
reprocessS1 . --info
```

---

## Related

- [`setupTrack`](setupTrack.md) — the assemble step
- [`setupSeveralTopsImages`](setupSeveralTopsImages.md) — writes the `setup_` scripts
- [`autoupdateS1`](../../asfSearchAndDownload/Documents/autoupdateS1.md) — the nightly this deliberately does not share state with
- [`fileS1`](../../asfSearchAndDownload/Documents/fileS1.md) — the refile step
