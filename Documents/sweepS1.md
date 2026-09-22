# sweepS1

Sweeps a Sentinel-1 project for frames that need attention and writes a
Markdown report. It catches the problems that otherwise only surface as a
failed nightly, or not at all — a swallowed thread exception, or a stage that
never ran.

```
sweepS1 [--project DIR] [--tracks track-N ...] [--firstDate YYYY-MM-DD]
        [--lastDate YYYY-MM-DD] [--sigThresh S] [--out FILE] [--quiet]
```

| Option | Default | Meaning |
|---|---|---|
| `--project DIR` | `.` | project root (a prime such as `Sentinel1`, or a secondary such as `Sentinel1-S1D`) |
| `--tracks` | all `track-*` | restrict to these track directories |
| `--firstDate` | `2026-01-01` | start of the acquisition-date window |
| `--lastDate` | today | end of the window |
| `--sigThresh S` | `10` | az.est sigma (m) above which a frame is `largeSigma` |
| `--out FILE` | `<project>/logs/sweep-<date>.md` | report path |
| `--quiet` | off | no per-track progress lines |

Each project is swept on its own, so a full check means one run per prime and
per secondary (`Sentinel1`, `-S1A`, `-S1C`, `-S1D` on each volume).

## What it reports

| issue | meaning |
|---|---|
| `untied` | offsets built but no `*.pairinfo`: the pair was processed but the tie stage never ran, so there is no fit, velocity or tie for it |
| `excludePending` | `Exclude.pending` marker — the baseline check flagged a low azimuth tiepoint count and is waiting on an operator decision |
| `noSolution` | az.est sigma < 0, the "no solution" sentinel |
| `largeSigma` | az.est sigma above `--sigThresh` |
| `missingFit` | no `motion/az.est.yaml` at all |
| `missingFiles` | an expected product is absent (velocity, velocity_nocull, geodat, baseline) |
| `gridMismatch` | `velocity_nocull` is on a different grid from the velocityStats reference for its frame range — the failure that makes autoclean raise inside a worker thread, where the exception is swallowed and the run still exits 0 |

A hard `Exclude` marker is counted but not treated as a fault.

## How frames are selected

A frame's dates come from its `*.pairinfo` (`orbit1 orbit2 date1 date2 nDays
looks`), so the window is the real acquisition span rather than file mtimes.
A frame whose pair overlaps the window is checked.

`*.pairinfo` is written by `setuptopstie` the first time the tie stage runs.
So a frame with no pairinfo is either an image that is not a pair start (the
second image of a pair, or the newest acquisition) — skipped — or a pair whose
offsets exist but which was never tied — reported as `untied`, dated from the
ESA product name on its `slc.par` title line.

Before `untied` was added (2026-09-22), such frames were skipped silently. All
63 processed pairs in insar10 `Sentinel1-S1D` sat in that state for nine days,
after its clone tracks were created without `tiepoints/tie_plan_header` and the
following `--fullUpdate` built no tie plans, and every sweep reported the
project clean.

---

## Part of the s1setup package.
