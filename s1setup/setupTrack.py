#!/usr/bin/env python3
"""
setupTrack.py - Sentinel-1 track setup orchestrator.

Replaces the setupTrack and setupTrack1 csh scripts. Runs the 5-step pipeline
for each unprocessed orbit under a track directory, or for explicit orbit dirs.

Usage:
  setupTrack.py track-N/                          # all orbits in a track dir
  setupTrack.py track-N/4547 track-N/4722         # explicit orbit dirs
  setupTrack.py track-N/ --check                   # dry run
  setupTrack.py track-N/ --firstdate 2025-01-01 --lastdate 2025-12-31
  setupTrack.py track-N/ --scratch /my/scratch     # override scratch location
  setupTrack.py track-N/ --overWrite --firstdate 2025-10-01 --lastdate 2025-12-31
                                                  # redo orbits in date range
                                                  # (only if TIFFs still present)
"""
import argparse
import contextlib
import getpass
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from s1setup.spinner import Spinner

RED   = '\033[91m'
BOLD  = '\033[1m'
RESET = '\033[0m'


def parseArgs():
    parser = argparse.ArgumentParser(
        description='Set up Sentinel-1 track: preproc + merge for each orbit',
        epilog='Part of the s1setup package.')
    parser.add_argument('dirs', nargs='*',
                        help='Track directory (e.g. track-1/) or explicit orbit '
                             'dirs (e.g. track-1/4547 track-1/4722)')
    parser.add_argument('--queue', action='store_true',
                        help='Take the units to process from toProcess.yaml '
                             '(written by checkFramesS1) instead of scanning '
                             'directories. Each unit is removed from the queue '
                             'as it finishes; failures are routed to '
                             'problem.yaml with a comment.')
    parser.add_argument('--assemblyDir', type=str, default=None,
                        help='Root holding the track-<n>/ dirs; queue units are '
                             'resolved against it (required with --queue)')
    parser.add_argument('--queueDir', type=str, default=None,
                        help='Directory holding the queue YAMLs '
                             '[default: --assemblyDir]')
    parser.add_argument('--track', type=str, default=None,
                        help='With --queue, only units of this track '
                             '(track-16 or 16)')
    parser.add_argument('--maxUnits', type=int, default=0,
                        help='With --queue, process at most N units '
                             '[0 = all]')
    parser.add_argument('--noStripTiffs', action='store_true',
                        help='With --queue, keep the measurement TIFFs after a '
                             'unit processes. They are ~30 GB per unit and are '
                             'stripped by default so the assembly tree does not '
                             'grow by the whole download volume; the source '
                             '.zip.1 is kept, so a unit can still be re-filed '
                             'and reprocessed')
    parser.add_argument('--lockHeld', action='store_true',
                        help='With --queue, do not take the assembly-tree lock '
                             '(the caller already holds it, e.g. autoupdateS1)')
    parser.add_argument('--check', action='store_true',
                        help='Dry run: show what would be processed, no execution')
    parser.add_argument('--firstdate', type=str, default=None,
                        metavar='YYYY-MM-DD',
                        help='Skip orbits with ascending node time before this date')
    parser.add_argument('--lastdate', type=str, default=None,
                        metavar='YYYY-MM-DD',
                        help='Skip orbits with ascending node time after this date')
    parser.add_argument('--scratch', type=str,
                        default=f'/dev/shm/{getpass.getuser()}/scratch',
                        help='Base scratch directory for intermediate SLCs '
                             '[default: /dev/shm/<user>/scratch]')
    parser.add_argument('--overWrite', action='store_true',
                        help='Reprocess orbits whose output already exists, but only '
                             'if the TIFF files are still present in the SAFE dirs. '
                             'Prompts for confirmation before deleting any output.')
    parser.add_argument('--noPrompt', action='store_true',
                        help='Skip confirmation prompt when used with --overWrite')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress subprocess output; show only setupTrack '
                             'step names and timing')
    parser.add_argument('--diskScratch', type=str,
                        default=f'/tmp/{getpass.getuser()}/scratch',
                        help='Fallback scratch on disk used if /dev/shm runs out '
                             'of space [default: /tmp/<user>/scratch]')
    args = parser.parse_args()

    if args.queue and args.dirs:
        parser.error('--queue takes its units from the queue, not positional dirs')
    if not args.queue and not args.dirs:
        parser.error('give a track directory or orbit dirs, or use --queue')
    if args.queue and not args.assemblyDir:
        parser.error('--queue needs --assemblyDir to resolve units against')
    if args.queue and args.overWrite:
        # --overWrite rmtree's output dirs; not against a list you have not read.
        parser.error('--overWrite is not allowed with --queue')

    firstDate = datetime.now() - timedelta(days=18200)
    lastDate = datetime(2100, 1, 1)
    if args.firstdate:
        firstDate = datetime.strptime(args.firstdate, '%Y-%m-%d')
    if args.lastdate:
        # Add one day so lastDate means end-of-day: an orbit on lastdate at any
        # time of day passes the "ascNodeTime > lastDate" filter in classifyOrbits.
        lastDate = datetime.strptime(args.lastdate, '%Y-%m-%d') + timedelta(days=1)
    return args, firstDate, lastDate


def isSourceOrbitDir(name):
    """True if the dir name looks like a raw orbit dir: digits with optional _seq suffix."""
    return bool(re.match(r'^\d{4,5}(_\d+)?$', name))


def parseOrbitSeq(name):
    """'4547' -> (4547, 0), '4547_1' -> (4547, 1)"""
    if '_' in name:
        parts = name.split('_')
        return int(parts[0]), int(parts[-1])
    return int(name), 0


def findOrbitDirs(trackDir):
    """Find all source orbit dirs under trackDir, sorted lexicographically."""
    result = []
    for entry in sorted(os.listdir(trackDir)):
        fullpath = os.path.join(trackDir, entry)
        if os.path.isdir(fullpath) and isSourceOrbitDir(entry):
            result.append(fullpath)
    return result


def readAscendingNodeTime(orbitPath):
    """Read the ascendingNodeTime file; return datetime or None."""
    f = os.path.join(orbitPath, 'ascendingNodeTime')
    if not os.path.exists(f):
        return None
    with open(f) as fp:
        line = fp.readline().strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(line, fmt)
        except ValueError:
            continue
    return None


def tiffsPresent(orbitPath):
    """True if measurement TIFF files still exist inside the SAFE directories."""
    return bool(glob.glob(os.path.join(orbitPath, '*.SAFE', 'measurement', '*.tiff')))


def writeSingleSafeTab(outputDir, date, seq):
    """Write SLC_tab_<date>-<seq> in outputDir for a single-SAFE unit: one
    line per swath of slc, slc.par, tops_par as bare file names, the same form
    catMultipleTops leaves for multi-SAFE units. Returns (tabPath, nSwaths)."""
    base = f'{date}-{seq}'
    tabPath = os.path.join(outputDir, f'SLC_tab_{base}')
    slcs = sorted(glob.glob(os.path.join(outputDir, f'{base}_iw?_hh.slc')))
    with open(tabPath, 'w') as fp:
        for slc in slcs:
            name = os.path.basename(slc)
            root = name[:-len('.slc')]
            fp.write(f'{name}  {name}.par  {root}.tops_par\n')
    return tabPath, len(slcs)


def slcScratchDir(orbitPath, orbitScratch):
    """Return orbitScratch if /dev/shm has enough room, else None (fall back to orbit dir).

    SLCs are ~2x TIFF size; merge tree peaks at ~1.5x the initial SLC total when
    all round-0 pairs run in parallel before cleanup. Factor of 3.0 on TIFF size
    gives comfortable headroom.
    """
    tiffBytes = sum(os.path.getsize(f)
                    for f in glob.glob(os.path.join(orbitPath, '*.SAFE',
                                                    'measurement', '*.tiff')))
    needed = int(tiffBytes * 2.2)
    free   = shutil.disk_usage(os.path.dirname(orbitScratch)).free
    if free >= needed:
        return orbitScratch
    print(f'  /dev/shm: {free // 2**30} GB free, need ~{needed // 2**30} GB '
          f'— falling back to orbit dir for SLC output')
    return None


def resolveQueueUnits(queueDir, assemblyDir, track=None, maxUnits=0):
    """Resolve toProcess.yaml entries to absolute orbit dirs.

    Queue units are 'track-<n>/<orbit>[_seq]' relative to assemblyDir, and the
    orbit-dir naming is identical to this module's, so they map straight onto
    orbit paths. Returns (units, unresolved) where units is
    [(unit, orbitPath, record), ...] in queue order and unresolved is
    [(unit, reason), ...].
    """
    # Imported lazily so the classic directory path keeps its fast start and
    # still runs where asfSearchAndDownload is not installed.
    try:
        from asfsearchdownload import queueS1
    except ImportError:
        print('Error: --queue needs the asfSearchAndDownload package',
              file=sys.stderr)
        sys.exit(1)

    if track and not track.startswith('track-'):
        track = f'track-{track}'
    assemblyDir = os.path.abspath(assemblyDir)
    units, unresolved = [], []
    for record in queueS1.readQueue(queueDir, 'toProcess'):
        unit = queueS1.entryUnit(record)
        if '/' not in unit:
            unresolved.append((unit, 'not a track/orbit unit'))
            continue
        if track and unit.split('/', 1)[0] != track:
            continue
        orbitPath = os.path.normpath(os.path.join(assemblyDir, unit))
        if os.path.commonpath([assemblyDir, orbitPath]) != assemblyDir:
            unresolved.append((unit, 'escapes assemblyDir'))
        elif not isSourceOrbitDir(os.path.basename(orbitPath)):
            unresolved.append((unit, 'not an orbit dir name'))
        elif not os.path.isdir(orbitPath):
            unresolved.append((unit, f'unit dir missing: {orbitPath}'))
        else:
            units.append((unit, orbitPath, record))
        if maxUnits and len(units) >= maxUnits:
            break
    return units, unresolved


# Skip codes whose units are finished with as far as the queue is concerned.
# 'dateFiltered' is deliberately absent: it is a property of this run's window,
# not of the unit, so the unit stays queued for a later run.
QUEUE_DONE_CODES = ('ignore', 'completed', 'outputExists')
QUEUE_PROBLEM_CODES = {'noSafe': 'no SAFE files under {path}'}


def applyDeltas(queueDir, remove=None, add=None):
    ''' Push one queue delta, reporting rather than raising if it cannot. '''
    from asfsearchdownload import queueS1
    if queueS1.applyQueueDeltas(queueDir, remove=remove, add=add) is None:
        print(f'{RED}  *** queue busy; {queueDir} not updated{RESET}')
        return False
    return True


def queueSkips(queueDir, runList, skips, unitFor, recordFor, unresolved, check):
    ''' Retire queued units that will not be processed: the finished ones just
    leave the queue, the anomalous ones move to problem with a comment. '''
    from asfsearchdownload import queueS1
    running = {path for path, _ in runList}
    remove, add = [], []
    for orbitPath, code, _ in skips:
        unit = unitFor.get(orbitPath)
        # 'retrying' units are in runList; their outcome decides, not this.
        if unit is None or orbitPath in running:
            continue
        if code in QUEUE_DONE_CODES:
            remove.append(unit)
        elif code in QUEUE_PROBLEM_CODES:
            remove.append(unit)
            add.append(queueS1.problemRecord(
                unit, QUEUE_PROBLEM_CODES[code].format(path=orbitPath),
                'setupTrack', base=recordFor.get(unit)))
    for unit, why in unresolved:
        remove.append(unit)
        add.append(queueS1.problemRecord(unit, why, 'setupTrack'))
    if not remove and not add:
        return
    if check:
        print(f'[check] would remove {len(remove)} unit(s) from toProcess, '
              f'add {len(add)} to problem')
        return
    applyDeltas(queueDir,
                remove={'toProcess': remove, 'problem': [e['unit'] for e in add]},
                add={'problem': add})


def stripMeasurementTiffs(orbitPath):
    ''' Delete the measurement TIFFs of a processed unit and report the bytes
    freed. They are ~30 GB per unit and are pure input: runPreProcTops reads
    them to build the SLCs but does not consume them, so without this the
    assembly tree grows by roughly the full download volume.

    Safe to lose because the source .zip.1 is kept in the archive -- a unit can
    be re-filed and reprocessed from it. Note it does foreclose setupTrack's
    in-place --overWrite, which requires the TIFFs (see tiffsPresent).

    Mirrors the glob used by insarScripts/bin/cleantopsbydate.py -tiff.
    '''
    freed = nRemoved = 0
    for tiff in glob.glob(os.path.join(orbitPath, '*.SAFE', 'measurement',
                                       '*.tiff')):
        try:
            size = os.path.getsize(tiff)
            os.remove(tiff)
            freed += size
            nRemoved += 1
        except OSError as exc:
            print(f'{RED}  *** could not remove {os.path.basename(tiff)}: '
                  f'{exc}{RESET}')
    return nRemoved, freed


def queueOutcome(queueDir, unit, record, status, detail, elapsed=None):
    ''' Record one unit's processing outcome in the queues. A failure is removed
    from problem before being re-added so the fresh comment lands and the entry
    counts as un-notified again. '''
    from asfsearchdownload import queueS1
    if unit is None:
        return
    if status == 'ok':
        applyDeltas(queueDir, remove={'toProcess': [unit]})
        # Flushed per unit, so an interrupted run keeps what already finished.
        queueS1.appendProcessed(queueDir,
                                queueS1.processedRecord(unit, elapsed, record))
    elif status == 'failed':
        comment = f'setup failed at {detail}' if detail else 'setup failed'
        applyDeltas(queueDir,
                    remove={'toProcess': [unit], 'problem': [unit]},
                    add={'problem': [queueS1.problemRecord(
                        unit, comment, 'setupTrack', base=record)]})
    # 'skipped' (and an interrupt, where status is None) leaves the unit queued.


def classifyOrbits(orbitDirs, firstDate, lastDate, overwrite, dateHint=None):
    """Classify orbits into those to process and those to skip.

    Returns:
        runList — list of (orbitPath, needsClear) where needsClear means
                  the existing output dir must be removed before processing.
        skips   — list of (orbitPath, code, msg); msg is None for the silent
                  date filter. The code lets queue mode decide each unit's
                  disposition; the message text is unchanged.
    """
    runList = []
    skips = []
    dateHint = dateHint or {}
    for orbitPath in orbitDirs:
        orbitPath = os.path.abspath(orbitPath)
        trackDir = os.path.dirname(orbitPath)
        orbitName = os.path.basename(orbitPath)
        orbit, seq = parseOrbitSeq(orbitName)
        outputDir = os.path.join(trackDir, f'{orbit}-{seq}')

        if os.path.exists(os.path.join(orbitPath, 'Ignore')):
            skips.append((orbitPath, 'ignore',
                          f'Skipping {orbitName}: Ignore file present'))
            continue

        if not glob.glob(os.path.join(orbitPath, '*.SAFE')):
            skips.append((orbitPath, 'noSafe',
                          f'Skipping {orbitName}: no SAFE files'))
            continue

        # Prefer the queued record's date: it is what checkFramesS1 reported and
        # survives a missing ascendingNodeTime cache.
        ascNodeTime = dateHint.get(orbitPath) or readAscendingNodeTime(orbitPath)
        if ascNodeTime is not None:
            if ascNodeTime < firstDate or ascNodeTime > lastDate:
                skips.append((orbitPath, 'dateFiltered', None))
                continue

        completed = os.path.exists(os.path.join(orbitPath, 'Completed'))
        failed    = os.path.exists(os.path.join(orbitPath, 'Failed'))

        if completed and not overwrite:
            skips.append((orbitPath, 'completed',
                          f'Skipping {orbitName}: Completed'))
        elif failed and not overwrite:
            skips.append((orbitPath, 'retrying',
                          f'Retrying {orbitName}: previous run Failed'))
            runList.append((orbitPath, False))
        elif not os.path.isdir(outputDir):
            runList.append((orbitPath, False))
        elif overwrite:
            if tiffsPresent(orbitPath):
                runList.append((orbitPath, True))
            else:
                skips.append((orbitPath, 'noTiffs',
                              f'Skipping {orbitName}: TIFF files removed from '
                              'SAFE, cannot reprocess'))
        else:
            skips.append((orbitPath, 'outputExists',
                          f'Skipping {orbitName}: {outputDir} already exists'))
    return runList, skips


def runStep(cmd, cwd, logfp, stepName, shellExe=None, quiet=False):
    """Run a subprocess step. stdout/stderr captured to logfp only. Returns True on success."""
    if isinstance(cmd, list):
        cmdStr = ' '.join(cmd)
    else:
        cmdStr = cmd
    print(f'  [{stepName}] {cmdStr}')
    logfp.write(f'\n=== {stepName} ===\ncmd: {cmdStr}\n')
    logfp.flush()
    t0 = time.time()
    try:
        if shellExe:
            proc = subprocess.run(cmdStr, shell=True, executable=shellExe,
                                  cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        else:
            proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        elapsed = time.time() - t0
        if proc.stdout:
            logfp.write(proc.stdout)
            logfp.flush()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmdStr)
        logfp.write(f'{stepName}: OK  ({elapsed:.1f}s)\n')
        print(f'{RED}  [{stepName}] done in {elapsed:.1f}s{RESET}')
        return True
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - t0
        msg = f'FAILED: {stepName} returned exit code {e.returncode} ({elapsed:.1f}s)'
        print(f'{RED}  *** {msg}{RESET}')
        logfp.write(f'{msg}\n')
        return False


def _shmLow(scratchBase, threshold=0.85):
    """True if the filesystem hosting scratchBase is more than threshold full."""
    try:
        usage = shutil.disk_usage(scratchBase)
        return usage.used / usage.total > threshold
    except OSError:
        return False


def _runSteps(orbitPath, scratchBase, trackDir, orbitName, outputDir, logfp, quiet):
    """Execute pipeline steps 1-5. Returns (ok, detail); detail names the failing
    step so the caller can report why, e.g. into a problem-queue comment."""
    if not runStep(['findgain.py', orbitPath], trackDir, logfp,
                   'findgain', quiet=quiet):
        return False, 'findgain'

    orbitScratch = os.path.join(scratchBase, orbitName)
    if os.path.isdir(orbitScratch):
        print(f'  Removing stale scratch dir: {orbitScratch}')
        logfp.write(f'pre-wipe stale scratch: {orbitScratch}\n')
        shutil.rmtree(orbitScratch)
    slcDest = slcScratchDir(orbitPath, orbitScratch)
    cmd = ['runPreProcTops.py', orbitName]
    if slcDest:
        cmd += ['--scratch', slcDest]
    logfp.write(f'runPreProcTops SLC dest: {slcDest or "orbit dir"}\n')
    if not runStep(cmd, trackDir, logfp, 'runPreProcTops', quiet=quiet):
        return False, 'runPreProcTops'

    frameRange = os.path.join(trackDir, 'frameRange')
    if os.path.exists(frameRange):
        trimScript = (shutil.which('trimTopsSLCsToFit.py') or
                      shutil.which('trimTopsSLCsToFit'))
        if trimScript is None:
            print('  *** trimTopsSLCsToFit(.py) not found in PATH')
            logfp.write('trimTopsSLCsToFit: not found in PATH\n')
            return False, 'trimTopsSLCsToFit not found in PATH'
        cmd = ([trimScript] if trimScript.endswith('.py')
               else ['/bin/csh', trimScript])
        if not runStep(cmd, orbitPath, logfp, 'trimTopsSLCsToFit', quiet=quiet):
            return False, 'trimTopsSLCsToFit'
    else:
        print('  [trimTopsSLCsToFit] skipped (no frameRange file in track dir)')
        logfp.write('trimTopsSLCsToFit: skipped (no frameRange)\n')

    nSafe = len(glob.glob(os.path.join(orbitPath, '*.SAFE')))
    if nSafe > 1:
        if not runStep(['catMultipleTops.py', '--scratch', scratchBase],
                       orbitPath, logfp, 'catMultipleTops', quiet=quiet):
            return False, 'catMultipleTops'
    else:
        print(f'  [catMultipleTops] skipped ({nSafe} SAFE) — renaming SLCs to output dir')
        logfp.write(f'catMultipleTops: skipped ({nSafe} SAFE), renaming SLCs\n')
        _, seq = parseOrbitSeq(orbitName)
        srcDir = slcDest if slcDest else orbitPath
        os.makedirs(outputDir, exist_ok=True)
        nMoved = 0
        date = None
        for fname in sorted(os.listdir(srcDir)):
            m = re.match(r'^(\d{8})t\d{6}(_iw\d_hh\..+)$', fname)
            if not m:
                continue
            date = m.group(1)
            newName = f'{date}-{seq}{m.group(2)}'
            shutil.move(os.path.join(srcDir, fname),
                        os.path.join(outputDir, newName))
            logfp.write(f'  {fname}  ->  {newName}\n')
            nMoved += 1
        logfp.flush()
        if nMoved == 0:
            print(f'{RED}  *** no SLC files found to rename in {srcDir}{RESET}')
            logfp.write(f'ERROR: no SLC files found in {srcDir}\n')
            return False, f'no SLC files found in {srcDir}'
        print(f'  [catMultipleTops] {nMoved} file(s) moved to {os.path.basename(outputDir)}')
        # The setup_<orbit>_<burst> scripts start from `ls SLC_tab*-<seq>` in
        # this dir; without the tab every Gamma step in them runs with empty
        # arguments. catMultipleTops writes it for multi-SAFE units.
        tabPath, nSwaths = writeSingleSafeTab(outputDir, date, seq)
        logfp.write(f'wrote {os.path.basename(tabPath)} ({nSwaths} swaths)\n')
        # Clean up scratch dir (catMultipleTops handles this itself in the multi-SAFE path)
        if slcDest and os.path.isdir(orbitScratch):
            shutil.rmtree(orbitScratch, ignore_errors=True)
            logfp.write(f'removed scratch: {orbitScratch}\n')
        # Copy ascendingNodeTime and absolutegain, then compute burst times.
        # In the multi-SAFE path catMultipleTops does all three; mirror that here.
        for sidecar in ('ascendingNodeTime', 'absolutegain'):
            src = os.path.join(orbitPath, sidecar)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(outputDir, sidecar))
                logfp.write(f'copied {sidecar} -> {os.path.basename(outputDir)}/\n')
            else:
                print(f'{RED}  *** {sidecar} not found in {orbitPath}{RESET}')
                logfp.write(f'WARNING: {sidecar} not found in {orbitPath}\n')
        if not runStep(['computeBurstTimes.py'], outputDir, logfp,
                       'computeBurstTimes', quiet=quiet):
            return False, 'computeBurstTimes'

    # Remove SLC_tab files left in the track dir by runPreProcTops
    for tab in glob.glob(os.path.join(trackDir, 'SLC_tab*t*')):
        os.remove(tab)
        logfp.write(f'removed {os.path.basename(tab)}\n')

    if not runStep(['radcalcoeffs.py', outputDir, orbitPath],
                   trackDir, logfp, 'radcalcoeffs', quiet=quiet):
        return False, 'radcalcoeffs'

    logfp.write(f'\nDone: {datetime.now()}\n')
    return True, None


def processOrbit(orbitPath, scratchBase, diskScratch, firstDate, lastDate,
                 check, quiet=False, logfp=None):
    """Run the 5-step pipeline for one orbit dir.

    Returns (status, detail) with status one of 'ok' / 'skipped' / 'failed'.
    The early skips used to return True as well, so a caller could not tell
    "processed" from "did nothing" -- which queue mode has to distinguish.
    """
    orbitPath = os.path.abspath(orbitPath)
    trackDir = os.path.dirname(orbitPath)
    orbitName = os.path.basename(orbitPath)
    orbit, seq = parseOrbitSeq(orbitName)
    outputDir = os.path.join(trackDir, f'{orbit}-{seq}')

    # Skip if already done
    if os.path.isdir(outputDir):
        print(f'Skipping {orbitName}: {outputDir} already exists')
        return 'skipped', 'output dir already exists'

    # Skip if no SAFE files
    if not glob.glob(os.path.join(orbitPath, '*.SAFE')):
        print(f'Skipping {orbitName}: no SAFE files')
        return 'skipped', 'no SAFE files'

    # Date filter — read from existing ascendingNodeTime if present
    ascNodeTime = readAscendingNodeTime(orbitPath)
    if ascNodeTime is not None:
        if ascNodeTime < firstDate or ascNodeTime > lastDate:
            print(f'Skipping {orbitName}: {ascNodeTime.date()} outside date range')
            return 'skipped', 'outside date range'

    if check:
        dateStr = ascNodeTime.date() if ascNodeTime else 'unknown date'
        print(f'Would process: {orbitName}  ->  {outputDir}  ({dateStr})')
        return 'skipped', 'check mode'

    completedFile = os.path.join(orbitPath, 'Completed')
    failedFile    = os.path.join(orbitPath, 'Failed')
    for f in (completedFile, failedFile):
        if os.path.exists(f):
            os.remove(f)

    print(f'\nProcessing {orbitName} -> {outputDir}')
    logfp.write(f'\n{"=" * 60}\norbit={orbitName}  started={datetime.now()}\n')
    logfp.flush()
    success = False
    t0 = time.time()
    spinner = Spinner().start()
    try:
        ok, detail = _runSteps(orbitPath, scratchBase, trackDir, orbitName,
                               outputDir, logfp, quiet)

        if not ok and _shmLow(scratchBase):
            # Likely ENOSPC — wipe /dev/shm remnants and retry on disk
            shutil.rmtree(os.path.join(scratchBase, orbitName), ignore_errors=True)
            print(f'{RED}  /dev/shm nearly full — retrying with disk scratch: '
                  f'{diskScratch}{RESET}')
            logfp.write(f'Retrying with disk scratch: {diskScratch}\n')
            os.makedirs(diskScratch, exist_ok=True)
            ok, detail = _runSteps(orbitPath, diskScratch, trackDir,
                                   orbitName, outputDir, logfp, quiet)

        elapsed = time.time() - t0
        mins, secs = divmod(elapsed, 60)
        elapsedStr = f'{int(mins)}m {secs:.0f}s' if mins else f'{secs:.1f}s'

        if not ok:
            logfp.write(f'orbit {orbitName} FAILED  ({elapsedStr})\n')
            return 'failed', detail

        success = True
        print(f'{BOLD}  Done -> {outputDir}  ({elapsedStr}){RESET}')
        logfp.write(f'orbit {orbitName} done  ({elapsedStr})\n')
        return 'ok', None
    finally:
        spinner.stop()
        open(completedFile if success else failedFile, 'w').close()


def main():
    args, firstDate, lastDate = parseArgs()
    # The assembly-tree lock, when taken, is held for the whole run.
    with contextlib.ExitStack() as lockStack:
        runSetup(args, firstDate, lastDate, lockStack)


def runSetup(args, firstDate, lastDate, lockStack):
    os.makedirs(args.scratch, exist_ok=True)

    queueDir = assemblyDir = None
    unitFor = {}          # orbitPath -> queue unit name
    recordFor = {}        # orbitPath -> queue record
    dateHint = {}         # orbitPath -> date from the queue record
    unresolved = []

    if args.queue:
        assemblyDir = os.path.abspath(args.assemblyDir)
        # Lazy, as everywhere else here: the classic directory path must not
        # pay for the asfSearchAndDownload import, nor require it installed.
        from asfsearchdownload import queueS1 as _queueS1
        queueDir = _queueS1.resolveQueueDir(
            assemblyDir, args.queueDir, migrate=not args.check)
        # Serialize against checkFramesS1, which shutil.moves SAFE dirs while
        # restructuring -- that would pull data out from under a running unit.
        # --lockHeld means our caller (autoupdateS1) already holds it.
        if not args.lockHeld and not args.check:
            from asfsearchdownload import queueS1
            lockCtx = queueS1.assemblyLock(assemblyDir)
            if not lockStack.enter_context(lockCtx):
                print(f'Error: another run holds '
                      f'{os.path.join(assemblyDir, queueS1.ASSEMBLY_LOCK_NAME)}',
                      file=sys.stderr)
                sys.exit(1)
        units, unresolved = resolveQueueUnits(queueDir, assemblyDir,
                                              args.track, args.maxUnits)
        # A wrong --assemblyDir resolves nothing; refuse rather than moving the
        # whole queue into problem.
        if unresolved and len(unresolved) > (len(units) + len(unresolved)) // 2:
            print(f'Error: {len(unresolved)} of {len(units) + len(unresolved)} '
                  'queued units did not resolve; check --assemblyDir',
                  file=sys.stderr)
            sys.exit(1)
        orbitDirs = []
        for unit, orbitPath, record in units:
            orbitDirs.append(orbitPath)
            unitFor[orbitPath] = unit
            recordFor[orbitPath] = record
            if isinstance(record, dict) and record.get('date'):
                try:
                    dateHint[orbitPath] = datetime.strptime(record['date'],
                                                            '%Y-%m-%d')
                except ValueError:
                    pass
        print(f'{len(orbitDirs)} unit(s) from {os.path.join(queueDir, "toProcess.yaml")}'
              + (f', {len(unresolved)} unresolved' if unresolved else ''))
        for unit, why in unresolved:
            print(f'  unresolved: {unit}: {why}')
    else:
        # Build list of candidate orbit dirs
        orbitDirs = []
        for d in args.dirs:
            d = d.rstrip('/')
            if not os.path.isdir(d):
                print(f'Error: not a directory: {d}', file=sys.stderr)
                sys.exit(1)
            name = os.path.basename(d)
            if isSourceOrbitDir(name):
                orbitDirs.append(os.path.abspath(d))
            else:
                orbitDirs += findOrbitDirs(d)

    if not orbitDirs and not unresolved:
        print('No orbit directories found.')
        sys.exit(0)

    runList, skips = classifyOrbits(orbitDirs, firstDate, lastDate,
                                    args.overWrite, dateHint=dateHint)

    for _, _, msg in skips:
        if msg:
            print(msg)

    if args.queue:
        queueSkips(queueDir, runList, skips, unitFor, recordFor, unresolved,
                   args.check)

    if not runList:
        print('Nothing to process.')
        return

    overwriteList = [(p, c) for p, c in runList if c]
    newList      = [(p, c) for p, c in runList if not c]

    if args.check:
        for orbitPath, needsClear in runList:
            orbit, seq = parseOrbitSeq(os.path.basename(orbitPath))
            outputDir = os.path.join(os.path.dirname(orbitPath), f'{orbit}-{seq}')
            ascNodeTime = readAscendingNodeTime(orbitPath)
            dateStr = ascNodeTime.date() if ascNodeTime else 'unknown'
            action = 'Reprocess' if needsClear else 'Process new'
            print(f'{action}: {os.path.basename(orbitPath)}  ({dateStr})  ->  {outputDir}')
        return

    if overwriteList:
        print(f'\nThe following {len(overwriteList)} existing orbit(s) will be '
              f'deleted and reprocessed:')
        for op, _ in overwriteList:
            orbit, seq = parseOrbitSeq(os.path.basename(op))
            outputDir = os.path.join(os.path.dirname(op), f'{orbit}-{seq}')
            ascNodeTime = readAscendingNodeTime(op)
            dateStr = ascNodeTime.date() if ascNodeTime else 'unknown'
            print(f'  {os.path.basename(op):12s}  {dateStr}  ->  {outputDir}')
        if not args.noPrompt:
            resp = input(f'\nDelete and reprocess these {len(overwriteList)} orbit(s)? [y/N] '
                         ).strip().lower()
            if resp != 'y':
                print('Aborted.')
                sys.exit(0)
        for op, _ in overwriteList:
            orbit, seq = parseOrbitSeq(os.path.basename(op))
            outputDir = os.path.join(os.path.dirname(op), f'{orbit}-{seq}')
            print(f'  Removing {outputDir}')
            shutil.rmtree(outputDir)
            for sentinel in ('Completed', 'Failed'):
                f = os.path.join(op, sentinel)
                if os.path.exists(f):
                    os.remove(f)

    # Group orbits by track directory so each track gets one log file.
    byTrack = {}
    for orbitPath, needsClear in runList:
        td = os.path.dirname(orbitPath)
        byTrack.setdefault(td, []).append((orbitPath, needsClear))

    nFailed = nStripped = 0
    bytesFreed = 0
    for trackDir, orbits in byTrack.items():
        trackName = os.path.basename(trackDir) or 'track'
        logPath = os.path.join(trackDir,
                               f'log.{trackName}.{os.getpid()}.'
                               f'{datetime.now().strftime("%Y%m%d")}')
        print(f'Log: {logPath}')
        with open(logPath, 'w') as runLog:
            runLog.write(f'setupTrack.py  track={trackName}  '
                         f'started={datetime.now()}  pid={os.getpid()}\n')
            runLog.write(f'args: {" ".join(sys.argv[1:])}\n')
            for orbitPath, _ in orbits:
                status = detail = None
                unitStart = time.time()
                try:
                    status, detail = processOrbit(
                        orbitPath, args.scratch, args.diskScratch,
                        firstDate, lastDate, False,
                        quiet=args.quiet, logfp=runLog)
                    if status == 'failed':
                        nFailed += 1
                finally:
                    # Flush per unit rather than at the end: a queue run lasts
                    # hours, and the queue should reflect reality at every
                    # instant so an interrupt neither loses nor redoes work.
                    if args.queue:
                        queueOutcome(queueDir, unitFor.get(orbitPath),
                                     recordFor.get(orbitPath), status, detail,
                                     elapsed=time.time() - unitStart)
                        # Reclaim the ~30 GB of measurement TIFFs as soon as the
                        # unit is recorded done -- during the run, not after it,
                        # so a long assembly does not grow the tree meanwhile.
                        if status == 'ok' and not args.noStripTiffs:
                            n, freed = stripMeasurementTiffs(orbitPath)
                            if n:
                                nStripped += n
                                bytesFreed += freed
                                print(f'  stripped {n} TIFF(s), '
                                      f'{freed / 1e9:.1f} GB freed')
                                runLog.write(f'stripped {n} tiff, '
                                             f'{freed} bytes\n')

    if args.queue:
        # Fold the dated files into the all-time record and drop the old ones.
        # Sweeps every dated file, not just this run's, so one orphaned by an
        # earlier crash is picked up here; pruning only happens after that.
        from asfsearchdownload import queueS1
        merged = queueS1.mergeProcessed(queueDir)
        if merged is None:
            print(f'{RED}  *** queue busy; completed.yaml not updated{RESET}')
        else:
            nAdded, nPruned = merged
            print(f'completed.yaml: +{nAdded} unit(s)'
                  + (f'; pruned {nPruned} dated file(s) older than '
                     f'{queueS1.RETAIN_DAYS} days' if nPruned else ''))

    if nFailed:
        print(f'\n{nFailed} orbit(s) failed.')
        sys.exit(1)
    print('\nDone.')


if __name__ == '__main__':
    main()
