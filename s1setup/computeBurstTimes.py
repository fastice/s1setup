#!/usr/bin/env python3
"""
Compute burst times and frame numbers from Gamma .tops_par files.

For each *.tops_par file in the current directory, reads burst_date fields
and writes a .btimes file (burstIndex timeSec frameNumber per line).
Touches missing.N if a gap > 3 s is detected between consecutive bursts.
Writes frames.FIRST.LAST from the first .tops_par.

Frame numbers are counted from one anchor, stepping by the number of burst
periods between consecutive bursts, rather than by rounding each burst on its
own -- see burstNumbers().  Rerunning this in an assembled directory rewrites
the numbering from the .tops_par files, which is how a slipped .btimes is
repaired; --check reports what would change without writing.

Run from within the merged orbit output directory (which contains ascendingNodeTime).

Part of the s1setup package.
"""
import argparse
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


def burstSteps(timeSecs, period):
    """
    Burst numbers each burst is past the first, from the times alone.

    Normally one per burst; a longer gap steps by however many periods it
    spans, so a dropped burst leaves a hole in the numbering instead of being
    absorbed into a contiguous count.
    """
    steps, offset = [0], 0
    for previous, this in zip(timeSecs, timeSecs[1:]):
        offset += max(1, round((this - previous) / period))
        steps.append(offset)
    return steps


def burstNumbers(timeSecs):
    """
    Number the bursts of one swath: (numbers, notes).

    Counted from a single anchor and stepped by the periods between bursts,
    rather than rounding each burst's own time.  Rounding each burst
    separately breaks whenever a swath's times sit near a half-period
    boundary: the fitted ratios then read 640.5016, 641.5013, ... and drift
    across .5 partway down the file, so two bursts round to the same number
    (a duplicate) or one is skipped.  Counting cannot do that.

    The anchor is the fitted time at burst offset 0 rounded to a period, which
    is what the old rule gave for the first burst, so a swath that was already
    numbered correctly is numbered identically.  The fit is against the burst
    offsets rather than the row index, so a gap does not tilt it.

    notes describes anything the times say that plain counting would not: the
    caller prints them, since they are corrections worth seeing.
    """
    n = len(timeSecs)
    if n == 0:
        return [], []
    if n == 1:
        return [round(timeSecs[0] / BURST_PERIOD)], []
    #
    # Nominal period first, only to get the offsets; then the period is fitted
    # against those offsets and the offsets recomputed from it
    #
    steps = burstSteps(timeSecs, BURST_PERIOD)
    slope, intercept = linearFit(steps, timeSecs)
    period = slope if 0.9 * BURST_PERIOD < slope < 1.1 * BURST_PERIOD \
        else BURST_PERIOD
    steps = burstSteps(timeSecs, period)
    slope, intercept = linearFit(steps, timeSecs)
    if not 0.9 * BURST_PERIOD < slope < 1.1 * BURST_PERIOD:
        slope, intercept = period, timeSecs[0]
    first = round(intercept / BURST_PERIOD)
    numbers = [first + step for step in steps]
    notes = []
    for i in range(1, n):
        gap = steps[i] - steps[i - 1]
        if gap > 1:
            notes.append(f'  bursts {i}->{i+1} are {timeSecs[i]-timeSecs[i-1]:.3f} '
                         f's apart = {gap} burst periods, so {gap-1} burst'
                         f'{"" if gap == 2 else "s"} went missing; numbering '
                         f'steps {numbers[i-1]} -> {numbers[i]}')
    return numbers, notes


def describeCorrection(old, new):
    """
    What changed between the numbering on disk and the recomputed one, or ''.

    Named after the failure it reports: a burst number counted twice, or one
    stepped over, both of which come from rounding each burst separately.
    """
    if old is None or old == new:
        return ''
    if len(old) != len(new):
        return (f'  burst count changed, {len(old)} -> {len(new)}: the '
                'tops_par no longer matches the btimes')
    i = next(k for k, (a, b) in enumerate(zip(old, new)) if a != b)
    if i > 0 and old[i] == old[i - 1]:
        was = f'burst number {old[i]} was counted twice'
    elif i > 0 and old[i] - old[i - 1] > 1:
        was = f'burst number {old[i-1]+1} was stepped over'
    else:
        was = f'burst {i+1} was numbered {old[i]}'
    return (f'  corrected from burst {i+1}: {was}, so {old[i]} -> {new[i]} and '
            f'{"+" if new[-1] > old[-1] else ""}{new[-1]-old[-1]} to the end '
            f'(last burst {old[-1]} -> {new[-1]})')


def readBtimes(btimesFile):
    """The burst numbers a .btimes already holds, or None if there is none"""
    if not os.path.exists(btimesFile):
        return None
    numbers = []
    with open(btimesFile, 'r') as fin:
        for line in fin:
            fields = line.split()
            if len(fields) == 3:
                numbers.append(int(fields[2]))
    return numbers


def swathGroup(name):
    """
    The product a swath file belongs to: its name with the iw<n> taken out.

    A directory can hold more than one product -- the assembled acquisition
    (20170107-0_iw1_hh) alongside a piece already cut from it
    (20170107_618_iw1_hh) -- and only swaths of the same product describe the
    same bursts.
    """
    parts = name.split('_')
    return '_'.join(p for p in parts if not (len(p) == 3 and p.startswith('iw')
                                             and p[2].isdigit()))


def checkSwaths(bySwath):
    """
    Warn where the swaths of one acquisition do not describe the same bursts.

    They are numbered independently, each from its own times, and that is the
    convention: the three swaths of a burst are acquired about a third of a
    period apart, so their numbers legitimately differ by one depending on
    where in the period they fall.  What they must agree on is the shape --
    how many bursts, and where the gaps are -- so that is what is checked.
    Reported only; nothing is renumbered on the strength of it, since forcing
    the swaths onto one grid would renumber most of the archive.
    """
    warnings = []
    groups = {}
    for name in sorted(bySwath):
        groups.setdefault(swathGroup(name), []).append(name)
    for names in groups.values():
        warnings += compareSwaths(names, bySwath)
    return warnings


def compareSwaths(names, bySwath):
    """checkSwaths() for the swaths of one product"""
    warnings = []
    if len(names) < 2:
        return warnings
    reference = names[0]
    ours = bySwath[reference]
    if not ours:
        return warnings

    def gapsOf(numbers):
        return [(a, b) for a, b in zip(numbers, numbers[1:]) if b - a > 1]

    for name in names[1:]:
        theirs = bySwath[name]
        if not theirs:
            warnings.append(f'  {name} has no bursts at all')
            continue
        #
        # One burst either end is ordinary: a swath can start or stop a burst
        # short of its neighbour.  More than that is not.
        #
        if abs(theirs[0] - ours[0]) > 1:
            warnings.append(f'  {name} starts at burst {theirs[0]} against '
                            f'{ours[0]} in {reference}')
        if abs(theirs[-1] - ours[-1]) > 1:
            warnings.append(f'  {name} ends at burst {theirs[-1]} against '
                            f'{ours[-1]} in {reference}')
        if len(gapsOf(theirs)) != len(gapsOf(ours)):
            warnings.append(f'  {name} has {len(gapsOf(theirs))} gap(s) '
                            f'against {len(gapsOf(ours))} in {reference}')
    return warnings


def repairBurstTimes(directory='.', check=False):
    """
    (Re)compute the .btimes of an assembled directory from its .tops_par files.

    This is both the normal post-assembly step and the repair for a directory
    whose numbering slipped: the .tops_par files and ascendingNodeTime are all
    it reads, and both survive the SLCs being cleaned away, so a directory can
    be renumbered without re-assembling anything.

    check=True computes and compares but writes nothing.

    Returns a dict: 'corrections' [(btimes, message)] for files whose numbering
    changed, 'notes' [(btimes, message)] for what the times themselves say
    (missing bursts), 'warnings' from checkSwaths, 'firstChanged' when the
    first burst number moved -- the pairing key, so a caller may want to stop
    -- 'holes' for files whose numbering is not contiguous even after
    recomputing (data really is missing), and 'sentinel'.
    """
    ascFile = os.path.join(directory, 'ascendingNodeTime')
    if not os.path.exists(ascFile):
        raise FileNotFoundError(f'no ascendingNodeTime in {directory}')
    ascNodeDt = readAscNodeDatetime(ascFile)
    topsFiles = sorted(glob.glob(os.path.join(directory, '*.tops_par')))
    if not topsFiles:
        raise FileNotFoundError(f'no .tops_par files in {directory}')

    result = {'corrections': [], 'notes': [], 'warnings': [], 'holes': [],
              'firstChanged': False, 'sentinel': None, 'changed': False}
    bySwath = {}
    firstFrame = lastFrame = None

    for j, topsFile in enumerate(topsFiles):
        timeSecs = [burstDateToTimeSec(d, ascNodeDt)
                    for d in parseBurstDates(topsFile)]
        numbers, notes = burstNumbers(timeSecs)
        btimesFile = topsFile.replace('.tops_par', '.btimes')
        name = os.path.basename(btimesFile)
        bySwath[name] = numbers
        existing = readBtimes(btimesFile)
        correction = describeCorrection(existing, numbers)
        if correction:
            result['corrections'].append((name, correction))
            result['changed'] = True
            if existing and numbers and existing[0] != numbers[0]:
                result['firstChanged'] = True
        for note in notes:
            result['notes'].append((name, note))
        if any(b - a != 1 for a, b in zip(numbers, numbers[1:])):
            result['holes'].append(name)
        if j == 0 and numbers:
            #
            # Both ends from the same swath.  This used to take the first
            # burst from the first tops_par and the last from whichever was
            # processed last, so the sentinel described no single swath.
            #
            firstFrame, lastFrame = numbers[0], numbers[-1]
        if check:
            continue
        with open(btimesFile, 'w') as fp:
            for i, (timeSec, frameNum) in enumerate(zip(timeSecs, numbers),
                                                    start=1):
                if i > 1 and (timeSec - timeSecs[i - 2]) > 3.0:
                    open(os.path.join(directory, f'missing.{i}'), 'w').close()
                print(i, f'{timeSec:.6f}', frameNum, file=fp)

    result['warnings'] = checkSwaths(bySwath)
    if firstFrame is not None:
        result['sentinel'] = f'frames.{firstFrame}.{lastFrame}'
        if not check:
            #
            # Cleared first: this only ever created, so a renumbered directory
            # would be left holding both the old sentinel and the new one
            #
            for stale in glob.glob(os.path.join(directory, 'frames.*.*')):
                if os.path.basename(stale) != result['sentinel']:
                    os.remove(stale)
            open(os.path.join(directory, result['sentinel']), 'w').close()
    return result


def reportRepair(result, directory='.', check=False):
    """Print what repairBurstTimes() found, in the order it matters"""
    for name, note in result['notes']:
        print(f'{name}:')
        print(note)
    for name, correction in result['corrections']:
        print(f'{name}:')
        print(correction)
    for warning in result['warnings']:
        print(f'*** swaths disagree in {directory}:')
        print(warning)
    if check and result['changed']:
        print(f'*** {directory}: the burst numbering on disk is wrong -- '
              'rerun computeBurstTimes.py there to correct it')


def main():
    parser = argparse.ArgumentParser(
        description='Compute burst times and frame numbers from the '
                    '.tops_par files of an assembled directory',
        epilog='Part of the s1setup package.')
    parser.add_argument('--check', '-check', action='store_true',
                        help='report what the numbering would become, without '
                             'writing anything')
    args = parser.parse_args()
    try:
        result = repairBurstTimes('.', check=args.check)
    except FileNotFoundError as exception:
        sys.exit(f'*** {exception}')
    reportRepair(result, '.', check=args.check)


if __name__ == '__main__':
    main()
