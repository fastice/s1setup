#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Oct  4 07:50:40 2018

@author: ian
"""
import glob
import time
import utilities as u
import os
import argparse

RED   = '\033[91m'
RESET = '\033[0m'


def _elapsed(t0, label):
    print(f'{RED}  [{label}] done in {time.time() - t0:.1f}s{RESET}')


myLog = u.logger(fileRoot='runPreProcTops')


def setupPreProcArgs():
    ''' Handle command line args'''
    parser = argparse.ArgumentParser(
        description='Unpack Sentinel-1 TOPS SAFE directories to SLC files, '
                    'extract ascending node time, and update state vectors.',
        epilog='Part of the s1setup package.')
    parser.add_argument('orbitDir', type=str, nargs=1, help='XXXX or XXXX_SEQ')
    parser.add_argument('--scratch', type=str, default=None,
                        help='Directory for SLC output files '
                             '[default: orbit dir itself]')
    # parse arguments
    args = parser.parse_args()
    # log arguments
    myLog.logArgs(args)
    #
    if len(args.orbitDir) > 1:
        u.myerror('more than one orbit dir specified', myLogger=myLog)
    if not os.path.exists(args.orbitDir[0]):
        u.myerror('orbit directory does not exist', myLogger=myLog)
    return args.orbitDir[0], args.scratch


def getSafes(inputSafe):
    ''' ls to get safes, and extract pol and xml info'''
    safes = u.dols('ls -d *.SAFE')
    safeData = []
    fp = open(inputSafe, 'w')
    for safe in safes:
        # write safe to input file
        print(safe, file=fp)
        #
        # go through xmls to extract pol and single pol xml name
        xmls = u.dols(f'ls {safe}/annotation/*iw1*.xml')
        pol = ''
        for xml in xmls:
            if '-hh-' in xml:
                pol = 'hh'
                currentXml = xml
            elif '-vv-' in xml:
                pol = 'vv'
                currentXml = xml
        if len(pol) < 2:
            u.myerror('reunPreProcTops: Could not parse polarization')
        # save results
        safeData.append({'safe': safe, 'pol': pol, 'xml': currentXml})
    fp.close()
    #
    # error check to make sure all polarizations are the same
    for safeD in safeData:
        if safeD['pol'] != safeData[0]['pol']:
            u.myerror('runPreProcTops.py - getSafes: inconsistent pol',
                      myLogger=myLog)
    return safeData


def makeAscNodeTime(xml):
    ''' parse out the ascending node time and make a file with that info'''
    fp = open(xml, 'r')
    for line in fp:
        if 'ascendingNodeTime' in line:
            time = line.split('>')[1].split('<')[0]
            fp = open('ascendingNodeTime', 'w')
            print(time, file=fp)
            fp.close()
            # done so return
            return


def renameSLCFiles(files1, modifier):
    ''' rename all slc related files so .slc becomes .modifier.slc'''
    newFiles = []
    for file1 in files1:
        file1New = file1.replace('.slc',  modifier+'.slc')
        os.rename(file1, file1New)
        os.rename(file1.replace('.slc', '.slc.par'),
                  file1New.replace('.slc', '.slc.par'))
        os.rename(file1.replace('.slc', '.tops_par'),
                  file1New.replace('.slc', '.tops_par'))
        newFiles.append(file1New)
    return newFiles


def runPreProc(inputSafe, pol, outputDir):
    ''' Run prec proc '''
    #
    myArgs = ['-s', '-t', inputSafe, outputDir, pol, 'log.preproc']
    env = os.environ.copy()
    env['OMP_NUM_THREADS'] = '4'
    u.callMyProg('S1_TOPS_preproc', myArgs=myArgs, screen=True, logger=myLog,
                 env=env)
    #
    files1 = sorted(glob.glob(os.path.join(outputDir, '*iw1*_??.slc')))
    files2 = sorted(glob.glob(os.path.join(outputDir, '*iw2*_??.slc')))
    files3 = sorted(glob.glob(os.path.join(outputDir, '*iw3*_??.slc')))
    #
    if len(files1) != len(files2) or len(files1) != len(files3):
        u.myerror('runPreProcTops.py - runPreProc: inconsistent number of '
                  'slcs produced', myLogger=myLog)
    return files1, files2, files3


def runDeramp(inputSafeOrig, inputSafeDeramp):
    ''' run the deramper '''
    #
    myArgs = [inputSafeOrig, inputSafeDeramp, '0', '1']
    env = os.environ.copy()
    env['OMP_NUM_THREADS'] = '4'
    u.callMyProg('SLC_deramp_S1_TOPS', myArgs=myArgs, screen=True,
                 logger=myLog, env=env)
    # indicate deramped
    fp = open('deramped', 'w')
    fp.close()


def callUpdateState(parfile):
    ''' run the state updates'''
    myArgs = [parfile]
    u.callMyProg('updateS1State.py', myArgs=myArgs, logger=myLog, screen=True)


def makeTopsTab(tabfile, fileRoot1, fileRoot2, fileRoot3):
    ''' make a tops tab file with appropriate par files'''
    fp = open(tabfile, 'w')
    print(fileRoot1, fileRoot1.replace('.slc', '.slc.par'),
          fileRoot1.replace('.slc', '.tops_par'), file=fp)
    print(fileRoot2, fileRoot2.replace('.slc', '.slc.par'),
          fileRoot2.replace('.slc', '.tops_par'), file=fp)
    print(fileRoot3, fileRoot3.replace('.slc', '.slc.par'),
          fileRoot3.replace('.slc', '.tops_par'), file=fp)
    fp.close()


def cleanupTabFile(tabfile):
    ''' remove all files in a tab file '''
    fp = open(tabfile, 'r')
    for line in fp:
        pieces = line.split()
        for piece in pieces:
            if os.path.isfile(piece):
                os.remove(piece)


def main():
    ''' Process all SAFE directories in an orbit directory to:
        a) produce asc node time file
        b) unpack each beam slc frame to an slc file
        c) update state vectors
        '''
    orbitDir, scratch = setupPreProcArgs()
    print(orbitDir)
    # change to the directory
    u.pushd(orbitDir)
    # outputDir: use scratch if provided, otherwise write SLCs into orbit dir
    outputDir = os.path.abspath(scratch) if scratch else os.path.abspath('.')
    os.makedirs(outputDir, exist_ok=True)
    # make the inputSafe file, and return safe info
    safes = getSafes('inputSAFE')
    # make the ascending node time file
    makeAscNodeTime(safes[0]['xml'])
    # run preproc on the all of the SAFE files
    t0 = time.time()
    files1, files2, files3 = runPreProc('inputSAFE', safes[0]['pol'], outputDir)
    _elapsed(t0, 'S1_TOPS_preproc')
    #
    # process each set of files to make their respective tops tab and deramp
    for file1, file2, file3 in zip(files1, files2, files3):
        # update state vectors
        t0 = time.time()
        callUpdateState(file1.replace('.slc', '.slc.par'))
        _elapsed(t0, f'updateState {os.path.basename(file1)}')
        # tab file lives in orbit dir; SLC paths may be absolute (scratch)
        tabfile = 'SLC_tab_' + os.path.basename(file1).split('_')[0]
        makeTopsTab(tabfile, file1, file2, file3)
    u.popd()
    #
    logfile = myLog.logfile()
    myLog.closeLog()
    # move the log to the directory where processing occurred.
    os.rename(logfile, os.path.join(orbitDir, logfile))


if __name__ == '__main__':
    main()
