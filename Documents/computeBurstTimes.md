# computeBurstTimes

Computes burst times and frame numbers from Gamma `.tops_par` files in the current directory. For each `.tops_par` file, reads `burst_date` fields, computes elapsed seconds from the ascending node time, and assigns frame numbers via a linear fit (more robust than direct rounding near half-period boundaries). Writes a `.btimes` file per `.tops_par`, touches `missing.N` if a burst gap > 3 s is detected, and touches `frames.FIRST.LAST` after the first file. Called by `setupTrack` and `catMultipleTops`.

Run from within the merged orbit output directory (which must contain `ascendingNodeTime`).

---

## Usage

```
computeBurstTimes.py
```

No arguments. Operates on `*.tops_par` files in the current directory.

---

## Output

| File | Content |
|------|---------|
| `<name>.btimes` | One line per burst: `burstIndex timeSec frameNumber` |
| `missing.N` | Touch file indicating a gap before burst N |
| `frames.FIRST.LAST` | Touch file recording first and last frame numbers |

---

## Part of the s1setup package.
