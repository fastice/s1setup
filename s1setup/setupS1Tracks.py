#!/usr/bin/env python3
"""Orchestrate Sentinel-1 velocity processing across a project's track dirs.

Sentinel-1 analogue of nisargrimpworkflow.setupNISARTracks. Run from the S1
project root -- the directory holding project.yaml, a templates/ dir, and the
track-* subdirectories. Implemented modes: --copyFiles (first-time setup),
--runVelstatsregions (common-box velocity rebuild + velocityStats), and
--runVelStats (velocityStats only). The RSLC->products pipeline is still stubbed.

How S1 differs from the NISAR workflow (why this is separate code, not a shared
body -- the stubs below are where these differences will land):

  1. S1 builds offsets and other products starting from RSLCs (NISAR ingests
     HDF5 RUNW/ROFF products).
  2. No ionosphere corrections.
  3. No dual/virtual-frame system -- frames are derived from the first burst
     number in the product (<orbit>_<startburst> dirs); there is no
     framePattern / virtual-frame assembly step.
  4. Separate programs (segmentTrack, setupSeveralTopsImages, setupTrack) build
     the orbit-frame directories; this tool assumes they already exist.
  5. No HDF5s.
"""
import os
import sys
import glob
import re
import argparse
import datetime
import subprocess
from concurrent.futures import ThreadPoolExecutor
import yaml
import utilities as u

PROJECT_DIR = os.getcwd()

BLUEBOLD = '\033[1;34m'
RESET = '\033[0m'


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            'Orchestrate Sentinel-1 velocity processing across all track\n'
            'directories in a project. Must be run from the project root -- the\n'
            'directory containing project.yaml, templates/, and the track-*\n'
            'subdirectories.\n\n'
            '--copyFiles instantiates the per-track tiepoint plan files\n'
            '(tie_plan_header, vel_thumb_plan, vel_thumb_header_<range>) from\n'
            'templates/, copying only files that do not already exist.\n'
            'velocityStats/ directories are assumed to already exist.\n\n'
            '--runVelstatsregions finds a common bounding box per velocityStats\n'
            'range, rebuilds the velocities on it, resizes velocity_nocull, and\n'
            'rebuilds velocityStats. --runVelStats runs the velocityStats step\n'
            'alone.\n\n'
            'The RSLC -> offsets/products pipeline is not yet implemented.'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Part of the s1setup package.')
    parser.add_argument('--tracks', nargs='+', metavar='track-N',
                        help='Restrict processing to these track directories '
                             '(e.g. --tracks track-12 track-64). '
                             'Default: all track-* directories under the project root')
    parser.add_argument('--copyFiles', action='store_true',
                        help='Instantiate tie_plan_header, vel_thumb_plan and '
                             'vel_thumb_header_<range> from templates/ into every '
                             'track tiepoints/ directory (substituting <TRACK>, '
                             '<DEM>, <TIEFILE>); skip files that already exist')
    parser.add_argument('--runVelstatsregions', action='store_true',
                        help='Find a common bounding box per velocityStats range so '
                             'every frame velocity map fits one grid, rebuild the '
                             'velocities on that grid, resize velocity_nocull to match, '
                             'and rebuild velocityStats. Runs, in order: '
                             '(0) ensure vel_thumb_header_<range> exist, (1) refreshties '
                             'bootstrap render, (2) makevelstatsregions, (3) forced '
                             'refreshties rebuild, (4) makevelnoclean -reSize, '
                             '(5) velocityStats (unless --noUpdateVelStats)')
    parser.add_argument('--runVelStats', action='store_true',
                        help='Run only step 5: velocityStats in each track velocityStats/ '
                             'dir (RA/XY per project.yaml velocityStatsMode)')
    parser.add_argument('--runRefresh', action='store_true',
                        help='Run refreshties.py (maketies + makeframetie -> '
                             'tie_script) across the selected tracks and return. '
                             'Legacy-baseline flavor (no -phase); honors --year '
                             'and --overWrite.')
    parser.add_argument('--tiesOnly', action='store_true',
                        help='With --runRefresh, pass -tiesOnly to refreshties '
                             '(tie_script only, skip vel thumbs). Errors if used '
                             'without --runRefresh.')
    parser.add_argument('--updateNoCull', action='store_true',
                        help='Run makevelnoclean.py across the selected tracks '
                             '(regenerate velocity_nocull/) and return. Plain '
                             'form -- no --runVelstatsregions header/reSize/date '
                             'staging.')
    parser.add_argument('--rebuildVel', action='store_true',
                        help='Rebuild BOTH velocity/ and velocity_nocull/ with '
                             'makevelnoclean (the --fullUpdate step-6 pair: '
                             '-redoculled then -reset) and return. Uses the same '
                             'window as --fullUpdate, so --firstDate/--lastDate/'
                             '--weeks set the date range instead of the default '
                             '52-week lookback. No vel_thumbs, no ties.')
    parser.add_argument('--runAutoClean', action='store_true',
                        help='Run autoclean.py in each selected track dir '
                             '(cd track; autoclean.py) and return; sequential '
                             '(autoclean is itself threaded). Honors --nThreads '
                             'and --sigThresh.')
    parser.add_argument('--sigThresh', type=float, default=3.0, metavar='S',
                        help='With --runAutoClean, sigma threshold passed to '
                             'autoclean.py -sigThresh [3.0]')
    parser.add_argument('--fullUpdate', action='store_true',
                        help='Run the full monthly update across the selected '
                             'tracks (MonthlyWorkflow steps 1-7): refresh ties, '
                             'nocull, velStats, autoclean, ties-only refresh, '
                             'last-year velocity, then check baselines (marking '
                             'low-tiepoint frames Exclude/Exclude.pending). Then '
                             'return.')
    parser.add_argument('--lastDate', type=str, default=None, metavar='YYYY-MM-DD',
                        help='With --fullUpdate/--rebuildVel, the last date in the run (end of '
                             'the lookback window). Default: today.')
    parser.add_argument('--weeks', type=int, default=52, metavar='N',
                        help='With --fullUpdate/--rebuildVel, lookback window length in weeks '
                             'back from --lastDate [52].')
    parser.add_argument('--firstDate', type=str, default=None, metavar='YYYY-MM-DD',
                        help='With --fullUpdate/--rebuildVel, override the computed window start '
                             '(step 6 velocity + step 7 baseline check). Default: '
                             '--weeks back from --lastDate, first of month.')
    parser.add_argument('--tieThresh', type=int, default=None, metavar='N',
                        help='With --fullUpdate, baseline azimuth-tiepoint count '
                             'below which a frame is marked Exclude.pending. '
                             'Overrides project.yaml tieThresh (fallback 100).')
    parser.add_argument('--excludeThresh', type=int, default=None, metavar='N',
                        help='With --fullUpdate, baseline azimuth-tiepoint count '
                             'at or below which (or no solution) a frame is hard '
                             'Excluded. Overrides project.yaml excludeThresh '
                             '(fallback 3).')
    parser.add_argument('--year', type=str, nargs='+', metavar='YYYY|all',
                        help='Years to process, e.g. --year 2015 2016, or '
                             '--year all for the whole archive (2015 through the '
                             'current year). Default: the current year only for '
                             'the refresh/vel_thumbs paths (--runRefresh and '
                             '--fullUpdate steps 1/5); --runVelstatsregions '
                             'always uses all years, since it sizes the common '
                             'region from the full history. The --weeks/'
                             '--firstDate/--lastDate lookback window is separate '
                             'and applies only to --fullUpdate steps 6-7 and '
                             '--rebuildVel.')
    parser.add_argument('--nThreads', type=int, default=48, metavar='N',
                        help='Total thread budget [48]. Concurrent tracks are capped at '
                             'min(nTracks, max(1, N//4)) so total concurrency stays near N '
                             'rather than N x ntracks')
    parser.add_argument('--noUpdateVelStats', action='store_true',
                        help='With --runVelstatsregions, skip the final velocityStats '
                             'rebuild (step 5)')
    parser.add_argument('--overWrite', action='store_true',
                        help='With --copyFiles, overwrite existing tie_plan_header and '
                             'vel_thumb_plan instead of skipping them')
    parser.add_argument('--applySecondary', action='store_true',
                        help='Also run the operation on every secondaryDirectories '
                             'project (e.g. the Sentinel1-S1A/-S1C 12-day clones). '
                             'Default: prime-only, except --runVelstatsregions which '
                             'always cascades a grid+velocity rebuild to the secondaries')
    parser.add_argument('--noSecondary', action='store_true',
                        help='Never touch secondaryDirectories projects, even for '
                             '--runVelstatsregions (escape hatch; also set internally '
                             'when re-invoked in a secondary to prevent recursion)')
    parser.add_argument('--secondaryVelRebuild', action='store_true',
                        help='(internal) In a secondary project: sync the '
                             'vel_thumb_header grid from --primeDir and rebuild the '
                             'frame velocities on it (no makevelstatsregions, no '
                             'velocityStats). Set by the prime when it cascades')
    parser.add_argument('--primeDir', metavar='DIR',
                        help='(internal) Prime project root, required with '
                             '--secondaryVelRebuild -- source of the shared grid')
    # TODO(S1 pipeline): the RSLC -> offsets/products -> ties -> mosaics steps
    # are not implemented yet. --check is a placeholder for the eventual product
    # check (cf. setupNISARTracks --check) and currently raises NotImplementedError.
    parser.add_argument('--check', action='store_true',
                        help='(not yet implemented) report S1 track product status')
    return parser.parse_args()


def getTrackDirs(tracks=None):
    """Return sorted track-* directories under the project root, numeric-sorted
    on the track integer. An explicit --tracks list is validated to exist."""
    if tracks:
        dirs = [os.path.join(PROJECT_DIR, t) for t in tracks]
        missing = [d for d in dirs if not os.path.isdir(d)]
        if missing:
            u.myerror(f'Track directories not found: {missing}')
        return sorted(dirs, key=lambda p: int(re.search(r'track-(\d+)', p).group(1)))
    return sorted(glob.glob(os.path.join(PROJECT_DIR, 'track-*')),
                  key=lambda p: int(re.search(r'track-(\d+)', p).group(1)))


def loadProjectConfig():
    """Return (proj dict, dem string, velMap string, regionPath string,
    templateContents dict) read from project.yaml. Unlike the NISAR variant there
    is no framePattern handling here -- S1 has no virtual-frame system (the
    downstream tools still read framePattern from project.yaml themselves)."""
    projPath = os.path.join(PROJECT_DIR, 'project.yaml')
    proj = {}
    if os.path.exists(projPath):
        with open(projPath) as f:
            proj = yaml.safe_load(f) or {}

    dem = ''
    velMap = ''
    regionPath = proj.get('region') or proj.get('regionFile', '')
    for ext in ('', '.yaml'):
        rpath = (regionPath + ext) if ext else regionPath
        if rpath and os.path.exists(rpath):
            with open(rpath) as f:
                regionYaml = yaml.safe_load(f) or {}
            dem = regionYaml.get('dem', '')
            velMap = regionYaml.get('velMap', '')
            break

    templatesDir = os.path.join(PROJECT_DIR, 'templates')
    templateFiles = {
        'tie_plan_header': proj.get('tie_plan_header_template',
                                    os.path.join(templatesDir, 'tie_plan_header')),
        'vel_thumb_plan':  proj.get('vel_thumb_plan_template',
                                    os.path.join(templatesDir, 'vel_thumb_plan')),
        'vel_thumb_header': proj.get('vel_thumb_header_template',
                                     os.path.join(templatesDir, 'vel_thumb_header')),
    }
    contents = {}
    for name, path in templateFiles.items():
        if os.path.exists(path):
            with open(path) as f:
                contents[name] = f.read()
        else:
            contents[name] = None
    return proj, dem, velMap, regionPath, contents


def applySubstitutions(content, trackNum, dem, tiepointFile):
    return (content.replace('<TRACK>', trackNum)
                    .replace('<DEM>', dem)
                    .replace('<TIEFILE>', tiepointFile))


def setupTrackDirs(trackDirs, copyFiles, overwrite=False):
    """Instantiate per-track tiepoint plan files from templates.

    For each track: ensure tiepoints/ exists; copy tie_plan_header and
    vel_thumb_plan into it (copy-if-absent unless overwrite); and create
    vel_thumb_header_<range> for each existing velocityStats/*-* dir. Unlike the
    NISAR variant this does NOT create the velocityStats/ directories -- they are
    assumed to already exist."""
    proj, dem, _, _, templates = loadProjectConfig()
    tiepointFile = proj.get('tiepointFile', '')

    nCreated = 0
    nSkipped = 0

    for trackDir in trackDirs:
        track = os.path.basename(trackDir)
        trackNum = track.split('-')[1]
        tpdir = os.path.join(trackDir, 'tiepoints')

        if not os.path.isdir(tpdir):
            os.makedirs(tpdir)
            print(f'Created {tpdir}')
            nCreated += 1

        if copyFiles:
            for tmplName, destName in (('tie_plan_header', 'tie_plan_header'),
                                       ('vel_thumb_plan', 'vel_thumb_plan')):
                dest = os.path.join(tpdir, destName)
                exists = os.path.exists(dest)
                if exists and not overwrite:
                    nSkipped += 1
                    continue
                if templates[tmplName] is None:
                    print(f'WARNING: template {tmplName} not found, skipping {dest}')
                    continue
                content = applySubstitutions(templates[tmplName], trackNum, dem, tiepointFile)
                with open(dest, 'w') as f:
                    f.write(content)
                if exists:
                    print(f'Overwrote {dest}')
                else:
                    print(f'Created {dest}')
                    nCreated += 1

        if templates['vel_thumb_header'] is not None:
            for vsd in sorted(glob.glob(os.path.join(trackDir, 'velocityStats', '*-*'))):
                frameRange = os.path.basename(vsd)
                xDashY = frameRange.replace('-', 'dash')
                dest = os.path.join(tpdir, f'vel_thumb_header_{xDashY}')
                if not os.path.exists(dest):
                    content = applySubstitutions(templates['vel_thumb_header'], trackNum, dem, tiepointFile)
                    with open(dest, 'w') as f:
                        f.write(content)
                    print(f'Created {dest}')
                    nCreated += 1
                else:
                    nSkipped += 1

    if nSkipped:
        print(f'Skipped {nSkipped} files/dirs (already exist), created {nCreated} new')


def warnProjectConfig(proj):
    """Warn about project.yaml keys that are wrong for S1 (common when the file
    was copied from a NISAR template). Warn-only -- the downstream tools still
    run, they just mis-parse burst-numbered frames."""
    sensor = proj.get('sensor')
    if sensor != 'Sentinel1':
        u.mywarning(f'project.yaml sensor is {sensor!r}, expected "Sentinel1"')
    framePattern = proj.get('framePattern')
    if framePattern is not None:
        # The S1 tools strip framePattern.split('?')[0] as a prefix. A non-empty
        # prefix (e.g. '00' from '00??', the NISAR pattern) mis-parses burst-
        # numbered frames. An all-'?' pattern gives an empty prefix AND, since
        # setuptopstie globs '*_<framePattern>', still restricts frame selection
        # to the right burst-number width (e.g. '???' -> 3-digit bursts), which is
        # safer than '*' (that would also match stray dirs like <orbit>_test).
        prefix = str(framePattern).split('?')[0] if '?' in str(framePattern) else ''
        if prefix:
            u.mywarning(f'project.yaml framePattern is {framePattern!r} (strips prefix '
                        f'{prefix!r}); S1 frames are burst-numbered (e.g. <orbit>_618) '
                        'and velocityStats ranges are burst ranges (e.g. 570-609), so '
                        'framePattern needs an empty prefix -- use an all-"?" pattern '
                        'matching the burst-number width (e.g. "???")')


def runChecked(cmd, cwd, label):
    """Run cmd under csh and abort if it fails.

    The --runVelstatsregions steps are ordered dependencies, so a step that
    dies has to stop the sequence rather than let the next one run on
    half-updated products. This was not always so: a stray
    vel_thumb_header_*.save in one track crashed makevelnoclean's header scan
    for the whole project, and because the exit status was dropped the run
    carried on to velocityStats and reported nothing wrong -- leaving
    velocity_nocull on the pre-resize grid everywhere."""
    result = subprocess.run(['csh', '-c', cmd], cwd=cwd)
    if result.returncode != 0:
        u.myerror(f'{label} failed (exit {result.returncode}) in {cwd}\n'
                  f'  {cmd}')
    return result.returncode


def concurrentTrackLimit(nTracks, nThreads):
    """Cap on how many tracks run at once so total concurrency stays near
    nThreads rather than nThreads x nTracks -- each running track is effectively
    allotted >= 4 threads."""
    return min(max(1, nTracks), max(1, nThreads // 4))


def allYears():
    """Every year of the S1 archive: 2015 through the current year."""
    return list(range(2015, datetime.datetime.now().year + 1))


# Kept as the old name for any external caller; allYears() is the current one.
defaultYears = allYears


def requestedYears(args):
    """Resolve --year into a list of ints, or None when it was not given.

    Accepts '--year all' (the whole archive, 2015..current) alongside an
    explicit list such as '--year 2015 2016'. Returning None lets each caller
    apply its own default -- the refresh/vel_thumbs paths use the current year
    only, while --runVelstatsregions uses every year."""
    if not args.year:
        return None
    if any(str(y).lower() == 'all' for y in args.year):
        return allYears()
    try:
        return [int(y) for y in args.year]
    except ValueError:
        u.myerror(f'--year takes years or "all", got {" ".join(args.year)}')


def currentYear():
    """Default for the refresh/vel_thumbs paths: this calendar year only.

    Re-running whole past years on every refresh is wasted work -- new
    acquisitions land in the current year, and an older year is rebuilt only
    when asked for explicitly (--year 2019) or wholesale (--year all)."""
    return [datetime.datetime.now().year]


def runRefreshTies(trackDirs, years, overWrite, concurrent, tiesOnly=False):
    """Run refreshties.py (maketies + makeframetie -> tie_script + vel_thumbs)
    across the tracks from the project root. S1 legacy-baseline flavor: no
    --yaml/--phase/--useSquint. Track-level parallelism is refreshties' own
    -nThreads, capped to `concurrent`. tiesOnly passes -tiesOnly (skip thumbs);
    the velocity-rebuild callers (--runVelstatsregions, --secondaryVelRebuild)
    leave it False since they need the thumbs.

    --tiff is passed unconditionally: S1 velocity products are GeoTIFF in all
    cases, so the format does not depend on each project.yaml carrying
    velThumbOutput (the clone projects in particular are easy to miss). The
    flag only forces tiff on; it never selects binary, so a project.yaml that
    already sets velThumbOutput: tiff is unaffected."""
    tracksStr = str([os.path.basename(d) for d in trackDirs]).replace(' ', '')
    overWriteFlag = '--overWrite ' if overWrite else ''
    tiesOnlyFlag = '-tiesOnly ' if tiesOnly else ''
    yearsStr = ' '.join(str(y) for y in years)
    cmd = (f'refreshties.py {tiesOnlyFlag}{overWriteFlag}--tiff '
           f'-nThreads {concurrent} '
           f'-toRun="{tracksStr}" {yearsStr} -noPrompt')
    print(f'Running: {cmd}  (in {PROJECT_DIR})')
    runChecked(cmd, PROJECT_DIR, 'refreshties')


def runVelstatsRegions(trackDirs, concurrent):
    """Run makevelstatsregions.py in each track dir (parallel across tracks) to
    write the common bounding box into each tiepoints/vel_thumb_header_<range>."""
    def _run(trackDir):
        print(f'Running makevelstatsregions.py in {trackDir}')
        result = subprocess.run(['csh', '-c', 'makevelstatsregions.py'],
                                cwd=trackDir)
        return trackDir, result.returncode

    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        results = list(executor.map(_run, trackDirs))
    failed = [d for d, rc in results if rc != 0]
    if failed:
        u.myerror(f'makevelstatsregions failed in: {failed}')


def runMakeVelNoClean(trackDirs, years, nThreads):
    """Regenerate velocity_nocull/ at the common box (matching the just-resized
    velocity/) so a later autoclean pass consumes correctly-sized data. Single
    flat pool over all velocity dirs, so the full budget is safe here."""
    tracksStr = str([os.path.basename(d) for d in trackDirs]).replace(' ', '')
    firstDate = f'{min(years)}:01:01'
    lastDate = f'{max(years)}:12:31'
    cmd = (f'makevelnoclean.py -toRun="{tracksStr}" -useHeader -reSize -noprompt -tiff '
           f'-threads={nThreads} -firstdate={firstDate} -lastdate={lastDate}')
    print(f'Running: {cmd}  (in {PROJECT_DIR})')
    runChecked(cmd, PROJECT_DIR, 'makevelnoclean')


def fullUpdateWindow(args):
    """Resolve the (firstDate, lastDate) window for --fullUpdate / --rebuildVel.

    lastDate defaults to today; firstDate defaults to --weeks (52) back from
    lastDate, first of month; --firstDate overrides. Dates returned as
    'YYYY-MM-DD'.

    This window governs the velocity rebuild and baseline check (--fullUpdate
    steps 6-7, --rebuildVel) ONLY. The tie-refresh/vel_thumbs steps are driven
    by --year instead (see requestedYears/currentYear) -- they rebuild whole
    calendar years, so a date window is the wrong unit for them."""
    lastDT = (datetime.datetime.strptime(args.lastDate, '%Y-%m-%d')
              if args.lastDate else datetime.datetime.now())
    if args.firstDate:
        firstDT = datetime.datetime.strptime(args.firstDate, '%Y-%m-%d')
    else:
        firstDT = (lastDT - datetime.timedelta(weeks=args.weeks)).replace(day=1)
    return firstDT.strftime('%Y-%m-%d'), lastDT.strftime('%Y-%m-%d')


def runMakeVelLastYear(trackDirs, firstDate, lastDate, nThreads):
    """--fullUpdate step 6 (also --rebuildVel): rebuild the window's velocities
    after the culled ties. Two makevelnoclean runs from PROJECT_DIR (culled then
    nocull), each bounded to [firstDate, lastDate] (-firstdate/-lastdate use
    YYYY:MM:DD).

    The nocull pass carries -reset because the point of this step is to *force*
    a rebuild: without it makevelnoclean skips any frame whose velocity_nocull
    product already exists, so the second set would silently not be rebuilt.
    The -redoculled pass needs no -reset -- that branch queues the velocity dir
    unconditionally."""
    tracksStr = str([os.path.basename(d) for d in trackDirs]).replace(' ', '')
    fd, ld = firstDate.replace('-', ':'), lastDate.replace('-', ':')
    for extra, label in ((' -redoculled', 'makevelnoclean redoculled'),
                         (' -reset', 'makevelnoclean nocull')):
        cmd = (f'makevelnoclean.py -firstdate={fd} -lastdate={ld} -noprompt -tiff{extra} '
               f'-toRun="{tracksStr}" -threads={nThreads}')
        print(f'Running: {cmd}  (in {PROJECT_DIR})')
        runChecked(cmd, PROJECT_DIR, label)


def parseCheckBaseline(stdout):
    """Parse checkbasesol.py stdout into (rows, warnings). Each data line is
    '<date> <orbit>_<frame> range <sigmaR> <nR> azimuth <sigmaA> <nA> [deltabp]'
    (with ANSI color codes); lines mentioning 'baseline' are warnings. Returns
    rows as dicts with date/orbit/frame/sigmaR/nR/sigmaA/nA and the raw warning
    lines. Robust to the trailing deltabp token -- keys off the range/azimuth
    labels rather than fixed columns."""
    rows, warnings = [], []
    for raw in stdout.splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', raw).strip()
        if not line:
            continue
        if 'baseline' in line.lower():
            warnings.append(line)
            continue
        tok = line.split()
        if 'range' not in tok or 'azimuth' not in tok or '_' not in tok[1]:
            continue
        try:
            ri, ai = tok.index('range'), tok.index('azimuth')
            orbit, frame = tok[1].split('_', 1)
            rows.append({'date': tok[0], 'orbit': orbit, 'frame': frame,
                         'sigmaR': float(tok[ri + 1]), 'nR': int(tok[ri + 2]),
                         'sigmaA': float(tok[ai + 1]), 'nA': int(tok[ai + 2])})
        except (ValueError, IndexError):
            continue
    return rows, warnings


def writeMarker(frameDir, name, reason):
    """Write an Exclude / Exclude.pending marker with the reason inside, matching
    the tieScript convention ('<datetime>: <reason>')."""
    with open(os.path.join(frameDir, name), 'w') as fp:
        print(f'{datetime.datetime.now()}: {reason}', file=fp)


def runCheckBaselines(trackDirs, firstDate, lastDate, tieThresh, excludeThresh):
    """--fullUpdate step 7: run checkbasesol.py per track and mark low-tiepoint
    frames. nA <= excludeThresh (or no solution, sigmaA < 0) -> hard Exclude;
    excludeThresh < nA < tieThresh -> Exclude.pending (needs operator check).
    checkbasesol.py already skips frames that already carry Exclude/Exclude.pending,
    so this only touches frames that passed ties. Writes a per-run log."""
    excluded, pending = [], []
    for trackDir in trackDirs:
        track = os.path.basename(trackDir)
        trackNum = track.split('-')[1]
        cmd = (f'checkbasesol.py --firstdate {firstDate} --lastdate {lastDate} '
               f'--track {trackNum}')
        print(f'Running: {cmd}  (in {PROJECT_DIR})')
        result = subprocess.run(['csh', '-c', cmd], cwd=PROJECT_DIR,
                                capture_output=True, text=True)
        if result.returncode != 0:
            u.mywarning(f'checkbasesol.py exited {result.returncode} for {track} '
                        f'(continuing)\n{result.stderr.strip()}')
        rows, warnings = parseCheckBaseline(result.stdout)
        nMarked = 0
        for row in rows:
            frameName = f'{row["orbit"]}_{row["frame"]}'
            frameDir = os.path.join(trackDir, frameName)
            if not os.path.isdir(frameDir) or \
                    os.path.exists(os.path.join(frameDir, 'Exclude')):
                continue
            nA = row['nA']
            if nA <= excludeThresh or row['sigmaA'] < 0:
                reason = (f'checkbaseline: only {nA} azimuth baseline tiepoints '
                          f'(<= {excludeThresh}) -- excluded')
                writeMarker(frameDir, 'Exclude', reason)
                excluded.append((track, frameName, nA, reason))
                nMarked += 1
            elif nA < tieThresh:
                reason = (f'checkbaseline: {nA} azimuth baseline tiepoints '
                          f'(< tieThresh {tieThresh}) -- needs operator check')
                writeMarker(frameDir, 'Exclude.pending', reason)
                pending.append((track, frameName, nA, reason))
                nMarked += 1
        print(f'  {track}: {len(rows)} frames checked, {nMarked} marked'
              f'{f", {len(warnings)} baseline warnings" if warnings else ""}')
        for w in warnings:
            print(f'    baseline warning: {w}')

    logPath = os.path.join(
        PROJECT_DIR,
        f'fullUpdate_checkbaseline_{datetime.datetime.now():%Y-%m-%d}.log')
    with open(logPath, 'w') as fp:
        print(f'# checkbaseline {datetime.datetime.now()}  '
              f'firstDate={firstDate}  lastDate={lastDate}  '
              f'tieThresh={tieThresh}  excludeThresh={excludeThresh}', file=fp)
        print(f'# EXCLUDED (real): {len(excluded)}   PENDING: {len(pending)}',
              file=fp)
        print('\n[EXCLUDED (real)]', file=fp)
        for track, frameName, nA, reason in excluded:
            print(f'{track} {frameName} nA={nA}  {reason}', file=fp)
        print('\n[PENDING]', file=fp)
        for track, frameName, nA, reason in pending:
            print(f'{track} {frameName} nA={nA}  {reason}', file=fp)
    print(f'checkbaseline: {len(excluded)} excluded, {len(pending)} pending '
          f'-- log {logPath}')


def runUpdateNoCull(trackDirs, nThreads):
    """Regenerate velocity_nocull/ across the selected tracks with a plain
    makevelnoclean run. Unlike runMakeVelNoClean (the --runVelstatsregions
    step 4), this omits -useHeader/-reSize/-firstdate/-lastdate -- it just
    rebuilds nocull as-is. Single flat pool over all velocity dirs, so the full
    thread budget is safe here."""
    tracksStr = str([os.path.basename(d) for d in trackDirs]).replace(' ', '')
    cmd = (f'makevelnoclean.py -toRun="{tracksStr}" -threads={nThreads} '
           '-noprompt -tiff')
    print(f'Running: {cmd}  (in {PROJECT_DIR})')
    runChecked(cmd, PROJECT_DIR, 'makevelnoclean')


def runAutoClean(trackDirs, nThreads, sigThresh):
    """Run autoclean.py in each selected track dir (cd track; autoclean.py
    -threads -sigThresh), sequentially -- autoclean is itself threaded, so
    tracks run one at a time to avoid oversubscribing the budget. Aborts if any
    track fails."""
    for trackDir in trackDirs:
        # A secondary (12-day) tree carries tracks whose frames have no offsets
        # yet (single SLCs, not yet paired); autoclean.py dies on those, so skip
        # them the way it would find nothing to clean.
        if not u.globOffsetProducts(os.path.join(trackDir, '*_*', 'azimuth.offsets')):
            print(f'Skipping autoclean in {trackDir}: no *_*/azimuth.offsets '
                  '(no offsets processed yet)')
            continue
        cmd = f'autoclean.py -threads={nThreads} -sigThresh={sigThresh}'
        print(f'Running: {cmd}  (in {trackDir})')
        runChecked(cmd, trackDir, 'autoclean')


def runVelStats(trackDirs, regionPath, velMap, concurrent):
    """Run velocityStats.py (no -nocull; RA/XY per project.yaml velocityStatsMode)
    in each track's velocityStats/ dir, parallel across tracks."""
    regionArg = f'-regionFile {regionPath} ' if regionPath else ''
    velMapArg = f'-velmap {velMap} ' if velMap else ''
    cmd = f'velocityStats.py {regionArg}{velMapArg}'.strip()

    jobs = []
    for trackDir in trackDirs:
        vsd = os.path.join(trackDir, 'velocityStats')
        if not os.path.isdir(vsd):
            print(f'Skipping {trackDir}: no velocityStats/ dir')
            continue
        jobs.append(vsd)

    def _run(vsd):
        print(f'Running: {cmd}  (in {vsd})')
        result = subprocess.run(['csh', '-c', cmd], cwd=vsd)
        return vsd, result.returncode

    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        results = list(executor.map(_run, jobs))
    failed = [d for d, rc in results if rc != 0]
    if failed:
        u.myerror(f'velocityStats failed in: {failed}')


def secondaryProjectDirs(proj):
    """Resolve the secondaryDirectories project.yaml key to existing absolute
    project roots (e.g. the Sentinel1-S1A/-S1C 12-day clones). Bare names resolve
    against the parent of PROJECT_DIR (matching cloneSLCdir.py); absolute or
    contains-slash entries are used as given. Self is excluded; missing dirs are
    warned and skipped."""
    entries = proj.get('secondaryDirectories') or []
    if isinstance(entries, str):
        entries = [entries]
    dirs = []
    for entry in entries:
        root = entry if (os.path.isabs(entry) or '/' in entry) \
            else os.path.join(os.path.dirname(PROJECT_DIR), entry)
        root = os.path.abspath(root)
        if root == os.path.abspath(PROJECT_DIR):
            continue
        if not os.path.isdir(root):
            u.mywarning(f'secondaryDirectory not found, skipping: {root}')
            continue
        dirs.append(root)
    return dirs


def _resolutionLine(headerPath):
    """Return the 'resolution ...' line (no trailing newline) from a
    vel_thumb_header, or None if absent."""
    with open(headerPath) as fp:
        for line in fp:
            if line.lstrip().startswith('resolution'):
                return line.rstrip('\n')
    return None


def syncThumbHeadersFromPrime(trackDirs, primeDir):
    """Give each secondary track the prime's common grid.

    For every prime tiepoints/vel_thumb_header_<range>, produce the matching
    header in this (secondary) project:
      - missing: create it from the prime header, rewriting the prime project
        root -> this project root in the track_root/tie_dir/vel_dir path lines so
        the secondary's mosaics land in the secondary tree (the template hardcodes
        the prime root); the resolution line is carried over from the prime.
      - existing: keep its own track_root/tie_dir/vel_dir and replace only the
        resolution line with the prime's.
    A range whose prime header (or resolution line) is missing is warned+skipped.
    Deliberately does NOT go through setupTrackDirs, whose template substitution
    would leave the prime root baked into the secondary paths."""
    primeDir = os.path.abspath(primeDir)
    for trackDir in trackDirs:
        track = os.path.basename(trackDir)
        tpdir = os.path.join(trackDir, 'tiepoints')
        primeTp = os.path.join(primeDir, track, 'tiepoints')
        if not os.path.isdir(tpdir):
            os.makedirs(tpdir)
        # tie_plan_header too: maketies -> setuptopstie dies without it, even on
        # a track that has no pairs yet, and only the -tiesOnly pass surfaces it.
        primeTie = os.path.join(primeTp, 'tie_plan_header')
        destTie = os.path.join(tpdir, 'tie_plan_header')
        if os.path.exists(primeTie) and not os.path.exists(destTie):
            with open(primeTie) as fp:
                tieText = fp.read()
            with open(destTie, 'w') as fp:
                fp.write(tieText.replace(primeDir + '/',
                                         os.path.abspath(PROJECT_DIR) + '/'))
            print(f'Created {destTie} (secondary-rooted)')
        for primeHeader in sorted(glob.glob(
                os.path.join(primeTp, 'vel_thumb_header_*dash*'))):
            name = os.path.basename(primeHeader)
            # only real headers -- not a .save or backup copy sitting beside them
            if not re.match(r'^vel_thumb_header_\d+dash\d+$', name):
                continue
            dest = os.path.join(tpdir, name)
            newRes = _resolutionLine(primeHeader)
            if newRes is None:
                u.mywarning(f'no resolution line in {primeHeader}; skipping {dest}')
                continue
            if os.path.exists(dest):
                with open(dest) as fp:
                    lines = fp.readlines()
                out = []
                replaced = False
                for line in lines:
                    if line.lstrip().startswith('resolution'):
                        out.append(newRes + '\n')
                        replaced = True
                    else:
                        out.append(line)
                if not replaced:
                    out.append(newRes + '\n')
                with open(dest, 'w') as fp:
                    fp.writelines(out)
                print(f'Updated resolution in {dest}')
            else:
                with open(primeHeader) as fp:
                    primeText = fp.read()
                # Point track_root/tie_dir/vel_dir at the secondary tree. Match a
                # path boundary (primeDir + '/') so a prime root that is a prefix
                # of the secondary name (Sentinel1 vs Sentinel1-S1A) is not hit.
                secText = primeText.replace(primeDir + '/',
                                            os.path.abspath(PROJECT_DIR) + '/')
                with open(dest, 'w') as fp:
                    fp.write(secText)
                print(f'Created {dest} (secondary-rooted, prime grid)')


def secondaryTracks(secondaryDir, requested):
    """Return the track-* basenames present in secondaryDir, intersected with
    `requested` (a list of basenames, or None for all)."""
    avail = sorted({os.path.basename(d)
                    for d in glob.glob(os.path.join(secondaryDir, 'track-*'))})
    if requested is None:
        return avail
    return [t for t in requested if t in avail]


def inSecondaryTree(trackDirs):
    """True if this project is a secondary (12-day clone) tree, detected by
    track-N/velocityStats being a symlink into the prime. Secondaries share the
    prime's velocityStats, so the velocityStats rebuild belongs to the prime run
    only -- but their velocity/velocity_nocull still have to be maintained here
    for autoclean. Tracks with a real velocityStats dir (or none) do not match."""
    return any(os.path.islink(os.path.join(d, 'velocityStats'))
               for d in trackDirs)


def secondaryFirstYear(secondaryDir, tracks):
    """
    Earliest acquisition year present in a secondary tree, or None if unknown.

    The secondaries are newer satellites brought online in the last couple of
    years, so asking one to process the prime's full year list makes maketies
    produce no tie_plan for the years before it existed. Read the year straight
    from the SLC parameter files ("date:  2026 06 13 ..."), which is the only
    source that exists before any processing has been done - tie_plan<year>
    files cannot be used, since those are an output of the run being clamped.

    Guards the parse to plausible years: some .par variants carry a different
    date layout and yield values like 12 or 30 in that field.
    """
    years = set()
    for track in tracks:
        for par in glob.glob(os.path.join(secondaryDir, track, '*_*', '*.slc.par')):
            try:
                with open(par, errors='replace') as fp:
                    for line in fp:
                        if line.startswith('date:'):
                            fields = line.split()
                            if len(fields) > 1 and fields[1].isdigit():
                                y = int(fields[1])
                                if 1990 <= y <= 2100:
                                    years.add(y)
                            break
            except Exception:
                continue
    return min(years) if years else None


def clampYearsForSecondary(secondaryDir, tracks, years):
    """Drop years earlier than the secondary's first acquisition."""
    first = secondaryFirstYear(secondaryDir, tracks)
    if first is None:
        return years, None
    kept = [y for y in years if int(y) >= first]
    return kept, first


def reinvokeInSecondary(secondaryDir, extraArgs):
    """Re-run this program in a secondary project (cwd=secondaryDir) so its own
    project.yaml/tracks drive it. --noSecondary is expected in extraArgs to stop
    the secondary cascading further."""
    cmd = [sys.executable, os.path.abspath(__file__)] + extraArgs
    print(f'--> secondary {secondaryDir}: {" ".join(extraArgs)}')
    result = subprocess.run(cmd, cwd=secondaryDir)
    if result.returncode != 0:
        u.myerror(f'secondary run failed (exit {result.returncode}) in '
                  f'{secondaryDir}')


def main():
    # TODO(S1 pipeline): implement the S1 processing entry point -- build offsets
    # and other products from RSLCs (difference 1), then ties/mosaics. No
    # ionosphere step (2), no virtual-frame assembly (3), orbit-frame dirs built
    # elsewhere (4), no HDF5 ingest (5). --copyFiles below is the only working
    # functionality so far.
    args = parse_args()

    if args.check:
        raise NotImplementedError(
            'S1 product check not yet implemented -- see differences 1-5 in the '
            'module docstring.')

    if args.tiesOnly and not args.runRefresh:
        u.myerror('--tiesOnly can only be combined with --runRefresh')

    trackDirs = getTrackDirs(args.tracks)
    proj, _, velMap, regionPath, _ = loadProjectConfig()

    if args.secondaryVelRebuild:
        # Internal mode, run in a secondary project by the prime's cascade: adopt
        # the prime's common grid (secondary-rooted headers) and rebuild the
        # frame velocities on it. No makevelstatsregions (no independent box), no
        # velocityStats (that runs once in the prime, aggregating these frames).
        if not args.primeDir:
            u.myerror('--secondaryVelRebuild requires --primeDir')
        # The prime always passes its resolved --year list when it cascades, so
        # this default only applies to a hand-run secondary. All years, matching
        # --runVelstatsregions, whose grid rebuild this mirrors.
        years = requestedYears(args) or allYears()
        concurrent = concurrentTrackLimit(len(trackDirs), args.nThreads)
        syncThumbHeadersFromPrime(trackDirs, args.primeDir)
        runRefreshTies(trackDirs, years, overWrite=True, concurrent=concurrent)
        runMakeVelNoClean(trackDirs, years, args.nThreads)
        return

    secondaries = [] if args.noSecondary else secondaryProjectDirs(proj)
    applySecondary = bool(secondaries) and \
        (args.applySecondary or proj.get('applySecondary', False))

    if args.copyFiles:
        setupTrackDirs(trackDirs, args.copyFiles, overwrite=args.overWrite)
        if applySecondary:
            reqTracks = [os.path.basename(d) for d in trackDirs] if args.tracks else None
            for sec in secondaries:
                secTracks = secondaryTracks(sec, reqTracks)
                if reqTracks is not None and not secTracks:
                    print(f'Skipping secondary {sec}: no matching tracks')
                    continue
                extra = ['--copyFiles', '--noSecondary']
                if args.overWrite:
                    extra.append('--overWrite')
                if reqTracks is not None:
                    extra += ['--tracks', *secTracks]
                reinvokeInSecondary(sec, extra)
        return

    warnProjectConfig(proj)
    concurrent = concurrentTrackLimit(len(trackDirs), args.nThreads)
    # Refresh/vel_thumbs default: this year only. --runVelstatsregions overrides
    # to every year below -- it sizes the common region from the full history.
    askedYears = requestedYears(args)
    years = askedYears or currentYear()

    if args.fullUpdate:
        # MonthlyWorkflow steps 1-7 chained. The refresh steps (1/5) run whole
        # calendar years -- the current one unless --year says otherwise -- while
        # steps 6/7 are bound to the [firstDate, lastDate] lookback window
        # (lastDate = last date in the run). The two are deliberately separate:
        # a date window is the wrong unit for refreshties, which rebuilds years.
        if args.applySecondary:
            u.mywarning('--applySecondary has no effect with --fullUpdate; run '
                        '--fullUpdate separately in each secondary project')
        firstDate, lastDate = fullUpdateWindow(args)
        year = years   # refresh years: --year, else the current year only
        print(f'fullUpdate window (steps 6-7): {firstDate} .. {lastDate}   '
              f'refresh years (steps 1/5): {" ".join(str(y) for y in year)}')
        tieThresh = args.tieThresh if args.tieThresh is not None \
            else proj.get('tieThresh', 100)
        excludeThresh = args.excludeThresh if args.excludeThresh is not None \
            else proj.get('excludeThresh', 3)

        tStart = datetime.datetime.now()

        def _elapsed(since):
            """Whole-second h:mm:ss for a start time."""
            return datetime.timedelta(
                seconds=round((datetime.datetime.now() - since).total_seconds()))

        # A secondary tree shares the prime's velocityStats (symlinked), so its
        # step 3 is a no-op at best and a duplicate rebuild at worst. Run
        # --fullUpdate in the prime and in each secondary separately; the
        # secondary run keeps velocity/velocity_nocull current for autoclean.
        secondaryTree = inSecondaryTree(trackDirs)
        if secondaryTree:
            print('Secondary tree detected (velocityStats symlinked to the '
                  'prime): step 3 will be skipped -- run velocityStats in the '
                  'prime project.')

        def _step(n, label, fn, skip=None):
            if skip:
                print(f'{BLUEBOLD}----- Skipped step {n}: {label}   ({skip}) '
                      f'-----{RESET}', flush=True)
                return
            t0 = datetime.datetime.now()
            print(f'\n===== fullUpdate step {n}: {label}  ({t0:%Y-%m-%d %H:%M:%S}) =====')
            fn()
            print(f'{BLUEBOLD}----- Finished step {n}: {label}   '
                  f'step time {_elapsed(t0)}   total {_elapsed(tStart)} '
                  f'-----{RESET}', flush=True)

        _step(1, 'refresh ties + velocities',
              lambda: runRefreshTies(trackDirs, year, overWrite=False,
                                     concurrent=concurrent, tiesOnly=False))
        _step(2, 'nocull velocities',
              lambda: runUpdateNoCull(trackDirs, args.nThreads))
        _step(3, 'velocityStats',
              lambda: runVelStats(trackDirs, regionPath, velMap, concurrent),
              skip='secondary tree: velocityStats belongs to the prime'
                   if secondaryTree else None)
        _step(4, 'autoclean',
              lambda: runAutoClean(trackDirs, args.nThreads, args.sigThresh))
        _step(5, 'rerun ties (tiesOnly)',
              lambda: runRefreshTies(trackDirs, year, overWrite=False,
                                     concurrent=concurrent, tiesOnly=True))
        _step(6, 'window velocity rebuild',
              lambda: runMakeVelLastYear(trackDirs, firstDate, lastDate,
                                         args.nThreads))
        _step(7, 'check baselines',
              lambda: runCheckBaselines(trackDirs, firstDate, lastDate,
                                        tieThresh, excludeThresh))
        return

    if args.runRefresh:
        runRefreshTies(trackDirs, years, overWrite=args.overWrite,
                       concurrent=concurrent, tiesOnly=args.tiesOnly)
        return

    if args.updateNoCull:
        runUpdateNoCull(trackDirs, args.nThreads)
        return

    if args.rebuildVel:
        # Same pair as --fullUpdate step 6, standalone: rebuild velocity/ and
        # velocity_nocull/ over an arbitrary window rather than the default
        # 52-week lookback. Ties are untouched -- this only re-runs mosaic3d
        # through the existing runOff in each velocity dir.
        firstDate, lastDate = fullUpdateWindow(args)
        print(f'rebuildVel window: {firstDate} .. {lastDate}')
        runMakeVelLastYear(trackDirs, firstDate, lastDate, args.nThreads)
        return

    if args.runAutoClean:
        runAutoClean(trackDirs, args.nThreads, args.sigThresh)
        return

    if args.runVelStats:
        # velocityStats.py aggregates the secondaryDirectories frames itself, so
        # the prime run already folds in the secondary velocities -- no cascade.
        runVelStats(trackDirs, regionPath, velMap, concurrent)
        return

    if args.runVelstatsregions:
        # Every year, not just the current one: step 2 sizes one common box per
        # velocityStats range from the frames on disk, so a partial rebuild would
        # size the region from part of the history. --year still overrides.
        years = askedYears or allYears()
        print(f'runVelstatsregions years: {" ".join(str(y) for y in years)}')
        # 0: make sure every velocityStats range has a vel_thumb_header before
        # makeframetie (inside refreshties) tries to open it.
        setupTrackDirs(trackDirs, copyFiles=False)
        # 1: bootstrap render at the current (auto-extent) headers.
        runRefreshTies(trackDirs, years, overWrite=False, concurrent=concurrent)
        # 2: compute the common box and write it into the headers.
        runVelstatsRegions(trackDirs, concurrent)
        # 3: rebuild every frame on the common grid.
        runRefreshTies(trackDirs, years, overWrite=True, concurrent=concurrent)
        # 4: resize velocity_nocull to match (for downstream autoclean).
        runMakeVelNoClean(trackDirs, years, args.nThreads)
        # 4b: critical cascade -- rebuild the secondary (12-day clone) velocities
        # on the shared grid so step 5 can aggregate them. Always on unless
        # --noSecondary (which empties `secondaries`).
        reqTracks = [os.path.basename(d) for d in trackDirs]
        for sec in secondaries:
            secTracks = secondaryTracks(sec, reqTracks)
            if not secTracks:
                print(f'Skipping secondary {sec}: no matching tracks')
                continue
            extra = ['--secondaryVelRebuild', '--primeDir', PROJECT_DIR,
                     '--tracks', *secTracks,
                     '--nThreads', str(args.nThreads), '--noSecondary']
            # Always pass the resolved list (not args.year) so the secondary
            # rebuilds exactly the years the prime just did, rather than falling
            # back to its own default -- but clamped to the years this secondary
            # could possibly have. These are newer satellites: asking S1D (first
            # acquisitions 2026) to rebuild 2015 yields no tie_plan2015 and used
            # to abort the whole cascade, taking velocityStats with it.
            secYears, firstYear = clampYearsForSecondary(sec, secTracks, years)
            if firstYear is not None and len(secYears) < len(years):
                print(f'  {os.path.basename(sec)}: first acquisition {firstYear}, '
                      f'processing {len(secYears)} of {len(years)} years')
            if not secYears:
                print(f'Skipping secondary {sec}: no data in the requested years')
                continue
            extra += ['--year', *[str(y) for y in secYears]]
            reinvokeInSecondary(sec, extra)
        # 5: rebuild velocityStats from the culled velocity/ dirs (prime frames
        # plus the just-rebuilt secondary frames, via velocityStats.py).
        if not args.noUpdateVelStats:
            runVelStats(trackDirs, regionPath, velMap, concurrent)
        return

    raise NotImplementedError(
        'S1 track processing not yet implemented -- only --copyFiles, '
        '--runVelstatsregions and --runVelStats work so far. See differences 1-5 '
        'in the module docstring.')


if __name__ == '__main__':
    main()
