#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Jun 11 09:58:40 2025

@author: ian
"""
import argparse
import utilities as u
from datetime import datetime
import glob
import os
import json
import shutil


def setupClone():
    '''
    Handle command line args
    '''
    parser = argparse.ArgumentParser(
        description='\n\n\033[1Clone and SLC dir using links and duplicates'
        '\033[0m\n\n',
        epilog='Part of the s1setup package.')

    parser.add_argument('track', type=str, default='',
                        help='track dir (e.g., track-90')
    parser.add_argument('sensor', type=str,
                        choices=['S1A', 'S1B', 'S1C', 'S1D'],
                        help='track dir (e.g., track-90')
    parser.add_argument('--sourcePath', type=str, default='Sentinel1',
                        help='Path to directory to clone')
    parser.add_argument('--firstdate', type=str, default='2015-01-01',
                        help='Use first dates >= first date [2015-01-01]')
    parser.add_argument('--lastdate', type=str, default='2050-01-01',
                        help='Use last dates <= lastdate [2050-01-01]')
    parser.add_argument('--check', action='store_true', default=False,
                        help='Check what will be done but do not change '
                        'anything')
    parser.add_argument('--noRemoveRedundant', action='store_true',
                        default=False,
                        help='Cull duplicate runboths only; keep every clone '
                        'directory (skip the redundant-clone removal)')
    #
    args = parser.parse_args()
    #
    if '/' not in args.sourcePath:
        args.sourcePath = f'{os.path.dirname(os.getcwd())}/{args.sourcePath}'
    #
    for st in ['track', '-']:
        if st not in args.track:
            u.myerror(f'Invalid track dir {args.track}')
    # Check dates
    try:
        date1 = datetime.strptime(args.firstdate, '%Y-%m-%d')
        date2 = datetime.strptime(args.lastdate, '%Y-%m-%d')
    except Exception:
        u.myerror(f'Error  parsing dates : {args.firstdate} and/or '
                  f'{args.lastdate}')
    #
    myArgs = {'check': args.check,
              'noRemoveRedundant': args.noRemoveRedundant,
              'sourcePath': args.sourcePath, 'track': args.track,
              'sensor': args.sensor,
              'firstdate': date1, 'lastdate': date2}
    return myArgs


def parseRunBoth(runboth):
    ''' parse frame and orbits from runboth '''
    line = ''
    with open(runboth) as fp:
        for line in fp:
            if 'setupStrackReg.py' in line:
                break
        else:
            line = ''  # no setupStrackReg line: runboth incomplete
    if len(line) > 1:
        line = line.split('--frame')[1].strip()
        return dict(zip(['frame', 'orbit1', 'orbit2', 'sensor'],
                    [x.strip() for x in line.split()]))
    return None


def removeIfDuplicate(runbothClone, runbothOrig, myArgs):
    ''' Check for duplicate, and remove if check False '''
    clone = parseRunBoth(runbothClone)
    orig = parseRunBoth(runbothOrig)
    if orig is None or clone is None:
        print(f'skiping {runbothClone} because one of the runboths is '
              'incomplete')
        return 0
    match = True
    for key in clone:
        if clone[key] != orig[key]:
            match = False
            break
    #
    if match:
        print(f'{runbothClone} duplicates original ', end='')
        if myArgs['check']:
            print('check set, so not deleting')
            return 0
        print(' so deleting')
        os.remove(runbothClone)
        return 1
    return 0


def readGeojson(geojsonFile, echo=False):
    '''
    Read sensor and date from geojson file
    '''
    if not os.path.exists(geojsonFile):
        u.myerror('Attempted to open geodat file that does not exist')
#
    with open(geojsonFile) as fp:
        geojsonData = json.load(fp)
        return parseGeojson(geojsonData, echo=echo)


def parseGeojson(geojsonData, echo=False):
    '''
    Do the parsing to get the date and sensor

    '''
    # geojson dicts
    props = geojsonData['properties']
    date = datetime.strptime(props['Date'], '%Y-%m-%d')
    for s1 in ["S1A", "S1C", "S1B", "S1D", None]:
        if s1 in props['ImageName']:
            break
    return s1, date


def primePairsSameSensor(sourceDir, sourceTrack, sensor):
    '''
    True if the prime already pairs this acquisition with the same sensor,
    i.e. its runboth names a second orbit whose geojson carries `sensor`.
    Such a pair is exactly what a clone would duplicate.
    '''
    pair = parseRunBoth(f'{sourceDir}/runboth') \
        if os.path.exists(f'{sourceDir}/runboth') else None
    if pair is None:
        return False
    partnerGeo = f'{sourceTrack}/{pair["orbit2"]}_{pair["frame"]}/' \
        'geodat10x2.geojson'
    if not os.path.exists(partnerGeo):
        return False
    return readGeojson(partnerGeo)[0] == sensor


def offsetsExist(frameDir):
    return len(glob.glob(f'{frameDir}/azimuth.offsets*')) > 0


def isClone(cloneDir, sourceTrack):
    ''' A clone holds symlinks into the prime track and no offsets. '''
    links = [f for f in glob.glob(f'{cloneDir}/*') if os.path.islink(f)]
    if not links or offsetsExist(cloneDir):
        return False
    prime = os.path.realpath(sourceTrack) + '/'
    return all(os.path.realpath(f).startswith(prime) for f in links)


def removeRedundantClones(myArgs, imageDirs):
    '''
    Delete clone directories that no longer serve a clone pair.

    After the duplicate runboths are gone, a clone is kept only if it
      - has offsets (a processed clone pair lives there),
      - still has a runboth (it starts a clone pair),
      - is the second image of any clone pair in the track, processed or not
        (runboth needs its SLC link, and every tie rerun's azest reads its
        geodat10x2.in), found from the start's runboth or *.pairinfo, or
      - is not paired same-sensor by the prime (a clone pair start waiting
        for its partner to be acquired).
    Everything else is a copy of a frame the prime already pairs the same
    way; it comes back from cloneSLCdir if it is ever needed as a second
    image.  Returns the number removed.
    '''
    track, sourceTrack = myArgs['track'], \
        f'{myArgs["sourcePath"]}/{myArgs["track"]}'
    neededSecond = set()
    # Every clone dir in the track, not just this date window, and processed
    # pairs too: removing their second image left azest unable to open
    # ../../<orbit2>_<frame>/geodat10x2.in.
    for cloneDir in glob.glob(f'{track}/*_*'):
        frame = os.path.basename(cloneDir).split('_')[-1]
        if os.path.exists(f'{cloneDir}/runboth'):
            pair = parseRunBoth(f'{cloneDir}/runboth')
            if pair is not None:
                neededSecond.add(f'{pair["orbit2"]}_{pair["frame"]}')
        for pairInfo in glob.glob(f'{cloneDir}/*.pairinfo'):
            with open(pairInfo) as fp:
                fields = fp.readline().split()
            if len(fields) > 1:
                neededSecond.add(f'{fields[1]}_{frame}')
    nRemoved = 0
    for imageDir in imageDirs:
        cloneDir = f'{track}/{imageDir}'
        if not os.path.isdir(cloneDir):
            continue
        if offsetsExist(cloneDir) or os.path.exists(f'{cloneDir}/runboth') \
                or imageDir in neededSecond \
                or not primePairsSameSensor(f'{sourceTrack}/{imageDir}',
                                            sourceTrack, myArgs['sensor']):
            continue
        if not isClone(cloneDir, sourceTrack):
            print(f'{cloneDir} is not a plain clone (kept)')
            continue
        if myArgs['check']:
            print(f'would remove redundant clone {cloneDir}')
        else:
            shutil.rmtree(cloneDir)
            print(f'removed redundant clone {cloneDir}')
        nRemoved += 1
    return nRemoved


def main():
    # Parse args
    myArgs = setupClone()
    #
    originals = sorted(glob.glob(
        f'{myArgs["sourcePath"]}/{myArgs["track"]}/*_*'))
    print(f'Total products = {len(originals)}')
    #
    nDup = 0
    imageDirs = []
    for sourceDir in originals:
        myGeoJson = f'{sourceDir}/geodat10x2.geojson'
        # Skip if not geojson
        if not os.path.exists(myGeoJson):
            continue
        #
        # Check date ans sensor
        s1, date = readGeojson(myGeoJson)
        if (s1 == myArgs['sensor'] and date >= myArgs['firstdate']
                and date <= myArgs['lastdate']):
            imageDir = os.path.basename(sourceDir)
            imageDirs.append(imageDir)
            runbothOrig = f'{sourceDir}/runboth'
            runbothClone = f'{myArgs["track"]}/{imageDir}/runboth'
            #
            if os.path.exists(runbothOrig) and os.path.exists(runbothClone):
                # make the copy
                nDup += removeIfDuplicate(runbothClone, runbothOrig, myArgs)
    print(f'Number of products removed {nDup}')
    # A culled runboth leaves the clone dir behind; take those (and never-
    # paired trailing clones) away too, unless a live clone pair needs them.
    if myArgs['noRemoveRedundant']:
        print('Redundant clone directories: not checked (--noRemoveRedundant)')
        return
    nDirs = removeRedundantClones(myArgs, imageDirs)
    what = 'would be removed' if myArgs['check'] else 'removed'
    print(f'Redundant clone directories {what}: {nDirs}')


if __name__ == '__main__':
    main()
