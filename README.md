# s1setup

Code for Sentinel-1 preprocessing (some requires the [Gamma SAR processor](https://www.gamma-rs.ch/software)).

**checkframes** scans the current directory for Sentinel-1 orbit subdirectories and checks each for burst-based frame coverage, gaps between SAFEs, and early/late frames outside the defined frame range. Can optionally move or split problematic directories.

## Installation

```bash
pip install git+https://github.com/fastice/s1setup.git@main
```

## Documentation

- [checkframes](Documents/checkframes.md) — check Sentinel-1 orbit directories for frame coverage and gaps

## For Further Information

Please address questions to ![](https://github.com/fastice/GrIMPTools/blob/main/Email.png).
