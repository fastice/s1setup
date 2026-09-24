# setuppairs

Sets up every unprocessed image pair in a track directory for one year. For each frame it runs `grepdate`, which lists the frame's acquisitions in date order with the gap in days to the next acquisition of the same frame. Every acquisition that has no `runboth` yet (`o--` status) and whose gap satisfies 0 < nDays <= the sensor's `maxDays` (36 for S1) is paired with the next acquisition, and `setupSARpair.py` is called to write the pair's `runboth` and `fast/dofast`.

Pairs are always between consecutive acquisitions of a frame, whatever the separation. With S1A/S1C/S1D interleaved, this produces 1-, 5-, 7- and 13-day pairs in the same run as the usual 6- and 12-day ones; nothing downstream assumes a particular repeat, since `setupSARpair.py`, `setupStrackReg` and `dofast.py` derive the temporal baseline from the geodat dates. Pairs whose second image falls in the following year are found by re-running `grepdate` for that year.

Run it in the track directory (`track-N/`, above the `<orbit>_<frame>` directories). It is idempotent: acquisitions that already have a `runboth` are skipped.

---

## Usage

```
setuppairs [options] year
```

| Argument | Description |
|----------|-------------|
| `year` | Year of the first image of each pair (2008–2040) |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--sensor NAME` | auto | Sensor (`S1`, `TSX`, `CSK`); auto-detected from `Sentinel`, `TSX` or `CSK` in the working path |
| `--region NAME` | auto | Region passed to `setupSARpair.py` (`greenland`, `antarctica`, `taku`); auto-detected from the geodat hemisphere when omitted |
| `--frame N` | all | Restrict to one frame number |
| `--tiff` | False | Pass `--tiff` through to every generated `runboth`/`dofast` |
| `--check` | False | Dry run — print each pair and the `setupSARpair.py` command that would run, without running it |
| `--maxDays N` | sensor `maxDays` | Longest pair to set up; can only lower the sensor limit. `prepareS1Pairs` passes 12 in the same-sensor secondaries |

---

## Example

```
cd /Volumes/insar9/ian/Sentinel1/track-10
setuppairs --check --frame 674 2026     # see what would be set up
setuppairs --frame 674 2026             # set it up
```

Sample `--check` output for a frame with interleaved S1C/S1D/S1A acquisitions:

```
1d pair
o--   1d   7356_674  24-APR-2026  +  114
 o--   5d   2501_674  25-APR-2026  +  115
[check] setupSARpair.py  --frame 674 7356 2501 S1
5d pair
...
```

---

## Notes

- `grepdate` (mosaicworkflow) and `setupSARpair.py` (`insarScripts/bin`) must be on the PATH.
- The status column is `grepdate`'s: `o--` not set up, `.--` `runboth` written but not run, `x..` offsets exist.
- Each generated `runboth` calls [setupStrackReg](setupStrackReg.md) twice (registration, then `--strackOffsets`) with `setupInt.py` between them, then `csh dofast`.

---

## Part of the s1setup package.
