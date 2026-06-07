# s1setup

Code for Sentinel-1 preprocessing (some requires the [Gamma SAR processor](https://www.gamma-rs.ch/software)).

**catMultipleTops** concatenates multiple Sentinel-1 TOPS SLC frames into a single merged SLC using Gamma's `SLC_cat_S1_TOPS`, merging pairwise in successive rounds then moving the result to a destination directory.

**cloneSLCdir** clones a Sentinel-1 SLC directory for a specific sensor and date range, copying small metadata files and symlinking large SLC files to avoid data duplication.

**cullSLCclones** removes duplicate `runboth` files from cloned Sentinel-1 SLC directories by comparing `setupStrackReg.py` frame parameters between clone and original. for a specific sensor and date range, copying small metadata files and symlinking large SLC files to avoid data duplication.

**checkframes** scans the current directory for Sentinel-1 orbit subdirectories and checks each for burst-based frame coverage, gaps between SAFEs, and early/late frames outside the defined frame range. Can optionally move or split problematic directories.

## Installation

```bash
pip install git+https://github.com/fastice/s1setup.git@main
```

**trimTopsSLCsToFit** trims Sentinel-1 TOPS SLC frames to fit within the track's burst/frame range by calling Gamma's `SLC_copy_S1_TOPS`. Run from within the orbit source directory.

## Documentation

- [catMultipleTops](Documents/catMultipleTops.md) — concatenate multiple TOPS SLC frames into one (requires Gamma)
- [checkframes](Documents/checkframes.md) — check Sentinel-1 orbit directories for frame coverage and gaps
- [cloneSLCdir](Documents/cloneSLCdir.md) — clone SLC directory by copying metadata and symlinking large files
- [cullSLCclones](Documents/cullSLCclones.md) — remove duplicate runboth files from cloned SLC directories
- [trimTopsSLCsToFit](Documents/trimTopsSLCsToFit.md) — trim TOPS SLC frames to fit the track frame range (requires Gamma)

## For Further Information

Please address questions to ![](https://github.com/fastice/GrIMPTools/blob/main/Email.png).
