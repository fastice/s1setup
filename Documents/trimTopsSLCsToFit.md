# trimTopsSLCsToFit

Trims Sentinel-1 TOPS SLC frames so their burst coverage fits within the track's defined frame range. Requires the [Gamma SAR processor](https://www.gamma-rs.ch/software) (`SLC_copy_S1_TOPS`).

Run from within the orbit source directory (e.g., `<track>/<orbit>/`).

---

## Usage

```
trimTopsSLCsToFit
```

No command-line arguments. The program reads configuration from files in the directory tree:

| File | Location | Description |
|------|----------|-------------|
| `frameRange` | `../` (track directory) | Two integers: `minFrame maxFrame` |
| `ascendingNodeTime` | Current directory | Ascending node datetime string |
| `SLC_tab_*` | Current directory | Gamma SLC tab files (one per SAFE) |

---

## What it does

For each `SLC_tab_*` file (matched to a `.SAFE`), `trimTopsSLCsToFit` computes burst-to-frame numbers using the ascending node time and the `BURST_PERIOD` (2.759 s). It then:

- **Early trim**: if the frame starts before `minFrame` but overlaps it, strips leading bursts so coverage begins at `minFrame`
- **Late trim**: if the frame extends past `maxFrame`, strips trailing bursts so coverage ends at `maxFrame`
- Frames fully within range are left unchanged

Trimming calls `SLC_copy_S1_TOPS` with a `burstTab` file specifying the first/last burst to keep. The trimmed files replace the originals in-place (originals may be on `/dev/shm`).

Intermediate files written: `burstTab`, `burstTabE` (early trim copy), `burstTabL` (late trim copy), `SLCtabnew`.

---

## Dependencies

- `SLC_copy_S1_TOPS` — must be on PATH (part of the Gamma SAR processor)

---

## Part of the s1setup package.
