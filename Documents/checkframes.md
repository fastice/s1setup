# checkframes

Checks Sentinel-1 orbit directories for frame/burst coverage gaps, early or late frames, and optionally moves or splits problematic directories.

Run from the directory containing the numbered orbit subdirectories (e.g., `12345/`, `12345_2/`).

---

## Usage

```
checkframes [options]
```

No positional arguments. The program scans the current directory for orbit subdirectories (4–5 digit names, optionally suffixed `_N`), reads the `.SAFE` archives inside each, and reports burst-based frame coverage.

---

## Options

| Option | Description |
|--------|-------------|
| `-halt` | Stop on the first error or inconsistency instead of warning and continuing |
| `-ignoreIncomplete` | Suppress warnings for frames that do not span the full frame range |
| `-firstdate YYYY:MM:DD` | Only check orbits on or after this date |
| `-lastdate YYYY:MM:DD` | Only check orbits on or before this date |
| `-mvoutofrange` | Move early or late `.SAFE` directories to a `tmp/` subdirectory |
| `-breakGap N` | If a gap is found, move the first N SAFEs into a new `<orbit>_4` (etc.) directory |
| `-breakLong N` | If an orbit has more than 127 frames, move the first N SAFEs into `<orbit>_3` |
| `-ignoreLong` | Suppress the "too many frames" warning for slightly over-length orbits |

---

## Frame range file

By default the valid burst range is `[300, 750]`. To override, create a file named `frameRange` in the current directory with two space-separated integers:

```
450 680
```

---

## Ascending node time

Burst numbers are computed from each `.SAFE`'s start/stop time relative to the ascending node time. On first run for each orbit directory, `checkframes` extracts the ascending node time from the first IW1 annotation XML and caches it in `<orbit>/ascendingNodeTime`.

---

## Examples

Basic check (warn but don't halt):
```
checkframes
```

Stop on first problem:
```
checkframes -halt
```

Check only orbits in a date range:
```
checkframes -firstdate 2024:01:01 -lastdate 2024:06:30
```

Move out-of-range frames to `tmp/`:
```
checkframes -mvoutofrange
```

Split oversized orbits (move first 60 SAFEs to `<orbit>_3`):
```
checkframes -breakLong 60
```

---

## Part of the s1setup package.
