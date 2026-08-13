#!/usr/bin/env python3
import utilities as u
import numpy as np
import sys
import os
from subprocess import call
import scipy.ndimage.morphology as morph
import threading
import time
import statistics


def findgainUsage():
    """ provide usage statement """
    print('\n\t\t\033[1;34m Extract gain values from SAFE files \033[0m\n')
    print('\033[1m\n Find gain for each beam and put result in sourceDir/absolutegain\n')
    print('\tfindgain.py -help sourceDir\033[0m')
    print('\nPart of the s1setup package.')
    exit()


def findgainProcessArgs():
    """ process arguments and defaults """
    args = sys.argv[1:]
    if len(args) > 1 or len(args) < 1:
        findgainUsage()
    for arg in args:
        if '-help' in arg:
            findgainUsage()
    return args[0]


def main():
    """ read gain value from safe files """
    # process command line args
    rawDir = findgainProcessArgs()
    #
    print(rawDir)
    safes = u.dols('ls -d ' + rawDir + '/*.SAFE/annotation/calibration/calibration*-hh-*.xml ' +
                   rawDir + '/*.SAFE/annotation/calibration/calibration*-vv-*.xml')
    c1 = []
    c2 = []
    c3 = []
    consts = {'iw1': c1, 'iw2': c2, 'iw3': c3}
    for safe in safes:
        fsafe = open(safe, 'r')
        iw = safe.split('-slc')[0].split('-')[-1]
        for line in fsafe:
            if 'absoluteCalibrationConstant' in line:
                line = line.split('>')[1].split('<')[0]
                consts[iw].append(float(line))
                break
        fsafe.close()
    c1 = statistics.median(c1)
    c2 = statistics.median(c2)
    c3 = statistics.median(c3)
    fgain = open(rawDir + '/absolutegain', 'w')
    print(c1, c2, c3, file=fgain)
    fgain.close()


if __name__ == '__main__':
    main()
