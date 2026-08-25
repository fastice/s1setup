#!/usr/bin/env python3
import utilities as u
import os
from subprocess import call
import sys
from datetime import datetime, timedelta
import glob


class SetupError(Exception):
    '''
    A framing failure, for one SLC directory or for one frame of it.

    Raised where the program used to call u.myerror or raise NameError, so a
    caller (segmentTrack) can collect every failure across the track instead of
    the first one ending the run.  Standalone behaviour is unchanged: main()
    runs with strict=True, which reports each kind exactly as it always has and
    exits.
    '''
    def __init__(self, kind, message, alert=None, slc=None, orbit=None,
                 seq=None, frameRange=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.alert = alert
        self.slc = slc
        self.orbit = orbit
        self.seq = seq
        self.frameRange = frameRange


#
# The kinds that used to end the run through u.myerror -- red message and an
# immediate exit -- rather than through the NameError path, which also removed
# the part-written run file
#
MYERRORKINDS = ('ascdesc', 'ascnode', 'gap', 'range')


def reportError(error, frun=None, runfile=None):
    '''
    Report a SetupError the way this program always has, and exit.

    Used only in strict (standalone) mode; a caller passing strict=False
    collects the errors instead.
    '''
    if error.kind in MYERRORKINDS:
        if error.alert is not None:
            u.myalert(error.alert)
        u.myerror(error.message)
    print(error.message)
    if frun is not None:
        frun.close()
        os.remove(runfile)
    print('\nSomething went wrong - see error message above ')
    exit()


def getascdesc():
    if os.path.isfile('ascending'):
        return 1
    elif os.path.isfile('descending'):
        return 0
    else:
        print('error no ascending descending file present')
        return -1


def getAscNodeTime(fileAsc):
    # get ascending node time
    try:
        fnode = open(fileAsc, 'r')
        line = fnode.readline().strip()
        fnode.close()
        #
        # Written with fractional seconds, but not always: a reader that
        # insists on them turns a perfectly good file into a failed track
        #
        fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in line else '%Y-%m-%dT%H:%M:%S'
        return datetime.strptime(line, fmt)
    except Exception:
        raise SetupError('ascnode', f'**** Error Reading Ascending node time '
                                    f'for {fileAsc} ****',
                         slc=os.path.dirname(fileAsc))


#
# read frames (frame-xxxx-yyy) and orbitframes (orbit-n-frame-number)
#
def getFramesAndOrbits():
    #
    # frames file
    #
    try:
        fin = open('frames', 'r')
        frames = []
        for frame in fin:
            frame = frame.strip('\n')
            if frame.find('-') > -1:
                a = frame.split('-')
                frames.append([int(a[1]), int(a[2])])
    except Exception:
        print('Could not open frames file')
        exit()
    fin.close()
    #
    # orbitframes file
    #
    try:
        with open('orbitframes', 'r') as fin:
            orbframes = []
            for orbframe in fin:
                orbframe = orbframe.strip('\n')
                if orbframe.find('-') > -1:
                    if '#' in orbframe:
                        continue
                    a = orbframe.split('-')
                    if len(a) == 4:
                        orbframes.append([int(a[0]), int(a[1]),
                                          int(a[2]), int(a[3])])
                    elif len(a) == 3:
                        orbframes.append([int(a[0]), 1, int(a[1]), int(a[2])])
                    else:
                        print(orbframes)
                        print('orbitframes line does not have 3 or 4 entries')
    except Exception:
        print('Could not open orbitframes file or invalid format')
        exit()
    return(frames, orbframes)

#
# for a given orbit, and sequence, this pulls the frame range
# 1) It could be the default from frames
# 2) It could be the range from orbit frames in the range (1, nframes)
# 3) It could be an extra frame, in which case the index > default number
# of frames
#


def determineFraming(orbit, seq, frames, orbframes):
    # default
    framing = []
    for f in frames:
        framing.append(list(f))
    nframes = len(frames)
    #
    # loop through list and update with entries from orbit frames
    #
    for orbdata in orbframes:
        if orbit == orbdata[0]:
            # get index - substract - 1 for zero indexing
            index = orbdata[1]-1
            #
            # This is invoked when you want to add an extra frame to force
            # scenes up (i.e. when there is a gap)
            # In this case, the frame index is greater than the default number
            # of frames
            #
            if index >= nframes:
                framing.append([orbdata[2], orbdata[3]])
            else:
                framing[index][0] = orbdata[2]
                framing[index][1] = orbdata[3]
    return(framing)


def checkBurstTimes(orbit, seq, framing):
    '''
    Read the burst file and check for errors and gaps
    '''
    #
    # Find the burst times file
    #
    d = str(orbit)+'-' + str(seq)
    burstTimes = sorted(glob.glob(f'{d}/*iw1*.btimes'))
    #
    # raise exception if unsuccessful
    #
    if len(burstTimes) < 1:
        raise SetupError('btimes',
                         f'Could not  find burst times file for:  {d}',
                         slc=d, orbit=orbit, seq=seq)
    #
    # read burst time file
    #
    fin = open(burstTimes[0], 'r')
    count = 0
    for burst in fin:
        burst = burst.strip('\n')
        num, time, burstNum = [eval(x) for x in burst.split()]
        if count == 0:
            first = burstNum
        elif burstNum - first != count:
            fin.close()
            raise SetupError('gap', f'\n\n ***** Gap in burst times ******\n\n '
                                    f'{burstTimes[0]} burstnum={burstNum}',
                             slc=d, orbit=orbit, seq=seq)
        count += 1
    last = burstNum
    fin.close()
    return(first, last)


def checkRange(frameRange, first, last, slc, done):
    '''
    Check if frame in range.
    Return True if should proceed with a setup
    Return False if: (usually a multi-sequence event)
    1) The case has already been handled (in done list)
    2) Its not in the frame range, but there are other sequences, so it make
    be ok, skip for now
    3) No valid range, raise SetupError so user can fix with entry in orbframes
    '''
    #
    # first and last frame
    firstRange = frameRange[0]
    lastRange = firstRange + frameRange[1] - 1
    #
    # This is the case where number of frames set to 0 indicates skip
    #
    if frameRange[1] == 0:
        return(False)
    #
    # Return false if this case has already been handled,
    #
    orb, seq = slc.split('-')
    orb = orb.strip()
    setupfile = f'setup_{orb}_{firstRange}'
    #
    if setupfile in done:
        return(False)
    #
    # check frame range, and if good return true
    #
    r = range(first, last + 1)
    if firstRange in r and lastRange in r:
        # ok, do nothing return, return True (good to go)
        return(True)
    #
    # Frame range didn't work, is there another sequence that might work
    # Check if there might be another sequence
    #
    # assume sequence 0-9, check for other sequences
    # command = 'ls -d '+orb+'-?'
    slcs = sorted(glob.glob(f'{orb}-?'))
    #
    # if the current slc, is not equal to the last slc, figure it may get
    # picked up on the next run
    # return False, says skip this time around
    #
    if slcs[-1].find(slc) < 0:
        return False
    #
    # Didn't work flag,error and print message (program should exit)
    #
    raise SetupError('range',
                     f'***** Frames {firstRange} to {lastRange} for {slc} not '
                     f'in burst time range {first} to {last} {slc}******',
                     alert='If multiple source images (e.g. _1) make sure '
                           'first image is ok, not just the one flagged',
                     slc=slc, orbit=int(orb), seq=int(seq),
                     frameRange=list(frameRange))


def makeSetupFile(orbit, seq, ascdesc, frameRange):
    '''
    Create Setup file
    '''
    filetmp = f'tmp.{orbit}.{seq}.{frameRange[0]}'
    with open(filetmp, 'w') as fout:
        print(orbit, file=fout)
        print(seq, file=fout)
        print(frameRange[0], file=fout)
        print(frameRange[1], file=fout)
        print(ascdesc, file=fout)
    # setup file name
    setupfile = f'setup_{orbit}_{frameRange[0]}'
    #
    # remove file if an old version exists
    if os.path.isfile(setupfile):
        os.remove(setupfile)
    #
    # Run setup
    call(f'setuptopsimage < {filetmp}', shell=True)
    # kill tmp input file
    os.remove(filetmp)
    # check setup file was created
    if os.path.isfile(setupfile):
        # make file executable
        call(f'chmod +x {setupfile}', shell=True)
        return(setupfile)
    else:
        raise SetupError('setupfile',
                         f'Setup file not created for {setupfile}',
                         orbit=orbit, seq=seq, frameRange=list(frameRange))


def pendingSetups(written):
    '''
    Setup scripts for pieces that were never built, other than those just
    written.

    A script is named for the piece it cuts, setup_<orbit>_<firstBurst>, and
    its <orbit>_<firstBurst> directory appears when it is run, so a script
    without one is either still waiting to be run or left over from a framing
    that has since moved.
    '''
    pending = []
    for setupFile in sorted(glob.glob('setup_*_*')):
        orbit, _, start = setupFile[len('setup_'):].partition('_')
        if not (orbit.isdigit() and start.isdigit()):
            continue
        if setupFile in written or os.path.isdir(f'{orbit}_{start}'):
            continue
        pending.append(setupFile)
    return pending


def removeStaleSetups(orbits, written):
    '''
    Remove the setup scripts superseded by this run.

    Only the orbits this run covered, and only the pieces never built: a
    re-framing that moves a start leaves the old script behind under its old
    name, since makeSetupFile only overwrites the name it is about to write.
    The scripts of built pieces are kept -- their directory exists, and
    segmentTrack reads them for the burst count the directory name does not
    carry -- and so are the pieces still pending outside the dates this run
    covered, which nothing has replaced.
    '''
    removed = []
    for setupFile in pendingSetups(written):
        orbit = setupFile[len('setup_'):].partition('_')[0]
        if int(orbit) not in orbits:
            continue
        os.remove(setupFile)
        removed.append(setupFile)
    return removed


def listedSetups(runFile):
    '''The setup scripts a run file lists, in the order it lists them'''
    names = []
    try:
        with open(runFile, 'r') as fin:
            for line in fin:
                name = line.strip()
                if name.startswith('setup_'):
                    names.append(name)
    except OSError:
        pass
    return names


def removeOldRunFiles(keep, written):
    '''
    Remove the earlier run files this one has made redundant.

    A run file goes only when everything it lists is either being written again
    now or has since been built.  Age is not the test: an older file still
    pointing at a piece nobody has built -- the pieces outside the dates this
    run covered are the usual case -- is the only record of that work, so it is
    left where it is.

    Returns (removed, kept), kept being (runFile, [scripts still outstanding])
    for each one held back, so the caller can say why it is still there.
    '''
    written = set(written)
    removed, kept = [], []
    for runFile in sorted(glob.glob('runSetup.*')):
        if runFile == keep or not os.path.isfile(runFile):
            continue
        outstanding = []
        for name in listedSetups(runFile):
            if name in written:
                continue
            orbit, _, start = name[len('setup_'):].partition('_')
            if orbit.isdigit() and start.isdigit() and \
                    os.path.isdir(f'{orbit}_{start}'):
                continue
            outstanding.append(name)
        if outstanding:
            kept.append((runFile, outstanding))
            continue
        os.remove(runFile)
        removed.append(runFile)
    return removed, kept


def hasSwathSLCs(slcDir):
    '''
    True if an assembled directory still holds its swath SLCs.

    Cleaning the SLCs away leaves everything else -- .slc.par, .tops_par,
    .btimes, ascendingNodeTime -- so the directory goes on looking assembled
    long after there is anything left to cut a piece from.
    '''
    for pattern in (f'{slcDir}/*iw*.slc', f'{slcDir}/*.slc'):
        for slc in glob.glob(pattern):
            try:
                if os.path.getsize(slc) > 0:
                    return True
            except OSError:
                continue
    return False


def setupSeveralImages(check=False, firstDate=None, lastDate=None, frames=None,
                       orbframes=None, strict=True, cleanStale=False,
                       quiet=False, requireSLCs=False):
    '''
    Set up every piece of the track that has not been built yet.

    The body of the program, callable so segmentTrack can run it over a framing
    it has only proposed.  Arguments beyond the command line ones:

        frames, orbframes   framing to use instead of reading `frames` and
                            `orbitframes`; both must be given
        strict              True (standalone) reports the first failure and
                            exits, exactly as this program always has; False
                            collects the failures, skips what they affect and
                            carries on, leaving the caller to report them
        cleanStale          remove the setup scripts of pieces never built and
                            the run files of earlier runs (write mode only)
        quiet               leave out the line per piece already built; the
                            count is in the result, and on a track where
                            everything is built those lines are the whole
                            output
        requireSLCs         skip a directory whose SLCs have been cleaned away
                            (hasSwathSLCs); it still has the metadata to frame
                            from, but the setup would build nothing.  Off by
                            default, so the command line behaves as it always
                            has; segmentTrack passes it

    Returns a dict: the scripts written ('setupFiles'), the pieces already
    built ('skipped'), the SetupErrors collected ('errors'), the run file
    written ('runfile', None under check), what cleanStale took away
    ('removed'), the scripts for pieces never built that this run left alone
    ('pending'), which are the ones the dates did not cover, and the earlier
    run files kept because they still list work nothing else covers
    ('keptRunFiles', as (file, [scripts])), and the directories skipped for
    having no SLCs left ('noSLCs', requireSLCs only).
    '''
    if firstDate is None:
        firstDate = datetime.now() - timedelta(18200)
    if lastDate is None:
        lastDate = datetime(2100, 1, 1)
    result = {'setupFiles': [], 'skipped': [], 'errors': [], 'runfile': None,
              'removed': [], 'pending': [], 'keptRunFiles': [], 'noSLCs': [],
              'noAscNode': []}
    #
    # Get ascending or descending status
    #
    ascdesc = getascdesc()
    if ascdesc < 0:
        error = SetupError('ascdesc', 'no asc desc')
        if strict:
            reportError(error)
        result['errors'].append(error)
        return result
    #
    # get cat sclsf
    #
    slccats = []
    for pattern in ['????-?', '?????-?']:
        slccats += sorted(glob.glob(pattern))
    #
    # Get Frame info
    #
    if frames is None or orbframes is None:
        frames, orbframes = getFramesAndOrbits()
    #
    # Loop through and process each
    #
    done = []
    covered = set()
    undated = []
    now = datetime.now()
    runfile = 'runSetup.'+now.strftime("%h:%d:%y:%T")
    #
    # The scripts to run are collected and the run file written at the end,
    # only if there are any.  A run with nothing to set up -- the usual case
    # once a track is up to date -- used to leave an empty runSetup behind
    # for the user to find and wonder about
    #
    toRun = []
    for slc in slccats:
        slc = slc.strip('\n')
        #
        # Get the framing information - either default or val from orbitframes
        # Check data exists for frame range
        #
        try:
            #
            # Without an ascending node time the directory cannot be dated,
            # so there is no telling whether it is even in the dates this run
            # covers.  Held back rather than failed here: whether it matters
            # depends on whether this orbit turns out to be one the run is
            # setting up, which is only known once the loop has been round
            # (see below).  A partial assembly leaves such directories lying
            # about for years, and failing a whole track over one that
            # nothing is being proposed for is what this used to do
            #
            try:
                slcDate = getAscNodeTime(f'{slc}/ascendingNodeTime')
            except SetupError as error:
                undated.append((slc, int(slc.split('-')[0]), error))
                continue
            if slcDate < firstDate or slcDate > lastDate:
                continue
            #
            # Nothing to cut from a directory whose SLCs are gone, however
            # complete its metadata still looks
            #
            if requireSLCs and not hasSwathSLCs(slc):
                result['noSLCs'].append(slc)
                continue
            orbit, seq = [int(x) for x in slc.split('-')]
            covered.add(orbit)
            framing = determineFraming(orbit, seq, frames, orbframes)
            first, last = checkBurstTimes(orbit, seq, framing)
        except SetupError as error:
            if strict:
                reportError(error)
            result['errors'].append(error)
            continue
        for frameRange in framing:
            #
            # One frame failing leaves the rest of the acquisition alone, so a
            # collecting caller still hears about every frame
            #
            try:
                if not checkRange(frameRange, first, last, slc, done):
                    continue
                outdir = f'{orbit}_{frameRange[0]}'
                if os.path.isdir(outdir):
                    # add to done if it already exists
                    done.append(f'setup_{orbit}_{frameRange[0]}')
                    result['skipped'].append(outdir)
                    if not quiet:
                        print('Skipping ', outdir, ' Already exists')
                    continue
                if check:
                    print(slcDate, orbit, seq, ascdesc, frameRange)
                    setupfile = f'setup_{orbit}_{frameRange[0]}'
                else:
                    setupfile = makeSetupFile(orbit, seq, ascdesc, frameRange)
                    toRun.append(setupfile)
            except SetupError as error:
                if strict:
                    reportError(error)
                result['errors'].append(error)
                continue
            done.append(setupfile)
            result['setupFiles'].append(setupfile)
    #
    # An orbit being set up has to have every one of its sequences readable --
    # a piece may have to be cut from any of them -- so an undated directory
    # of one of those is the error it always was.  One of an orbit this run is
    # not setting up is only reported
    #
    for slc, orbit, error in undated:
        if orbit in covered:
            if strict:
                reportError(error)
            result['errors'].append(error)
        else:
            result['noAscNode'].append(slc)
    print('done')
    if not check:
        if toRun:
            with open(runfile, 'w') as frun:
                print('#', file=frun)
                for setupfile in toRun:
                    print(setupfile, file=frun)
                print('\n', file=frun)
            result['runfile'] = runfile
        #
        # After the loop, so a script this run rewrote is not taken for a
        # superseded one
        #
        if cleanStale:
            result['removed'] += removeStaleSetups(covered,
                                                   result['setupFiles'])
            gone, held = removeOldRunFiles(result['runfile'],
                                           result['setupFiles'])
            result['removed'] += gone
            result['keptRunFiles'] = held
    result['pending'] = pendingSetups(result['setupFiles'])
    return result


def setupSeveralUsage():
    print('\n\t\t\033[1;34m  Setup Full SLCs from Beam SLCs  \033[0m\n')
    print('\n\033[1mSetup Gamma code to cat single beam slcs from X-Y '
          'directories\n')
    print('Usage: \tsetupSeveralTopsImages.py -check -cleanStale -firstdate '
          'YYYY:MM:DD -lastdate YYYY:MM:DD\n')
    print('\t-cleanStale removes the setup scripts of pieces never built and '
          'the run files of earlier runs')
    print('\tFraming is determined by file frames with entries '
          ' "frame"-FirstFrame-NumberOfFrames')
    print('\n\tSpecial cases are defined file orbitframes, which has the '
          'format:\n')
    print('\t\torbit-FirstFrame-NumberFrames <--- orbits with a single frame')
    print('\t\torbit-FirstFrame-FrameIndex-NumberFrames <--- orbits with a '
          'multiple frames')
    print('\tWhere: ')
    print('\t\t FrameIndex\tindex into default frames (a higher value lets '
          'you specify extra frames')
    print('\t\t NumberFrames\tSet to zero to skip this orbit/frame combo '
          '\n\033[0m')
    print('Part of the s1setup package.')
    exit()


def processArg(argv):
    check = False
    cleanStale = False
    firstDate = datetime.now() - timedelta(18200)
    lastDate = datetime(2100, 1, 1)
    fdate, ldate = False, False
    try:
        for s in argv[1:]:
            print(s)
            if '-check' in s:
                check = True
            elif '-cleanStale' in s:
                cleanStale = True
            elif '-firstdate' in s:
                # flag next argument as date
                fdate = True
            elif fdate:
                firstDate = datetime.strptime(s, "%Y:%m:%d")
                fdate = False
            elif '-lastdate' in s:
                # flag next argument as date
                ldate = True
            elif ldate:
                lastDate = datetime.strptime(s, "%Y:%m:%d")
                ldate = False
            elif '-help' in s:
                setupSeveralUsage()
            else:
                setupSeveralUsage()
        return check, cleanStale, firstDate, lastDate
    except Exception:
        setupSeveralUsage()
        raise NameError()
        u.myerror('stop')


def main():
    #
    #
    #
    check, cleanStale, firstDate, lastDate = processArg(sys.argv)
    setupSeveralImages(check=check, firstDate=firstDate, lastDate=lastDate,
                       cleanStale=cleanStale, strict=True)


if __name__ == '__main__':
    main()
