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

**setupTrack** orchestrates the 5-step Sentinel-1 preprocessing pipeline (findgain → runPreProcTops → trimTopsSLCsToFit → catMultipleTops → radcalcoeffs) for all unprocessed orbits under a track directory.

**setupSeveralTopsImages** creates `setup_orbit_frame` scripts for each orbit/frame combination by reading `frames` and `orbitframes` files and calling the Gamma binary `setuptopsimage`.

**setupStrackReg** runs the Sentinel-1 speckle-tracking registration pipeline for an image pair: coarse registration, simulated offsets, fine registration via `strack`, and culling. Can also run full speckle tracking for high-resolution offsets.

**updateS1State** updates Gamma SLC parameter files with precise state vectors by locating the matching OPOD `.EOF` file for the acquisition date and calling Gamma's `S1_OPOD_vec`.

**computeBurstTimes** computes burst times and frame numbers from Gamma `.tops_par` files in the current directory, writing `.btimes` files and touch files for missing bursts and frame ranges.

**findgain** extracts absolute calibration gain values from Sentinel-1 SAFE annotation XML files and writes the median per-swath values to `absolutegain`.

**radcalcoeffs** computes the radiometric calibration coefficient (beta nought) by reading the calibration XML files from the SAFE annotation directories and writes the result to `betaNought`.

**runPreProcTops** unpacks Sentinel-1 TOPS SAFE directories into per-beam SLC files, extracts the ascending node time, and updates state vectors via `updateS1State`.

## Documentation

- [catMultipleTops](Documents/catMultipleTops.md) — concatenate multiple TOPS SLC frames into one (requires Gamma)
- [checkframes](Documents/checkframes.md) — check Sentinel-1 orbit directories for frame coverage and gaps
- [cloneSLCdir](Documents/cloneSLCdir.md) — clone SLC directory by copying metadata and symlinking large files
- [computeBurstTimes](Documents/computeBurstTimes.md) — compute burst times and frame numbers from .tops_par files
- [cullSLCclones](Documents/cullSLCclones.md) — remove duplicate runboth files from cloned SLC directories
- [findgain](Documents/findgain.md) — extract absolute calibration gain from SAFE annotation XML files
- [radcalcoeffs](Documents/radcalcoeffs.md) — compute beta nought calibration coefficient from SAFE calibration XML
- [runPreProcTops](Documents/runPreProcTops.md) — unpack TOPS SAFE directories into per-beam SLC files (requires Gamma)
- [setupSeveralTopsImages](Documents/setupSeveralTopsImages.md) — create setup scripts for each orbit/frame combination (requires Gamma)
- [setupStrackReg](Documents/setupStrackReg.md) — run speckle-tracking registration and offset pipeline for an image pair
- [setupTrack](Documents/setupTrack.md) — orchestrate the 5-step Sentinel-1 preprocessing pipeline
- [trimTopsSLCsToFit](Documents/trimTopsSLCsToFit.md) — trim TOPS SLC frames to fit the track frame range (requires Gamma)
- [updateS1State](Documents/updateS1State.md) — update Gamma SLC parameter files with precise OPOD state vectors

## For Further Information

Please address questions to ![](https://github.com/fastice/GrIMPTools/blob/main/Email.png).
