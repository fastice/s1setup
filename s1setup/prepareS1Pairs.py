#!/usr/bin/env python3
"""
prepareS1Pairs - take newly assembled Sentinel-1 data to a list of runboths.

Run from a project top dir (e.g. /Volumes/insar9/ian/Sentinel1, the one holding
project.yaml and the track-N dirs). For each track, in order:

  1 segment     segmentTrack -refreshLinks -commit  -> runSetup.<stamp>
  2 build SLCs  each setup_<orbit>_<burst> listed   -> <orbit>_<burst>/*.slc
  3 pairs       setuppairs <year>                   -> runboth
  4 secondaries for each secondaryDirectories entry (Sentinel1-S1A/-S1C/-S1D):
                cloneSLCdir -> setuppairs -> cullSLCclones --noRemoveRedundant,
                the order the secondaries' own updateClones scripts use, over
                the last six months, 12-day pairs only, and only acquisitions
                whose prime SLC still exists
  5 runboth list every runboth this run created and nothing has run yet, as
                `cd <dir> ; runboth` lines for parallel_boss (pboss.py -c)

Until now each step was run by hand. Everything here is serial: the setup
scripts stage through /dev/shm and are memory-heavy, so they run one at a time
across all tracks, the way reprocessS1 runs them.

A track whose segmenting fails -- including the unattended stop on missing
bursts, where segmentTrack would otherwise prompt -- writes nothing, is skipped
for the later steps, and is reported. If any track failed to segment, the
report is mailed as soon as segmenting is over (to --notifyEmail, else
project.yaml notifyEmail). Every step's full output is kept under
logs/prepareS1Pairs_<stamp>/, with a run log and summary beside it; the exit
status is 1 if anything failed.

    prepareS1Pairs --check --tracks track-90
    prepareS1Pairs --tracks track-90 track-141
    prepareS1Pairs --notifyEmail you@uw.edu
"""
import argparse
import datetime
import glob
import logging
import os
import re
import socket
import subprocess
import sys

import yaml

log = logging.getLogger('prepareS1Pairs')

TRACK_RE = re.compile(r'track-(\d+)$')
SETUP_RE = re.compile(r'^setup_(\d+)_(\d+)$')
SENSOR_RE = re.compile(r'(S1[A-D])$')
STEPS = {1: 'segment', 2: 'build SLCs', 3: 'pairs (prime)',
         4: 'secondaries', 5: 'runboth list'}


def parseArgs():
    '''Handle command line args'''
    parser = argparse.ArgumentParser(
        description='\n\n\033[1mSegment, build SLCs, set up pairs (prime and '
        'secondaries) and list the new runboths\033[0m\n\n',
        epilog='Part of the s1setup package.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tracks', nargs='+', metavar='track-N', default=None,
                        help='Only these track dirs [every track-* here]')
    parser.add_argument('--year', nargs='+', type=int, default=None,
                        help='Years to set up pairs for (setuppairs <year>) '
                        '[this year]')
    parser.add_argument('--notifyEmail', type=str, default=None,
                        help='Mail the report here if segmenting fails '
                        '[project.yaml notifyEmail; none = no mail]')
    parser.add_argument('--noSecondary', action='store_true', default=False,
                        help='Skip the secondaryDirectories step')
    parser.add_argument('--cloneFirstDate', type=str, default=None,
                        metavar='YYYY-MM-DD',
                        help='Secondaries: clone and cull acquisitions from '
                        'this date [six months ago]')
    parser.add_argument('--secondaryMaxDays', type=int, default=12,
                        help='Secondaries: longest same-sensor pair to clone '
                        'or set up, in days [12]')
    parser.add_argument('--out', type=str, default=None,
                        help='Runboth list to write '
                        '[newRunboth-<stamp>.pyboss in this dir]')
    parser.add_argument('--check', action='store_true', default=False,
                        help='Dry run: every step reports what it would do, '
                        'nothing is written except the log, no mail')
    return parser.parse_args()


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def trackKey(path):
    '''Numeric sort key for a track-N path.'''
    return int(TRACK_RE.search(path.rstrip('/')).group(1))


def getTracks(topDir, requested):
    '''The prime track dirs to process, in track-number order.'''
    if requested:
        dirs = [os.path.join(topDir, t.rstrip('/')) for t in requested]
        missing = [d for d in dirs
                   if not os.path.isdir(d) or not TRACK_RE.search(d)]
        if missing:
            sys.exit(f'no such track dir: {", ".join(missing)}')
    else:
        dirs = [d for d in glob.glob(os.path.join(topDir, 'track-*'))
                if os.path.isdir(d) and TRACK_RE.search(d)]
    if not dirs:
        sys.exit(f'no track-* directories under {topDir}')
    return sorted(dirs, key=trackKey)


def setupLogging(topDir):
    '''Run log (file + stdout) and the directory for each step's output.'''
    stamp = datetime.datetime.now().strftime('%Y-%m-%dT%H%M%S')
    logDir = os.path.join(topDir, 'logs')
    outDir = os.path.join(logDir, f'prepareS1Pairs_{stamp}')
    os.makedirs(outDir, exist_ok=True)
    logPath = os.path.join(logDir, f'prepareS1Pairs_{stamp}.log')
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(logPath),
                    logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    return stamp, logPath, outDir


def runCommand(cmd, cwd, outPath):
    '''Run cmd (a list) in cwd with all output to outPath. Returns
    (returncode, output text).'''
    with open(outPath, 'w') as fp:
        result = subprocess.run(cmd, cwd=cwd, stdout=fp,
                                stderr=subprocess.STDOUT)
    try:
        with open(outPath) as fp:
            text = fp.read()
    except OSError:
        text = ''
    return result.returncode, text


def tail(text, n=6):
    '''The last n non-blank lines, ANSI colour stripped, one per line.'''
    lines = [re.sub(r'\x1b\[[0-9;]*m', '', line).rstrip()
             for line in text.splitlines()]
    return '\n'.join(f'      {line}' for line in
                     [line for line in lines if line.strip()][-n:])


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

class Report:
    '''Per-step, per-place outcomes. Its text is the summary and mail body.'''

    def __init__(self, topDir, logPath, check):
        self.topDir, self.logPath, self.check = topDir, logPath, check
        self.started = datetime.datetime.now()
        self.rows = []                 # (step, where, status, detail, isError)
        self.runbothList = None
        self.nRunboth = 0

    def add(self, step, where, status, detail='', error=False):
        self.rows.append((step, where, status, detail, error))
        line = f'[{STEPS[step]}] {where}: {status}'
        (log.error if error else log.info)(
            line + (f'\n{detail}' if detail and error else
                    f' -- {detail}' if detail else ''))

    def errors(self, step=None):
        return [r for r in self.rows
                if r[4] and (step is None or r[0] == step)]

    def text(self, upToStep=5):
        out = ['prepareS1Pairs summary' + ('  [CHECK: dry run]' if self.check
                                           else ''),
               f'project  {self.topDir}',
               f'host     {socket.gethostname()}',
               f'started  {self.started:%Y-%m-%d %H:%M:%S}',
               f'report   {datetime.datetime.now():%Y-%m-%d %H:%M:%S}',
               f'log      {self.logPath}', '']
        for step in range(1, upToStep + 1):
            rows = [r for r in self.rows if r[0] == step]
            out.append(f'== {step} {STEPS[step]} ==')
            if not rows:
                out.append('  (nothing)')
            for _, where, status, detail, error in rows:
                flag = 'ERROR ' if error else ''
                out.append(f'  {where:<28} {flag}{status}'
                           + (f' -- {detail}' if detail and '\n' not in detail
                              else ''))
                if detail and '\n' in detail:
                    out.append(detail)
            out.append('')
        if upToStep >= 5 and self.runbothList:
            out.append(f'runboth list: {self.nRunboth} -> {self.runbothList}')
        out.append(f'errors: {len(self.errors())}')
        return '\n'.join(out) + '\n'


def notify(recipient, subject, body, check):
    '''Mail the report through the local MTA, as autoupdateS1 does. Never
    lets a mail failure stop the run.'''
    if not recipient or check:
        return
    try:
        from asfsearchdownload.autoupdateS1 import mailReport
        mailReport(recipient, subject, body)
    except Exception as e:                          # never fail on mail
        log.warning(f'could not mail the report: {e}')


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def segmentStep(trackDirs, args, rep, outDir):
    '''1: segmentTrack in each track. Returns {trackDir: runfile or None} for
    the tracks that did not fail.'''
    cmd = ['segmentTrack.py'] + ([] if args.check
                                 else ['-refreshLinks', '-commit'])
    done = {}
    for trackDir in trackDirs:
        name = os.path.basename(trackDir)
        rc, text = runCommand(cmd, trackDir,
                              os.path.join(outDir, f'{name}.segment.log'))
        if rc != 0:
            rep.add(1, name, f'segmentTrack exit {rc}', tail(text), error=True)
            continue
        runfile = re.search(r'scripts listed in (runSetup\.\S+)', text)
        runfile = runfile.group(1) if runfile else None
        # segmentTrack checks, then (under -commit) builds; its last pieces
        # line is the one that counts
        pieces = re.findall(r'(\d+) pieces? ((?:would be )?set up), '
                            r'(\d+) already built', text)
        if 'Nothing to do' in text:
            status = 'nothing to do'
        elif pieces:
            n, verb, already = pieces[-1]
            status = f'ok, {n} piece(s) {verb}, {already} already built' \
                + (f' ({runfile})' if runfile else '')
        else:
            status = 'ok, no pieces reported'
        if args.check:
            # -refreshLinks writes, so a dry run only sees what is linked
            status += ' [check: newly assembled data is not linked yet]'
        rep.add(1, name, status)
        done[trackDir] = runfile
    return done


def setupScripts(trackDir, runfile):
    '''(orbit, burst) for each setup_<orbit>_<burst> a runSetup lists.'''
    pieces = []
    try:
        with open(os.path.join(trackDir, runfile)) as fp:
            for line in fp:
                match = SETUP_RE.match(line.strip())
                if match:
                    pieces.append((match.group(1), match.group(2)))
    except OSError as e:
        log.error(f'cannot read {runfile} in {trackDir}: {e}')
    return pieces


def buildStep(segmented, args, rep):
    '''2: run each setup script, one at a time across all tracks.'''
    from s1setup.reprocessS1 import frameSlcOk, runSetupScript
    for trackDir, runfile in segmented.items():
        name = os.path.basename(trackDir)
        if not runfile:
            continue
        pieces = setupScripts(trackDir, runfile)
        built, failed, already = 0, [], 0
        for index, (orbit, burst) in enumerate(pieces, start=1):
            if frameSlcOk(trackDir, orbit, burst):
                already += 1
                continue
            if args.check:
                log.info(f'[check] would run setup_{orbit}_{burst} in {name}')
                built += 1
                continue
            failure = runSetupScript(trackDir, orbit, burst, index,
                                     len(pieces))
            if failure:
                failed.append(f'{orbit}_{burst}: {failure}')
            else:
                built += 1
        verb = 'would build' if args.check else 'built'
        status = f'{built} {verb}, {already} already built, ' \
            f'{len(failed)} failed'
        rep.add(2, name, status, '\n'.join(f'      {f}' for f in failed),
                error=bool(failed))


def pairTrack(trackDir, years, args, rep, outDir, label, step, extra=()):
    '''setuppairs <year> in one track dir. Returns pairs set up.'''
    total = 0
    for year in years:
        cmd = ['setuppairs.py', str(year)] + list(extra) + \
            (['--check'] if args.check else [])
        rc, text = runCommand(cmd, trackDir,
                              os.path.join(outDir, f'{label}.pairs{year}.log'))
        match = re.search(r'(\d+) pairs', text)
        if rc != 0 or 'grep command failed' in text:
            rep.add(step, f'{label} {year}', f'setuppairs exit {rc}',
                    tail(text), error=True)
            continue
        total += int(match.group(1)) if match else 0
    return total


def pairStep(segmented, years, args, rep, outDir):
    '''3: setuppairs in each prime track that segmented.'''
    for trackDir in segmented:
        name = os.path.basename(trackDir)
        n = pairTrack(trackDir, years, args, rep, outDir, name, 3)
        rep.add(3, name, f'{n} pair(s) {"would be " if args.check else ""}'
                'set up')


def secondaryStep(topDir, secondaries, segmented, years, args, rep, outDir):
    '''4: cloneSLCdir -> setuppairs -> cullSLCclones in each secondary.

    Limited to the clone window (six months unless --cloneFirstDate), to
    same-sensor pairs of at most --secondaryMaxDays, and to acquisitions whose
    prime SLC exists. Without the limits a fresh run backfills long pairs
    across old gaps (36-day S1C pairs in Feb-May, the first time it was run).
    '''
    firstDate = args.cloneFirstDate or (
        datetime.date.today() - datetime.timedelta(days=183)).isoformat()
    maxDays = ['--maxDays', str(args.secondaryMaxDays)]
    check = ['--check'] if args.check else []
    log.info(f'secondaries: from {firstDate}, pairs <= '
             f'{args.secondaryMaxDays} days, SLC required')
    for secDir in secondaries:
        sensor = SENSOR_RE.search(os.path.basename(secDir))
        if not sensor:
            rep.add(4, os.path.basename(secDir),
                    'skipped: no S1A/S1C/S1D in the name', error=True)
            continue
        sensor = sensor.group(1)
        for trackDir in segmented:
            name = os.path.basename(trackDir)
            secTrack = os.path.join(secDir, name)
            if not os.path.isdir(secTrack):
                continue
            where = f'{os.path.basename(secDir)}/{name}'
            label = f'{os.path.basename(secDir)}.{name}'
            rc, text = runCommand(
                ['cloneSLCdir.py', '--sourcePath', topDir, '--firstdate',
                 firstDate, '--requireSlc'] + maxDays + [name, sensor] + check,
                secDir,
                os.path.join(outDir, f'{label}.clone.log'))
            if rc != 0:
                rep.add(4, where, f'cloneSLCdir exit {rc}', tail(text),
                        error=True)
                continue
            cloned = re.search(r'duplicated (\d+)', text)
            nCloned = int(cloned.group(1)) if cloned else 0
            # A secondary only gets frames where the prime has a same-sensor pair skipping the
            # other sensor (e.g. D in C D D: D(skip C)D). None anywhere -> nothing cloned, no frame
            # dirs, and setuppairs would 'fail' on the empty dir: that is not an error.
            if not glob.glob(os.path.join(secTrack, '[0-9]*_[0-9]*')):
                if nCloned and args.check:
                    rep.add(4, where, f'would clone {nCloned}; its pairs are counted once cloned')
                else:
                    rep.add(4, where, f'no {sensor} pair to make (no same-sensor pair skipping '
                            'the other sensor in the prime)')
                continue
            nPairs = pairTrack(secTrack, years, args, rep, outDir, label, 4,
                               extra=maxDays)
            rc, text = runCommand(
                ['cullSLCclones.py', '--noRemoveRedundant', '--sourcePath',
                 topDir, '--firstdate', firstDate, name, sensor] + check,
                secDir, os.path.join(outDir, f'{label}.cull.log'))
            if rc != 0:
                rep.add(4, where, f'cullSLCclones exit {rc}', tail(text),
                        error=True)
                continue
            culled = re.search(r'products removed (\d+)', text)
            rep.add(4, where,
                    f'cloned {cloned.group(1) if cloned else "?"}, '
                    f'{nPairs} pair(s), culled '
                    f'{culled.group(1) if culled else "?"} duplicate '
                    'runboth(s)')


def runboths(roots):
    '''Every runboth under the roots' track-N/<orbit>_<frame> dirs.'''
    found = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, 'track-*', '*_*', 'runboth')):
            found.add(os.path.realpath(path))
    return found


def unrun(runbothPath):
    '''True if the pair has no offsets yet (grepdate status .--).'''
    return not glob.glob(os.path.join(os.path.dirname(runbothPath),
                                      'azimuth.offsets*'))


def listStep(before, roots, outPath, args, rep):
    '''5: the runboths this run created, prime first, as pboss lines.'''
    order = {os.path.realpath(r): i for i, r in enumerate(roots)}

    def sortKey(path):
        frameDir = os.path.dirname(path)
        trackDir = os.path.dirname(frameDir)
        root = os.path.dirname(trackDir)
        return (order.get(root, len(order)), trackKey(trackDir), frameDir)

    new = sorted((p for p in runboths(roots) - before if unrun(p)),
                 key=sortKey)
    rep.nRunboth = len(new)
    if args.check:
        rep.add(5, 'runboth list', f'check: {len(new)} new runboth(s) now; a '
                'real run lists what setuppairs creates')
        return
    if not new:
        rep.add(5, 'runboth list', 'no new runboths')
        return
    with open(outPath, 'w') as fp:
        for path in new:
            fp.write(f'cd {os.path.dirname(path)} ; runboth\n')
    rep.runbothList = outPath
    rep.add(5, 'runboth list', f'{len(new)} new runboth(s) -> {outPath}')


# --------------------------------------------------------------------------

def main():
    '''main for prepareS1Pairs'''
    args = parseArgs()
    topDir = os.getcwd()
    projPath = os.path.join(topDir, 'project.yaml')
    if not os.path.exists(projPath):
        sys.exit(f'no project.yaml in {topDir}: run from the project top dir')
    with open(projPath) as fp:
        proj = yaml.safe_load(fp) or {}
    trackDirs = getTracks(topDir, args.tracks)
    years = args.year or [datetime.date.today().year]
    recipient = args.notifyEmail or proj.get('notifyEmail')

    stamp, logPath, outDir = setupLogging(topDir)
    rep = Report(topDir, logPath, args.check)
    log.info(f'prepareS1Pairs in {topDir}: {len(trackDirs)} track(s), '
             f'years {years}' + ('  [CHECK: dry run]' if args.check else ''))
    secondaries = []
    if not args.noSecondary:
        # Resolved exactly as setupS1Tracks does; it reads the project dir
        # from the working directory, which is this one.
        from s1setup.setupS1Tracks import secondaryProjectDirs
        secondaries = secondaryProjectDirs(proj)
    roots = [topDir] + secondaries
    log.info(f'secondaries: {", ".join(secondaries) or "none"}')
    before = runboths(roots)
    log.info(f'{len(before)} runboth(s) already present')

    def stepHeader(n):
        log.info(f'===== step {n}: {STEPS[n]} =====')

    stepHeader(1)
    segmented = segmentStep(trackDirs, args, rep, outDir)
    if rep.errors(1):
        failed = [where for _, where, _, _, _ in rep.errors(1)]
        subject = (f'prepareS1Pairs {os.path.basename(topDir)}: segmentTrack '
                   f'failed on {", ".join(failed)} on {socket.gethostname()}')
        notify(recipient, subject, rep.text(upToStep=1), args.check)

    stepHeader(2)
    if args.check:
        log.info('[check] segmentTrack writes no runSetup in a dry run, so '
                 'no setup scripts can be listed here')
    buildStep(segmented, args, rep)

    stepHeader(3)
    pairStep(segmented, years, args, rep, outDir)

    stepHeader(4)
    if args.noSecondary:
        rep.add(4, 'secondaries', 'skipped (--noSecondary)')
    else:
        secondaryStep(topDir, secondaries, segmented, years, args, rep, outDir)

    stepHeader(5)
    outPath = args.out or os.path.join(topDir, f'newRunboth-{stamp}.pyboss')
    listStep(before, roots, os.path.abspath(outPath), args, rep)

    summaryPath = os.path.join(os.path.dirname(logPath),
                               f'prepareS1Pairs_{stamp}.summary')
    with open(summaryPath, 'w') as fp:
        fp.write(rep.text())
    log.info(f'summary: {summaryPath}')
    print('\n' + rep.text())
    return 1 if rep.errors() else 0


if __name__ == '__main__':
    sys.exit(main())
