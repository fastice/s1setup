# cloneSLCdir

Clones a Sentinel-1 SLC directory for a specific sensor and date range by copying small metadata files and symlinking large SLC files. Creates a lightweight duplicate that shares the original binary data.

---

## Usage

```
cloneSLCdir [options] track sensor
```

| Argument | Description |
|----------|-------------|
| `track` | Track directory name (must contain `track` and `-`, e.g., `track-90`) |
| `sensor` | Sensor to clone: `S1A`, `S1B`, `S1C`, or `S1D` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--sourcePath DIR` | `Sentinel1` | Path to the source directory to clone from (relative paths are resolved against the parent of CWD) |
| `--firstdate YYYY-MM-DD` | `2015-01-01` | Only clone acquisitions on or after this date |
| `--lastdate YYYY-MM-DD` | `2050-01-01` | Only clone acquisitions on or before this date |
| `--check` | False | Dry run — show what would be done without making changes |
| `--noSkipIfExists` | False | Re-clone even if the destination already exists |
| `--maxDays N` | no limit | Clone a pair start only if its next same-sensor acquisition of the frame is within N days (it is still cloned as the second image of a start within N days, and the newest acquisition is still cloned to await its partner) |
| `--requireSlc` | False | Only clone acquisitions whose prime `<orbit>_<frame>.slc` exists and is not empty; a clone links it, so without it the clone could never be paired |

---

## What it does

For each acquisition directory in `<sourcePath>/<track>/` that matches the sensor and date range (determined from `geodat10x2.geojson`), **and that a clone pair actually needs**:

- a **pair start** -- the prime does not already pair it with the same sensor (its `runboth` is absent or names another sensor's orbit), so the clone will make the same-sensor 12-day pair; or
- a **second image** -- the previous same-sensor acquisition of the frame is a start whose clone pair has not been processed yet.

Acquisitions the prime already pairs same-sensor are skipped (the count is printed); they would only ever be copies whose `runboth` [cullSLCclones](cullSLCclones.md) deletes.

It then:

- **Copies** (small files): `.par`, `Sentinel-IW.par`, `.cw`, `geo*`, `.thetac`, `betaNought`
- **Symlinks** (large files): `.slc`, `P*.10x2.pow`

Also symlinks `<track>/velocityStats` → `<sourcePath>/<track>/velocityStats` if it doesn't already exist, creates `<track>/tiepoints/tie_plan_header` from the prime's copy with the project root rewritten (maketies dies without it, even on a track with no pairs), and syncs the `vel_thumb_header_<range>` grid headers from the prime (`setupS1Tracks.syncThumbHeadersFromPrime`).

---

## Examples

Clone S1A acquisitions from 2023 into `track-90/`:
```
cloneSLCdir track-90 S1A --firstdate 2023-01-01 --lastdate 2023-12-31
```

Dry run to check what would be cloned:
```
cloneSLCdir track-90 S1C --check
```

---

## Part of the s1setup package.
