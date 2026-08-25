#!/usr/bin/env python3
"""Convert a processed frame directory from raw flat binary to GeoTIFF.

The offsets workflow now runs GeoTIFF-backed by default (setupStrackReg defaults
tiff: True), but every frame directory processed before that switch is still raw
flat binary. This converts one in place so it looks like a directory that was
processed natively with tiff: rasters become GeoTIFF, the tiff-backed VRTs get
written, and the post-processing scripts (runcull/runCullReg<N>/cleanoff/
fast/runFastCull) are regenerated in tiff form.

Directories come in vintages and the strategy depends on which one is found:

  modern  -- a VRT set already exists (range.offsets.vrt present). The VRTs are
             the manifest: every raw band they reference is converted, masks
             included, and each VRT is rewritten in place as tiff-backed.
  legacy  -- no VRTs (the majority of pre-2023 dirs). A product table drives the
             conversion instead, covering the products read downstream plus the
             cull chain; grids come from the .dat sidecars. The simulation
             rasters (offsets.*, offsets.reg.*) are left raw and reported --
             regenerating those is a simoffsets run, not a format conversion.

Takes frame directories directly, or sweeps whole tracks: --tracks track-26
track-74, or --allTracks for every track-* under --project (default: cwd).
A sweep runs --nThreads (4) directories at a time, keeps going past a directory
that fails, and prints a summary at the end. --firstdate/--lastdate restrict it
to frames acquired in a window (default: all time); a frame whose date cannot be
read is skipped rather than converted whenever a window is given.

Originals are deleted, or with --debug moved into a debug/ subdirectory of the
frame directory itself (azimuth.offsets -> <frame>/debug/azimuth.offsets), so the
rollback copy stays with the frame it came from. --dryRun reports and touches
nothing. Already tiff-backed VRTs are skipped, so re-running is a no-op.
"""
import argparse
import glob
import os
import re
import shutil
import threading
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from osgeo import gdal

import utilities as u
import sarfunc as s

# Raw type by trailing name component. Everything not listed is a Float32
# offset/sigma raster. Verified against real dirs by size / (nr*na): .mt and
# .mask are single byte, lat/lon are double.
BYTE_SUFFIXES = ('.mt', '.mask')
DOUBLE_SUFFIXES = ('.lat', '.lon')

GDAL_TYPES = {'u1': gdal.GDT_Byte, '>f4': gdal.GDT_Float32,
              '>f8': gdal.GDT_Float64}
NODATA = {'u1': 0., '>f4': -2.e9, '>f8': -2.e9}

# Serialises the script-regeneration step, which has to chdir (see
# regenerateInPlace). Conversion itself uses absolute paths and runs unlocked.
CHDIR_LOCK = threading.Lock()

# --debug writes the originals into this subdirectory of the frame dir itself,
# so the rollback copy travels with the frame it belongs to.
DEBUG_DIR = 'debug'


def rawDtype(name):
    """numpy dtype string for a raw product, from its trailing component. Raw
    GrIMP binaries are big-endian (freadBS/fwriteBS byteswap unconditionally)."""
    if name.endswith(BYTE_SUFFIXES):
        return 'u1'
    if name.endswith(DOUBLE_SUFFIXES):
        return '>f8'
    return '>f4'


def isPairProduct(name, pair):
    """True if name is a direct strack/cullst output, which names its tiffs by
    band role rather than <source>.tif. Those are exactly the pair-prefixed
    products that are not an interp or merge result -- the Python writers
    (cleanoff, regOffsetsMerge, mergefast, processfast) own those."""
    base = os.path.basename(name)
    if pair is None or not base.startswith(f'{pair}.'):
        return False
    return '.interp' not in base and '.merge' not in base


def tifName(product, description, pair):
    """Native tiff name for a product. strack/cullst strip the source's last
    extension and append .<Description>.tif (deriveTif in writeCullData.c /
    writeVrt.c); everyone else appends .tif to the source name."""
    if isPairProduct(product, pair):
        root = product.rsplit('.', 1)[0]
        return f'{root}.{description}.tif'
    return f'{product}.tif'


def pairPrefix(dirPath):
    """The <orbit1>_<frame>.<orbit2>_<frame> key, read from the strack products
    present. Returns None if the directory has none."""
    for path in sorted(glob.glob(os.path.join(dirPath, '*.offsets.dat'))):
        m = re.match(r'(\d+_\d+\.\d+_\d+)\.offsets\.dat$', os.path.basename(path))
        if m:
            return m.group(1)
    for path in sorted(glob.glob(os.path.join(dirPath, '*.offsets.d?'))):
        m = re.match(r'(\d+_\d+\.\d+_\d+)\.offsets\.d[ra]$', os.path.basename(path))
        if m:
            return m.group(1)
    return None


def readDat(datFile):
    """Parse a .dat sidecar into the fields genMeta() needs. First data line is
    'rStart aStart nr na deltaR deltaA [azErr]', optionally followed by a line
    naming the pair's two geodats. None if it cannot be parsed.

    Parsed here rather than through u.offsets.readOffsetsDat, which resolves the
    name against the object's own path (mangling an absolute one) and exits the
    process via myerror rather than raising."""
    try:
        with open(datFile) as fp:
            lines = fp.readlines()
    except OSError:
        return None
    dat = None
    for index, line in enumerate(lines):
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            dat = {'r0': int(parts[0]), 'a0': int(parts[1]),
                   'nr': int(parts[2]), 'na': int(parts[3]),
                   'dr': int(parts[4]), 'da': int(parts[5]),
                   'azErr': float(parts[6]) if len(parts) > 6 else 0.,
                   'geo1': None, 'geo2': None}
        except ValueError:
            return None
        if index + 1 < len(lines):
            geo = lines[index + 1].split()
            if len(geo) == 2:
                dat['geo1'], dat['geo2'] = geo
        break
    return dat


def resolveGrid(product, dtype):
    """Find the .dat description of a raw product by testing the sidecars in its
    directory and keeping the one whose grid matches the file size exactly.

    The size check is what makes a wrong table entry fail loudly instead of
    silently producing a garbage raster, so a product whose size matches no
    sidecar is skipped rather than guessed at. Candidates are tried closest-name
    first so the right sidecar is picked when several describe the same grid."""
    size = os.path.getsize(product)
    itemSize = np.dtype(dtype).itemsize
    dirPath = os.path.dirname(product) or '.'
    base = os.path.basename(product)
    candidates = sorted(glob.glob(os.path.join(dirPath, '*.dat')),
                        key=lambda p: -len(os.path.commonprefix(
                            [os.path.basename(p), base])))
    for datFile in candidates:
        dat = readDat(datFile)
        if dat is not None and dat['nr'] * dat['na'] * itemSize == size:
            return dat
    return None


def writeTif(path, data, dtype, meta):
    """Single-band GeoTIFF with the pixel-coordinate geotransform every GrIMP
    tiff writer uses (writeImageTiff in utilities/offsets.py, writeFlatTiff in
    simInSAR). No vertical flip: row 0 stays azimuth 0."""
    na, nr = data.shape
    # GDAL's WriteArray copies the buffer without honouring its byte order, so a
    # big-endian array straight off disk has to be brought to native first or
    # every float lands byteswapped.
    data = np.ascontiguousarray(data.astype(data.dtype.newbyteorder('=')))
    driver = gdal.GetDriverByName('GTiff')
    ds = driver.Create(path, nr, na, 1, GDAL_TYPES[dtype],
                       options=['COMPRESS=DEFLATE'])
    ds.SetGeoTransform([-0.5, 1., 0., -0.5, 0., 1.])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA[dtype])
    band.WriteArray(data)
    if meta:
        ds.SetMetadata({k: str(v) for k, v in meta.items()})
    ds = None


def writeTiffVrt(vrtPath, nr, na, bands, meta):
    """Write a tiff-backed VRT over single-band GeoTIFFs.

    bands is [(tifPath, description, dtype)]. Same SimpleSource shape as
    u.offsets.writeOffsetVrt's tiff branch, but with a per-band data type --
    writeOffsetVrt hardcodes Float32, which would silently retype the Byte
    MatchType/Mask rasters that cullst and siminsar write as GDT_Byte."""
    dirPath = os.path.dirname(vrtPath) or '.'
    if os.path.exists(vrtPath):
        os.remove(vrtPath)
    vrt = gdal.GetDriverByName('VRT').Create(vrtPath, nr, na, bands=0,
                                             eType=gdal.GDT_Float32)
    vrt.SetGeoTransform([-0.5, 1., 0., -0.5, 0., 1.])
    vrt.SetMetadata({k: str(v) for k, v in meta.items()})
    for number, (tif, description, dtype) in enumerate(bands, start=1):
        source = ('<SimpleSource>'
                  f'<SourceFilename relativeToVRT="1">'
                  f'{os.path.relpath(tif, dirPath)}</SourceFilename>'
                  '<SourceBand>1</SourceBand>'
                  f'<SrcRect xOff="0" yOff="0" xSize="{nr}" ySize="{na}"/>'
                  f'<DstRect xOff="0" yOff="0" xSize="{nr}" ySize="{na}"/>'
                  '</SimpleSource>')
        vrt.AddBand(GDAL_TYPES[dtype])
        band = vrt.GetRasterBand(number)
        band.SetMetadataItem('source_0', source, 'new_vrt_sources')
        band.SetNoDataValue(NODATA[dtype])
        band.SetMetadataItem('Description', description)
    vrt = None


class Report:
    """What happened in one directory, printed at the end."""

    def __init__(self, dirPath):
        self.dirPath = dirPath
        self.vintage = None
        self.converted = []
        self.vrts = []
        self.scripts = []
        self.skipped = []
        self.failed = False
        self.traceback = None

    def skip(self, name, why):
        self.skipped.append((name, why))

    def text(self, root=None):
        """Rendered as a block rather than printed, so a threaded sweep can emit
        each directory's report whole instead of interleaving lines."""
        label = os.path.relpath(self.dirPath, root) if root else self.dirPath
        lines = [f'=== {label}  [{self.vintage}] ===',
                 f'  {len(self.converted)} rasters -> tif, {len(self.vrts)} '
                 f'vrts, {len(self.scripts)} scripts']
        lines += [f'    script: {name}' for name in self.scripts]
        lines += [f'    skipped {name}: {why}' for name, why in self.skipped]
        lines += [f'    left raw: {name}' for name in leftoverRaw(self.dirPath)]
        return '\n'.join(lines)

    def show(self, root=None):
        print(f'\n{self.text(root)}')


def leftoverRaw(dirPath, minSize=1_000_000):
    """Sizeable non-tif/vrt files still in the directory, so the report says what
    was deliberately not converted (the interferogram, the power image, the
    unreferenced .azd rasters) rather than leaving it to be discovered later."""
    names = []
    for base in (dirPath, os.path.join(dirPath, 'fast')):
        if not os.path.isdir(base):
            continue
        for path in sorted(glob.glob(os.path.join(base, '*'))):
            name = os.path.basename(path)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if name.endswith(('.tif', '.vrt', '.dat', '.par', '.cw', '.in',
                              '.geojson', '.slc', '.pow', '.list')):
                continue
            if os.path.getsize(path) >= minSize:
                names.append(os.path.relpath(path, dirPath))
    return names


# ----------------------------------------------------------------------------
# modern dirs: the VRT set is the manifest
# ----------------------------------------------------------------------------
RAW_CHILDREN = ('SourceFilename', 'ImageOffset', 'PixelOffset', 'LineOffset',
                'ByteOrder')


def vrtRawBands(vrtPath):
    """[(bandNumber, sourcePath, description)] for the raw-backed bands of a VRT,
    or [] if it carries none (already tiff-backed, or not an offsets VRT). The
    band number is the real 1-based position, not the position among raw bands,
    so a partly-converted VRT still reads the right band."""
    root = ET.parse(vrtPath).getroot()
    dirPath = os.path.dirname(vrtPath) or '.'
    bands = []
    for number, band in enumerate(root.findall('VRTRasterBand'), start=1):
        if band.get('subClass') != 'VRTRawRasterBand':
            continue
        source = band.find('SourceFilename')
        if source is None or not source.text:
            continue
        description = ''
        for mdi in band.iter('MDI'):
            if mdi.get('key') == 'Description':
                description = (mdi.text or '').strip()
        bands.append((number,
                      os.path.normpath(os.path.join(dirPath, source.text)),
                      description))
    return bands


def retargetVrt(vrtPath, tifFor):
    """Rewrite a raw-backed VRT as tiff-backed by editing its XML: drop the raw
    subclass and its children, append a SimpleSource pointing at the tif. Editing
    rather than rebuilding keeps the dataset metadata, band Description and
    NoDataValue byte-for-byte."""
    tree = ET.parse(vrtPath)
    root = tree.getroot()
    dirPath = os.path.dirname(vrtPath) or '.'
    nr, na = root.get('rasterXSize'), root.get('rasterYSize')
    for band in root.findall('VRTRasterBand'):
        if band.get('subClass') != 'VRTRawRasterBand':
            continue
        source = band.find('SourceFilename')
        product = os.path.normpath(os.path.join(dirPath, source.text))
        tif = tifFor.get(product)
        if tif is None:
            continue
        del band.attrib['subClass']
        for tag in RAW_CHILDREN:
            for child in band.findall(tag):
                band.remove(child)
        simple = ET.SubElement(band, 'SimpleSource')
        fileName = ET.SubElement(simple, 'SourceFilename')
        fileName.set('relativeToVRT', '1')
        fileName.text = os.path.relpath(tif, dirPath)
        ET.SubElement(simple, 'SourceBand').text = '1'
        for rect in ('SrcRect', 'DstRect'):
            element = ET.SubElement(simple, rect)
            element.set('xOff', '0')
            element.set('yOff', '0')
            element.set('xSize', nr)
            element.set('ySize', na)
    tree.write(vrtPath)


def convertModern(dirPath, report, debugDir=None, dryRun=False, known=None):
    """Convert every raw band referenced by the directory's VRTs, then rewrite
    each VRT in place as tiff-backed. Returns (tifFor, rawFiles).

    The VRTs are rewritten, not replaced, so an original is copied into debugDir
    before it is touched -- by disposal time the file on disk is the new one."""
    pair = pairPrefix(dirPath)
    vrtPaths = sorted(glob.glob(os.path.join(dirPath, '*.vrt')) +
                      glob.glob(os.path.join(dirPath, '*', '*.vrt')))
    tifFor, rawFiles, vrtFiles = dict(known or {}), set(), []
    fresh = set()
    for vrtPath in vrtPaths:
        if isSkippedSubdir(os.path.dirname(vrtPath), debugDir):
            continue
        try:
            bands = vrtRawBands(vrtPath)
        except ET.ParseError:
            report.skip(os.path.relpath(vrtPath, dirPath), 'unparsable vrt')
            continue
        if not bands:
            continue
        vrtFiles.append(vrtPath)
        dataset = gdal.Open(vrtPath)
        meta = dataset.GetMetadata()
        for number, product, description in bands:
            rawFiles.add(product)
            if product in tifFor:
                continue
            tif = tifName(product, description, pair)
            tifFor[product] = tif
            fresh.add(product)
            report.converted.append(os.path.relpath(tif, dirPath))
            if dryRun:
                continue
            data = dataset.GetRasterBand(number).ReadAsArray()
            writeTif(tif, data, rawDtype(product), meta)
        dataset = None
    report.vrts += [os.path.relpath(p, dirPath) for p in vrtFiles]
    if not dryRun:
        backup(vrtFiles, dirPath, debugDir)
        for vrtPath in vrtFiles:
            retargetVrt(vrtPath, tifFor)
    return {product: tifFor[product] for product in fresh}, rawFiles


# ----------------------------------------------------------------------------
# legacy dirs: a product table is the manifest
# ----------------------------------------------------------------------------
def vrtTable(pair):
    """[(vrtName, [(product, description), ...])] for a frame dir, in the band
    order each producer writes. Sources: writeCullVrt (writeCullData.c),
    writeVrtFile (writeVrt.c), cleanoff.py:227-243 and its createInterpVrt,
    regOffsetsMerge.py."""
    table = [
        ('azimuth.offsets.vrt',
         [('azimuth.offsets', 'AzimuthOffsets'),
          ('azimuth.offsets.sa', 'AzimuthSigma')]),
        ('range.offsets.vrt',
         [('range.offsets', 'RangeOffsets'),
          ('range.offsets.sr', 'RangeSigma')]),
        ('offsets.range-azimuth.vrt',
         [('range.offsets', 'RangeOffsets'),
          ('range.offsets.sr', 'RangeSigma'),
          ('azimuth.offsets', 'AzimuthOffsets'),
          ('azimuth.offsets.sa', 'AzimuthSigma')]),
        ('offsets.slow.vrt',
         [('range.offsets.slow', 'RangeOffsets'),
          ('range.offsets.slow.sr', 'RangeSigma'),
          ('azimuth.offsets.slow', 'AzimuthOffsets'),
          ('azimuth.offsets.slow.sa', 'AzimuthSigma')]),
        ('register.offsets.vrt',
         [('register.offsets.dr', 'RangeOffsets'),
          ('register.offsets.da', 'AzimuthOffsets')]),
    ]
    if pair is None:
        return table
    for base in (f'{pair}.offsets', f'{pair}.register.offsets'):
        table += [
            (f'{base}.vrt',
             [(f'{base}.dr', 'RangeOffsets'),
              (f'{base}.da', 'AzimuthOffsets'),
              (f'{base}.cc', 'Correlation')]),
            (f'{base}.mt.vrt', [(f'{base}.mt', 'MatchType')]),
            (f'{base}.cull.vrt',
             [(f'{base}.cull.dr', 'RangeOffsets'),
              (f'{base}.cull.da', 'AzimuthOffsets'),
              (f'{base}.sr', 'RangeSigma'),
              (f'{base}.sa', 'AzimuthSigma'),
              (f'{base}.cull.cc', 'Correlation')]),
            (f'{base}.cull.mt.vrt', [(f'{base}.cull.mt', 'MatchType')]),
        ]
    # The two interp VRTs differ: cleanoff's carries the sigma bands, whereas
    # regOffsetsMerge's createInterpVrt writes offsets only.
    table.append((f'{pair}.offsets.cull.interp.vrt',
                  [(f'{pair}.offsets.cull.interp.dr', 'RangeOffsets'),
                   (f'{pair}.offsets.cull.interp.da', 'AzimuthOffsets'),
                   ('range.offsets.slow.sr', 'RangeSigma'),
                   ('azimuth.offsets.slow.sa', 'AzimuthSigma')]))
    table.append((f'{pair}.register.offsets.cull.interp.vrt',
                  [(f'{pair}.register.offsets.cull.interp.dr', 'RangeOffsets'),
                   (f'{pair}.register.offsets.cull.interp.da', 'AzimuthOffsets')]))
    return table


FAST_TABLE = [
    ('offsets.fast.vrt',
     [('range.offsets.fast', 'RangeOffsets'),
      ('range.offsets.fast.sr', 'RangeSigma'),
      ('azimuth.offsets.fast', 'AzimuthOffsets'),
      ('azimuth.offsets.fast.sa', 'AzimuthSigma')]),
    ('offsets.noclean.fast.vrt',
     [('range.offsets.noclean.fast', 'RangeOffsets'),
      ('range.offsets.fast.sr', 'RangeSigma'),
      ('azimuth.offsets.noclean.fast', 'AzimuthOffsets'),
      ('azimuth.offsets.fast.sa', 'AzimuthSigma')]),
    ('range.offsets.fast.vrt',
     [('range.offsets.fast', 'RangeOffsets'),
      ('range.offsets.fast.sr', 'RangeSigma')]),
    ('azimuth.offsets.fast.vrt',
     [('azimuth.offsets.fast', 'AzimuthOffsets'),
      ('azimuth.offsets.fast.sa', 'AzimuthSigma')]),
]


def locateProduct(dirPath, product):
    """Absolute path to a table product, tolerating the pre-2020 interp name
    order (<root>.cull.dr.interp for <root>.cull.interp.dr). Returns
    (path, isOldName) or (None, False)."""
    path = os.path.join(dirPath, product)
    if os.path.exists(path):
        return path, False
    m = re.match(r'(.*)\.interp\.(dr|da)$', product)
    if m:
        old = os.path.join(dirPath, f'{m.group(1)}.{m.group(2)}.interp')
        if os.path.exists(old):
            return old, True
    return None, False


def convertLegacy(dirPath, report, debugDir=None, dryRun=False):
    """Convert the in-scope products of a dir that has no VRTs, then write the
    modern VRT set over them. Old interp names are normalised to the modern
    order as part of the conversion."""
    pair = pairPrefix(dirPath)
    entries = [(dirPath, name, bands) for name, bands in vrtTable(pair)]
    fastDir = os.path.join(dirPath, 'fast')
    if os.path.isdir(fastDir):
        entries += [(fastDir, name, bands) for name, bands in FAST_TABLE]

    tifFor, rawFiles, vrtFiles = {}, set(), []
    for base, vrtName, bands in entries:
        resolved = []
        missing = []
        for product, description in bands:
            path, isOld = locateProduct(base, product)
            if path is None:
                missing.append(product)
            else:
                resolved.append((path, product, description, isOld))
        if missing:
            report.skip(vrtName, f'absent: {", ".join(missing)}')
            continue
        # Grid is resolved per product and cross-checked against its file size.
        grids = {}
        bad = None
        for path, product, description, _ in resolved:
            dat = resolveGrid(path, rawDtype(product))
            if dat is None:
                bad = f'{os.path.basename(path)} size matches no .dat'
                break
            grids[path] = dat
        if bad is not None:
            report.skip(vrtName, bad)
            continue

        for path, product, description, isOld in resolved:
            rawFiles.add(path)
            if path in tifFor:
                continue
            tif = os.path.join(base, os.path.basename(
                tifName(product, description, pair)))
            tifFor[path] = tif
            report.converted.append(os.path.relpath(tif, dirPath))
            if isOld:
                report.skip(os.path.basename(path),
                            f'legacy name, written as {os.path.basename(tif)}')
            if dryRun:
                continue
            dat = grids[path]
            dtype = rawDtype(product)
            data = np.fromfile(path, dtype=dtype).reshape((dat['na'],
                                                           dat['nr']))
            writeTif(tif, data, dtype, datMeta(dat))
        vrtFiles.append((base, vrtName, resolved, grids))

    if not dryRun:
        for base, vrtName, resolved, grids in vrtFiles:
            writeLegacyVrt(base, vrtName, resolved, grids, tifFor)
    report.vrts = [name for _, name, _, _ in vrtFiles]

    # A pre-2023 dir can still carry the odd raw-backed VRT the product table
    # does not describe (offsets.SECorrection.vrt is the common one). Sweep those
    # with the VRT-driven path so both vintages leave a dir with no raw bands
    # behind, rather than the answer depending on which manifest was used.
    strayTifs, strayRaw = convertModern(dirPath, report, debugDir=debugDir,
                                        dryRun=dryRun, known=tifFor)
    tifFor.update(strayTifs)
    rawFiles |= strayRaw
    return tifFor, rawFiles


def datMeta(dat):
    """Dataset metadata for a converted raster, matching the keys genMeta()
    writes so a converted VRT carries what a natively-written one does."""
    meta = {'r0': f'{dat["r0"]}', 'a0': f'{dat["a0"]}',
            'deltaR': f'{dat["dr"]}', 'deltaA': f'{dat["da"]}',
            'ByteOrder': 'MSB', 'sigmaRange': '0.0',
            'sigmaStreaks': f'{dat["azErr"]}'}
    if dat['geo1'] is not None:
        meta['geo1'], meta['geo2'] = dat['geo1'], dat['geo2']
    return meta


def writeLegacyVrt(base, vrtName, resolved, grids, tifFor):
    """Write one tiff-backed VRT over the converted products, carrying the band
    Descriptions the C readers match on (readBothOffsetsStrackVrt checks for
    'Range'/'Azimuth')."""
    dat = grids[resolved[0][0]]
    bands = [(tifFor[path], description, rawDtype(product))
             for path, product, description, _ in resolved]
    writeTiffVrt(os.path.join(base, vrtName), dat['nr'], dat['na'], bands,
                 datMeta(dat))


# ----------------------------------------------------------------------------
# disposal
# ----------------------------------------------------------------------------
def debugTarget(path, dirPath, debugDir):
    """Where path is saved under debugDir, with its directory created."""
    target = os.path.join(debugDir, os.path.relpath(path, dirPath))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target


def backup(paths, dirPath, debugDir):
    """Copy files that are about to be overwritten in place (VRTs, scripts) into
    debugDir. A no-op without --debug, since there is nothing to roll back to."""
    if debugDir is None:
        return
    os.makedirs(debugDir, exist_ok=True)
    for path in sorted(paths):
        if os.path.exists(path):
            shutil.copy2(path, debugTarget(path, dirPath, debugDir))


def dispose(paths, dirPath, debugDir, dryRun=False):
    """Move each original into debugDir preserving its path relative to dirPath,
    or delete it when no debugDir was given."""
    if dryRun:
        return
    if debugDir is not None:
        os.makedirs(debugDir, exist_ok=True)
    for path in sorted(paths):
        if not os.path.exists(path):
            continue
        if debugDir is None:
            os.remove(path)
        else:
            shutil.move(path, debugTarget(path, dirPath, debugDir))


# ----------------------------------------------------------------------------
# post-processing scripts
# ----------------------------------------------------------------------------
def frameSensor(dirPath):
    """(orbit1, orbit2, frame, sensor) for a frame dir, or None if it cannot be
    identified. orbit1/frame come from the directory name, orbit2 from the
    pairinfo, sensor from the nearest project.yaml (falling back to the path
    heuristic grepdate.getSensor uses)."""
    m = re.match(r'(\d+)_(\d+)$', os.path.basename(dirPath))
    if m is None:
        return None
    orbit1, frame = int(m.group(1)), int(m.group(2))
    orbit2 = None
    for path in sorted(glob.glob(os.path.join(dirPath, '*.pairinfo'))):
        pieces = os.path.basename(path).split('.')
        if len(pieces) >= 2 and pieces[1].isdigit():
            orbit2 = int(pieces[1])
            break
    if orbit2 is None:
        return None
    sensor = None
    search = dirPath
    for _ in range(4):
        search = os.path.dirname(search)
        projectYaml = os.path.join(search, 'project.yaml')
        if os.path.isfile(projectYaml):
            import yaml
            with open(projectYaml) as fp:
                sensor = (yaml.safe_load(fp) or {}).get('sensor')
            break
    if sensor in (None, 'Sentinel1'):
        sensor = 'S1' if sensor == 'Sentinel1' else sensor
    if sensor is None:
        for tag, name in (('TSX', 'TSX'), ('CSK', 'CSK'), ('Sentinel', 'S1')):
            if tag in dirPath:
                sensor = name
                break
    return (orbit1, orbit2, frame, sensor) if sensor else None


def speckleRootOf(scriptPath):
    """The input root of the first cullst line of a runFastCull script -- the
    <pair>.offsets.<n>.speckle base makeCullFile needs to regenerate it."""
    try:
        with open(scriptPath) as fp:
            for line in fp:
                pieces = line.split()
                if pieces and pieces[0] == 'cullst' and len(pieces) >= 2:
                    return pieces[-2]
    except OSError:
        pass
    return None


def regenerateScripts(dirPath, report, debugDir=None, dryRun=False):
    """Rewrite the post-processing scripts in tiff form, reusing the generators
    that a native run uses so the result matches one. Only scripts that already
    exist are rewritten -- the long-running drivers (runboth, fast/dofast,
    runAll, runSimll, setupStrackReg) are deliberately left alone."""
    identity = frameSensor(dirPath)
    if identity is None:
        report.skip('scripts', 'cannot identify orbit/frame/sensor')
        return
    orbit1, orbit2, frame, sensorName = identity
    try:
        sensorInfo = s.sensorDefinitions(sensorName).SAR
    except Exception:
        report.skip('scripts', f'unknown sensor {sensorName}')
        return

    pair = f'{orbit1}_{frame}.{orbit2}_{frame}'
    jobs = []
    if os.path.exists(os.path.join(dirPath, 'runcull')):
        jobs.append(('runcull', dict(offsetFile=f'{pair}.offsets',
                                     scaleFactor=1)))
    for legacy in sorted(glob.glob(os.path.join(dirPath, 'runCullReg*')) +
                         glob.glob(os.path.join(dirPath, 'runcullReg*'))):
        digits = re.findall(r'(\d+)$', os.path.basename(legacy))
        scale = int(digits[0]) if digits else 1
        jobs.append((os.path.basename(legacy),
                     dict(offsetFile=f'{pair}.register.offsets',
                          scaleFactor=scale, register=True)))
        break
    fastScript = os.path.join(dirPath, 'fast', 'runFastCull')
    if os.path.exists(fastScript):
        speckle = speckleRootOf(fastScript)
        if speckle is None:
            report.skip('fast/runFastCull', 'no cullst line to read the root from')
        else:
            jobs.append(('fast/runFastCull', dict(offsetFile=speckle,
                                                  scaleFactor=1,
                                                  fastCull=True)))

    cleanoffPath = os.path.join(dirPath, 'cleanoff')
    existing = [os.path.join(dirPath, name) for name, _ in jobs]
    if os.path.exists(cleanoffPath):
        existing.append(cleanoffPath)
    if dryRun:
        report.scripts = [name for name, _ in jobs]
        if os.path.exists(cleanoffPath):
            report.scripts.append('cleanoff')
        return
    backup(existing, dirPath, debugDir)


    # os.chdir is process-global, so only one thread may be inside the
    # regeneration section at a time. Everything else in this module works on
    # absolute paths and is unaffected by another thread's cwd.
    with CHDIR_LOCK:
        regenerateInPlace(dirPath, jobs, cleanoffPath, pair, sensorInfo,
                          orbit1, orbit2, frame, report)


def regenerateInPlace(dirPath, jobs, cleanoffPath, pair, sensorInfo,
                      orbit1, orbit2, frame, report):
    """The cwd-dependent half of regenerateScripts: makeCullFile/makeCleanOff
    write into the current directory, so each job has to chdir. Called only
    under CHDIR_LOCK."""
    cwd = os.getcwd()
    for name, kwargs in jobs:
        workDir = os.path.join(dirPath, os.path.dirname(name)) or dirPath
        offsetFile = kwargs.pop('offsetFile')
        scaleFactor = kwargs.pop('scaleFactor')
        # makeCullFile opens the script for writing before it reads the sidecar
        # for NR/NA, so a missing .dat leaves a truncated script behind and (via
        # u.myerror) takes the process down. Check first rather than clean up
        # after: fast/runFastCull is the common case, since the .speckle
        # products it culls are deleted once mergefast has run.
        if not os.path.exists(os.path.join(workDir, f'{offsetFile}.dat')):
            report.skip(name, f'{offsetFile}.dat absent, left as-is')
            continue
        try:
            os.chdir(workDir)
            written = s.makeCullFile(sensorInfo, offsetFile, orbit1, orbit2,
                                     frame, scaleFactor, tiff=True, **kwargs)
        except BaseException as error:
            # BaseException, not Exception: u.myerror raises SystemExit.
            report.skip(name, f'could not regenerate ({error})')
            continue
        finally:
            os.chdir(cwd)
        report.scripts.append(os.path.join(os.path.dirname(name), written)
                              if os.path.dirname(name) else written)
        # runcullReg (pre-2020) and runCullReg<N> describe the same step; drop
        # the legacy-named one so the dir carries only what a native run writes.
        stale = os.path.join(workDir, os.path.basename(name))
        if os.path.basename(name) != written and os.path.exists(stale):
            os.remove(stale)

    if os.path.exists(cleanoffPath):
        try:
            os.chdir(dirPath)
            s.makeCleanOff(f'{pair}.offsets', sensorInfo['sensor'], tiff=True)
            report.scripts.append('cleanoff')
        except BaseException as error:
            report.skip('cleanoff', f'could not regenerate ({error})')
        finally:
            os.chdir(cwd)


def convertOne(dirPath, debugName=None, dryRun=False):
    """Convert one frame directory. Returns its Report; never raises, so one bad
    directory cannot take down a sweep of thousands."""
    report = Report(dirPath)
    try:
        modern = os.path.exists(os.path.join(dirPath, 'range.offsets.vrt'))
        report.vintage = 'modern' if modern else 'legacy'
        convert = convertModern if modern else convertLegacy
        debugDir = frameDebugDir(dirPath, debugName)
        tifFor, rawFiles = convert(dirPath, report, debugDir=debugDir,
                                   dryRun=dryRun)
        if not tifFor:
            report.skip('directory', 'nothing to convert (already tiff?)')
            return report
        dispose(rawFiles, dirPath, debugDir, dryRun=dryRun)
        regenerateScripts(dirPath, report, debugDir=debugDir, dryRun=dryRun)
    except BaseException as error:
        report.failed = True
        report.skip('directory', f'FAILED: {error}')
        report.traceback = traceback.format_exc()
    return report


def frameDebugDir(dirPath, debugName):
    """Where a frame's originals are saved: <frameDir>/<debugName>.

    Inside the frame dir rather than a shared root, so the rollback copy sits
    with the frame it came from (azimuth.offsets -> <frame>/debug/azimuth.offsets)
    and there is no way for two frames' identically-named products to collide."""
    if debugName is None:
        return None
    return os.path.join(dirPath, debugName)


def isSkippedSubdir(path, debugDir):
    """True for a subdirectory the scans must not descend into: the velocity
    products (makevelnoclean's business) and the --debug copy of the originals,
    which would otherwise be re-converted on the next run."""
    name = os.path.basename(path)
    if name.startswith('velocity') or name == DEBUG_DIR:
        return True
    return debugDir is not None and os.path.abspath(path) == debugDir


def frameDate(dirPath):
    """Acquisition date of a frame directory, or None if it cannot be read.

    The pairinfo carries it in both vintages ('<orbit1> <orbit2> <date1>
    <date2> <nlr> <nla>'); the geodat is the fallback for a dir that has none."""
    for path in sorted(glob.glob(os.path.join(dirPath, '*.pairinfo'))):
        try:
            with open(path) as fp:
                pieces = fp.readline().split()
            if len(pieces) >= 3:
                return datetime.strptime(pieces[2], '%Y-%m-%d')
        except (OSError, ValueError):
            pass
    for path in sorted(glob.glob(os.path.join(dirPath, 'geodat*.in'))):
        try:
            with open(path) as fp:
                for line in fp:
                    if 'image date' in line.lower():
                        return datetime.strptime(
                            line.split(':')[1].strip(), '%d %b %Y')
        except (OSError, ValueError, IndexError):
            pass
    return None


def parseDate(text, label):
    """Accept either YYYY-MM-DD or the YYYY:MM:DD form makevelnoclean uses."""
    for fmt in ('%Y-%m-%d', '%Y:%m:%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    u.myerror(f'{label}: expected YYYY-MM-DD, got {text}')


def inDateRange(dirPath, firstDate, lastDate):
    """(keep, why) for a frame directory against the date window. A directory
    whose date cannot be read is skipped rather than converted, so a windowed
    run never touches something it could not place in time."""
    if firstDate is None and lastDate is None:
        return True, None
    date = frameDate(dirPath)
    if date is None:
        return False, 'no date in pairinfo/geodat, outside a windowed run'
    if firstDate is not None and date < firstDate:
        return False, f'{date:%Y-%m-%d} before --firstdate'
    if lastDate is not None and date > lastDate:
        return False, f'{date:%Y-%m-%d} after --lastdate'
    return True, None


def frameDirs(base):
    """The <orbit>_<frame> product directories directly under base."""
    return sorted((path for path in glob.glob(os.path.join(base, '*_*'))
                   if os.path.isdir(path) and
                   re.match(r'\d+_\d+$', os.path.basename(path))),
                  key=lambda p: [int(x) for x in
                                 os.path.basename(p).split('_')])


def resolveDirs(args):
    """Turn --tracks/--allTracks/positional dirs into a frame-directory list."""
    if args.tracks or args.allTracks:
        root = os.path.abspath(args.project) if args.project else os.getcwd()
        if args.allTracks:
            tracks = sorted(glob.glob(os.path.join(root, 'track-*')),
                            key=lambda p: int(re.search(r'track-(\d+)',
                                                        p).group(1)))
        else:
            tracks = [os.path.join(root, name) for name in args.tracks]
        missing = [t for t in tracks if not os.path.isdir(t)]
        if missing:
            u.myerror(f'track directories not found: {missing}')
        if not tracks:
            u.myerror(f'no track-* directories under {root}')
        dirs = []
        for track in tracks:
            found = frameDirs(track)
            if not found:
                u.mywarning(f'no <orbit>_<frame> directories in {track}')
            dirs += found
        return dirs, root
    if args.dirs:
        return [os.path.abspath(d) for d in args.dirs], None
    return [os.getcwd()], None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Part of the s1setup package.')
    parser.add_argument('dirs', nargs='*', default=None, metavar='DIR',
                        help='Frame directories to convert [current directory]')
    parser.add_argument('--tracks', nargs='+', metavar='track-N',
                        help='Convert every <orbit>_<frame> directory in these '
                             'tracks, resolved against --project')
    parser.add_argument('--allTracks', action='store_true',
                        help='Convert every frame directory of every track-* '
                             'under --project')
    parser.add_argument('--project', default=None, metavar='DIR',
                        help='Project root holding the track-* directories '
                             '[current directory]')
    parser.add_argument('--nThreads', type=int, default=4, metavar='N',
                        help='Frame directories converted in parallel [4]')
    parser.add_argument('--debug', nargs='?', const=DEBUG_DIR, default=None,
                        metavar='NAME',
                        help=f'Move the originals into <frameDir>/{DEBUG_DIR}/ '
                             'instead of deleting them, so the rollback copy '
                             'stays with its frame. NAME overrides the '
                             'subdirectory name')
    parser.add_argument('-firstdate', '--firstdate', default=None,
                        metavar='YYYY-MM-DD',
                        help='Skip frames acquired before this date [all time]')
    parser.add_argument('-lastdate', '--lastdate', default=None,
                        metavar='YYYY-MM-DD',
                        help='Skip frames acquired after this date [all time]')
    parser.add_argument('--dryRun', action='store_true',
                        help='Report what would be done and touch nothing')
    args = parser.parse_args()
    if args.tracks and args.allTracks:
        u.myerror('use --tracks or --allTracks, not both')
    firstDate = parseDate(args.firstdate, '--firstdate') if args.firstdate \
        else None
    lastDate = parseDate(args.lastdate, '--lastdate') if args.lastdate else None
    if firstDate and lastDate and firstDate > lastDate:
        u.myerror('--firstdate is after --lastdate')

    if args.debug is not None and (os.sep in args.debug or
                                   os.path.isabs(args.debug)):
        u.myerror(f'--debug takes a subdirectory name, not a path: {args.debug}'
                  f' (the originals are saved inside each frame directory)')

    gdal.UseExceptions()
    dirs, root = resolveDirs(args)
    for dirPath in dirs:
        if not os.path.isdir(dirPath):
            u.myerror(f'not a directory: {dirPath}')
    debugName = args.debug

    outOfRange = []
    if firstDate is not None or lastDate is not None:
        kept = []
        for dirPath in dirs:
            keep, why = inDateRange(dirPath, firstDate, lastDate)
            (kept if keep else outOfRange).append(
                dirPath if keep else (dirPath, why))
        dirs = kept
        window = (f'{firstDate:%Y-%m-%d}' if firstDate else 'start',
                  f'{lastDate:%Y-%m-%d}' if lastDate else 'now')
        print(f'date window {window[0]} .. {window[1]}: {len(dirs)} of '
              f'{len(dirs) + len(outOfRange)} frame directories selected')
        if not dirs:
            for dirPath, why in outOfRange[:5]:
                print(f'  {os.path.basename(dirPath)}: {why}')
            return

    sweep = len(dirs) > 1
    if sweep:
        print(f'{len(dirs)} frame directories, {args.nThreads} at a time'
              f'{"  (dryRun)" if args.dryRun else ""}')
    threads = max(1, min(args.nThreads, len(dirs)))
    with ThreadPoolExecutor(max_workers=threads) as executor:
        reports = list(executor.map(
            lambda d: convertOne(d, debugName=debugName, dryRun=args.dryRun),
            dirs))

    for report in reports:
        report.show(root)
    if sweep:
        summarise(reports, debugName, len(outOfRange))


def summarise(reports, debugName, outOfRange=0):
    """Totals for a sweep, plus the failures repeated at the end where they are
    visible after thousands of lines of per-directory output."""
    failed = [r for r in reports if getattr(r, 'failed', False)]
    converted = [r for r in reports if r.converted and not
                 getattr(r, 'failed', False)]
    untouched = [r for r in reports if not r.converted and not
                 getattr(r, 'failed', False)]
    rasters = sum(len(r.converted) for r in reports)
    print(f'\n{"=" * 60}\n{len(reports)} directories: {len(converted)} converted'
          f' ({rasters} rasters), {len(untouched)} already tiff/nothing to do,'
          f' {len(failed)} failed'
          + (f', {outOfRange} outside the date window' if outOfRange else ''))
    byVintage = {}
    for report in converted:
        byVintage[report.vintage] = byVintage.get(report.vintage, 0) + 1
    if byVintage:
        print('  vintages: ' + ', '.join(f'{k}={v}'
                                         for k, v in sorted(byVintage.items())))
    if debugName:
        print(f'  originals saved in each frame\'s {debugName}/ subdirectory')
    for report in failed:
        print(f'\n  FAILED {report.dirPath}')
        for line in (report.traceback or '').strip().splitlines()[-3:]:
            print(f'    {line}')


if __name__ == '__main__':
    main()
