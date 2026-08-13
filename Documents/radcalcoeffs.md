# radcalcoeffs

Computes the radiometric calibration coefficient (beta nought) for a Sentinel-1 image product by reading the calibration XML files from the SAFE annotation directories. Writes the result to `imageDir/betaNought`. Called by `setupTrack` as the final step of the preprocessing pipeline.

Requires the `myDev` module (`ss.S1XMLheader`) to parse the calibration XML.

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
