#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 20 13:54:02 2018

@author: ian
"""
import argparse
import utilities as u
import sarfunc as s
import os
import subprocess
import stat

# strack and coarsereg link libgdal, which drags in libopenblas; its constructor spawns an idle
# thread pool sized to the machine (~60 threads, ~9 GB of reserved address space)
# before main() runs. Nothing in GrIMP uses BLAS, and the pool can only be capped
# from the environment. OPENBLAS_NUM_THREADS, never OMP_NUM_THREADS, which would
# serialise strack's own OpenMP loop.
BLAS_ENV = dict(os.environ, OPENBLAS_NUM_THREADS='1')

ISPpath = '/home/ian/gammaISP/GAMMA_SOFTWARE-20160611/ISP/bin/'


# logger object for creating log
myLog = u.logger(fileRoot='setupStrackReg')


def setupStrackRegArgs():
    ''' Handle command line args'''
    myFlags = {'cwOffsets': True, 'simOffsets': True, 'strackReg': True,
               'strackOffsets': False, 'cullOnly': False, 'setupOnly': False,
               'simOnly': False, 'tiff': True}
    parser = argparse.ArgumentParser(description='\033[1mRun registration'
                                     ' procedure [default] or main speckle'
                                     ' tracker\033[0m',
                                     epilog='Notes: Ususally called as part '
                                            'of setuppairs.py. '
                                            'Part of the s1setup package.')
    parser.add_argument('orbit1', type=int, nargs=1, help='Orbit 1')
    parser.add_argument('orbit2', type=int, nargs=1, help='Orbit 2')
    parser.add_argument('sensor', type=str, nargs=1,
                        help='Sensor (CSK, S1, TSX)')
    parser.add_argument('--frame', type=int, default=1,
                        help='optional frame number')
    parser.add_argument('--region', type=str, default='default',
                        help='region to define velocity and dem maps used in '
                        'processing [greenland, antarctica, taku]')
    parser.add_argument('--info', action='store_true', default=False,
                        help='print processing flags for this run, then exit')
    parser.add_argument('--cwOffsets', action='store_true', default=False,
                        help='only cwOffsets and other flagged steps')
    parser.add_argument('--simOffsets', action='store_true', default=False,
                        help='only simoffsets and other flagged steps')
    parser.add_argument('--strackReg', action='store_true', default=False,
                        help='only run strack for registration offsets '
                        '(everything else setup) and other flagged steps')
    parser.add_argument('--strackOffsets', action='store_true', default=False,
                        help='\033[1mrun main speckle tracking and no '
                        'registration steps \033[0m')
    parser.add_argument('--setupOnly', action='store_true', default=False,
                        help='\033[1msetup but do not run main speckle '
                        'yracking and no registration steps \033[0m')
    parser.add_argument('--cullOnly', action='store_true', default=False,
                        help='\033[1mOnly cull on high res offsets '
                        '(must be called with --strackOffsets) \033[0m')
    parser.add_argument('--noTiff', action='store_true', default=False,
                        help='Write the main speckle-tracked offsets, cull and '
                        'interp outputs as raw binary instead of the default '
                        'GeoTIFF + tiff-backed VRT')
    #
    # Now what happens anyway; kept so the runboth/dofast scripts already
    # written with it, and anything else passing it, keep working
    #
    parser.add_argument('--tiff', action='store_true', default=False,
                        help='(now the default -- accepted for existing '
                        'callers)')
    #
    args = parser.parse_args()
    # log arguments
    myLog.logArgs(args)
    # logArgs dumps the raw namespace, in which the deprecated --tiff no-op
    # always reads 'tiff = False' even when tiff mode is on -- which reads as
    # the opposite of what happens. Log what actually takes effect.
    myLog.logLine(f'EFFECTIVE: tiff = {not args.noTiff}')
    #
    # already registered, so do offset matching
    if args.strackOffsets:
        # turn everything else off, except strackOffsets
        for myKey in myFlags.keys():
            myFlags[myKey] = False
        myFlags['strackOffsets'] = True
        if args.cullOnly:
            myFlags['cullOnly'] = True
        if args.setupOnly:
            myFlags['setupOnly'] = True
    #
    elif args.cullOnly:
        u.myerror('Use --cullOnly flag with strackOffsets flag')
    elif args.cwOffsets or args.simOffsets or args.strackReg:
        if args.strackOffsets:
            u.myerror('Cannot call strackOffsets and any of --coarseReg '
                      '--cwOffsets --simOffsets --strackReg')
        for myKey in myFlags.keys():
            myFlags[myKey] = False
        myFlags['cwOffsets'] = args.cwOffsets
        myFlags['simOffsets'] = args.simOffsets
        myFlags['simOnly'] = args.simOffsets
        myFlags['strackReg'] = args.strackReg
    #
    orbit1, orbit2 = args.orbit1[0], args.orbit2[0]
    sensor = args.sensor[0]
    #
    region = args.region.lower()
    if 'default' == region:
        geo = u.geodatrxa(file=s.sensorDefinitions(sensor).geodatName())
        region = 'antarctica' if geo.isSouth() else 'greenland'
    # really basic check on orbit range
    if orbit1 < 0 or orbit1 > 200000 or orbit2 < 0 or orbit2 > 200000:
        u.myerror(f'Invalid Orbit {args.orbit1} {args.orbit2}')
    myFlags['info'] = args.info
    myFlags['tiff'] = not args.noTiff
    return orbit1, orbit2, args.frame, sensor, region, myFlags


def mkSLC2link(orbit2, frame, slcFiles2, resetPar=False):
    ''' make par_MSP file for this directory, overwrite if
    resetPar=True, which is used with strackReg'''
    slc2link = f'{orbit2}_{frame}.slc'
    slc2file = f'../{orbit2}_{frame}/{orbit2}_{frame}.slc'
    if not os.path.exists(slc2link):
        subprocess.call('ln -s '+slc2file+' .', shell=True,
                        executable='/bin/csh')
    # make a new isp file for the second image. An SLC with a .slc.vrt (e.g.
    # NISAR) has its isp par already and checkSARFiles drops cwpar for it, so
    # link that par here, where makeInsarInfo expects image 2's isppar.
    if 'cwpar' not in slcFiles2:
        if not os.path.lexists(slcFiles2['isppar']):
            os.symlink(f'../{orbit2}_{frame}/{slcFiles2["isppar"]}',
                       slcFiles2['isppar'])
        return
    if not os.path.exists(slcFiles2['cwpar']) or resetPar:
        devnull = open(os.devnull, 'w')
        command = f'par_MSP ../{orbit2}_{frame}/{slcFiles2["sensorpar"]} '
        command += f'../{orbit2}_{frame}/{slcFiles2["cwpar"]} '
        command += f'{slcFiles2["isppar"]}'
        #
        subprocess.call(command, shell=True, executable='/bin/csh',
                        stdout=devnull, stderr=devnull)
        devnull.close()


def coarseReg(slcFiles1, slcFiles2, path1, path2):
    myLog.logEntry('coarseReg')
    myArgs = [f'{path1}/{slcFiles1["geo"]}', f'{path2}/{slcFiles2["geo"]}']
    myOutput = u.callMyProg('coarsereg', myArgs=myArgs, logger=myLog,
                             env=BLAS_ENV)
    dr, da = [int(x) for x in myOutput.split()]
    myLog.logReturn('coarseReg')
    return dr, da


def callCreateOffsets(dr, da, slcFiles1, slcFiles2, path1, orbit1,
                      orbit2, frame):
    myLog.logEntry('callCreateOffsets')
    myArgs = [f'{path1}/{slcFiles1["isppar"]}',
              f'{path1}/{slcFiles2["isppar"]}']
    offFile = f'{path1}/{orbit1}_{frame}.{orbit2}_{frame}.off'
    if os.path.isfile(offFile):  # Start with clean file
        os.remove(offFile)
    myArgs.append(offFile)
    #
    u.callMyProg(ISPpath+'create_offset', myArgs=myArgs,
                 myInputs=[['None '], [dr, da], [''], [128, 128], ['0.1'],
                           [''], ['']], logger=myLog)
    myLog.logReturn('callCreateOffsets')
    return f'{orbit1}_{frame}.{orbit2}_{frame}.off'


def callRunCWOffsets(offFile, dr, da, slcFiles1, slcFiles2, path1, path2,
                     orbit1, orbit2, frame):
    myLog.logEntry('callRunCWOffsets')
    # inputs
    myArgs = [f'{path1}/{slcFiles1["slc"]}', f'{path1}/{slcFiles2["slc"]}']
    myArgs += [f'{path1}/{slcFiles1["isppar"]}',
               f'{path1}/{slcFiles2["isppar"]}']
    myArgs += [offFile]
    # outputs
    myArgs += [offFile.replace('.off', '.offs'),
               offFile.replace('.off', '.snr')]
    u.callMyProg(ISPpath+'offset_pwr', myArgs=myArgs,
                 screen=True, logger=myLog)
    myLog.logReturn('callRunCWOffsets')


def callOffsetFit(offFile):
    myLog.logEntry('callOffsetFit')
    # input
    myArgs = [offFile.replace('.off', '.offs'),
              offFile.replace('.off', '.snr'), offFile]
    # output
    myArgs += [offFile.replace('.off', '.coffs'),
               offFile.replace('.off', '.coffsets'), '0']
    #
    u.callMyProg(ISPpath+'offset_fit', myArgs=myArgs, screen=True,
                 logger=myLog)
    myLog.logReturn('callOffsetFit')


def writeSimOffDat(sensorInfo, orbit1, frame, reg=None):
    ''' write an offset dat file to guide offset simulation'''
    if not reg:
        fp = open(sensorInfo['offsetsBase']+'.dat', 'w')
        scaleDelta = 1
    else:
        fp = open(sensorInfo['offsetsRegBase']+'.dat', 'w')
        scaleDelta = sensorInfo['scaleDelta']
    nr, na = s.sizeOffsets(sensorInfo, scaleDelta, orbit1, frame)
    #
    drScale = sensorInfo['deltaR'] * scaleDelta
    daScale = sensorInfo['deltaA'] * scaleDelta
    print(f'{sensorInfo["rStart"]} {sensorInfo["aStart"]} {nr} {na} '
          f'{drScale} {daScale}', file=fp)
    fp.close()


def simOffsets(slcFiles, regionInfo, sensorInfo, orbit1, frame, tiff=False):
    ''' generate simulated offsets for starting guess'''
    myLog.logEntry('simOffsets')
    # In tiff mode simoffsets.py writes the sim offsets as GeoTIFF + .vrt so the
    # simulated initial-guess (main and registration) matches the rest of the
    # tiff-backed workflow.
    tiffArg = ['--tiff'] if tiff else []
    # for full offsets
    writeSimOffDat(sensorInfo, orbit1, frame)
    myArgs = [f'-dem={regionInfo.dem()}', f'-region={regionInfo.name()}']
    if regionInfo.velMap() is not None:
        myArgs += [f'-velMap={regionInfo.velMap()}']
    myArgs += [f'-offsetsDat={sensorInfo["offsetsBase"]}.dat', '-syncDat']
    myArgs += [f'-geodatFile={slcFiles["geo"]}'] + tiffArg
    # call command
    u.callMyProg('simoffsets.py', myArgs=myArgs, screen=True, logger=myLog)
    # for registration offsets
    writeSimOffDat(sensorInfo, orbit1, frame, reg=True)
    myArgs = [f'-dem={regionInfo.dem()}', f'-region={regionInfo.name()}']
    if regionInfo.velMap() is not None:
        myArgs += [f'-velMap={regionInfo.velMap()}']
    myArgs += [f'-offsetsDat={sensorInfo["offsetsRegBase"]}.dat',
               f'-azOffsets={sensorInfo["offsetsRegBase"]}.da', '-syncDat']
    myArgs += [f'-geodatFile={slcFiles["geo"]}'] + tiffArg
    u.callMyProg('simoffsets.py', myArgs=myArgs, screen=True, logger=myLog)
    myLog.logReturn('simOffsets')


def readOffsetDat(offsetFile):
    ''' read and return parameters from and offset dat file (use .da.dat)'''
    print(offsetFile)
    fpOff = open(offsetFile+'.dat', 'r')
    for line in fpOff:
        if len(line.split()) > 5:
            break
    fpOff.close
    return [int(x) for x in line.split()[0:6]]


def makeStrackRegBase(slcFiles1, slcFiles2, orbit1, orbit2, frame, sensor):
    ''' This is only used if a baseline params is needed for complex matching
    for registeration'''
    #
    # create insar info
    path1, path2 = s.pairPath(orbit1, orbit2, frame, frame)
    sensorInfoInt = s.sensorDefinitions(sensor).inSAR
    insarInfo = s.makeInsarInfo(orbit1, orbit2, path1, path2, frame,
                                sensorInfoInt, slcFiles1, slcFiles2)
    #
    geodat = u.geodatrxa(file=insarInfo['insarGeo'])
    nAzLines = geodat.na
    # create/regen baseline files
    s.makeBaselines(insarInfo)
    s.makeBaseParams(insarInfo, nAzLines, suffix='.reg')


def simOffsetsExist(offsetsRoot):
    ''' True if the simulated offsets for offsetsRoot are on disk in either
    form: the tiff-backed vrt written by simoffsets.py --tiff, or the legacy
    raw .da. Testing only for the raw file silently dropped the simulated
    initial guess in tiff mode and fell through to a register.offsets.<scale>
    that does not exist on the first pass. '''
    return os.path.exists(f'{offsetsRoot}.vrt') or \
        os.path.exists(f'{offsetsRoot}.da')


def initShiftsExist(initShifts):
    ''' True if the initial-shift offsets strack needs are on disk in either
    form. strack resolves initshift through checkForVrt (readBothOffsetsStrack.c),
    so the tiff-backed <root>.vrt is enough -- regOffsetsMerge in tiff mode
    writes only <root>.da.tif/.dr.tif and no raw pair. The legacy paths still
    accept both raws, or the pair of .dat sidecars a pre-vrt directory carries.
    '''
    if os.path.exists(f'{initShifts}.vrt'):
        return True
    if os.path.exists(f'{initShifts}.da') and os.path.exists(f'{initShifts}.dr'):
        return True
    return os.path.exists(f'{initShifts}.da.dat') and \
        os.path.exists(f'{initShifts}.dr.dat')


def maskFileAndVrt(offsetsRoot, tiff=False):
    ''' Locate the tracking mask for offsetsRoot, returning (maskFile, maskVrt).

    siminsar -tiff writes <root>.mask.tif + <root>.mask.vrt with no raw <root>.mask,
    and strack reads the vrt in preference to the raw file (getMask.c), so in tiff
    mode the vrt has to be handed to setupStrackInput -- without it writeMaskFile
    emits only offsetmaskfile/offsetmaskdat and strack falls back to a raw mask that
    is no longer there. Returns (None, None) when no mask exists in either form. '''
    maskFile = f'{offsetsRoot}.mask'
    maskVrt = f'{maskFile}.vrt'
    if tiff and os.path.exists(maskVrt):
        return maskFile, maskVrt
    if os.path.exists(maskFile):
        return maskFile, None
    return None, None


def runStrackRegister(slcFiles1, slcFiles2, sensorInfo, orbit1, orbit2, frame,
                      tiff=False):
    ''' run strack to co-register are low resolution.
    If necessary run multiple times on low res slc'''
    myLog.logEntry('runStrackRegister')
    subSLC = sensorInfo['subSLC']
    maskFile, maskVrt = maskFileAndVrt(sensorInfo['offsetsRegBase'], tiff=tiff)
    first, lastScale = True, 0
    #
    # Main loop to run tracker
    for scaleFactor in subSLC:
        if first and simOffsetsExist(sensorInfo['offsetsRegBase']):
            initShifts = sensorInfo['offsetsRegBase']
        else:
            initShifts = f'register.offsets.{lastScale}'
        print(initShifts)
        # make an early baseline for speckle tracking
        if not sensorInfo['noComplexRegMatch']:
            makeStrackRegBase(slcFiles1, slcFiles2, orbit1, orbit2, frame,
                              sensorInfo['sensor'])
        # setup strack
        strackFile, offsetFile = s.setupStrackInput(slcFiles1, slcFiles2,
                                                    sensorInfo, orbit1, orbit2,
                                                    frame, scaleFactor,
                                                    initShifts, maskFile,
                                                    register=True,
                                                    myLogger=myLog,
                                                    maskVrt=maskVrt)
        #
        first = False
        lastScale = scaleFactor
        myArgs = []
        if sensorInfo['noComplexRegMatch']:
            myArgs.append('-noComplex')
        if sensorInfo['IntegerComplex']:
            myArgs.append('-integerComplex')
        if tiff:
            myArgs.append('-tiff')
        myArgs.append(strackFile)
        # run strack
        u.callMyProg('strack', myArgs=myArgs, screen=True, logger=myLog,
                     env=BLAS_ENV)
        # run cull
        cullFile = s.makeCullFile(sensorInfo, offsetFile, orbit1, orbit2,
                                  frame, scaleFactor, register=True, tiff=tiff)
        u.callMyProg('csh', myArgs=[cullFile], screen=True, logger=myLog)
    myLog.logEntry('runStrackRegister')


# Moved to sarfunc.makeCullFile beside makeCullFile so it can be reused without
# importing this module (which opens a log file at import). Re-exported here so
# the call site below and any external caller are unchanged.
makeCleanOff = s.makeCleanOff


def runCullHiRes(sensorInfo, orbit1, orbit2, frame, tiff=False):
    ''' run the culler
    follows template from setupStrackInput (they need to be the same)'''
    myLog.logEntry('runCullHiRes')
    scaleFactor = 1
    offsetFile = f'{orbit1:d}_{frame:d}.{orbit2:d}_{frame:d}.offsets'
    cullFile = s.makeCullFile(sensorInfo, offsetFile, orbit1, orbit2,
                              frame, scaleFactor, tiff=tiff)
    # make script called by cull file
    makeCleanOff(offsetFile, sensorInfo['sensor'], tiff=tiff)
    # runcull saved for future run, but run it now
    u.callMyProg('csh', myArgs=[cullFile], screen=True, logger=myLog)
    myLog.logReturn('runCullHiRes')


def runStrack(slcFiles1, slcFiles2, sensorInfo, orbit1, orbit2, frame,
              setupOnly=False, tiff=False):
    ''' Run the main speckle tracking routine for high-res offsets'''
    myLog.logEntry('runStrack')
    initShifts = sensorInfo['registerBase']
    # mask file setup
    maskFile, maskVrt = maskFileAndVrt(sensorInfo['offsetsBase'], tiff=tiff)
    # initial shifts file
    if not initShiftsExist(initShifts):
        u.myerror(f'Cannot start strack, missing {initShifts} offsets '
                  f'(no {initShifts}.vrt, no raw .da/.dr, no .dat sidecars)')
    # create strack input file
    scaleFactor = 1
    # added verify to avoid some steps when doing cull only
    strackFile, offsetFile = s.setupStrackInput(slcFiles1, slcFiles2,
                                                sensorInfo, orbit1, orbit2,
                                                frame, scaleFactor, initShifts,
                                                maskFile, myLogger=myLog,
                                                setupOnly=setupOnly,
                                                maskVrt=maskVrt)
    # setup command
    myArgs = []
    if sensorInfo['IntegerComplex']:
        myArgs.append('-integerComplex')
    if sensorInfo['noComplexMatch']:
        myArgs.append('-noComplex')
    if not sensorInfo['applyHanningToComplex']:
        myArgs.append('-noHanning')
    if tiff:
        myArgs.append('-tiff')
    #
    myArgs.append(strackFile)
    # call matcher
    if not setupOnly:
        u.callMyProg('strack', myArgs=myArgs, screen=True, logger=myLog,
                     env=BLAS_ENV)
    # check file size for failed strack or not prev run strack (cullOnly case).
    # In tiff mode strack writes a tiff-backed VRT (no raw .da) - validate it.
    if tiff:
        from osgeo import gdal
        vrt = offsetFile + '.vrt'
        ds = gdal.Open(vrt) if os.path.exists(vrt) else None
        if ds is None or ds.RasterXSize < 1:
            u.myerror(f'runStrack: missing/invalid tiff result {vrt}',
                      myLogger=myLog)
        ds = None
    else:
        testOff = u.offsets(fileRoot=offsetFile+'.da', datFile=offsetFile+'.dat')
        testOff.checkOffsetFiles()
    myLog.logReturn('runStrack')


def printInfo(orbit1, orbit2, frame, slcFiles1, slcFiles2, sensorInfo,
              myFlags):
    ''' This will print some information about what parameters would be used
    if the program were run, but instead of running it exits'''
    #
    print(f'\n\033[1mOrbit1, Orbit2, Frame:\033[35;1m '
          f'{orbit1} {orbit2} {frame}\033[0m')
    if myFlags['strackOffsets']:
        print('\033[1mTracking Mode:\033[0m \033[35;1m '
              'Full Speckle Tracking Mode\033[0m')
    else:
        print('\033[1mTracking Mode:\033[35;1m  Registration Mode \033[0m')
    #
    SLCtype = 'Float32Complex'
    if sensorInfo['IntegerComplex']:
        SLCtype = 'Integer16Complex'
    print(f'\033[1mSLC 1, SLC 2:\033[35;1m {slcFiles1["slc"]}'
          f'{slcFiles2["slc"]} \033[0m\033[1m which are: '
          f'\033[35;1m {SLCtype}\033[0m')
    if myFlags['strackOffsets']:
        if sensorInfo['noComplexMatch']:
            print('\033[1m\nOffsets will be determined using amplitude-only '
                  'matching\033[0m')
        else:
            print('\033[1m\nOffsets will be determined with \033[35;1m '
                  'complex/amplitude matching\033[0m ', end='')
            if sensorInfo['useInt']:
                print('\033[1musing \033[35;1m '
                      f'{s.ifgFilename(orbit1, orbit2, frame, sensorInfo)}.'
                      '\033[0m')
            else:
                print('\033[1musing \033[35;1m with only a baseline ramp '
                      'removed.\033[0m ')
        if maskFileAndVrt(sensorInfo['offsetsBase'],
                          tiff=myFlags['tiff'])[0] is not None:
            print(f'\033[1m\nMatcher will use the mask information in: '
                  f'\033[35;1m {sensorInfo["offsetsBase"]}.mask.\033[0m')
    else:
        if sensorInfo['noComplexRegMatch']:
            print('\033[1m\nRegostration offsets will be determined using '
                  '\033[35;1m amplitude-only matching\033[0m')
        else:
            print('\033[1m\nRegistration offsets will be determined with'
                  ' \033[35;1m complex/amplitude matching with only a '
                  'baseline ramp removed\033[0m ')
        print(f'\033[1m\nSetup offsets with cwOffsets:\033[35;1m '
              f'{myFlags["cwOffsets"]} \033[0m')
        print(f'\033[1mSetup offsets simOffsets: \033[35;1m '
              f'{myFlags["simOffsets"]} \033[0m')
        print(f'\033[1mSetup offsets strackReg: \033[35;1m '
              f'{myFlags["strackReg"]} \033[0m')
        if maskFileAndVrt(sensorInfo['offsetsRegBase'],
                          tiff=myFlags['tiff'])[0] is not None:
            print(f'\033[1m\nMatcher will use the mask info in\033[35;1m'
                  f'{sensorInfo["offsetsRegBase"]+".mask"}.\033[0m')
    print('Exiting - rerun with no --info flag\n')
    myLog.logLine('Exiting - After run with info flag - remove --'
                  'info to run program')
    myLog.closeLog()
    exit()


def main():
    ''' Runs strack for both registration and the main offset stracking.
    Can also run individual parts
    --cwOffsets - just create a gamma offset file
    --simOffsets - just run the offset creation (offsets.X)
    --strackReg - by default it will run the registration, but this will
        skip other setup steps
    --strackOffsets - run the offset tracker and cull results
    --cullOnly - only cull the data
    Will select the deflt region [greenland, antarctica] based on local geodat
    '''
    #
    # get args
    orbit1, orbit2, frame, sensor, region, myFlags = setupStrackRegArgs()
    # get the region info
    regionInfo = s.defaultRegionDefs(region)
    # get the sensor info
    sarDef = s.sensorDefinitions(sensor)
    sensorInfo = sarDef.SAR
    #
    myLog.logLine('calling pairPath')
    path1, path2 = s.pairPath(orbit1, orbit2, frame, frame)
    # Force reset par if strack Reg to start with a clean file (fixes
    # doppler bug in Oct 2018)
    # verifyExists false if cull only to avoid checking for unneeded files
    verifyExists = not (myFlags['cullOnly'] or myFlags['setupOnly'] or
                        myFlags['simOnly'])
    slcFiles1 = s.checkSARFiles(path1, orbit1, frame, sensorInfo,
                                myLogger=myLog, resetPar=myFlags['strackReg'],
                                verifyExists=verifyExists)
    slcFiles2 = s.checkSARFiles(path2, orbit2, frame, sensorInfo,
                                myLogger=myLog, verifyExists=verifyExists)
    #
    if myFlags['info']:
        printInfo(orbit1, orbit2, frame, slcFiles1, slcFiles2, sensorInfo,
                  myFlags)
    # set up link as needed
    if verifyExists:
        myLog.logLine('calling mkSLC2link for image 2', logTime=True)
        mkSLC2link(orbit2, frame, slcFiles2, resetPar=myFlags['strackReg'])
    # run modules
    if myFlags['cwOffsets']:
        # do coarse registration
        dr, da = coarseReg(slcFiles1, slcFiles2, path1, path2)
        # create intitial offsets file for cw software
        offFile = callCreateOffsets(dr, da, slcFiles1, slcFiles2, path1,
                                    orbit1, orbit2, frame)
    # run cw registration routine
        callRunCWOffsets(offFile, dr, da, slcFiles1, slcFiles2, path1, path2,
                         orbit1, orbit2, frame)
    # fit offsets
        callOffsetFit(offFile)
    #
    # next step is to simoffsets be explicity with maps then
    # setup for strackReg -- see sentine

    if myFlags['simOffsets']:
        simOffsets(slcFiles1, regionInfo, sensorInfo, orbit1, frame,
                   tiff=myFlags['tiff'])

    # compute fine res reg offsets
    if myFlags['strackReg']:
        runStrackRegister(slcFiles1, slcFiles2, sensorInfo,
                          orbit1, orbit2, frame, tiff=myFlags['tiff'])
    #
    # run the offset tracker
    if myFlags['strackOffsets'] and not myFlags['cullOnly']:
        runStrack(slcFiles1, slcFiles2, sensorInfo, orbit1, orbit2, frame,
                  setupOnly=myFlags['setupOnly'], tiff=myFlags['tiff'])
    # cull if strack, but not setupOnly, or if cullOnly
    if((myFlags['strackOffsets'] and not myFlags['setupOnly']) or
       myFlags['cullOnly']):
        runCullHiRes(sensorInfo, orbit1, orbit2, frame, tiff=myFlags['tiff'])
    #
    myLog.logLine('Program complete - closing log', logTime=True)
    myLog.closeLog()


if __name__ == '__main__':
    main()
