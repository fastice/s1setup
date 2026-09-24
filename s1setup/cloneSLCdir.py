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
import shutil
import json
from s1setup.cullSLCclones import primePairsSameSensor


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
    parser.add_argument('--noSkipIfExists', action='store_true', default=False,
                        help='Do not skip cases where an old copy already '
                        'exists')
    parser.add_argument('--maxDays', type=int, default=None,
                        help='Only clone a pair start whose next same-sensor '
                        'acquisition of the frame is within this many days '
                        '(e.g. 12 to keep a clone set to 12-day pairs); the '
                        'newest acquisition is still cloned to await its '
                        'partner [no limit]')
    parser.add_argument('--requireSlc', action='store_true', default=False,
                        help='Only clone acquisitions whose prime '
                        '<orbit>_<frame>.slc exists and is not empty (a clone '
                        'links it, so without it the clone cannot be paired)')
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
    myArgs = {'check': args.check, 'noSkipIfExists': args.noSkipIfExists,
              'maxDays': args.maxDays, 'requireSlc': args.requireSlc,
              'sourcePath': args.sourcePath, 'track': args.track,
              'sensor': args.sensor,
              'firstdate': date1, 'lastdate': date2}
    return myArgs


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


def duplicateDir(sourceDir, imageDir, track):
    '''
    Copy small files and link large files to duplicate a directory

    '''

    toCopy = glob.glob(f'{sourceDir}/*{imageDir}*par')
    toCopy += glob.glob(f'{sourceDir}/Sentinel-IW.par')
    toCopy += glob.glob(f'{sourceDir}/*{imageDir}*cw')
    toCopy += glob.glob(f'{sourceDir}/geo*')
    toCopy += glob.glob(f'{sourceDir}/*.thetac')
    toCopy += glob.glob(f'{sourceDir}/betaNought')
    #
    toLink = glob.glob(f'{sourceDir}/{imageDir}.slc')
    toLink += glob.glob(f'{sourceDir}/P{imageDir}.10x2.pow')
    #
    if not os.path.exists(track):
        os.mkdir(track)
    if not os.path.exists(f'{track}/{imageDir}'):
        os.mkdir(f'{track}/{imageDir}')
    #
    # Copies
    for source in toCopy:
        shutil.copy(source, f'{track}/{imageDir}/{os.path.basename(source)}')

    #
    # Links
    for source in toLink:
        dest = f'{track}/{imageDir}/{os.path.basename(source)}'
        if os.path.islink(dest):
            os.unlink(dest)
        os.symlink(source, dest)
    #
    print(f'Duplicated {imageDir} with {len(toCopy)}/{len(toLink)} cp/ln')
    return 1


def slcExists(path):
    """True if the SLC (or the file its link points at) exists and is not
    empty."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def cloneWanted(candidates, sourceTrack, sensor, track, maxDays=None):
    '''
    Decide which prime dirs need a clone.

    A clone exists to make the same-sensor 12-day pair the prime does not
    make. So a prime dir is wanted as a pair START when the prime does not
    already pair it with the same sensor, and as the SECOND image when the
    previous same-sensor acquisition of the same frame is a start whose clone
    pair has not been processed yet (once it has, the SLC link is not needed).
    Anything else (the prime already makes its same-sensor pair) would only
    ever be a copy the pair cull deletes the runboth of, so it is not created.
    Returns {imageDir: reason} for the wanted ones.
    '''
    wanted = {}
    byFrame = {}
    for imageDir, date in candidates:
        byFrame.setdefault(imageDir.split('_')[1], []).append((date, imageDir))
    for frame, images in byFrame.items():
        prevOpenStart = False
        prevDate = None
        images = sorted(images)
        for k, (date, imageDir) in enumerate(images):
            start = not primePairsSameSensor(f'{sourceTrack}/{imageDir}',
                                             sourceTrack, sensor)
            # With maxDays, a start whose partner is further away than that
            # would only ever be paired longer than the clone set is for, so
            # it is not a start. The newest acquisition has no partner yet and
            # stays a start, waiting for one.
            if start and maxDays is not None and k + 1 < len(images) \
                    and (images[k + 1][0] - date).days > maxDays:
                start = False
            inReach = maxDays is None or prevDate is None or \
                (date - prevDate).days <= maxDays
            if start:
                wanted[imageDir] = 'pair start'
            elif prevOpenStart and inReach:
                wanted[imageDir] = 'second image'
            prevOpenStart = start and \
                not glob.glob(f'{track}/{imageDir}/azimuth.offsets*')
            prevDate = date
    return wanted


def setupCloneTiepoints(track, sourcePath, check=False):
    '''
    Give the clone track what the tie chain needs: tiepoints/tie_plan_header
    (maketies/setuptopstie die without it, even on a track with no pairs) and
    the vel_thumb_header_<range> grid headers, both taken from the prime with
    the prime project root rewritten to this one.
    '''
    primeRoot = os.path.abspath(sourcePath)
    cloneRoot = os.getcwd()
    tpDir = f'{track}/tiepoints'
    primeHeader = f'{primeRoot}/{track}/tiepoints/tie_plan_header'
    dest = f'{tpDir}/tie_plan_header'
    if os.path.exists(primeHeader) and not os.path.exists(dest):
        if check:
            print(f'would create {dest}')
        else:
            os.makedirs(tpDir, exist_ok=True)
            with open(primeHeader) as fin, open(dest, 'w') as fout:
                fout.write(fin.read().replace(primeRoot + '/',
                                              cloneRoot + '/'))
            print(f'created {dest}')
    if check:
        return
    try:
        from s1setup.setupS1Tracks import syncThumbHeadersFromPrime
        syncThumbHeadersFromPrime([track], primeRoot)
    except Exception as e:      # headers are not fatal to a clone
        print(f'vel_thumb_header sync skipped: {e}')


def main():
    # Parse args
    myArgs = setupClone()
    #
    originals = sorted(glob.glob(
        f'{myArgs["sourcePath"]}/{myArgs["track"]}/*_*'))
    print(f'Total products = {len(originals)}')
    #
    sourceTrack = f'{myArgs["sourcePath"]}/{myArgs["track"]}'
    candidates = []
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
            candidates.append((os.path.basename(sourceDir), date))
    if myArgs['requireSlc']:
        # A clone symlinks the prime SLC; once that has been cleaned away the
        # clone would only hold a dangling link, so it is not made.
        withSlc = [(i, d) for i, d in candidates
                   if slcExists(f'{sourceTrack}/{i}/{i}.slc')]
        if len(withSlc) < len(candidates):
            print(f'{len(candidates) - len(withSlc)} {myArgs["sensor"]} '
                  'products skipped: prime SLC missing or empty')
        candidates = withSlc
    wanted = cloneWanted(candidates, sourceTrack, myArgs['sensor'],
                         myArgs['track'], maxDays=myArgs['maxDays'])
    nSkip = len(candidates) - len(wanted)
    print(f'{len(candidates)} {myArgs["sensor"]} products in range, '
          f'{len(wanted)} need a clone, {nSkip} already paired same-sensor '
          'by the prime'
          + (f' or with no same-sensor partner within {myArgs["maxDays"]} days'
             if myArgs['maxDays'] is not None else ''))
    #
    nDup = 0
    for imageDir, date in candidates:
        if imageDir not in wanted:
            continue
        if (not os.path.exists(f'{myArgs["track"]}/{imageDir}')) \
                and (not myArgs['noSkipIfExists']):
            if myArgs['check']:
                print(f'would clone {imageDir} ({wanted[imageDir]})')
                continue
            # make the copy
            nDup += duplicateDir(f'{sourceTrack}/{imageDir}', imageDir,
                                 myArgs['track'])
    print(f'Number of products duplicated {nDup}')
    setupCloneTiepoints(myArgs['track'], myArgs['sourcePath'],
                        check=myArgs['check'])
    velStatsDest = f'{myArgs["track"]}/velocityStats'
    velStatsSource = f'{myArgs["sourcePath"]}/{myArgs["track"]}/velocityStats'
    if not os.path.exists(velStatsDest) and not myArgs['check']:
        os.symlink(velStatsSource, velStatsDest)


if __name__ == '__main__':
    main()
