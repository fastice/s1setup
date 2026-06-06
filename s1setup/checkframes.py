#!/usr/bin/env python3
# import utilities as u
import sys
import os
import shutil
# from subprocess import call
# from time import strptime, strftime
from datetime import datetime, timedelta
# , date
import glob
#
# create ascending node time file
#


def createAscNodeTime(orbit):
    try:
        fileAsc = f'{orbit}/ascendingNodeTime'
        # get first SAFE xml file
        xmls = sorted(glob.glob(f'{orbit}/*.SAFE/annotation/*iw1*.xml'))
        # open and search fo xml
        with open(xmls[0], 'r') as fxml:
            for line in fxml:
                if 'ascendingNodeTime' in line:
                    break
        # parse line
        ascNodeStr = line.split('>')[1].split('<')[0]
        # print(ascNodeStr,fileAsc)
        #
        with open(fileAsc, 'w') as fnode:
            # print('creating ',fileAsc)
            print(ascNodeStr, end='', file=fnode)
    except Exception:
        print('\n **** Error Trying to Create AscendingNodeTime file *****\n')
        raise NameError()

    return


def getAscNodeTime(orbit):
    # get ascending node time
    try:
        fileAsc = f'{orbit}/ascendingNodeTime'
        # if file doesn't exist, then create
        if not os.path.isfile(fileAsc):
            createAscNodeTime(orbit)
        #
        # now read file (even if just created, this will serve as check
        #
        with open(fileAsc, 'r') as fnode:
            line = fnode.readline()
            ascNode = datetime.strptime(line.strip(), "%Y-%m-%dT%H:%M:%S.%f")
        return ascNode
    except Exception:
        print(f'\n**** Error Reading Ascending node time for {orbit} ******\n')
        raise NameError()


def getOrbitSafe(orbit):
    # get ascNode
    ascNodeTime = getAscNodeTime(orbit)
    # get listing for directory+ str(orbit)+'_9/*.SAFE')
    listing = sorted(glob.glob(f'{orbit}/*.SAFE'))
    # check for _ sequences
    orbitInfo = []
    for safe in listing:
        # print(safe)
        safedir = safe.split('/')[1]
        pieces = safedir.split('_')
        sensor = pieces[0]
        time1 = datetime.strptime(pieces[5], "%Y%m%dT%H%M%S")
        time2 = datetime.strptime(pieces[6], "%Y%m%dT%H%M%S")
        # compute burst
        burst1 = round((time1 - ascNodeTime).seconds/2.759)
        burst2 = round((time2 - ascNodeTime).seconds/2.759)
        orbitInfo.append([orbit, sensor, time1, time2, burst1, burst2, safe])
    return orbitInfo


def getOrbDirs():
    listings = []
    for pattern in ['????', '?????', '????_?', '?????_?']:
        listings += sorted(glob.glob(pattern))
    dirs = []
    for listing in listings:
        try:
            # check X in X or X_Y is an integer, which indicates and orbit
            # d = int(listing.split('_')[0])
            #
            safe = sorted(glob.glob(f'{listing}/*.SAFE'))[0].split('/')[1]
            # print(safe)
            pieces = safe.split('_')
            time1 = datetime.strptime(pieces[5], "%Y%m%dT%H%M%S")
            dirs.append([listing, time1])
        except Exception:
            print('Skipping non orbit dir ', listing)
    # this last bit is to sort by date
    dirs = sorted(dirs, key=lambda date: date[1])
    d = [x[0] for x in dirs]
    # for dir in dirs: d.append(dir[0])
    return d


def getFrameRange():
    ''' read frame range from file, frameRange, and return'''
    try:
        with open('frameRange') as fframe:
            line = fframe.readline().strip()
            line = line.split()
            frameRange = [int(line[0]), int(line[1])]
            print(f'FrameRange {frameRange}')
    except Exception:
        frameRange = [300, 750]
        print('Could not read frameRange file - using extended range '
              f'{frameRange}')
    return frameRange


def usage():
    print('\033[1m\nCheck safe directories to make sure there are no gaps and '
          'they cover the frame range\n')
    print('\tUsage: checkframes.py -halt -ignoreLong -firstdate YYYY:MM:DD '
          ' -lastdate YYYY:MM:DD -mvoutofrange -breakGap -breakLong n\n')
    print('where\n')
    print('\t-halt\t stop program on any error or inconsistency - otherwise '
          'just warn and move on \n')
    print('\t-ignoreIncomplete\t don not print message or halt for incomplete '
          'frames \n')
    print('\t-firstdate YYYY:MM:DD\t only check images for first date and '
          'later')
    print('\t-lastdate YYYY:MM:DD\t only check images for second date and '
          'earlier\n')
    print('\t-breakLong n\t move the first n images to a second dir '
          'for > 127 frames \n')
    print('\t-breakGap\t break in two if gap\n')
    print('\t-ignoreLong n\t Ignore long (good for slight over length\n')
    print('\t-mvoutofrange\t mv early or late frames to tmp dir\n\033[0m')
    print('Part of the s1setup package.')
    return


def processArg(argv):
    halt = False
    ignoreIncomplete = False
    fdate = False
    ldate = False
    firstDate = datetime.strptime('2000:01:01', "%Y:%m:%d")
    lastDate = datetime.strptime('2100:01:01', "%Y:%m:%d")
    mvoutofrange = False
    breakLong = 0
    breakGap = False
    ignoreLong = False
    try:
        for s in argv[1:]:
            if '-halt' in s:
                halt = True
            elif '-ignoreIncomplete' in s:
                ignoreIncomplete = True
            elif '-mvoutofrange' in s:
                mvoutofrange = True
            elif '-firstdate' in s:
                # flag next argument as date
                fdate = True
            elif '-lastdate' in s:
                # flag next argument as date
                ldate = True
            elif 'breakGap' in s:
                breakGap = True
            elif 'ignoreLong' in s:
                ignoreLong = True
            elif 'breakLong' in s:
                breakLong = -1
            elif breakLong < 0:
                breakLong = int(s)
            elif fdate:
                firstDate = datetime.strptime(s, "%Y:%m:%d")
                fdate = False
            elif ldate:
                lastDate = datetime.strptime(s, "%Y:%m:%d")
                ldate = False
            else:
                usage()
                exit()
        return halt, ignoreIncomplete, firstDate, lastDate, mvoutofrange, \
            breakLong, breakGap, ignoreLong
    except Exception:
        usage()
        raise NameError()
        exit()


def frameCheck(orbitInfo, frameRange):
    #
    count = 0
    early = []
    late = []
    incomplete = []
    first = orbitInfo[0][4]
    last = orbitInfo[-1][5]
    if first > frameRange[0] or last < frameRange[1]:
        incomplete = [first, last]
    for orbit in orbitInfo:
        # check frame not earler than range
        if orbit[5] < frameRange[0]:
            early.append(count)
        elif orbit[4] > frameRange[1]:
            late.append(count)
        count += 1
    return early, late, incomplete

#
# Check for gaps - return index of gaps
#


def checkGaps(orbitInfo):
    last = orbitInfo[0]
    gaps = []
    count = 0
    for orb in orbitInfo[1:]:
        if orb[4]-last[5] > 1:
            gap = count
            gaps.append(gap)
        last = orb
        count += 1
    return gaps


def makeTmp():
    ''' make tmp directory if one doesn't exist '''
    if os.path.isdir('tmp'):
        return
    if os.path.exists('tmp'):
        os.remove('tmp')
    os.mkdir('tmp')


def fixGap(orbit, orbitInfo, start, gap, gapCount):
    ''' Mv images to another directory '''
    gapDir = f'{orbit}_{gapCount+4}'
    if not os.path.exists(gapDir):
        os.mkdir(gapDir)
    print(f'moving gap {gapCount} files to {gapDir}')
    for i in range(start, gap+1):
        newLoc = os.path.join(gapDir, os.path.basename(orbitInfo[i][6]))
        print(orbitInfo[i][6], newLoc)
        shutil.move(orbitInfo[i][6], newLoc)
    return gapCount + 1, i


def main():
    #
    # process command line to stop on errors rather than warn
    #
    start = datetime.now()
    halt, ignoreIncomplete, firstDate, lastDate, mvoutofrange, breakLong,\
        breakGap, ignoreLong = processArg(sys.argv)
    print(halt, firstDate, lastDate)
    if mvoutofrange:
        makeTmp()
    # 4537-3-439-45
    # trap all errors here
    #
    try:
        # get the orbit listing
        print(f'Before getOrbs {datetime.now()-start}')
        orbits = getOrbDirs()
        # get frame Range
        print(f'Before getGetFrameRange {datetime.now()-start}')
        frameRange = getFrameRange()
        #
        # get data info for each orbit
        #
        print(f'Before orbits loop {datetime.now()-start}')
        priorDate = datetime(1900, 1, 1, 0, 0, 0)
        for orbit in orbits:
            err = False
            orbitInfo = getOrbitSafe(orbit)
            # print('x', orbit, orbitInfo)
            if (orbitInfo[0][2] - firstDate) > timedelta(seconds=0) and \
                    (orbitInfo[0][2] - lastDate) < timedelta(seconds=0):
                days = (orbitInfo[0][2] - priorDate)
                days = round(days.days + days.seconds/86400.)
                #nFrames = len(orbitInfo
                nFrames = orbitInfo[-1][-2] - orbitInfo[0][-3] + 1
                if days > 365:
                    days = -99
                print('Start Orbit {:7s}'.format(orbit),
                      orbitInfo[0][2].strftime('%Y:%m:%d'),
                      '{:4d}'.format(days), end='')
                priorDate = orbitInfo[0][2]
                #
                # Gap check
                #
                gaps = checkGaps(orbitInfo)
                if len(gaps) > 0:
                    gapCount = 0
                    start = 0
                    for gap in gaps:
                        print('|', gap, '|')
                        err = True
                        print('\n-----------------------------------------GAP-'
                              '------------------------------------------')
                        diff = orbitInfo[gap][3]-orbitInfo[gap][2]
                        print(f'\033[1m {orbitInfo[gap][0]},  '
                              f'{orbitInfo[gap][2].strftime("%m-%d-%Y")}'
                              '\033[0m'
                              f'{orbitInfo[gap][1]}[\033[1m'
                              f'{orbitInfo[gap][2].strftime("%H:%M:%S")}'
                              '\033[0m'
                              f'{orbitInfo[gap][3].strftime("%H:%M:%S")}]'
                              f'[[,\033[1m'
                              f'{orbitInfo[gap+1][2].strftime("%H:%M:%S")}'
                              '[033[0m'
                              f'{orbitInfo[gap+1][3].strftime("%H:%M:%S")}]'
                              f'\033[1m {diff.seconds}\033[0m')
                        print(orbitInfo[gap][4:])
                        print(orbitInfo[gap+1][4:])

                        if breakGap:
                            gapCount, start = fixGap(orbit, orbitInfo, start, gap, gapCount)
                        if halt:
                            raise NameError()
                else:
                    print('  No Gaps, ', end='')
                #
                # Check Frame Range
                #
                early, late, incomplete = frameCheck(orbitInfo, frameRange)
                if len(incomplete) > 0 and not ignoreIncomplete:
                    err = True
                    print('\n[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[ Frames'
                          ' Do Not Span Full Frange ]]]]]]]]]]]]]]]]]]]]]]]]]'
                          ']]]]]]]]]]]]]]]')
                    print(f'{orbit} File span range {incomplete} which does '
                          f'not cover full range {frameRange}')
                    # reset to avoid print message repeatedly
                    incomplete = []
                if nFrames > 127 and breakLong > 0:

                    firstFramesDir = f'{orbit}_3'

                    if not os.path.exists(firstFramesDir):
                        print(f'\n>>>>>> Breaking large track for {orbit}>>>>')
                        os.mkdir(firstFramesDir)
                        for i in range(0, breakLong):
                            shutil.move(orbitInfo[i][6],
                                        os.path.join(firstFramesDir,
                                                     os.path.basename(
                                                     orbitInfo[i][6])))
                    else:
                        print(f'\n>>>> {firstFramesDir} exists, could not split')
                elif nFrames > 127 and not ignoreLong:
                    print(f'\n** {orbit} warning too many frames: {nFrames} **\n')
                    #mkdir
                for i in early:
                    err = True
                    print('\n>>>>>>>>>>>>>>>>> EARLY Frame >>>>>>>>>>>>>>>>>>')
                    print(orbitInfo[i][6], end='\n')
                    if mvoutofrange:
                        shutil.move(orbitInfo[i][6],
                                    os.path.join('tmp',
                                                 os.path.basename(
                                                     orbitInfo[i][6])))
                for i in late:
                    err = True
                    print('\n<<<<<<<<<<<<<<<<< LATE Frame <<<<<<<<<<<<<<<<<<<')
                    print(f'{orbitInfo[i][6]} for bursts [,'
                          f'{orbitInfo[i][4]} {orbitInfo[i][5]},]'
                          f'outside {frameRange}', end='\n')
                    if mvoutofrange:
                        shutil.move(orbitInfo[i][6],
                                    os.path.join('tmp',
                                                 os.path.basename(
                                                     orbitInfo[i][6])))
                if len(incomplete) < 1 and len(gaps) < 1:
                    print(' fully covers ', frameRange)
                else:
                    print(f' partial coverage with frames {incomplete}'
                          f', for {frameRange}')
                #
                # exit if err occurs and halt on error set
                #
                if err and halt:
                    exit()
#                for gap in gaps

    except Exception:
        print('Error see above for error message ')
        exit()


if __name__ == '__main__':
    main()
