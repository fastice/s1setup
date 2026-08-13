#!/usr/bin/env python3
"""
Compute burst times and frame numbers from Gamma .tops_par files.

For each *.tops_par file in the current directory, reads burst_date fields
and writes a .btimes file (burstIndex timeSec frameNumber per line).
Touches missing.N if a gap > 3 s is detected between consecutive bursts.
After processing the first .tops_par, touches frames.FIRST.LAST.

Frame numbers are computed via a linear fit across all bursts in each file
to avoid rounding errors that arise when individual burst times fall near
a half-period boundary.

Run from within the merged orbit output directory (which contains ascendingNodeTime).

Part of the s1setup package.
"""
import glob
import os
import sys
from datetime import datetime

BURST_PERIOD = 2.759  # seconds between Sentinel-1 burst start times


def readAscNodeDatetime(ascFile):
    with open(ascFile) as f:
        line = f.readline().strip()
    fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in line else '%Y-%m-%dT%H:%M:%S'
    return datetime.strptime(line, fmt)


def parseBurstDates(topsParFile):
    """Return burst_date values in burst order."""
    dates = {}
    with open(topsParFile) as f:
        for line in f:
            if line.startswith('burst_date_'):
                key, val = line.split(':', 1)
                idx = int(key.strip().split('_')[-1])
                dates[idx] = val.strip()
    return [dates[k] for k in sorted(dates)]


def burstDateToTimeSec(dateStr, ascNodeDt):
    fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in dateStr else '%Y-%m-%dT%H:%M:%S'
    bt = datetime.strptime(dateStr, fmt)
    return (bt - ascNodeDt).total_seconds()


def linearFit(xs, ys):
    """Return (slope, intercept) of least-squares line through (xs, ys)."""
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def fittedFrameNums(timeSecs):
    """
    Compute frame numbers using a linear fit over all burst times.

    A direct round(t / BURST_PERIOD) can be off by 1 for bursts whose time
    falls close to a half-period boundary.  Fitting a line through all burst
    times (which should be evenly spaced) and rounding from the fitted values
    is robust to that error.
    """
    n = len(timeSecs)
    if n == 1:
        return [round(timeSecs[0] / BURST_PERIOD)]
    indices = list(range(n))
    slope, intercept = linearFit(indices, timeSecs)
    rawNums = [round(t / BURST_PERIOD) for t in timeSecs]
    fitNums = [round((slope * i + intercept) / BURST_PERIOD) for i in indices]
    for i, (r, f) in enumerate(zip(rawNums, fitNums)):
        if r != f:
            print(f'  corrected burst {i+1}: raw frame {r} -> fitted frame {f}')
    return fitNums


def main():
    if not os.path.exists('ascendingNodeTime'):
        sys.exit('*** no ascendingNodeTime file')
    ascNodeDt = readAscNodeDatetime('ascendingNodeTime')

    topsFiles = sorted(glob.glob('*.tops_par'))
    if not topsFiles:
        sys.exit('*** no .tops_par files found')

    firstFrame = lastFrame = None

    for j, topsFile in enumerate(topsFiles):
        burstDates = parseBurstDates(topsFile)
        timeSecs = [burstDateToTimeSec(d, ascNodeDt) for d in burstDates]
        frameNums = fittedFrameNums(timeSecs)
        btimesFile = topsFile.replace('.tops_par', '.btimes')

        with open(btimesFile, 'w') as fp:
            for i, (timeSec, frameNum) in enumerate(zip(timeSecs, frameNums), start=1):
                if j == 0 and i == 1:
                    firstFrame = frameNum

                if i > 1 and (timeSec - timeSecs[i - 2]) > 3.0:
                    open(f'missing.{i}', 'w').close()

                lastFrame = frameNum
                print(i, f'{timeSec:.6f}', frameNum, file=fp)

    if firstFrame is not None:
        open(f'frames.{firstFrame}.{lastFrame}', 'w').close()


if __name__ == '__main__':
    main()
