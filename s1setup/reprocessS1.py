#!/usr/bin/env python3
"""
reprocessS1.py - refile, reassemble and reframe Sentinel-1 orbits over a date
window, one unit at a time, driven by a queue.

Rebuilds what an archive sweep has already thrown away. A unit that has been
through setupTrack keeps its .SAFE directories but not their measurement
TIFFs, and the setup_<orbit>_<burst> scripts delete the subswath SLCs as they
mosaic each frame -- so redoing anything downstream of the frames means going
back to the archive zips. For each queued unit this runs

  refile   archive .zip.1 -> the unit's own .SAFE dirs (measurement TIFFs back)
  assemble setupTrack --overWrite            -> <orbit>-<seq>/*_iw?_*.slc
  frames   setup_<orbit>_<burst>, in order   -> <orbit>_<burst>/<...>.slc
  clean    drop the TIFFs and the subswath SLCs again

Assemble and frames both stage through /dev/shm, so they are never overlapped:
the loop is strictly one unit, one step at a time.

The queue is the one from asfsearchdownload.queueS1 (its lock, its atomic
writes, its processed/completed lifecycle) pointed at a queueDir of its own, so
it shares no state with the nightly autoupdateS1.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import yaml

from asfsearchdownload import fileS1, queueS1
from s1setup import setupTrack as setupTrackMod

RED = '\033[91m'
BOLD = '\033[1m'
RESET = '\033[0m'

SOURCE = 'reprocessS1'
STAGING = '.staging'
RUN_LOCK_NAME = '.reprocessS1.run.lock'
# Comfortably above one unit (~12 min measured) but well below a whole drain,
# because the lock is heartbeated per unit rather than held on one timestamp.
RUN_LOCK_STALE = 3600

# Bytes per pixel of the Gamma image_format a frame SLC is written in.
PIXEL_BYTES = {'SCOMPLEX': 4, 'FCOMPLEX': 8}


def parseArgs():
    parser = argparse.ArgumentParser(
        description='Refile, reassemble and reframe S1 orbits over a date '
                    'window, from a queue',
        epilog='Part of the s1setup package.')
    parser.add_argument('target', nargs='?', default=None,
                        help='With --buildQueue, the config yaml '
                             '[./reprocess.yaml]. Otherwise the queue to run: '
                             'either its toProcess.yaml or the directory '
                             'holding it [.]')
    parser.add_argument('--buildQueue', action='store_true',
                        help='Scan the window and write toProcess.yaml instead '
                             'of running anything')
    parser.add_argument('--info', action='store_true',
                        help='Print the queue paths, counts and lock state')
    parser.add_argument('--config', type=str, default=None,
                        help='Config yaml to run with [the one --buildQueue '
                             'recorded beside the queue]')
    parser.add_argument('--firstDate', type=str, default=None,
                        metavar='YYYY-MM-DD',
                        help='Window start, overriding the config')
    parser.add_argument('--lastDate', type=str, default=None,
                        metavar='YYYY-MM-DD',
                        help='Window end, overriding the config')
    parser.add_argument('--tracks', type=str, default=None,
                        help='Only these tracks, space or comma separated: '
                             '"16 25 90" or "track-16,track-25" [all]')
    parser.add_argument('--maxOrbits', type=int, default=0,
                        help='Stop after N units [0 = drain the queue]. Use a '
                             'small number to try it out.')
    parser.add_argument('--rebuildFrames', action='store_true',
                        help='Rebuild every frame of a unit, not only the ones '
                             'whose <orbit>_<burst>.slc is missing or the wrong '
                             'size')
    parser.add_argument('--noPrefetch', action='store_true',
                        help='Do not refile the next unit while the current '
                             'one assembles. The overlap is safe (refiling '
                             'never touches /dev/shm) but holds a second '
                             "unit's TIFFs, ~25 GB, on disk")
    parser.add_argument('--noClean', action='store_true',
                        help='Keep the measurement TIFFs and subswath SLCs '
                             'after a unit finishes (they are ~40 GB a unit)')
    parser.add_argument('--stopOnError', action='store_true',
                        help='Stop at the first failed unit instead of moving '
                             'it to problem.yaml and carrying on')
    parser.add_argument('--check', action='store_true',
                        help='Dry run: report what would happen, change nothing')
    return parser.parse_args()


def readConfig(path, args):
    """ Config keys, with the defaults that make sense for S1-Greenland.

    CLI --firstDate/--lastDate/--tracks override the file, the same way
    autoupdateS1 lets a one-off run widen or narrow its window.
    """
    if not os.path.exists(path):
        sys.exit(f'no such config: {path}')
    with open(path) as fp:
        config = yaml.safe_load(fp) or {}
    config['configPath'] = os.path.abspath(path)
    for key in ('archiveDir', 'assemblyDir', 'procRoots'):
        if key not in config:
            sys.exit(f'{path}: missing required key {key}')
    config['archiveDir'] = os.path.abspath(config['archiveDir'])
    config['assemblyDir'] = os.path.abspath(config['assemblyDir'])
    config['procRoots'] = [os.path.abspath(p) for p in config['procRoots']]
    config['queueDir'] = os.path.abspath(
        config.get('queueDir',
                   os.path.join(config['assemblyDir'], 'reprocess')))
    user = os.environ.get('USER', 'ian')
    config.setdefault('scratch', f'/dev/shm/{user}/reprocess')
    config.setdefault('diskScratch', f'/tmp/{user}/scratch')
    config.setdefault('minFreeTB', 1.0)
    for key, override in (('firstDate', args.firstDate),
                          ('lastDate', args.lastDate)):
        value = override or config.get(key)
        # Only --buildQueue scans a window; a run works from what is queued.
        if value is None and args.buildQueue:
            sys.exit(f'no {key}: give it in {path} or with --{key}')
        config[key] = str(value).replace('-', '') if value else None
    config['tracks'] = parseTracks(args.tracks
                                   if args.tracks is not None
                                   else config.get('tracks'))
    return config


def trackNumber(name):
    """ 'track-16' or '16' -> 16, else None. """
    match = re.match(r'^(?:track-)?(\d+)$', str(name).strip())
    return int(match.group(1)) if match else None


def parseTracks(spec):
    """ '16 25' / 'track-16,track-25' / [16, 25] -> {16, 25}; None -> None. """
    if spec is None:
        return None
    items = spec.replace(',', ' ').split() if isinstance(spec, str) else spec
    tracks = set()
    for item in items:
        number = trackNumber(item)
        if number is None:
            sys.exit(f'not a track: {item}')
        tracks.add(number)
    return tracks or None


def resolveQueueDir(target):
    """ The queue directory, given either it or the toProcess.yaml inside it. """
    target = os.path.abspath(target or '.')
    if os.path.isdir(target):
        return target
    if os.path.isfile(target):
        return os.path.dirname(target)
    sys.exit(f'no such queue: {target}')


def procDirFor(procRoots, track):
    """ The processing dir holding this track's setup_ scripts, or None. """
    for root in procRoots:
        candidate = os.path.join(root, f'track-{track}')
        if os.path.isdir(candidate):
            return candidate
    return None


def sourceDirOf(script):
    """ The <orbit>-<seq> a setup_ script builds from ('set SOURCEDIR=62572-0').

    Parsed rather than assumed: a datatake split by checkFramesS1 has both a
    <orbit>-0 and a <orbit>-4, and the script name carries only the orbit.
    """
    try:
        with open(script) as fp:
            match = re.search(r'^\s*set\s+SOURCEDIR\s*=\s*(\S+)', fp.read(),
                              re.MULTILINE)
    except OSError:
        return None
    return match.group(1) if match else None


def scratchOf(script):
    """ The SCRATCH the setup_ script stages through ('set SCRATCH=/dev/shm'). """
    try:
        with open(script) as fp:
            match = re.search(r'^\s*set\s+SCRATCH\s*=\s*(\S+)', fp.read(),
                              re.MULTILINE)
    except OSError:
        return None
    return match.group(1) if match else None


def frameSlcOk(procDir, orbit, burst):
    """ True if <orbit>_<burst>.slc exists at exactly the size its par implies.

    Existence alone is not enough: an interrupted mosaic leaves a short file,
    and skipping on that would carry the damage forward.
    """
    frameDir = os.path.join(procDir, f'{orbit}_{burst}')
    slc = os.path.join(frameDir, f'{orbit}_{burst}.slc')
    par = f'{slc}.par'
    if not os.path.exists(slc) or not os.path.exists(par):
        return False
    fields = {}
    try:
        with open(par) as fp:
            for line in fp:
                if ':' in line:
                    key, _, value = line.partition(':')
                    fields[key.strip()] = value.split()[0] if value.split() \
                        else ''
        nRange = int(fields['range_samples'])
        nAzimuth = int(fields['azimuth_lines'])
        pixelBytes = PIXEL_BYTES[fields['image_format']]
    except (OSError, KeyError, ValueError, IndexError):
        return False
    return os.path.getsize(slc) == nRange * nAzimuth * pixelBytes


def archiveCandidates(config):
    """ (track, orbit) -> acquisition date, from the archive granule names.

    Cheaper and safer than walking the assembly tree: the names carry both the
    date and the orbit, so nothing under assemblyDir is stat'd for a unit that
    is out of the window anyway.
    """
    candidates = {}
    for zipFile in glob.glob(f'{config["archiveDir"]}/*-*/*.zip*'):
        try:
            track, orbit, date1, _, _ = fileS1.parseFileName(zipFile)
        except Exception:
            continue
        date = date1.strftime('%Y%m%d')
        if date < config['firstDate'] or date > config['lastDate']:
            continue
        candidates[(track, int(orbit))] = date
    return candidates


def unitDirsFor(trackDir, orbit):
    """ The source unit dirs of an orbit: <orbit> and any <orbit>_<seq> split. """
    return sorted(glob.glob(f'{trackDir}/{orbit}') +
                  glob.glob(f'{trackDir}/{orbit}_[0-9]*'))


def granuleZips(unitDir):
    """ Archive zip basenames of the SAFEs currently filed in a unit dir. """
    return sorted(f'{os.path.basename(safe)[:-len(".SAFE")]}.zip'
                  for safe in glob.glob(f'{unitDir}/*.SAFE'))


def archivePath(archiveDir, zipName):
    """ Where a granule zip lives in the archive, filed or not. Returns the
    path that exists, preferring the .zip.1 an already-filed granule carries. """
    month = f'{zipName.split("_")[5][0:4]}-{zipName.split("_")[5][4:6]}'
    for candidate in (f'{archiveDir}/{month}/{zipName}.1',
                      f'{archiveDir}/{month}/{zipName}'):
        if os.path.exists(candidate):
            return candidate
    return None


def buildQueue(config, args):
    """ Scan the window and write one toProcess record per unit needing work. """
    queueDir = config['queueDir']
    candidates = archiveCandidates(config)
    known = {queueS1.entryUnit(e) for name in ('toProcess', 'problem')
             for e in queueS1.readQueue(queueDir, name)}
    known |= {queueS1.entryUnit(e)
              for e in queueS1._readList(queueS1.completedPath(queueDir))}

    records, skipped = [], []
    for (track, orbit), date in sorted(candidates.items()):
        if config['tracks'] and track not in config['tracks']:
            continue
        trackDir = os.path.join(config['assemblyDir'], f'track-{track}')
        procDir = procDirFor(config['procRoots'], track)
        if procDir is None:
            skipped.append((track, str(orbit), 'no processing dir'))
            continue
        scripts = sorted(glob.glob(f'{procDir}/setup_{orbit}_*'))
        bySource = {}
        for script in scripts:
            bySource.setdefault(sourceDirOf(script), []).append(script)
        for unitDir in unitDirsFor(trackDir, orbit):
            unitName = f'track-{track}/{os.path.basename(unitDir)}'
            if unitName in known:
                continue
            _, seq = setupTrackMod.parseOrbitSeq(os.path.basename(unitDir))
            unitTag = os.path.basename(unitDir)
            if os.path.exists(os.path.join(unitDir, 'Ignore')):
                skipped.append((track, unitTag, 'Ignore marker'))
                continue
            granules = granuleZips(unitDir)
            if not granules:
                skipped.append((track, unitTag, 'no SAFE dirs'))
                continue
            unitScripts = bySource.get(f'{orbit}-{seq}', [])
            if not unitScripts:
                skipped.append((track, unitTag, 'no setup_ script'))
                continue
            bursts = sorted(int(os.path.basename(s).split('_')[2])
                            for s in unitScripts)
            todo = [b for b in bursts
                    if args.rebuildFrames or not frameSlcOk(procDir, orbit, b)]
            if not todo:
                skipped.append((track, unitTag, 'merged frames already built'))
                continue
            missing = [g for g in granules
                       if archivePath(config['archiveDir'], g) is None]
            if missing:
                skipped.append((track, unitTag, 'archive zip(s) missing'))
                continue
            records.append({'unit': unitName,
                            'orbit': str(orbit),
                            'seq': seq,
                            'date': f'{date[0:4]}-{date[4:6]}-{date[6:8]}',
                            'procDir': procDir,
                            'granules': granules,
                            'frames': todo,
                            'allFrames': bursts})

    reportSkips(skipped)
    nFrames = sum(len(r['frames']) for r in records)
    scope = ('all tracks' if not config['tracks']
             else f'{len(config["tracks"])} track(s)')
    print(f'\nwindow {config["firstDate"]}..{config["lastDate"]}, {scope}: '
          f'{len(candidates)} datatake(s) in the archive')
    print(f'{BOLD}{len(records)} unit(s) to reprocess, '
          f'{nFrames} merged frame(s) to build{RESET}')
    if args.check:
        print(f'\n[check] would write {queueS1.queuePath(queueDir, "toProcess")}')
        for record in records[:20]:
            print(f'  {record["unit"]:24s} {record["date"]}  '
                  f'frames {record["frames"]}')
        if len(records) > 20:
            print(f'  ... {len(records) - 20} more')
        return 0
    os.makedirs(queueDir, exist_ok=True)
    if queueS1.applyQueueDeltas(queueDir, add={'toProcess': records}) is None:
        print(f'{RED}queue busy; nothing written{RESET}', file=sys.stderr)
        return 1
    # So `reprocessS1 <queueDir>` can find the config that drives it: the queue
    # sits by the assembly tree and the config wherever it was written.
    queueS1.recordConfigPath(queueDir, config['configPath'])
    print(f'wrote {queueS1.queuePath(queueDir, "toProcess")}')
    return 0


def reportSkips(skipped):
    """ Why units were left out, per track for anything that needs a person.

    'frames already built' is the ordinary case and only gets a count; the rest
    are named track by track, because a track missing its setup_ scripts wants
    segmentTrack / setupSeveralTopsImages run on it before this can help.
    """
    byReason = {}
    for track, unitTag, why in skipped:
        byReason.setdefault(why, {}).setdefault(track, []).append(unitTag)
    for why in sorted(byReason, key=lambda w: -sum(
            len(v) for v in byReason[w].values())):
        tracks = byReason[why]
        total = sum(len(v) for v in tracks.values())
        if why == 'merged frames already built':
            print(f'skipped {total:4d} unit(s): {why} '
                  f'({len(tracks)} track(s))')
            continue
        print(f'skipped {total:4d} unit(s): {BOLD}{why}{RESET}')
        for track in sorted(tracks):
            units = ' '.join(sorted(tracks[track]))
            print(f'    track-{track:<5} {len(tracks[track]):3d}  {units}')


def freeTB(path):
    return shutil.disk_usage(path).free / 1e12


def elapsedStr(seconds):
    if seconds < 90:
        return f'{seconds:.0f} s'
    if seconds < 5400:
        return f'{seconds / 60:.1f} min'
    return f'{seconds / 3600:.1f} h'


def step(number, total, name, message=''):
    print(f'  {BOLD}[step {number}/{total}] {name:<9}{RESET}{message}',
          flush=True)


def refileUnit(record, config, unitDir, quiet=False):
    """ Put the measurement TIFFs back by refiling the unit's archive zips.

    The archive holds them as .zip.1, which fileS1 neither globs nor derives a
    .SAFE name from, so each is staged as a symlink named .zip. fileOneZip
    unzips into the dir the .SAFE already sits in, which is what keeps a
    checkFramesS1 split (<orbit>_1, <orbit>_4) intact. Nothing in the archive
    is renamed -- only the staged symlink is.
    """
    staging = os.path.join(config['queueDir'], STAGING,
                           record['unit'].replace('/', '_'))
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        total = len(record['granules'])
        for index, zipName in enumerate(record['granules'], start=1):
            source = archivePath(config['archiveDir'], zipName)
            if source is None:
                return f'archive zip missing: {zipName}'
            if not quiet:
                print(f'    ({index}/{total}) {zipName}', flush=True)
            link = os.path.join(staging, zipName)
            os.symlink(source, link)
            status, _, _ = fileS1.fileOneZip(link, config['assemblyDir'],
                                             overwrite=True,
                                             createTrackDir=False,
                                             quiet=quiet)
            if status == fileS1.ERROR:
                return f'refile failed: {zipName}'
        if not setupTrackMod.tiffsPresent(unitDir):
            return 'no measurement TIFFs after refile'
        return None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def assembleUnit(record, config, unitDir):
    """ setupTrack --overWrite on the one unit dir: rebuild the subswath SLCs.

    Classic (non --queue) mode deliberately: --overWrite is rejected with
    --queue, and classic mode takes no assembly-tree lock, so this does not
    contend with the nightly autoupdateS1 on another host.
    """
    # A scratch of its own per unit, removed whatever happens. catMultipleTops
    # writes its intermediate SLC_tab_<time> into the scratch *base* and only
    # cleans up the SLCs, so a shared scratch accumulates one tab file per unit
    # -- and a failed assemble leaves its intermediate SLCs there too, which is
    # GBs of tmpfs rather than bytes. Owning the directory makes both go away
    # without deleting by pattern, which two runs sharing a scratch could not
    # do safely.
    unitScratch = os.path.join(config['scratch'],
                               record['unit'].replace('/', '_'))
    shutil.rmtree(unitScratch, ignore_errors=True)
    os.makedirs(unitScratch, exist_ok=True)
    # --quiet: over a run of hundreds of units the Gamma chatter buries the
    # progress. Nothing is lost -- setupTrack captures it all to its own
    # log.<track>.<pid>.<date> in the track dir either way.
    command = ['setupTrack', unitDir, '--overWrite', '--noPrompt', '--quiet',
               '--scratch', unitScratch,
               '--diskScratch', config['diskScratch']]
    print(f'    {" ".join(command)}', flush=True)
    try:
        result = subprocess.run(command)
    finally:
        shutil.rmtree(unitScratch, ignore_errors=True)
    if result.returncode != 0:
        return f'setupTrack exited {result.returncode}'
    outputDir = os.path.join(os.path.dirname(unitDir),
                             f'{record["orbit"]}-{record["seq"]}')
    if not glob.glob(f'{outputDir}/*_iw?_*.slc'):
        return f'no subswath SLCs in {outputDir}'
    return None


def buildFrames(record, config):
    """ Run each setup_<orbit>_<burst> in burst order, in the processing dir.

    A crashed earlier run can leave the script's SCRATCH dir behind, and the
    script's own `mkdir $SCRATCH/<frame>` does not check -- it would carry on
    over the stale contents -- so it is cleared first.
    """
    procDir, orbit = record['procDir'], record['orbit']
    total = len(record['frames'])
    for index, burst in enumerate(record['frames'], start=1):
        script = os.path.join(procDir, f'setup_{orbit}_{burst}')
        if not os.path.exists(script):
            return f'missing {os.path.basename(script)}'
        scratch = scratchOf(script) or '/dev/shm'
        stale = os.path.join(scratch, f'{orbit}_{burst}')
        if os.path.exists(stale):
            print(f'    clearing stale {stale}')
            shutil.rmtree(stale, ignore_errors=True)
        # The script's merge-back is `mv <new>/* <existing>_save`, and mv will
        # not merge one directory into another that is not empty -- so a frame
        # rebuilt over an old one keeps the old iw/ pars and silently discards
        # the ones just made ('inter-device move failed ... Directory not
        # empty'). The script repopulates iw/ itself, so clearing it first is
        # what makes the merge complete.
        staleIw = os.path.join(procDir, f'{orbit}_{burst}', 'iw')
        if os.path.isdir(staleIw):
            print(f'    clearing stale {staleIw}')
            shutil.rmtree(staleIw, ignore_errors=True)
        started = time.time()
        # Gamma's per-burst chatter is ~7000 lines a unit, which over a full
        # queue buries everything else. Captured rather than dropped: on a
        # failure the script says nothing itself, so the log is the only
        # account of what went wrong.
        logPath = os.path.join(procDir,
                               f'log.{orbit}_{burst}.{os.getpid()}.'
                               f'{datetime.now().strftime("%Y%m%d")}')
        print(f'    ({index}/{total}) {os.path.basename(script)} '
              f'-> {os.path.basename(logPath)}', flush=True)
        with open(logPath, 'w') as logfp:
            result = subprocess.run(script, shell=True, executable='/bin/csh',
                                    cwd=procDir, stdout=logfp,
                                    stderr=subprocess.STDOUT)
        # The generated scripts do not check their own steps, so the exit code
        # is only the last command's. The built SLC is the real test.
        if not frameSlcOk(procDir, orbit, burst):
            try:
                with open(logPath) as logfp:
                    for line in logfp.read().splitlines()[-15:]:
                        print(f'      {line}', file=sys.stderr)
            except OSError:
                pass
            return (f'frame {burst} not built (setup script exited '
                    f'{result.returncode}; see {logPath})')
        print(f'    ({index}/{total}) {orbit}_{burst}.slc ok '
              f'({elapsedStr(time.time() - started)})', flush=True)
    return None


def cleanUnit(record, unitDir):
    """ Give back the measurement TIFFs and the subswath SLCs.

    The frames on insar9/insar10 are the product; everything rebuilt under
    assemblyDir is scaffolding and can come back from the archive.
    """
    nTiffs, freed = setupTrackMod.stripMeasurementTiffs(unitDir)
    outputDir = os.path.join(os.path.dirname(unitDir),
                             f'{record["orbit"]}-{record["seq"]}')
    nSlc = 0
    for slc in glob.glob(f'{outputDir}/*.slc'):
        freed += os.path.getsize(slc)
        os.remove(slc)
        nSlc += 1
    return (f'{nTiffs} TIFF(s) and {nSlc} subswath SLC(s), '
            f'{freed / 1e9:.1f} GB freed')


class Prefetcher:
    """ Refile the next unit while the current one assembles.

    Refiling is unzip into the assembly tree and never touches /dev/shm, so it
    is the one step that can overlap the two that do. Depth one: at most two
    units' measurement TIFFs (~50 GB) are on disk at a time, and the free-space
    guard is re-checked before each prefetch rather than only per unit.

    The worker is a daemon: an interrupt must not leave the interpreter waiting
    on a thread blocked in unzip. The cost is a possible half-extracted .SAFE,
    which fileOneZip refiles over on the next attempt.
    """

    MISSING = object()          # this record was not prefetched

    def __init__(self, config):
        self.config = config
        self.thread = None
        self.record = None
        self.detail = None

    def start(self, record):
        """ Begin refiling record in the background; call after take(). """
        self.thread = self.record = None
        if record is None:
            return
        if freeTB(self.config['assemblyDir']) < self.config['minFreeTB'] * 2:
            return              # too tight to hold two units at once
        self.record = record
        self.detail = None
        self.elapsed = self.waited = 0.0
        self.thread = threading.Thread(target=self._run, args=(record,),
                                       daemon=True)
        self.thread.start()
        # Its unzip is -q, so without this the overlap is invisible and the
        # first unit -- which has nothing to overlap with -- looks hung.
        print(f'  ...refiling {record["unit"]} '
              f'({len(record["granules"])} granule(s)) in the background',
              flush=True)

    def _run(self, record):
        unitDir = os.path.join(self.config['assemblyDir'], record['unit'])
        started = time.time()
        try:
            self.detail = refileUnit(record, self.config, unitDir, quiet=True)
        except Exception as exc:                    # noqa: BLE001
            self.detail = f'unexpected: {exc}'
        finally:
            self.elapsed = time.time() - started

    def take(self, record):
        """ (detail, worked, waited) for record, or MISSING if not prefetched.

        `worked` minus `waited` is what the overlap actually saved, which is
        the only honest way to report it: the wait falls inside the unit's
        wall clock but outside any of its four steps.
        """
        if self.thread is None or self.record is not record:
            return self.MISSING
        waitStart = time.time()
        if self.thread.is_alive():
            print('  ...waiting on the prefetched refile', flush=True)
        self.thread.join()
        self.waited = time.time() - waitStart
        return self.detail, self.elapsed, self.waited


def processUnit(record, config, args, prefetched=Prefetcher.MISSING):
    """ One unit through refile -> assemble -> frames -> clean. """
    unitDir = os.path.join(config['assemblyDir'], record['unit'])
    if not os.path.isdir(unitDir):
        return f'unit dir missing: {unitDir}'
    free = freeTB(config['assemblyDir'])
    if free < config['minFreeTB']:
        return f'only {free:.2f} TB free on assemblyDir; stopping'

    def refileStage():
        if prefetched is Prefetcher.MISSING:
            return refileUnit(record, config, unitDir)
        detail, worked, waited = prefetched
        print(f'    done in the background during the previous unit: '
              f'{elapsedStr(worked)} of unzip, {elapsedStr(waited)} waited',
              flush=True)
        return detail

    stages = [('refile', f'{len(record["granules"])} granule(s) from the archive',
               refileStage),
              ('assemble', 'setupTrack --overWrite -> subswath SLCs',
               lambda: assembleUnit(record, config, unitDir)),
              ('merge', f'{len(record["frames"])} merged frame(s) from the '
                        'subswath SLCs',
               lambda: buildFrames(record, config))]
    if not args.noClean:
        stages.append(('clean', 'give back the TIFFs and subswath SLCs', None))
    for number, (name, what, run) in enumerate(stages, start=1):
        step(number, len(stages), name, what)
        started = time.time()
        if run is None:
            print(f'    {cleanUnit(record, unitDir)}', flush=True)
        else:
            detail = run()
            if detail is not None:
                return f'{name}: {detail}'
        print(f'    {name} done in {elapsedStr(time.time() - started)}',
              flush=True)
    return None


def runQueue(config, args):
    queueDir = config['queueDir']
    inQueue = [e for e in queueS1.readQueue(queueDir, 'toProcess')
               if isinstance(e, dict)]
    queued = [e for e in inQueue
              if not config['tracks']
              or trackNumber(e['unit'].split('/')[0]) in config['tracks']]
    remaining = len(queued)
    if args.maxOrbits:
        queued = queued[:args.maxOrbits]
    if not queued:
        print(f'nothing to do ({len(inQueue)} unit(s) in the queue)')
        return 0

    nFrames = sum(len(e['frames']) for e in queued)
    print(f'{BOLD}{len(queued)} unit(s), {nFrames} merged frame(s) this run; '
          f'{remaining} unit(s) match in the queue{RESET}')
    print(f'queue   {queueS1.queuePath(queueDir, "toProcess")}')
    print(f'free    {freeTB(config["assemblyDir"]):.2f} TB on '
          f'{config["assemblyDir"]}')
    if args.check:
        for index, record in enumerate(queued, start=1):
            print(f'  [{index}/{len(queued)}] {record["unit"]:24s} '
                  f'{record["date"]}  {len(record["granules"])} granule(s)  '
                  f'merged frames {record["frames"]}')
        return 0
    if shutil.which('SLC_mosaic_S1_TOPS') is None:
        sys.exit('Gamma is not on PATH (SLC_mosaic_S1_TOPS not found); run '
                 'this from a login shell')

    # One runner at a time. queueS1's own lock keeps the YAML consistent, but
    # not the choice of units: two runners would read the same head of
    # toProcess and work the same ones, and one would rmtree an <orbit>-<seq>
    # the other was building. Non-blocking, so a second terminal says so and
    # exits rather than queueing up behind a day-long drain.
    with queueS1.queueLock(queueDir, wait=0, staleSeconds=RUN_LOCK_STALE,
                           name=RUN_LOCK_NAME) as acquired:
        if not acquired:
            _, holder, age = queueS1.lockState(queueDir, RUN_LOCK_NAME)
            print(f'{RED}another reprocessS1 holds '
                  f'{os.path.join(queueDir, RUN_LOCK_NAME)}: {holder}, '
                  f'{elapsedStr(age)} ago{RESET}', file=sys.stderr)
            return 1
        return runUnits(queued, config, args, queueDir)


def runUnits(queued, config, args, queueDir):
    """ The unit loop, under the run lock. """
    # Symlinks only, and nothing in them is precious: a run killed mid-refile
    # leaves a staging dir behind, since the daemon worker never runs its
    # cleanup.
    shutil.rmtree(os.path.join(queueDir, STAGING), ignore_errors=True)
    lockPath = os.path.join(queueDir, RUN_LOCK_NAME)

    nFailed = 0
    unitTimes = []
    prefetcher = Prefetcher(config)
    if not args.noPrefetch:
        prefetcher.start(queued[0])
    for index, record in enumerate(queued, start=1):
        started = time.time()
        # Heartbeat: the stale window has to clear a crashed runner promptly,
        # so it is sized to one unit rather than to a whole drain, and the
        # mtime is pushed forward as each unit starts.
        try:
            os.utime(lockPath, None)
        except OSError:
            pass
        print(f'\n{BOLD}=== [{index}/{len(queued)}] {record["unit"]}  '
              f'{record["date"]}  {len(record["frames"])} merged frame(s) '
              f'{record["frames"]}{RESET}', flush=True)
        prefetched = prefetcher.take(record)
        # Start the next refile before this unit's assemble, not after: the
        # overlap is the whole point, and refile touches no /dev/shm.
        if not args.noPrefetch:
            prefetcher.start(queued[index] if index < len(queued) else None)
        try:
            detail = processUnit(record, config, args, prefetched)
        except KeyboardInterrupt:
            raise
        except Exception as exc:                    # noqa: BLE001
            detail = f'unexpected: {exc}'
        elapsed = time.time() - started
        if detail is None:
            unitTimes.append(elapsed)
            left = len(queued) - index
            eta = (f', ~{elapsedStr(sum(unitTimes) / len(unitTimes) * left)} '
                   f'for the {left} left' if left else '')
            print(f'  {BOLD}[{index}/{len(queued)}] {record["unit"]} ok in '
                  f'{elapsedStr(elapsed)}{RESET}{eta}', flush=True)
            queueS1.applyQueueDeltas(queueDir,
                                     remove={'toProcess': [record['unit']]})
            queueS1.appendProcessed(
                queueDir,
                queueS1.processedRecord(record['unit'], elapsed=elapsed,
                                        base=record))
        else:
            nFailed += 1
            print(f'{RED}  [{index}/{len(queued)}] {record["unit"]} FAILED at '
                  f'{detail}{RESET}', file=sys.stderr, flush=True)
            problem = queueS1.problemRecord(record['unit'], detail, SOURCE,
                                            base=record)
            queueS1.applyQueueDeltas(
                queueDir,
                remove={'toProcess': [record['unit']],
                        'problem': [record['unit']]},
                add={'problem': [problem]})
            if args.stopOnError:
                print('--stopOnError set; stopping', file=sys.stderr)
                break
    merged = queueS1.mergeProcessed(queueDir)
    stillQueued = len(queueS1.readQueue(queueDir, 'toProcess'))
    nCompleted = len(queueS1._readList(queueS1.completedPath(queueDir)))
    print(f'\n{BOLD}done: {len(queued) - nFailed} ok, {nFailed} failed; '
          f'{stillQueued} still in toProcess, {nCompleted} in '
          f'{os.path.basename(queueS1.completedPath(queueDir))}'
          f'{"" if merged else " (merge deferred; queue busy)"}{RESET}')
    return 1 if nFailed else 0


def info(config):
    queueDir = config['queueDir']
    print(f'config      {config["archiveDir"]} -> {config["assemblyDir"]}')
    print(f'procRoots   {", ".join(config["procRoots"])}')
    print(f'window      {config["firstDate"]} .. {config["lastDate"]}')
    print(f'scratch     {config["scratch"]} (disk fallback '
          f'{config["diskScratch"]})')
    for line in queueS1.describe(queueDir):
        print(line)
    held, holder, age = queueS1.lockState(queueDir, RUN_LOCK_NAME)
    print(f'{RUN_LOCK_NAME:<18} '
          + (f'HELD by {holder} ({elapsedStr(age)} ago)' if held else 'free'))
    for name in ('toProcess', 'problem'):
        entries = queueS1.readQueue(queueDir, name)
        frames = sum(len(e.get('frames', [])) for e in entries
                     if isinstance(e, dict))
        print(f'{name:12s} {len(entries):4d} unit(s), '
              f'{frames} merged frame(s)')
    return 0


def main():
    args = parseArgs()
    if args.buildQueue:
        return buildQueue(readConfig(args.target or 'reprocess.yaml', args),
                          args)
    # Running: the queue is the argument, and it remembers which config built
    # it -- the queue sits by the assembly tree, the config wherever it lives.
    queueDir = resolveQueueDir(args.target)
    configPath = args.config or queueS1.readConfigPath(queueDir)
    if not configPath:
        sys.exit(f'{queueDir} has no configPath; give one with --config')
    config = readConfig(configPath, args)
    config['queueDir'] = queueDir
    if args.info:
        return info(config)
    return runQueue(config, args)


if __name__ == '__main__':
    sys.exit(main())
