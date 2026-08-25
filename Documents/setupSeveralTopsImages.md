# setupSeveralTopsImages

Creates `setup_orbit_frame` scripts for each orbit/frame combination found in the current directory. Reads framing from two control files (`frames` and `orbitframes`), checks burst time coverage, and calls the Gamma binary `setuptopsimage` to produce each setup script. Collects the scripts into a `runSetup` file for batch execution — written only when there is
something to run, so a run that finds the track already up to date leaves no empty driver behind.

Run from the directory containing the `frames`, `orbitframes`, `ascending`/`descending`, and orbit source directories (`NNNNN-N`).

---

## Usage

```
setupSeveralTopsImages.py [options]
```

No positional arguments. Scans for `NNNNN-N` directories automatically.

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-check` | False | Dry run: print what would be processed without creating setup files |
| `-cleanStale` | False | Remove what this run has superseded: setup scripts for pieces never built (no `<orbit>_<firstBurst>` directory), among the orbits the run covered and not just written, plus any earlier `runSetup.*` whose every entry is either written again now or already built. Scripts for pieces already built, pieces outside the dates the run covered, and run files still listing unbuilt work this run does not cover, are all left alone |
| `-firstdate YYYY:MM:DD` | ~50 years ago | Only process orbits with ascending node time on or after this date |
| `-lastdate YYYY:MM:DD` | 2100:01:01 | Only process orbits with ascending node time on or before this date |
| `-help` | — | Print usage and exit |

---

## Control files

**`frames`** — one line per frame: `frame-FirstBurst-NumberOfBursts`

**`orbitframes`** — per-orbit overrides:
- `orbit-FirstBurst-NumberBursts` — single-frame override
- `orbit-FrameIndex-FirstBurst-NumberBursts` — multi-frame override (higher `FrameIndex` adds an extra frame)
- Lines beginning with `#` are comments; set `NumberBursts` to 0 to skip an orbit/frame

**`ascending` / `descending`** — empty file whose name flags the pass direction

---

## Output

- `setup_orbit_frame` — executable Gamma setup script for each orbit/frame pair
- `runSetup.Mon:DD:YY:HH:MM:SS` — shell script listing all setup files to run; not written at all
  where the run made no setup scripts

## Directories that cannot be dated

A directory with no readable `ascendingNodeTime` cannot be placed in time, so there is no telling
whether it even falls in the dates the run covers. Whether that matters depends on the orbit:

- **an orbit the run is setting up** — a piece may have to be cut from any of its sequences, so an
  undated one is an **error**, as it always was;
- **any other orbit** — reported and passed over. A partial assembly leaves such directories lying
  about for years (track-156 has `6277-0` and `6277-4` with no `ascendingNodeTime` at all), and
  failing a whole track over one that nothing is being proposed for helps nobody. `segmentTrack`
  passes over the same directories when it collects acquisitions, so this keeps the two agreeing.

The decision is made after the main loop, once the orbits actually set up are known, and the
skipped directories come back in `noAscNode`.

The time is read with or without fractional seconds. It is normally written with them, but a
reader that insisted on them turned a perfectly good file into a failed track.

---

## Called from segmentTrack

`segmentTrack` runs this program itself, over the framing it has proposed rather than the one on
disk (see [segmentTrack](segmentTrack.md#the-setup-step)). Two things differ from a standalone run:

- **Framing from memory.** `setupSeveralImages(frames=..., orbframes=...)` takes the two lists
  instead of reading `frames` and `orbitframes`, so a proposal can be checked before it is
  committed.
- **Stale directories skipped.** `requireSLCs=True` skips any `<orbit>-<seq>` whose swath SLCs
  have been cleaned away — the metadata all stays behind, so it still frames, but the setup would
  build nothing. Off by default, so a command-line run behaves as it always has.
- **Quiet.** `quiet=True` leaves out the `Skipping <dir> Already exists` line per piece; on a
  track where nearly everything is built those lines are the whole output, and the count is in the
  summary segmentTrack prints instead.
- **Failures are collected, not fatal.** Run standalone this program stops at the first bad frame
  — `u.myerror` or the run-file cleanup path — and everything behind it stays hidden. Called with
  `strict=False` each failure becomes a `SetupError` in the returned `errors` list, the frame or
  directory it affects is skipped and the rest of the track is processed. The caller decides what
  to do; segmentTrack reports them and writes nothing.

Command-line behaviour is unchanged: `main()` runs with `strict=True` and reports and exits exactly
as it always has.

---

## Part of the s1setup package.
