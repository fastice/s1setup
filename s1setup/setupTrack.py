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
    parser.add_argument('dirs', nargs='+',
                        help='Track directory (e.g. track-1/) or explicit orbit '
                             'dirs (e.g. track-1/4547 track-1/4722)')
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


def classifyOrbits(orbitDirs, firstDate, lastDate, overwrite):
    """Classify orbits into those to process and those to skip.

    Returns:
        toProcess  — list of (orbitPath, needsClear) where needsClear means
                     the existing output dir must be removed before processing.
        skipMsgs   — list of human-readable skip messages.
    """
    toProcess = []
    skipMsgs = []
    for orbitPath in orbitDirs:
        orbitPath = os.path.abspath(orbitPath)
        trackDir = os.path.dirname(orbitPath)
        orbitName = os.path.basename(orbitPath)
        orbit, seq = parseOrbitSeq(orbitName)
        outputDir = os.path.join(trackDir, f'{orbit}-{seq}')

        if os.path.exists(os.path.join(orbitPath, 'Ignore')):
            skipMsgs.append(f'Skipping {orbitName}: Ignore file present')
            continue

        if not glob.glob(os.path.join(orbitPath, '*.SAFE')):
            skipMsgs.append(f'Skipping {orbitName}: no SAFE files')
            continue

        ascNodeTime = readAscendingNodeTime(orbitPath)
        if ascNodeTime is not None:
            if ascNodeTime < firstDate or ascNodeTime > lastDate:
                continue  # silently filter by date

        completed = os.path.exists(os.path.join(orbitPath, 'Completed'))
        failed    = os.path.exists(os.path.join(orbitPath, 'Failed'))

        if completed and not overwrite:
            skipMsgs.append(f'Skipping {orbitName}: Completed')
        elif failed and not overwrite:
            skipMsgs.append(f'Retrying {orbitName}: previous run Failed')
            toProcess.append((orbitPath, False))
        elif not os.path.isdir(outputDir):
            toProcess.append((orbitPath, False))
        elif overwrite:
            if tiffsPresent(orbitPath):
                toProcess.append((orbitPath, True))
            else:
                skipMsgs.append(
                    f'Skipping {orbitName}: TIFF files removed from SAFE, cannot reprocess')
        else:
            skipMsgs.append(f'Skipping {orbitName}: {outputDir} already exists')
    return toProcess, skipMsgs


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
    """Execute pipeline steps 1-5. Returns True on success."""
    if not runStep(['findgain.py', orbitPath], trackDir, logfp,
                   'findgain', quiet=quiet):
        return False

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
        return False

    frameRange = os.path.join(trackDir, 'frameRange')
    if os.path.exists(frameRange):
        trimScript = (shutil.which('trimTopsSLCsToFit.py') or
                      shutil.which('trimTopsSLCsToFit'))
        if trimScript is None:
            print('  *** trimTopsSLCsToFit(.py) not found in PATH')
            logfp.write('trimTopsSLCsToFit: not found in PATH\n')
            return False
        cmd = ([trimScript] if trimScript.endswith('.py')
               else ['/bin/csh', trimScript])
        if not runStep(cmd, orbitPath, logfp, 'trimTopsSLCsToFit', quiet=quiet):
            return False
    else:
        print('  [trimTopsSLCsToFit] skipped (no frameRange file in track dir)')
        logfp.write('trimTopsSLCsToFit: skipped (no frameRange)\n')

    nSafe = len(glob.glob(os.path.join(orbitPath, '*.SAFE')))
    if nSafe > 1:
        if not runStep(['catMultipleTops.py', '--scratch', scratchBase],
                       orbitPath, logfp, 'catMultipleTops', quiet=quiet):
            return False
    else:
        print(f'  [catMultipleTops] skipped ({nSafe} SAFE) — renaming SLCs to output dir')
        logfp.write(f'catMultipleTops: skipped ({nSafe} SAFE), renaming SLCs\n')
        _, seq = parseOrbitSeq(orbitName)
        srcDir = slcDest if slcDest else orbitPath
        os.makedirs(outputDir, exist_ok=True)
        nMoved = 0
        for fname in sorted(os.listdir(srcDir)):
            m = re.match(r'^(\d{8})t\d{6}(_iw\d_hh\..+)$', fname)
            if not m:
                continue
            newName = f'{m.group(1)}-{seq}{m.group(2)}'
            shutil.move(os.path.join(srcDir, fname),
                        os.path.join(outputDir, newName))
            logfp.write(f'  {fname}  ->  {newName}\n')
            nMoved += 1
        logfp.flush()
        if nMoved == 0:
            print(f'{RED}  *** no SLC files found to rename in {srcDir}{RESET}')
            logfp.write(f'ERROR: no SLC files found in {srcDir}\n')
            return False
        print(f'  [catMultipleTops] {nMoved} file(s) moved to {os.path.basename(outputDir)}')
        # Clean up scratch dir (catMultipleTops handles this itself in the multi-SAFE path)
        if slcDest and os.path.isdir(orbitScratch):
            shutil.rmtree(orbitScratch, ignore_errors=True)
            logfp.write(f'removed scratch: {orbitScratch}\n')
        # Copy ascendingNodeTime and compute burst times.
        # In the multi-SAFE path catMultipleTops does both; mirror that here.
        srcAnt = os.path.join(orbitPath, 'ascendingNodeTime')
        dstAnt = os.path.join(outputDir, 'ascendingNodeTime')
        if os.path.exists(srcAnt):
            shutil.copy2(srcAnt, dstAnt)
            logfp.write(f'copied ascendingNodeTime -> {os.path.basename(outputDir)}/\n')
        else:
            print(f'{RED}  *** ascendingNodeTime not found in {orbitPath}{RESET}')
            logfp.write(f'WARNING: ascendingNodeTime not found in {orbitPath}\n')
        if not runStep(['computeBurstTimes.py'], outputDir, logfp,
                       'computeBurstTimes', quiet=quiet):
            return False

    # Remove SLC_tab files left in the track dir by runPreProcTops
    for tab in glob.glob(os.path.join(trackDir, 'SLC_tab*t*')):
        os.remove(tab)
        logfp.write(f'removed {os.path.basename(tab)}\n')

    if not runStep(['radcalcoeffs.py', outputDir, orbitPath],
                   trackDir, logfp, 'radcalcoeffs', quiet=quiet):
        return False

    logfp.write(f'\nDone: {datetime.now()}\n')
    return True


def processOrbit(orbitPath, scratchBase, diskScratch, firstDate, lastDate,
                 check, quiet=False, logfp=None):
    """Run the 5-step pipeline for one orbit dir. Returns True on success."""
    orbitPath = os.path.abspath(orbitPath)
    trackDir = os.path.dirname(orbitPath)
    orbitName = os.path.basename(orbitPath)
    orbit, seq = parseOrbitSeq(orbitName)
    outputDir = os.path.join(trackDir, f'{orbit}-{seq}')

    # Skip if already done
    if os.path.isdir(outputDir):
        print(f'Skipping {orbitName}: {outputDir} already exists')
        return True

    # Skip if no SAFE files
    if not glob.glob(os.path.join(orbitPath, '*.SAFE')):
        print(f'Skipping {orbitName}: no SAFE files')
        return True

    # Date filter — read from existing ascendingNodeTime if present
    ascNodeTime = readAscendingNodeTime(orbitPath)
    if ascNodeTime is not None:
        if ascNodeTime < firstDate or ascNodeTime > lastDate:
            print(f'Skipping {orbitName}: {ascNodeTime.date()} outside date range')
            return True

    if check:
        dateStr = ascNodeTime.date() if ascNodeTime else 'unknown date'
        print(f'Would process: {orbitName}  ->  {outputDir}  ({dateStr})')
        return True

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
        ok = _runSteps(orbitPath, scratchBase, trackDir, orbitName,
                       outputDir, logfp, quiet)

        if not ok and _shmLow(scratchBase):
            # Likely ENOSPC — wipe /dev/shm remnants and retry on disk
            shutil.rmtree(os.path.join(scratchBase, orbitName), ignore_errors=True)
            print(f'{RED}  /dev/shm nearly full — retrying with disk scratch: '
                  f'{diskScratch}{RESET}')
            logfp.write(f'Retrying with disk scratch: {diskScratch}\n')
            os.makedirs(diskScratch, exist_ok=True)
            ok = _runSteps(orbitPath, diskScratch, trackDir, orbitName,
                           outputDir, logfp, quiet)

        elapsed = time.time() - t0
        mins, secs = divmod(elapsed, 60)
        elapsedStr = f'{int(mins)}m {secs:.0f}s' if mins else f'{secs:.1f}s'

        if not ok:
            logfp.write(f'orbit {orbitName} FAILED  ({elapsedStr})\n')
            return False

        success = True
        print(f'{BOLD}  Done -> {outputDir}  ({elapsedStr}){RESET}')
        logfp.write(f'orbit {orbitName} done  ({elapsedStr})\n')
        return True
    finally:
        spinner.stop()
        open(completedFile if success else failedFile, 'w').close()


def main():
    args, firstDate, lastDate = parseArgs()
    os.makedirs(args.scratch, exist_ok=True)

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

    if not orbitDirs:
        print('No orbit directories found.')
        sys.exit(0)

    toProcess, skipMsgs = classifyOrbits(orbitDirs, firstDate, lastDate, args.overWrite)

    for msg in skipMsgs:
        print(msg)

    if not toProcess:
        print('Nothing to process.')
        return

    overwriteList = [(p, c) for p, c in toProcess if c]
    newList      = [(p, c) for p, c in toProcess if not c]

    if args.check:
        for orbitPath, needsClear in toProcess:
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
    for orbitPath, needsClear in toProcess:
        td = os.path.dirname(orbitPath)
        byTrack.setdefault(td, []).append((orbitPath, needsClear))

    nFailed = 0
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
                ok = processOrbit(orbitPath, args.scratch, args.diskScratch,
                                  firstDate, lastDate, False,
                                  quiet=args.quiet, logfp=runLog)
                if not ok:
                    nFailed += 1

    if nFailed:
        print(f'\n{nFailed} orbit(s) failed.')
        sys.exit(1)
    print('\nDone.')


if __name__ == '__main__':
    main()
