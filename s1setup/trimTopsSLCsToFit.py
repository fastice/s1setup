#!/usr/bin/env python3
"""
Trim Sentinel-1 TOPS SLC frames to fit within a specified burst/frame range.

Reads ../frameRange (minFrame maxFrame on first line) and ascendingNodeTime,
then for each iw1 frame in the current orbit directory trims leading/trailing
bursts so the coverage fits exactly within [minFrame, maxFrame].

Calls Gamma SLC_copy_S1_TOPS. Run from within the orbit source directory.

Part of the s1setup package.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

BURST_PERIOD = 2.759  # seconds between burst start times


def readFrameRange(trackDir):
    with open(os.path.join(trackDir, 'frameRange')) as f:
        parts = f.readline().split()
    return int(parts[0]), int(parts[1])


def readAscNodeSec(ascFile):
    """Parse ascendingNodeTime file, return seconds-of-day."""
    with open(ascFile) as f:
        line = f.readline().strip()
    fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in line else '%Y-%m-%dT%H:%M:%S'
    t = datetime.strptime(line, fmt)
    return t.hour * 3600.0 + t.minute * 60.0 + t.second + t.microsecond / 1e6


def parseTopsPar(topsParFile):
    """Return (nBurst, firstBurstTimeSec, lastBurstTimeSec)."""
    n = None
    times = {}
    with open(topsParFile) as f:
        for line in f:
            line = line.strip()
            if line.startswith('number_of_bursts'):
                n = int(line.split()[1])
            m = re.match(r'burst_start_time_(\d+):\s+([\d.]+)', line)
            if m:
                times[int(m.group(1))] = float(m.group(2))
    return n, times[1], times[n]


def burstTimeToFrame(burstSec, ascSec):
    return int((burstSec - ascSec) / BURST_PERIOD + 0.5)


def writeBurstTab(firstBurst, lastBurst, path='burstTab'):
    with open(path, 'w') as f:
        for _ in range(3):
            print(firstBurst, lastBurst, file=f)


def readTabTokens(tabFile):
    """Return all whitespace-separated tokens from a SLC tab file (9 expected)."""
    tokens = []
    with open(tabFile) as f:
        for line in f:
            tokens.extend(line.split())
    return tokens


def trimFrame(parFile, topsParFile, ascSec, minFrame, maxFrame):
    nBurst, fTime, eTime = parseTopsPar(topsParFile)
    frame1 = burstTimeToFrame(fTime, ascSec)
    frame2 = burstTimeToFrame(eTime, ascSec)
    print(f'  {os.path.basename(parFile)}: frames {frame1}-{frame2}  '
          f'(range {minFrame}-{maxFrame}, nBurst={nBurst})')

    nNew = 0

    # Early trim: frame starts before minFrame but covers it
    if frame1 < minFrame <= frame2:
        firstBurst = minFrame - frame1 + 1
        lastBurst = nBurst
        nNew = lastBurst - firstBurst + 1
        writeBurstTab(firstBurst, lastBurst)
        shutil.copy('burstTab', 'burstTabE')
        print(f'    TrimEarly: keeping bursts {firstBurst}-{lastBurst}')

    # Late trim: frame starts before maxFrame but extends past it
    if frame1 < maxFrame < frame2:
        firstBurst = 1
        nNew = maxFrame - frame1 + 1
        writeBurstTab(firstBurst, nNew)
        shutil.copy('burstTab', 'burstTabL')
        print(f'    TrimLate: keeping bursts 1-{nNew}')

    if nNew == 0:
        return

    tabName = 'SLC_tab_' + os.path.basename(parFile).split('_iw')[0]
    if not os.path.exists(tabName):
        sys.exit(f'*** cannot find SLC tab file: {tabName}')

    # Build SLCtabnew by renaming iw -> iwnew in all file paths
    with open(tabName) as f:
        content = f.read()
    with open('SLCtabnew', 'w') as f:
        f.write(content.replace('iw', 'iwnew'))

    result = subprocess.run(['SLC_copy_S1_TOPS', tabName, 'SLCtabnew', 'burstTab'])
    if result.returncode != 0:
        sys.exit('*** SLC_copy_S1_TOPS failed')

    # Replace originals with trimmed files (originals may be in scratch)
    for orig, new in zip(readTabTokens(tabName), readTabTokens('SLCtabnew')):
        os.remove(orig)
        shutil.move(new, orig)


def main():
    trackDir = os.path.dirname(os.getcwd())

    if not os.path.exists(os.path.join(trackDir, 'frameRange')):
        sys.exit('*** no frameRange file in track directory')
    minFrame, maxFrame = readFrameRange(trackDir)

    ascFile = 'ascendingNodeTime'
    if not os.path.exists(ascFile):
        sys.exit('*** no ascendingNodeTime file')
    ascSec = readAscNodeSec(ascFile)
    print(f'ascendingNode {ascSec:.6f}  frameRange {minFrame}-{maxFrame}')

    # Read iw1 par/tops_par paths from SLC_tab files — works whether files are
    # local or staged to /dev/shm (absolute paths in tab files)
    tabFiles = sorted(glob.glob('SLC_tab_*'))
    safes    = sorted(glob.glob('*.SAFE'))

    if not tabFiles:
        sys.exit('*** no SLC_tab_* files found')
    if len(tabFiles) != len(safes):
        sys.exit(f'*** mismatch: {len(tabFiles)} SLC_tabs, {len(safes)} SAFEs')
    print(f'{len(safes)} frames to check')

    for tabFile in tabFiles:
        with open(tabFile) as f:
            parts = f.readline().split()  # first line = iw1
        trimFrame(parts[1], parts[2], ascSec, minFrame, maxFrame)


if __name__ == '__main__':
    main()
