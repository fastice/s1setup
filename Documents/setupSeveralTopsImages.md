# setupSeveralTopsImages

Creates `setup_orbit_frame` scripts for each orbit/frame combination found in the current directory. Reads framing from two control files (`frames` and `orbitframes`), checks burst time coverage, and calls the Gamma binary `setuptopsimage` to produce each setup script. Collects the scripts into a `runSetup` file for batch execution.

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
- `runSetup.Mon:DD:YY:HH:MM:SS` — shell script listing all setup files to run

---

## Part of the s1setup package.
