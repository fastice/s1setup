# findgain

Extracts absolute calibration gain values from Sentinel-1 SAFE annotation XML files. Reads the `absoluteCalibrationConstant` field from each sub-swath (IW1, IW2, IW3) across all SAFE directories and writes the median per-swath values to `sourceDir/absolutegain`. Called by `setupTrack` as the first step of the preprocessing pipeline.

Run from any directory; pass the orbit source directory as the argument.

---

## Usage

```
findgain.py sourceDir
```

| Argument | Description |
|----------|-------------|
| `sourceDir` | Orbit directory containing `*.SAFE` subdirectories |

---

## Output

`sourceDir/absolutegain` — three space-separated median calibration constants (IW1, IW2, IW3).

---

## Part of the s1setup package.
