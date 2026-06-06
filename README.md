# s1setup

Code for Sentinel-1 preprocessing (some requires the [Gamma SAR processor](https://www.gamma-rs.ch/software)).

**checkframes** scans the current directory for Sentinel-1 orbit subdirectories and checks each for burst-based frame coverage, gaps between SAFEs, and early/late frames outside the defined frame range. Can optionally move or split problematic directories.

## Installation

```bash
pip install git+https://github.com/fastice/s1setup.git@main
```

**trimTopsSLCsToFit** trims Sentinel-1 TOPS SLC frames to fit within the track's burst/frame range by calling Gamma's `SLC_copy_S1_TOPS`. Run from within the orbit source directory.

## Documentation

- [checkframes](Documents/checkframes.md) — check Sentinel-1 orbit directories for frame coverage and gaps
- [trimTopsSLCsToFit](Documents/trimTopsSLCsToFit.md) — trim TOPS SLC frames to fit the track frame range (requires Gamma)

## For Further Information

Please address questions to ![](https://github.com/fastice/GrIMPTools/blob/main/Email.png).
