# updateS1State

Updates a Gamma SLC parameter file with precise orbital state vectors by locating the matching Sentinel-1 OPOD `.EOF` file for the acquisition date and calling Gamma's `S1_OPOD_vec`. Searches the OPOD archive at `/Volumes/insar9/ian/Data/SentinelGreenland/OPOD/` using the day before and after the acquisition date. Replaces an earlier shell script that failed on month boundaries.

---

## Usage

```
updateS1State.py parfile [sensor] [year month day]
```

| Argument | Description |
|----------|-------------|
| `parfile` | Gamma SLC parameter file to update |
| `sensor` | Optional: `S1A`, `S1B`, or `S1C`; auto-detected from `parfile` if omitted |
| `year month day` | Optional: override date (three integers); parsed from `parfile` if omitted |

---

## What it does

1. Reads sensor (`S1A`/`S1B`/`S1C`) from `parfile` (or command line)
2. Reads acquisition date from `parfile` (or command line)
3. Constructs the OPOD filename pattern using the day before and after the acquisition
4. Locates the matching `.EOF` file in the OPOD archive
5. Calls `S1_OPOD_vec parfile <opod_file>` to update the state vectors

Creates a `StateError.*` touch file if the sensor cannot be identified or the OPOD file cannot be found.

---

## Part of the s1setup package.
