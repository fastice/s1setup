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


def main():
    # Parse args
    myArgs = setupClone()
    #
    originals = sorted(glob.glob(
        f'{myArgs["sourcePath"]}/{myArgs["track"]}/*_*'))
    print(f'Total products = {len(originals)}')
    #
    nDup = 0
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
            #
            if (not os.path.exists(f'{myArgs["track"]}/{imageDir}')) \
                    and (not myArgs['noSkipIfExists']):
                # make the copy
                nDup += duplicateDir(sourceDir, imageDir, myArgs['track'])
    print(f'Number of products duplicated {nDup}')
    velStatsDest = f'{myArgs["track"]}/velocityStats'
    velStatsSource = f'{myArgs["sourcePath"]}/{myArgs["track"]}/velocityStats'
    if not os.path.exists(velStatsDest):
        os.symlink(velStatsSource, velStatsDest)


if __name__ == '__main__':
    main()
