# prepareS1Pairs

Takes newly assembled Sentinel-1 data through to a list of runboths, in one
command. Run it from a project top dir — the one holding `project.yaml` and the
`track-N` directories, e.g. `/Volumes/insar9/ian/Sentinel1`.

Each step used to be run by hand. The per-track routine was three passes of
`segmentTrack`, then the `runSetup.*` scripts under csh, then `setuppairs`.
Each secondary needed its `updateClones` script, and the runboth list for
parallel_boss was written by hand.

---

## Usage

```
prepareS1Pairs [--tracks track-N ...] [--year YYYY ...] [--notifyEmail ADDR]
               [--noSecondary] [--cloneFirstDate YYYY-MM-DD]
               [--secondaryMaxDays N] [--out FILE] [--check]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tracks` | every `track-*` | Only these track dirs |
| `--year` | this year | Years passed to `setuppairs <year>`; several are allowed. `setuppairs` already pairs a year's last acquisition with the next year's first. |
| `--notifyEmail ADDR` | `project.yaml` `notifyEmail` | Where to mail the report if segmenting fails. If neither is set, nothing is mailed. |
| `--noSecondary` | off | Skip the secondaries step |
| `--cloneFirstDate YYYY-MM-DD` | six months ago | Secondaries: clone and cull acquisitions from this date |
| `--secondaryMaxDays N` | 12 | Secondaries: longest same-sensor pair to clone or set up |
| `--out FILE` | `newRunboth-<stamp>.pyboss` here | Runboth list to write |
| `--check` | off | Dry run: every step reports what it would do; nothing is written except the log, and no mail is sent |

---

## What it does

Everything is **serial**. The setup scripts stage through `/dev/shm` and are
memory-heavy, so they run one at a time across all tracks, as `reprocessS1`
runs them.

| step | where | runs |
|---|---|---|
| 1 segment | each track dir | `segmentTrack.py -refreshLinks -commit` |
| 2 build SLCs | each track dir | each `setup_<orbit>_<burst>` listed in the `runSetup.<stamp>` step 1 wrote |
| 3 pairs | each track dir | `setuppairs.py <year>` |
| 4 secondaries | each `secondaryDirectories` entry | `cloneSLCdir.py --requireSlc --maxDays 12` → `setuppairs.py --maxDays 12 <year>` → `cullSLCclones.py --noRemoveRedundant`, over the last six months |
| 5 runboth list | top dir | every runboth created by this run that has not been run, one `cd <dir> ; runboth` line each |

**Step 1.** A track whose segmenting fails writes nothing and is skipped by the
later steps. This includes the unattended stop on missing bursts, where
`segmentTrack` would otherwise ask at the terminal and, with no one there,
answers no. If any track failed, the report is mailed as soon as segmenting is
over; the failed tracks then need resolving by hand.

**Step 2.** Each setup script is run by `reprocessS1.runSetupScript`, the same
code `reprocessS1` uses. It first clears a stale `/dev/shm/<orbit>_<burst>`
staging dir and the frame's old `iw/`, since both break the script's own merge.
It then runs the script under `/bin/csh` with a per-script log. A frame counts
as built only if `<orbit>_<burst>.slc` exists at the full size its `.par`
implies (`frameSlcOk`), because the scripts never check their own steps and
their exit status is just that of the last command. A frame that is already
built is skipped. A failed frame is reported, and the track's other frames
carry on.

**Step 4.** The order is the one the secondaries' own `updateClones` scripts
use. `cloneSLCdir` reads the prime's runboths to decide what needs a clone, so
it runs after the prime's `setuppairs`. `cullSLCclones` removes the duplicate
runboths `setuppairs` has just written, so it runs last. `--noRemoveRedundant`
keeps every clone directory; to remove the redundant ones, run `cullSLCclones`
without it.

The secondaries exist for **12-day same-sensor pairs**, so step 4 is limited to
those (`--secondaryMaxDays`), to the last six months (`--cloneFirstDate`), and
to acquisitions whose prime SLC still exists. The first run, before these limits,
cloned from Jan 1 and backfilled 36-day S1C pairs across the Feb – May gaps; those
were dropped. Longer secondary pairs made before the limits are left as they are.

The secondaries are found exactly as `setupS1Tracks` finds them,
from `project.yaml` `secondaryDirectories`. Only tracks that exist in a
secondary are processed there, and the sensor is taken from the directory name
(`Sentinel1-S1D` → `S1D`).

**Step 5.** A runboth is new if it did not exist when the run started, still
exists (the cull may have removed it), and has no `azimuth.offsets*` yet.
Prime lines come first, then each secondary's, all as absolute paths:

```
cd /Volumes/insar9/ian/Sentinel1/track-90/4156_405 ; runboth
cd /Volumes/insar9/ian/Sentinel1-S1D/track-90/4156_405 ; runboth
```

This is the format `pboss.py -c <file>` queues, one task per line.

---

## Logs and report

- `logs/prepareS1Pairs_<stamp>.log`: the run log, also echoed to the terminal.
- `logs/prepareS1Pairs_<stamp>/`: every command's full output, for example
  `track-90.segment.log`, `track-90.pairs2026.log` and
  `Sentinel1-S1D.track-90.clone.log`.
- `logs/prepareS1Pairs_<stamp>.summary`: per step and per track, the status and
  counts, plus the path of the runboth list. It is also the mail body.

The exit status is 1 if any step recorded an error, otherwise 0.

---

## Notes

- **`--check` only sees data that is already linked into the track.** Linking
  newly assembled units is `segmentTrack -refreshLinks`, which writes and has
  no dry-run form, so a dry run cannot see data assembled since the last real
  run. `segmentTrack` also writes no `runSetup` in a dry run, so step 2 has
  nothing to list.
- `setuppairs` exits 0 even when it fails internally. The authoritative result
  is the runboth list, not the step's exit code.
- Mail goes through the local MTA (`mail`, then `mailx`), using the same code
  as `autoupdateS1`, and a mail failure never stops the run.

---

## Part of the s1setup package.
