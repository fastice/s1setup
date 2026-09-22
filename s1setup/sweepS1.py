#!/usr/bin/env python3
"""Sweep a Sentinel-1 project for frames that need attention, and write a report.

Walks every track (or a chosen subset), selects the frames whose acquisition
falls in a date window, and checks each one for the problems that otherwise only
surface as a failed nightly or a swallowed thread exception:

  excludePending  Exclude.pending marker -- flagged by the baseline check for a
                  low azimuth tiepoint count, waiting on an operator decision
  excluded        hard Exclude marker (reported, not treated as a fault)
  noSolution      az.est sigma < 0, the "no solution" sentinel
  largeSigma      az.est sigma above --sigThresh
  untied          offsets built but no *.pairinfo: the pair was processed but
                  the tie stage never ran, so nothing downstream exists
  missingFit      no motion/az.est.yaml at all
  missingFiles    an expected product is absent (velocity, velocity_nocull,
                  geodat, baseline)
  gridMismatch    velocity_nocull is on a different grid from the velocityStats
                  reference for its frame range -- the failure that makes
                  autoclean raise inside a worker thread, where the exception is
                  swallowed and the run still exits 0

Dates come from each frame's `*.pairinfo` (`orbit1 orbit2 date1 date2 nDays looks`),
so the window is the real acquisition span rather than file mtimes.

    sweepS1 --firstDate 2026-01-01
    sweepS1 --firstDate 2026-01-01 --tracks track-25 track-54 --out /tmp/sweep.md
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys
from collections import Counter, defaultdict

try:
    from osgeo import gdal
    gdal.UseExceptions()
except ImportError:                                            # pragma: no cover
    gdal = None

ISSUES = ['untied', 'excludePending', 'noSolution', 'largeSigma',
          'missingFit', 'missingFiles', 'gridMismatch']
FRAME_RE = re.compile(r'^\d+_\d+$')


def parseDate(s):
    return dt.datetime.strptime(s, '%Y-%m-%d').date()


def frameDates(frameDir):
    """(date1, date2) from the frame's pairinfo, or (None, None)."""
    for p in glob.glob(os.path.join(frameDir, '*.pairinfo')):
        try:
            f = open(p).read().split()
            if len(f) >= 4:
                return parseDate(f[2]), parseDate(f[3])
        except (ValueError, OSError):
            continue
    return None, None


TITLE_RE = re.compile(r'(s1[abcd])-iw\d?-slc-\w+-(\d{8})t', re.I)


def acquisitionDate(frameDir):
    """The frame's own acquisition date from the ESA product name on its
    slc.par title line, or None. Used only when there is no pairinfo."""
    for par in glob.glob(os.path.join(frameDir, '*.slc.par')):
        if os.path.basename(par).startswith('p'):
            continue
        try:
            with open(par) as fp:
                for line in fp:
                    if line.startswith('title'):
                        m = TITLE_RE.search(line)
                        if m:
                            return dt.datetime.strptime(m.group(2), '%Y%m%d').date()
                        break
        except OSError:
            pass
    return None


def fitSigma(frameDir):
    """(sigma, path) from motion/az.est.yaml; sigma None when absent/unparsable."""
    p = os.path.join(frameDir, 'motion', 'az.est.yaml')
    if not os.path.exists(p):
        return None, p
    try:
        for line in open(p):
            m = re.match(r'sigma:\s*([-\d.eE+]+)', line)
            if m:
                return float(m.group(1)), p
    except OSError:
        pass
    return None, p


def rangeGrids(trackDir):
    """{(lo, hi): (shape, name)} from the track's velocityStats reference maps."""
    out = {}
    vs = os.path.join(trackDir, 'velocityStats')
    if gdal is None or not os.path.isdir(vs):
        return out
    for r in sorted(os.listdir(vs)):
        m = re.fullmatch(r'(\d+)-(\d+)', r)
        if not m or not os.path.isdir(os.path.join(vs, r)):
            continue
        for cand in ('velocity.vx.tif', 'velocity_nocull.vx.tif'):
            ref = os.path.join(vs, r, cand)
            if os.path.exists(ref):
                try:
                    d = gdal.Open(ref)
                    out[(int(m.group(1)), int(m.group(2)))] = ((d.RasterYSize, d.RasterXSize), r)
                    del d
                except Exception:                              # noqa: BLE001
                    pass
                break
    return out


def rasterShape(path):
    if gdal is None or not os.path.exists(path):
        return None
    try:
        d = gdal.Open(path)
        s = (d.RasterYSize, d.RasterXSize)
        del d
        return s
    except Exception:                                          # noqa: BLE001
        return None


def checkFrame(frameDir, grids, sigThresh):
    """[(issue, detail)] for one frame."""
    found = []
    base = os.path.basename(frameDir)

    if os.path.exists(os.path.join(frameDir, 'Exclude.pending')):
        found.append(('excludePending', 'Exclude.pending present'))
    excluded = os.path.exists(os.path.join(frameDir, 'Exclude'))

    sigma, fitPath = fitSigma(frameDir)
    if sigma is None:
        # a hard-Excluded frame is not expected to carry a fit
        if not excluded:
            found.append(('missingFit', f'no {os.path.relpath(fitPath, frameDir)}'))
    elif sigma < 0:
        found.append(('noSolution', f'az.est sigma = {sigma:g} (no-solution sentinel)'))
    elif sigma > sigThresh:
        found.append(('largeSigma', f'az.est sigma = {sigma:.2f} m > {sigThresh:g}'))

    missing = [w for w in ('velocity/mosaicOffsets.vrt',
                           'velocity_nocull/mosaicOffsets.vrt',
                           'motion/baseline.10x2')
               if not os.path.exists(os.path.join(frameDir, w))
               and not glob.glob(os.path.join(frameDir, w.replace('.vrt', '.v[xr]')))]
    if not glob.glob(os.path.join(frameDir, 'geodat*')):
        missing.append('geodat*')
    if missing and not excluded:
        found.append(('missingFiles', ', '.join(missing)))

    # velocity_nocull must sit on its range's reference grid; when it does not,
    # autoclean raises inside a worker thread and the run still exits 0
    m = re.search(r'_(\d+)$', base)
    if m and grids:
        fn = int(m.group(1))
        hit = [(shp, rn) for (lo, hi), (shp, rn) in grids.items() if lo <= fn <= hi]
        if len(hit) == 1:
            want, rname = hit[0]
            got = rasterShape(os.path.join(frameDir, 'velocity_nocull', 'mosaicOffsets.vrt'))
            if got is not None and got != want:
                found.append(('gridMismatch',
                              f'velocity_nocull {got} but range {rname} reference is {want}'))
        elif len(hit) > 1:
            found.append(('gridMismatch',
                          'frame falls in >1 velocityStats range: '
                          + ', '.join(rn for _, rn in hit)))
    return found, excluded


def sweepTrack(trackDir, first, last, sigThresh):
    grids = rangeGrids(trackDir)
    rows, nFrames, nOutside, nExcluded = [], 0, 0, 0
    for fd in sorted(glob.glob(os.path.join(trackDir, '*_*'))):
        if not os.path.isdir(fd) or not FRAME_RE.match(os.path.basename(fd)):
            continue
        d1, d2 = frameDates(fd)
        if d1 is None:
            # No pairinfo. setuptopstie writes it the first time the tie stage
            # runs, so a frame with offsets but no pairinfo was processed and
            # never tied -- and every check below would skip it. (2026-09-22:
            # all 63 insar10 Sentinel1-S1D pairs sat like this for nine days.)
            # A frame with neither is a partner image or a newest acquisition.
            if glob.glob(os.path.join(fd, 'azimuth.offsets*')):
                d1 = acquisitionDate(fd)
                if d1 is None or first <= d1 <= last:
                    nFrames += 1
                    rows.append((os.path.basename(fd), d1 or '?', 'untied',
                                 'offsets built but no pairinfo: tie stage '
                                 'never ran'))
            continue
        if d2 < first or d1 > last:
            nOutside += 1
            continue
        nFrames += 1
        issues, excluded = checkFrame(fd, grids, sigThresh)
        if excluded:
            nExcluded += 1
        for kind, detail in issues:
            rows.append((os.path.basename(fd), d1, kind, detail))
    return rows, nFrames, nOutside, nExcluded, len(grids)


def writeReport(path, project, first, last, perTrack, sigThresh, tracksAsked):
    tot = Counter()
    for _, (rows, *_rest) in perTrack.items():
        for _, _, kind, _ in rows:
            tot[kind] += 1
    nFrames = sum(v[1] for v in perTrack.values())
    with open(path, 'w') as fp:
        w = fp.write
        w(f'# Sentinel-1 sweep report\n\n')
        w(f'- project: `{project}`\n')
        w(f'- window: **{first} .. {last}** (frame acquisition dates, from `*.pairinfo`)\n')
        w(f'- tracks: {"all" if tracksAsked is None else " ".join(tracksAsked)} '
          f'({len(perTrack)} swept)\n')
        w(f'- frames in window: **{nFrames}**\n')
        w(f'- large-sigma threshold: {sigThresh:g} m\n')
        w(f'- generated: {dt.datetime.now():%Y-%m-%d %H:%M}\n\n')

        w('## Summary\n\n| issue | count |\n|---|---|\n')
        for k in ISSUES:
            w(f'| {k} | {tot.get(k, 0)} |\n')
        w(f'| **total flagged** | **{sum(tot.values())}** |\n\n')

        w('## By track\n\n')
        w('| track | frames | excluded | ' + ' | '.join(ISSUES) + ' |\n')
        w('|---' * (len(ISSUES) + 3) + '|\n')
        for t in sorted(perTrack, key=lambda s: int(re.sub(r'\D', '', s) or 0)):
            rows, nF, _nOut, nEx, _ng = perTrack[t]
            c = Counter(k for _, _, k, _ in rows)
            w(f'| {t} | {nF} | {nEx} | '
              + ' | '.join(str(c.get(k, 0)) or '.' for k in ISSUES) + ' |\n')
        w('\n')

        for k in ISSUES:
            hits = [(t, f, d, det) for t, (rows, *_r) in perTrack.items()
                    for f, d, kind, det in rows if kind == k]
            if not hits:
                continue
            w(f'## {k}  ({len(hits)})\n\n| track | frame | date | detail |\n|---|---|---|---|\n')
            for t, f, d, det in sorted(hits):
                w(f'| {t} | {f} | {d} | {det} |\n')
            w('\n')
        if not sum(tot.values()):
            w('No issues found.\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', default='.', help='project root [.]')
    ap.add_argument('--tracks', nargs='*', default=None,
                    help='track dirs to sweep (e.g. --tracks track-25 track-54); '
                         'default every track-* in the project')
    ap.add_argument('--firstDate', default='2026-01-01', help='window start YYYY-MM-DD')
    ap.add_argument('--lastDate', default=None, help='window end YYYY-MM-DD [today]')
    ap.add_argument('--sigThresh', type=float, default=10.0,
                    help='az.est sigma above this is reported as largeSigma [10]')
    ap.add_argument('--out', default=None, help='report path [<project>/logs/sweep-<date>.md]')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    first = parseDate(args.firstDate)
    last = parseDate(args.lastDate) if args.lastDate else dt.date.today()
    if args.tracks:
        trackDirs = [os.path.join(project, t) for t in args.tracks]
        missing = [t for t in trackDirs if not os.path.isdir(t)]
        if missing:
            sys.exit(f'no such track dir: {", ".join(missing)}')
    else:
        trackDirs = sorted(glob.glob(os.path.join(project, 'track-*')))
    if not trackDirs:
        sys.exit(f'no track-* directories under {project}')

    out = args.out or os.path.join(project, 'logs', f'sweep-{last:%Y%m%d}.md')
    os.makedirs(os.path.dirname(out), exist_ok=True)

    if not args.quiet:
        print(f'sweeping {len(trackDirs)} track(s) in {project}')
        print(f'window {first} .. {last}, sigThresh {args.sigThresh:g} m')

    perTrack = {}
    for td in trackDirs:
        t = os.path.basename(td)
        rows, nF, nOut, nEx, nGrids = sweepTrack(td, first, last, args.sigThresh)
        perTrack[t] = (rows, nF, nOut, nEx, nGrids)
        if not args.quiet:
            c = Counter(k for _, _, k, _ in rows)
            print(f'  {t:10s} {nF:5d} in window ({nOut} outside)  '
                  f'{nEx:4d} excluded  {len(rows):4d} flagged'
                  + (f'  {dict(c)}' if c else ''))

    writeReport(out, project, first, last, perTrack, args.sigThresh, args.tracks)
    tot = sum(len(v[0]) for v in perTrack.values())
    if not args.quiet:
        print(f'\n{tot} issue(s) across {sum(v[1] for v in perTrack.values())} frames')
        print(f'wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
