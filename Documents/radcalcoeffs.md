# radcalcoeffs

Computes the radiometric calibration coefficient (beta nought) for a Sentinel-1 image product by reading the calibration XML files from the SAFE annotation directories. Writes the result to `imageDir/betaNought`. Called by `setupTrack` as the final step of the preprocessing pipeline.

Parses the calibration XML with `s1setup.s1XMLreader.S1XMLreader` (BeautifulSoup + lxml). That
class is a copy of `myDev.S1XMLheader` under a different name: `myDev` is a loose directory in
`~/PycharmProjects`, not an installed package, so it imports only when `PYTHONPATH` happens to
include that directory — true of an interactive shell, not of cron. `myDev` is left untouched for
anything else still importing it.

---

## Usage

```
radcalcoeffs.py [--pol POL] imageDir safeDir
```

| Argument | Description |
|----------|-------------|
| `imageDir` | Output image product directory (must exist) |
| `safeDir` | Directory containing `S1?_IW*.SAFE` subdirectories |

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--pol POL` | `hh` | Polarization to read (`hh`, `hv`, `vv`) |

---

## Output

`imageDir/betaNought` — single line containing the beta nought calibration value.

---

## Part of the s1setup package.
