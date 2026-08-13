#!/usr/bin/env python3
import utilities as u
import os
from subprocess import call
import sys
from datetime import datetime, timedelta
import glob


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
        line = fnode.readline()
        ascNode = datetime.strptime(line.strip(), "%Y-%m-%dT%H:%M:%S.%f")
        fnode.close()
        return ascNode
    except Exception:
        u.myerror(f'**** Error Reading Ascending node time for {fileAsc} ****')


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
        print(f'Could not  find burst times file for:  {d}')
        raise NameError()
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
            u.myerror(f'\n\n ***** Gap in burst times ******\n\n '
                      f'{burstTimes[0]} burstnum={burstNum}')
            raise NameError()
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
    3) No valid range, flag error so user can fix with entry in orbframes
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
    u.myalert('If multiple source images (e.g. _1) make sure first image is '
              'ok, not just the one flagged')
    u.myerror(f'***** Frames {firstRange} to {lastRange} for {slc} not in '
              f'burst time range {first} to {last} {slc}******')


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
        print(f'Setup file not created for {setupfile}')
        raise NameError()


def setupSeveralUsage():
    print('\n\t\t\033[1;34m  Setup Full SLCs from Beam SLCs  \033[0m\n')
    print('\n\033[1mSetup Gamma code to cat single beam slcs from X-Y '
          'directories\n')
    print('Usage: \tsetupSeveralTopsImages.py -check -firstdate YYYY:MM:DD -'
          'lastdate YYYY:MM:DD\n')
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
    firstDate = datetime.now() - timedelta(18200)
    lastDate = datetime(2100, 1, 1)
    fdate, ldate = False, False
    try:
        for s in argv[1:]:
            print(s)
            if '-check' in s:
                check = True
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
        return check, firstDate, lastDate
    except Exception:
        setupSeveralUsage()
        raise NameError()
        u.myerror('stop')


def main():
    #
    #
    #
    check, firstDate, lastDate = processArg(sys.argv)
    #
    # Get ascending or descending status
    #
    ascdesc = getascdesc()
    if ascdesc < 0:
        u.myerror('no asc desc')
    #
    # get cat sclsf
    #
    slccats = []
    for pattern in ['????-?', '?????-?']:
        slccats += sorted(glob.glob(pattern))
    #
    # Get Frame info
    #
    frames, orbframes = getFramesAndOrbits()
    orbits = []
    for orbf in orbframes:
        orbits.append(orbf[0])
    #
    # Loop through and process each
    #
    done = []
    now = datetime.now()
    runfile = 'runSetup.'+now.strftime("%h:%d:%y:%T")
    if not check:
        frun = open(runfile, 'w')
        print('#', file=frun)
    for slc in slccats:
        slc = slc.strip('\n')
        slcDate = getAscNodeTime(f'{slc}/ascendingNodeTime')
        # print(slcDate)
        if slcDate < firstDate or slcDate > lastDate:
            continue
        orbit, seq = [int(x) for x in slc.split('-')]
        #print(orbit, seq)
        #
        # Get the framing information - either default or val from orbitframes
        #
        try:
            framing = determineFraming(orbit, seq, frames, orbframes)
            first, last = checkBurstTimes(orbit, seq, framing)

            for frameRange in framing:
                if checkRange(frameRange, first, last, slc, done):
                    outdir = f'{orbit}_{frameRange[0]}'
                    if os.path.isdir(outdir) is False:
                        if not check:
                            setupfile = makeSetupFile(orbit, seq, ascdesc,
                                                      frameRange)
                            print(setupfile, file=frun)
                        else:
                            print(slcDate, orbit, seq, ascdesc, frameRange)
                            setupfile = f'setup_{orbit}_{frameRange[0]}'
                        if len(setupfile) > 0:
                            done.append(setupfile)
                    # add to done if it already exists
                    else:
                        setupfile = f'setup_{orbit}_{frameRange[0]}'
                        done.append(setupfile)
                        print('Skipping ', outdir, ' Already exists')
        #
        # Check data exists for frame range
        #

        except NameError:
            if not check:
                frun.close()
            # clean up since aborted
            os.remove(runfile)
            print('\nSomething went wrong - see error message above ')
            exit()
    print('done')
    if not check:
        print('\n', file=frun)
        frun.close()


if __name__ == '__main__':
    main()
