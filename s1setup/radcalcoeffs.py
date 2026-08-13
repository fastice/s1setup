#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Nov 14 07:31:38 2018

@author: ian
"""

import argparse
import os
import utilities as u
import myDev as ss


def radCalCoeffsArgs():
    ''' Handle command line args'''
    parser = argparse.ArgumentParser(
        description='\n\n\033[1mCompute radiometric calibration coefficients'
                    '\033[0m\n\n',
        epilog='Part of the s1setup package.')
    parser.add_argument('--pol', metavar='polarization', type=str,
                        default='hh', help='polarization (hh,hv,vv) [hh]')
    parser.add_argument('imageDir', metavar='image-directory', type=str,
                        nargs=1,
                        help='directory for image product being calibrated')
    parser.add_argument('safeDir', metavar='safe-directory', type=str, nargs=1,
                        help='directory with SAFE files')
    args = parser.parse_args()
    if not os.path.exists(args.imageDir[0]):
        u.myerror(f'radcalcoeffs: Output dir ({args.imageDir[0]}) does not exist')
    if not os.path.exists(args.safeDir[0]):
        u.myerror('SAFEs dir does not exist')
    return args.imageDir[0], args.safeDir[0], args.pol


def getS1XMLs(safeDir, pol):
    ''' get the list of xml files from safe directory with correct
    polarization'''
    safes = u.dols('ls -d {0:s}/S1?_IW*.SAFE'.format(safeDir))
    calFiles = []
    for safe in safes:
        cals = u.dols(
            'ls -d {0:s}/annotation/s1*-{1:s}-*.xml'.format(safe, pol))
        # Kluge: in case default hh doesn't work
        if len(cals) == 0:
            cals = u.dols(
                 'ls -d {0:s}/annotation/s1*-{1:s}-*.xml'.format(safe, 'vv'))
        calFiles += cals
    return calFiles


def processCalFile(s1XML):
    calFile = 'calibration/calibration-{0:s}'.format(os.path.basename(s1XML))
    calFile = os.path.join(os.path.dirname(s1XML), calFile)
    if not os.path.exists(calFile):
        u.myerror('Missing calibration file {0:s}'.format(calFile))
    # read xml
    myCalXML = ss.S1XMLheader(calFile)
    return myCalXML.betaNought()


def processFiles(s1XMLs):
    ''' Read each cal file and strip needed data'''
    #
    # loop over calFiles
    betaNoughts = []
    for s1XML in s1XMLs:
        #
        betaNought = processCalFile(s1XML)
        betaNoughts.append(betaNought)
    print(betaNoughts)
    if betaNoughts[0] == betaNoughts[-1]:
        return betaNoughts[0]
    u.myerror('radcalcoeffs - processFiles differing beta nought values')


def main():
    #
    # Get the directories to process
    imageDir, safeDir, pol = radCalCoeffsArgs()
    print(pol)
    print(f'safeDir {safeDir}')
    #
    s1XMLs = getS1XMLs(safeDir, pol)
    #
    betaNought = processFiles(s1XMLs)
    #
    fp = open(os.path.join(imageDir, 'betaNought'), 'w')
    print(betaNought, file=fp)
    fp.close()
    print(betaNought)


if __name__ == '__main__':
    main()
