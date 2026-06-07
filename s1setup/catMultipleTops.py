#!/usr/bin/env python3
import argparse
import getpass
import shutil
import subprocess
import time
import utilities as u
import os
from subprocess import call
from shutil import copyfile, move

#
# Start with a list, break in to pairs, then make list for the pair results,
# if there is an odd number of
# files, add it to next result so it will get merged in the next round
# For example
# 1 2 3 4 5 > mergeFiles=(1,2) (3,4), newFiles=[merged12,merged34,5]
#


def processFileList(files, root, pol, scratchDir):
    #
    n = 1
    newFiles = []
    mergeFiles = []
    while n < len(files):
        newfile = scratchDir+'/'+root+'.'+str(n)
        fout = open(newfile, 'w')
        for i in range(1, 4):
            tmp = newfile+'_iw'+str(i)+'_'+pol
            print(tmp+'.slc ', tmp+'.slc.par ', tmp+'.tops_par', file=fout)
        fout.close()
        newFiles.append(newfile)
        pair = [files[n-1], files[n]]
        mergeFiles.append(pair)
        n += 2
    if len(files) % 2 > 0:
        newFiles.append(files[-1])
        pair
    return newFiles, mergeFiles

# clean file


def cleanPair(pairfile):
    fp1 = open(pairfile)
    for line in fp1:
        pieces = line.split()
        print(pieces)
        # always remove slc
        os.remove(pieces[0])
        # keep par if not in ram disk
        if 'scratch' in pieces[1]:
            os.remove(pieces[1])
            os.remove(pieces[2])
    fp1.close()
#
# Do the actual merge
#


def processMerge(newFiles, mergeFiles):
    procs = []
    for i, pair in enumerate(mergeFiles):
        cmd = f'SLC_cat_S1_TOPS {pair[0]} {pair[1]} {newFiles[i]}'
        print(f'Merging {pair} -> {newFiles[i]}')
        procs.append(subprocess.Popen(cmd, shell=True))

    failed = False
    for i, proc in enumerate(procs):
        if proc.wait() != 0:
            print(f'*** SLC_cat_S1_TOPS failed for pair {mergeFiles[i]}')
            failed = True

    for pair in mergeFiles:
        cleanPair(pair[0])
        cleanPair(pair[1])

    if failed:
        u.myerror('one or more merges failed')


def parseArgs():
    parser = argparse.ArgumentParser(
        description='Concatenate multiple Sentinel-1 TOPS frames into one SLC',
        epilog='Part of the s1setup package.')
    parser.add_argument('--scratch', type=str,
                        default=f'/dev/shm/{getpass.getuser()}/scratch',
                        help='base scratch directory for intermediate SLCs '
                             '[default: /dev/shm/<user>/scratch]')
    return parser.parse_args()


def main():
    args = parseArgs()
    # per-orbit subdir avoids collisions if multiple orbits run concurrently
    scratchDir = os.path.join(args.scratch, os.path.basename(os.getcwd()))
    os.makedirs(scratchDir, exist_ok=True)
    try:
        _main(scratchDir)
    finally:
        shutil.rmtree(scratchDir, ignore_errors=True)


MAX_BURSTS = 128


def readBurstCount(tabFile):
    """Return number_of_bursts from the iw1 tops_par listed in a SLC_tab file."""
    with open(tabFile) as f:
        parts = f.readline().split()
    topsParPath = parts[2]
    with open(topsParPath) as f:
        for line in f:
            if line.startswith('number_of_bursts'):
                return int(line.split()[1])
    u.myerror(f'number_of_bursts not found in {topsParPath}')


def _tty(msg):
    """Write msg directly to the controlling terminal, bypassing stdout capture."""
    try:
        with open('/dev/tty', 'w') as tty:
            print(msg, file=tty, flush=True)
    except OSError:
        pass  # no controlling terminal (batch job)


def _fmt(s):
    """Format seconds as '4m 12s' or '3.1s'."""
    m = int(s // 60)
    return f'{m}m {s % 60:.0f}s' if m else f'{s:.1f}s'


def _main(scratchDir):
    #
    # this is all the SLC_TAB files
    files = u.dols('ls SLC_tab*t*')
    print(files)
    if len(files) < 1:
        u.myerror('files < 1')
    print('Catting '+str(len(files))+' files')
    #
    # check burst limit before doing any work
    totalBursts = sum(readBurstCount(f) for f in files)
    if totalBursts > MAX_BURSTS:
        u.myerror(f'Total burst count ({totalBursts}) across {len(files)} frames '
                  f'exceeds Gamma limit of {MAX_BURSTS} — aborting')
    print(f'Total bursts: {totalBursts}')
    #
    # use default seq=0, unless one is in name as x_seq
    path = os.getcwd()
    dir = path.split()[-1]
    seq = '0'
    if '_' in dir:
        seq = dir.split('_')[-1]
        print('Sequence ', seq)
    #
    # destinations directory
    destDir = path.split('_')[0]+'-'+seq
    #
    # get pol from SLC_tab (works whether SLCs are local or staged to /dev/shm)
    with open(files[0]) as f:
        firstSlc = f.readline().split()[0]
    pol = os.path.basename(firstSlc).split('_')[2].split('.')[0]
    print('Pol ', pol)
    #
    print(files[0])
    # get the basename between SLC_tab and t
    basename = files[0].split('tab_')[-1]
    basename = basename.split('t')[0]
    print(basename)
    #
    done = True
    # force into loop to process results
    nOrig = len(files)
    if nOrig > 1:
        done = False
        count = 0
    elif nOrig == 1:
        # just copy the one file over
        newFiles = files[0].split('_iw1_')
        print(newFiles)
    #
    # Loop to develop a plan
    #
    t0_merge = time.time()
    while not done:
        #
        # Find pairs to combine
        #
        newFiles, mergeFiles = processFileList(files, 'round' + str(count),
                                               pol, scratchDir)
        #
        # run the merge on the sequences
        #
        processMerge(newFiles, mergeFiles)
        files = newFiles
        count += 1
        if len(newFiles) == 1:
            done = True
    t_merge = time.time() - t0_merge
    _tty(f'  [catMultipleTops] merge done ({_fmt(t_merge)}) — moving to {os.path.basename(destDir)}')
    #
    # make output directory
    #
    t0_move = time.time()
    try:
        os.stat(destDir)
    except Exception:
        os.mkdir(destDir)
    #
    # update tab file with final name
    move(newFiles[0], newFiles[0]+'.tmp')
    foldtab = open(newFiles[0]+'.tmp', 'r')
    fnewtab = open(newFiles[0], 'w')
    for line in foldtab:
        print(line)
        print(newFiles[0])
        newline = line.replace(newFiles[0], basename+'-'+seq)
        print(newline)
        print(newline, file=fnewtab, end='')
    foldtab.close()
    fnewtab.close()
    #
    # move results to seq dir
    #
    print('moving results to ', destDir)
    move(newFiles[0], destDir+'/SLC_tab_'+basename+'-'+seq)
    results = u.dols('ls '+newFiles[0]+'*')
    print(results)
    oldbase = newFiles[0]
    for oldfile in results:
        newfile = destDir+'/'+oldfile
        print('=--', oldbase, basename+'-'+seq)
        newfile = newfile.replace(oldbase, basename+'-'+seq)
        move(oldfile, newfile)
    copyfile('ascendingNodeTime', destDir+'/ascendingNodeTime')
    copyfile('absolutegain', destDir+'/absolutegain')
    if nOrig == 1:
        parts = u.dols('ls *par *slc')
        basename = parts[0].split('t')[0]+'-'+seq
        for part in parts:
            file1 = basename+'_iw'+part.split('_iw')[-1]
            call('cp  '+part+' '+destDir+'/'+file1, shell=True,
                 executable='/bin/csh')
        newfile = os.path.dirname(newfile)+'/SLC_tab_'+basename
        print(newfile)
        fpOut = open(newfile, 'w')
        for i in ['_iw1_', '_iw2_', '_iw3_']:
            print(basename+i+pol+'.slc', basename+i+pol+'.slc.par',
                  basename+i+pol+'.tops_par')
            print(basename+i+pol+'.slc', basename+i+pol+'.slc.par',
                  basename+i+pol+'.tops_par', file=fpOut)
        fpOut.close()
    #
    # Clean up any remaining local iw slcs
    #
    for slc in u.dols('ls *iw*.slc'):
        os.remove(slc)
    t_move = time.time() - t0_move
    _tty(f'  [catMultipleTops] move done ({_fmt(t_move)}) — computing burst times')
    #
    # compute the bursttimes
    #
    t0_bursts = time.time()
    u.pushd(destDir)
    subprocess.run(['computeBurstTimes.py'], check=False)
    t_bursts = time.time() - t0_bursts
    timing = (f'catMultipleTops: merge={_fmt(t_merge)}  '
              f'move={_fmt(t_move)}  burstTimes={_fmt(t_bursts)}')
    print(timing)
    _tty(f'  [catMultipleTops] burst times done ({_fmt(t_bursts)})  |  {timing}')


if __name__ == '__main__':
    main()
