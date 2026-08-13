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
| `--queue` | False | Take units from `toProcess.yaml` instead of scanning directories |
| `--assemblyDir PATH` | — | Root holding the `track-<n>/` dirs; **required** with `--queue` |
| `--queueDir PATH` | `--assemblyDir` | Where the queue YAMLs live |
| `--track track-16` | all | With `--queue`, restrict to one track |
| `--maxUnits N` | 0 (all) | With `--queue`, process at most N units |

---

## Queue mode

Instead of scanning a track directory, take the work list from the
`toProcess.yaml` that `checkFramesS1` (asfSearchAndDownload) maintains:

```
setupTrack.py --queue --assemblyDir /Volumes/insar8/ian/Data/SentinelGreenland
setupTrack.py --queue --assemblyDir ... --track track-16 --maxUnits 5
setupTrack.py --queue --assemblyDir ... --check      # report only, writes nothing
```

Queue units are `track-<n>/<orbit>[_seq]` relative to `assemblyDir`, and the
orbit-dir naming is identical to this tool's, so split/gap units (`_1`… `_4`)
work unchanged. A queue spanning several tracks is fine — each track still gets
its own log file.

**The queue is consumed as work finishes**, flushed after each unit (not batched
at the end) so an interrupted run neither loses nor repeats work. A unit is
removed at the *end* of its processing, never claimed up front: at worst a
concurrent run duplicates one unit, which the output-dir check then skips.

| Outcome | Leaves `toProcess` | Added to `problem` |
|---|---|---|
| processed OK | yes | — |
| already `Completed`, or output dir exists | yes | — |
| `Ignore` file present | yes | — |
| outside `--firstdate`/`--lastdate` | **no** — a property of the run, not the unit | — |
| no SAFE files | yes | `no SAFE files under <path>` |
| unit dir missing | yes | `unit dir missing: <path>` |
| processing failed | yes | `setup failed at <step>` |

Problem entries carry a `comment` naming the failing step, so the notification
`autoupdateS1` sends says *why* rather than just "failed". A repeat failure
re-queues the entry, which refreshes the comment and makes it notify again.

### Record of successful runs

Successes are recorded in two files beside the queues (neither is a queue —
they only grow, so they never take part in the queue read-modify-write):

```
processed.<YYYY-MM-DD>.yaml   this run, appended after each unit
completed.yaml                the all-time record, appended at end of run
```

```yaml
- unit: track-90/7086
  date: '2026-04-05'          # acquisition date, from the queue record
  finished: '2026-08-13T11:04:22'
  elapsed: 412                # seconds
  host: helheim
```

The dated file is flushed **per unit**, so an interrupted run keeps everything
that already finished. At the end of the run every dated file is folded into
`completed.yaml` — all of them, not just this run's, so a file orphaned by an
earlier crash is picked up here. Records are keyed on `(unit, finished)`, which
makes re-merging a no-op while keeping a genuine reprocess of the same unit as a
separate entry.

Dated files are then pruned after **5 days** (`queueS1.RETAIN_DAYS`), always
*after* the merge, so one is never deleted before it has been folded in.

`--assemblyDir` is required rather than guessed from the queue file's location:
`queueDir` can differ from `assemblyDir`, and guessing wrong would resolve every
unit under the wrong tree. If more than half the queued units fail to resolve the
run aborts **before writing anything**, on the assumption that `--assemblyDir` is
wrong. `--overWrite` is rejected with `--queue` (it deletes output dirs).

Dates come from the queue record when present, falling back to the orbit's
`ascendingNodeTime` file, so `--firstdate` means the same thing here as in
`checkFramesS1`.

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
