#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Set up every unprocessed pair in a track directory for a given year.

For each frame, `grepdate` lists the acquisitions in date order with the gap
(in days) to the next acquisition of the same frame.  Every acquisition that
has not been set up yet (`o--` status) and whose gap is 0 < nDays <= the
sensor's maxDays is paired with the next one, whatever that gap is -- 1, 5, 6,
7, 12, 13 ... day pairs are all formed the same way.  `setupSARpair.py` then
writes the pair's `runboth` and `fast/dofast`.
"""
import utilities as u
import os
from subprocess import call, check_output, STDOUT
import sys
import argparse
import sarfunc as s
import numpy as np


def setuppairsArgs():
    ''' Handle command line args'''
    parser = argparse.ArgumentParser(
        description='\033[1mSet up all of the available pairs\033[0m',
        epilog='Run in a track directory. Should automatically detect '
        'greenland or antarctica and should auto detect sensor. Pairs are '
        'formed between consecutive acquisitions of a frame for any '
        'separation up to the sensor maxDays. Calls setupSARpair.py for '
        'each pair. Part of the s1setup package.')
    parser.add_argument('year', type=int, nargs=1, help='Orbit 1')
    parser.add_argument('--sensor', type=str, default='default',
                        help='sensor name [S1,TSX,CSK] - only needed if '
                        'sensor name is not in path (Sentinel,TSX,CSK)')
    parser.add_argument('--region', type=str, default=None,
                        help='region name [greenland, taku, antarctica]')
    parser.add_argument('--frame', type=int, default=-1,
                        help='optional frame number [all valid frames]')
    parser.add_argument('--tiff', action='store_true', default=False,
                        help='Set up every pair to run GeoTIFF-backed '
                        '(propagates --tiff into each runboth/dofast)')
    parser.add_argument('--check', action='store_true', default=False,
                        help='Dry run - print the pairs and the '
                        'setupSARpair.py commands without running them')
    args = parser.parse_args()
    year = args.year[0]

    if year < 2008 or year > 2040:
        u.myerror('invalid year {0:d}'.format(year))
    return year, args.sensor, [args.frame], args.region, args.tiff, args.check
#
# Setup pairs and run command
#


def setupPair(line, nextLine, frame, sensor, region, tiff=False, check=False):
    orb1 = line.split()[2]
    orb1 = orb1.split('_')[0]
    orb2 = nextLine.split()[2]
    orb2 = orb2.split('_')[0]
    #
    regionStr = ''
    if region is not None:
        regionStr = f'--region {region}'
    tiffStr = ' --tiff' if tiff else ''
    #
    fullcommand = \
        f'setupSARpair.py {regionStr} --frame {frame} {orb1} {orb2} ' \
        f'{sensor}{tiffStr}'
    #
    if check:
        print(f'[check] {fullcommand}')
        return
    call(fullcommand, shell=True)


#
# run the grepdate command
#
def grepDate(year, frame):
    # This is used to pull all frames
    if frame is None:
        cmd = f'grepdate.py {year}'
    # Target a single frame
    else:
        cmd = f'grepdate.py --frame {frame} {year} '
    #
    try:
        print(cmd, file=sys.stderr)
        lines = str(check_output(cmd, stderr=STDOUT, shell=True),
                    'utf-8').split('\n')
        lines = lines[3:-1]
    except Exception:
        print('\n**** grep command failed - check in correct directory ****\n')
        lines = []
    return lines


#
# do full grepdate to get unique frames
#
def getFrames(year):
    #
    frames = []
    for line in grepDate(year, None):
        frames.append(line.split()[2].split('_')[1])
    return list(np.unique(frames))


def getMaxDaysAndSensor(sensor):
    ''' autodetect the sensor from the directory name and return '''
    # ------ modify this for other sensors
    if sensor == 'default':
        if 'TSX' in os.getcwd():
            sensor = 'TSX'
        elif 'CSK' in os.getcwd():
            sensor = 'CSK'
        elif 'Sentinel' in os.getcwd():
            sensor = 'S1'
        else:
            print('\n**** Could not determine sensor ****\n')
            exit()
    #
    try:
        sensorDef = s.sensorDefinitions(sensor)
    except Exception:
        u.myerror(f'Invalid sensor {sensor}')
    #
    return sensorDef.SAR['maxDays'], sensor


def setupFramePairs(year, frame, maxDays, sensor, region, tiff=False,
                    check=False):
    '''
    Set up every unprocessed pair for one frame in one year.

    grepdate lists the frame's acquisitions in date order, each with the gap
    to the next acquisition of the same frame (0 if there is none within
    maxDays).  The partner of a line is simply the next line, except for the
    last line of the year, whose partner is the first line of the next year.
    Nothing here depends on the gap being 6 or 12 days.

    Returns the number of pairs set up.
    '''
    lines = grepDate(year, frame)
    nProcessed = 0
    nDays = 0
    for count, line in enumerate(lines):
        a = line.split()
        if len(a) > 5:
            nDays = int(a[1].replace('d', ''))
        if line.find('o--') >= 0 and nDays <= maxDays and nDays > 0:
            if count + 1 < len(lines):
                nextLine = lines[count + 1]
            else:
                # Partner falls in the next year, so grepdate did not list
                # it; it is the first acquisition of the frame next year.
                print('Next year')
                linesNextYear = grepDate(int(year) + 1, frame)
                if len(linesNextYear) == 0:
                    print(f'No partner found next year for {a[2]}')
                    continue
                nextLine = linesNextYear[0]
            print(f'{nDays}d pair')
            print(line, '\n', nextLine)
            #
            setupPair(line, nextLine, frame, sensor, region, tiff=tiff,
                      check=check)
            nProcessed += 1
    return nProcessed


def main():
    ''' Run in track directory to set up pairs '''
    #
    # Get args
    year, sensor, frames, region, tiff, check = setuppairsArgs()
    # auto detect and valdidate sensore and ge maxDays
    maxDays, sensor = getMaxDaysAndSensor(sensor)
    print(sensor, maxDays)
    # get frames
    if frames[0] < 0:
        frames = getFrames(year)
    else:
        frames = [str(frames[0])]
    print('Frames ', frames)
    #
    # loop over frames
    #
    nProcessed = 0
    for frame in frames:
        nProcessed += setupFramePairs(year, frame, maxDays, sensor, region,
                                      tiff=tiff, check=check)
    what = 'would be set up' if check else 'set up'
    print(f'{nProcessed} pairs {what}')


if __name__ == '__main__':
    main()
