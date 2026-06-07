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
| `sensor` | Sensor to clone: `S1A`, `S1B`, or `S1C` |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--sourcePath DIR` | `Sentinel1` | Path to the source directory to clone from (relative paths are resolved against the parent of CWD) |
| `--firstdate YYYY-MM-DD` | `2015-01-01` | Only clone acquisitions on or after this date |
| `--lastdate YYYY-MM-DD` | `2050-01-01` | Only clone acquisitions on or before this date |
| `--check` | False | Dry run — show what would be done without making changes |
| `--noSkipIfExists` | False | Re-clone even if the destination already exists |

---

## What it does

For each acquisition directory in `<sourcePath>/<track>/` that matches the sensor and date range (determined from `geodat10x2.geojson`):

- **Copies** (small files): `.par`, `Sentinel-IW.par`, `.cw`, `geo*`, `.thetac`, `betaNought`
- **Symlinks** (large files): `.slc`, `P*.10x2.pow`

Also symlinks `<track>/velocityStats` → `<sourcePath>/<track>/velocityStats` if it doesn't already exist.

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
