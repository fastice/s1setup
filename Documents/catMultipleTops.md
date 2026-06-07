# catMultipleTops

Concatenates multiple Sentinel-1 TOPS SLC frames into a single merged SLC using Gamma's `SLC_cat_S1_TOPS`. Runs pairs of frames through successive merge rounds until a single output remains, then moves the result to a destination directory and runs `computeBurstTimes.py`.

Run from within the orbit source directory. Requires the [Gamma SAR processor](https://www.gamma-rs.ch/software).

---

## Usage

```
catMultipleTops [--scratch DIR]
```

No positional arguments. The program finds all `SLC_tab*t*` files in the current directory and merges them.

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--scratch DIR` | `/dev/shm/<user>/scratch` | Base directory for intermediate SLCs during merging |

---

## What it does

1. Reads all `SLC_tab*t*` files and checks total burst count against the Gamma limit (128)
2. Merges frames pairwise in successive rounds via `SLC_cat_S1_TOPS` (pairs run in parallel)
3. Cleans up intermediate SLCs after each round
4. Moves the final merged SLC to `<orbit>-<seq>/` and writes a new `SLC_tab` with updated paths
5. Copies `ascendingNodeTime` and `absolutegain` to the destination
6. Runs `computeBurstTimes.py` in the destination directory

The scratch directory is cleaned up on exit (even on failure).

---

## Dependencies

- `SLC_cat_S1_TOPS`, `computeBurstTimes.py` — must be on PATH (Gamma SAR processor)

---

## Part of the s1setup package.
