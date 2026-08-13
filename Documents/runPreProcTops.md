# runPreProcTops

Unpacks Sentinel-1 TOPS SAFE directories in an orbit directory into per-beam SLC files, extracts the ascending node time, and updates state vectors. Calls Gamma's `S1_TOPS_preproc` to unpack each SAFE, then calls `updateS1State.py` on each output SLC parameter file, and writes `SLC_tab_*` files for downstream processing. Called by `setupTrack` as the second step of the preprocessing pipeline.

Run from any directory; pass the orbit directory as the argument.

---

## Usage

```
runPreProcTops.py orbitDir [--scratch DIR]
```

| Argument | Description |
|----------|-------------|
| `orbitDir` | Orbit directory (`NNNNN` or `NNNNN_SEQ`) containing SAFE directories |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--scratch DIR` | orbit dir | Write SLC output files to this directory instead of the orbit dir (e.g. `/dev/shm/user/scratch`) |

---

## What it does

1. Builds `inputSAFE` file listing all `*.SAFE` directories
2. Extracts ascending node time from the first SAFE's IW1 annotation XML → `ascendingNodeTime`
3. Runs `S1_TOPS_preproc` to unpack all SAFEs into per-beam SLC files
4. For each output SLC: calls `updateS1State.py` to update Gamma state vectors, writes a `SLC_tab_*` file

---

## Part of the s1setup package.
