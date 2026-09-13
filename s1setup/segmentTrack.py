#!/usr/bin/env python3
"""
Propose orbitframes entries for a Sentinel-1 track directory.

setupSeveralTopsImages.py re-frames an assembled track into per-piece
<orbit>_<firstBurst> directories, using the default segmentation in `frames`
and the per-orbit overrides in `orbitframes`.  `orbitframes` has always been
hand maintained.  This script derives the same entries from the burst coverage
that catMultipleTops/computeBurstTimes already record in each <orbit>-<seq>
directory.

The key constraint is that the output directory is named <orbit>_<firstBurst>
and pairing (mosaicworkflow.grepdate.findPair) matches on that name exactly.
So firstBurst is the pairing key and nBursts is free: a piece may be shortened
freely, but a shifted start creates a new key and needs matching pieces in the
neighbouring acquisitions before a pair can form.

What is being maximised is the pairs -- the nearest-neighbour 6-day pair and
the alternating 12-day pair, which may be framed differently and may overlap.
So the framing of a new acquisition is carried forward from the last built
acquisition whose burst coverage matches it (the satellites interleave and do
not cover the same bursts, hence "matches" rather than "last"), with the
lengths reconciled across the built acquisitions in that window.  Nothing is
invented outside the ground those acquisitions were cut over, no piece runs
longer than maxBursts, and pieces cut here are broken to sit inside the
velocityStats boxes the autocleaning works in.

An acquisition that has been built already is never rewritten: its lines
describe directories that exist and pair.  It is still framed, because that is
what the comparison is read against, but the only thing written for it is an
extra piece appended after its own lines, where a key introduced further along
needs a partner.  Scored over the whole record of the 30 Greenland tracks the
rules reproduce 92.0% of built acquisitions exactly, forming 3279 pairs
against the 3011 the built segmentation forms.

Nothing is written unless a write mode is asked for.  Without one, every mode
prints to the terminal as before.  The write modes are:

    -refreshLinks   link the assembled <orbit>-<seq> directories that have no
                    link here yet, so their acquisitions become visible
    -commit         write the entries just proposed into orbitframes, then
                    build the setup scripts for them
    -undo           put back the orbitframes from before the last -commit,
                    saying how far back that is and asking first
    -undoNoPrompt   the same, without the question
    -undoLinks      remove the links made by the last -refreshLinks

so the routine update is `-refreshLinks`, read the proposal, then `-commit`.
-commit merges by orbit: the lines of the orbits it proposes for are replaced
and every other line, comments included, is left as it is.  Both undo modes
work off timestamped copies kept in orbitframesBackup/, and stepping back
twice takes two runs.

Every mode but -dump then runs setupSeveralTopsImages over the framing just
proposed -- merged into orbitframes in memory, so it is checked whether or not
it is being committed.  That pass writes nothing and collects every failure
rather than stopping at the first, which is what setupSeveralTopsImages does on
its own.  If it reports anything, nothing is written at all: not orbitframes,
not the setup scripts.  The framing needs fixing first, and each failure is
said to be either the proposal's or something the run cannot rewrite.  That
pass covers its own window of the record, so the derivation reaches back over
any acquisition in it that has never been framed, however far past -lookBack
that is: an acquisition nothing has proposed for is checked against the default
`frames` entries, which its coverage usually cannot carry, and one of those
would block the commit.  A clean pass under
-commit writes the entries and then the setup_<orbit>_<firstBurst> scripts and
their runSetup driver, clearing out the scripts of pieces never built and the
run files of earlier runs on the way, so running twice leaves one current set.
-noSetup stops after orbitframes and writes no scripts.

A burst missing from an assembled SLC is reported, drawn against the frames
being cut and put to the user rather than ending the run: the numbering steps
over the hole, the burst positions are read back out of the burst times, and a
frame either side of it is cut exactly as it always was.  Only a frame cut over
the hole is refused.  -acceptGaps answers yes in advance; a run with no
terminal to ask on stops.

Run from the track directory (the one holding `frames`, `orbitframes` and the
<orbit>-<seq>directories).

Part of the s1setup package.
"""
import argparse
import glob
import os
import sys
import yaml
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from s1setup.computeBurstTimes import repairBurstTimes
from s1setup.setupSeveralTopsImages import (getFramesAndOrbits,
                                            determineFraming,
                                            setupSeveralImages)

#
# Directory patterns used by setupSeveralTopsImages to find assembled SLCs
#
SLCPATTERNS = ['????-?', '?????-?']
#
# Where the backups for -undo/-undoLinks are kept, in the track directory
#
BACKUPDIR = 'orbitframesBackup'
#
# An assembled directory older than this is not linked by -refreshLinks unless
# -linkAll is given.  Relative to now rather than an absolute cutoff, so
# processing a decade of old data for a new region still links normally.
#
DEFAULTLINKAGEDAYS = 365
#
# How much of an acquisition its neighbours' framing has to cut before it is
# accepted.  Below this the framing is taken from an older acquisition that
# frames more, which is what rescues an acquisition whose neighbours are a
# fraction of its length (track-163).  Measured against the acquisition's own
# data, so a track whose framing is merely changing does not trip it.
#
REACHBACKFRACTION = 0.5
#
# Longest piece worth cutting.  A run longer than this is broken into equal
# pieces sharing a burst at each seam, the same one-burst overlap the tracks
# already carry between neighbouring pieces.
#
DEFAULTMAXBURSTS = 76
#
# How far back of the record the setup step looks, from the last acquisition
# that was built.  A track carries a decade of acquisitions and the old ones
# were framed against SLCs that have been re-downloaded since, so checking them
# reports problems that are neither new nor worth fixing.  -firstdate overrides
# it, which is how an older stretch is deliberately taken in.  It also sets how
# far back the proposal has to reach (frameFrom): whatever is checked must have
# been framed, or the check reads the `frames` defaults for it.
#
SETUPWINDOWDAYS = 90
#
# For the few messages that say a choice was made for the user, which must not
# be lost in a long report
#
BOLD = '\033[1;33m'
ERROR = '\033[1;31m'
PLAIN = '\033[0m'


def stamp():
    '''Timestamp for a backup name.  Sorts lexicographically, so the newest
    backup is the last one by name.'''
    return datetime.now().strftime('%Y%m%dT%H%M%S')


def newestBackup(pattern):
    '''Newest BACKUPDIR/<pattern>, or None if there is no backup yet'''
    if not os.path.isdir(BACKUPDIR):
        return None
    found = sorted(glob.glob(f'{BACKUPDIR}/{pattern}'))
    if not found:
        return None
    return found[-1]


def backupTime(backup):
    '''
    When a backup was made, from the stamp in its name.

    Falls back to the file's own mtime for a name that does not parse, so a
    backup renamed or copied by hand can still be dated.
    '''
    try:
        return datetime.strptime(backup.split('.')[-1], '%Y%m%dT%H%M%S')
    except ValueError:
        return datetime.fromtimestamp(os.path.getmtime(backup))


def describeAge(when):
    '''
    How long ago `when` was, in the two largest units that apply.

    Read before an undo is agreed to, so it says which commit is coming back:
    one from a few minutes ago is this session's, one from last week is
    somebody else's work being thrown away.
    '''
    delta = datetime.now() - when
    if delta < timedelta(0):
        return 'in the future'
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return 'less than a minute ago'
    units = [('day', minutes // 1440), ('hour', minutes % 1440 // 60),
             ('minute', minutes % 60)]
    parts = [f'{count} {name}{"" if count == 1 else "s"}'
             for name, count in units if count]
    return ' '.join(parts[:2]) + ' ago'


def confirm(question):
    '''
    Ask before an irreversible step; anything but y/yes leaves it undone.

    A run with no terminal to ask on (a script, a pipe) gets no as its answer
    rather than an exception -- -undoNoPrompt is how that run means yes.
    '''
    try:
        answer = input(f'{question} [y/N] ')
    except EOFError:
        return False
    return answer.strip().lower() in ('y', 'yes')


def atomicWrite(path, lines):
    '''
    Replace path with lines, through a temporary file in the same directory.

    The same approach as asfSearchAndDownload queueS1._atomicDump: an
    interrupted run leaves either the old orbitframes or the new one, never a
    half-written file for setupSeveralTopsImages to read.
    '''
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w') as fout:
        for line in lines:
            fout.write(f'{line}\n')
    os.replace(tmp, path)


def atomicCopy(source, dest):
    '''Copy source over dest atomically, byte for byte (used by -undo)'''
    tmp = f'{dest}.tmp.{os.getpid()}'
    with open(source, 'rb') as fin, open(tmp, 'wb') as fout:
        fout.write(fin.read())
    os.replace(tmp, dest)


def nearestProjectYaml(startDir='.'):
    '''
    Parsed project.yaml from the nearest directory at or above startDir, or {}.

    Sentinel-1 has no project.yaml yet, so this normally returns {} and the
    defaults apply.  It follows the walk-up convention the NISAR workflow
    already uses (nisargrimpworkflow estimateIonosphere) rather than inventing
    a second one, and is reimplemented here only because s1setup does not
    depend on that package.
    '''
    thisDir = os.path.abspath(startDir)
    while True:
        projectFile = os.path.join(thisDir, 'project.yaml')
        if os.path.isfile(projectFile):
            try:
                with open(projectFile, 'r') as fin:
                    return yaml.safe_load(fin) or {}
            except Exception as exception:
                print(f'*** ignoring unreadable {projectFile}: {exception}')
                return {}
        parent = os.path.dirname(thisDir)
        if parent == thisDir:
            return {}
        thisDir = parent


def parseAscNodeTime(fileAsc):
    '''Read an ascendingNodeTime file, with or without fractional seconds'''
    with open(fileAsc, 'r') as fnode:
        line = fnode.readline().strip()
    fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in line else '%Y-%m-%dT%H:%M:%S'
    return datetime.strptime(line, fmt)


def burstRuns(slcDir):
    '''
    Contiguous burst runs in an assembled <orbit>-<seq> directory.

    Taken from iw1 alone, because that is what setupSeveralTopsImages'
    checkBurstTimes validates against -- the other swaths can start a burst
    either side and using them would reject framings that build fine.  Falls
    back to the frames.FIRST.LAST sentinel, which is less reliable because
    computeBurstTimes takes FIRST from iw1 but LAST from the last swath it
    processed.

    Returns (runs, gappy).  A gappy directory has a hole in its burst
    sequence, which makes setupSeveralTopsImages abort the whole track no
    matter what orbitframes says, so it is reported for repair rather than
    worked around.
    '''
    for btimes in sorted(glob.glob(f'{slcDir}/*iw1*.btimes')):
        numbers = []
        for line in open(btimes):
            if not line.strip():
                continue
            try:
                numbers.append(int(line.split()[2]))
            except (IndexError, ValueError):
                continue
        if not numbers:
            continue
        runs = []
        first = numbers[0]
        for this, nextOne in zip(numbers, numbers[1:]):
            if nextOne != this + 1:
                runs.append((first, this))
                first = nextOne
        runs.append((first, numbers[-1]))
        return runs, len(runs) > 1
    #
    # No .btimes, try the sentinel file
    #
    for sentinel in glob.glob(f'{slcDir}/frames.*.*'):
        pieces = os.path.basename(sentinel).split('.')
        try:
            return [(int(pieces[1]), int(pieces[2]))], False
        except (IndexError, ValueError):
            continue
    return [], False


def mergeRuns(runs):
    '''
    Sort and de-duplicate the burst runs of an acquisition.

    Runs from different <orbit>-<seq> directories are deliberately *not*
    merged even where they abut: each is a separate SLC and a piece has to be
    cut from one of them, so joining two that happen to meet would propose a
    piece no single directory can build.
    '''
    return sorted({(first, last) for first, last in runs})


def hasSwathSLCs(assembled):
    '''
    True if an assembled directory still holds its swath SLCs.

    Cleaning the SLCs away leaves the metadata behind -- the .slc.par,
    .tops_par, .btimes, ascendingNodeTime and burst files all stay -- so the
    directory goes on looking assembled long after there is anything left to
    cut a piece from.  Linking one of those puts an acquisition into the record
    that no setup can build.
    '''
    #
    # The swath naming (<date>-<seq>_iw<n>_<pol>.slc) first, then any .slc at
    # all, so an unusual name still counts.  A zero-length file is a leftover,
    # not an SLC.
    #
    for pattern in (f'{assembled}/*iw*.slc', f'{assembled}/*.slc'):
        for slc in glob.glob(pattern):
            try:
                if os.path.getsize(slc) > 0:
                    return True
            except OSError:
                continue
    return False


def getAcquisitions(firstDate, lastDate):
    '''
    Collect the assembled SLCs as one entry per orbit, date ordered.

    An orbit split across several sequences (a split datatake) contributes one
    burst run per sequence, since setupSeveralTopsImages will realize a piece
    if any sequence of that orbit covers it.

    An acquisition whose SLCs have all been cleaned away is marked 'noData'
    rather than dropped.  It still parses -- the metadata stays behind -- and
    its coverage is worth keeping, since the anchors and the derived
    segmentation are learned from the whole record and most of a mature track
    has been cleaned.  What it cannot do is carry a new piece, so nothing is
    proposed for it (proposeTrack, addMates) and no setup is attempted.

    Returns (acquisitions, noData), noData being the stale directories.
    '''
    byOrbit = {}
    noData = []
    slcDirs = []
    for pattern in SLCPATTERNS:
        slcDirs += sorted(glob.glob(pattern))
    for slcDir in slcDirs:
        orbitStr, seqStr = slcDir.split('-')
        if not (orbitStr.isdigit() and seqStr.isdigit()):
            continue
        ascFile = f'{slcDir}/ascendingNodeTime'
        if not os.path.isfile(ascFile):
            continue
        runs, gappy = burstRuns(slcDir)
        if not runs:
            continue
        #
        # After the burst times, which are what the coverage is read from: a
        # stale directory still contributes its history, just no new work
        #
        stale = not hasSwathSLCs(slcDir)
        if stale:
            noData.append(slcDir)
        orbit = int(orbitStr)
        if orbit not in byOrbit:
            byOrbit[orbit] = {'orbit': orbit,
                              'date': parseAscNodeTime(ascFile),
                              'runs': [], 'dirs': [], 'gappy': [],
                              'noData': True}
        #
        # One sequence still holding its SLCs is enough to build from
        #
        if not stale:
            byOrbit[orbit]['noData'] = False
        byOrbit[orbit]['dirs'].append((slcDir, [tuple(x) for x in runs]))
        if gappy:
            #
            # A burst went missing during assembly.  The numbering steps over
            # it, which is the record of what is not there, and the runs
            # either side of it are sound -- a piece is only ever cut from
            # inside one run (usableRun) -- so the coverage is contributed and
            # the hole reported for the user to accept
            #
            byOrbit[orbit]['gappy'].append(slcDir)
        byOrbit[orbit]['runs'] += [list(x) for x in runs]
    acquisitions = []
    for acq in byOrbit.values():
        if acq['date'] < firstDate or acq['date'] > lastDate:
            continue
        acq['runs'] = mergeRuns(acq['runs'])
        acquisitions.append(acq)
    return (sorted(acquisitions, key=lambda a: (a['date'], a['orbit'])),
            noData)


def usableRun(runs, first, last):
    '''Return the run fully containing [first, last], or None (checkRange)'''
    for runFirst, runLast in runs:
        if runFirst <= first and last <= runLast:
            return (runFirst, runLast)
    return None


def realizedStarts(orbit, runs, frames, orbframes):
    '''
    Pieces setupSeveralTopsImages would actually build for this orbit.

    Mirrors determineFraming followed by checkRange's in-range test, and
    returns {firstBurst: nBursts}; the firstBursts are the pairing keys.
    '''
    starts = {}
    for first, nBursts in determineFraming(orbit, 0, frames, orbframes):
        if nBursts == 0:
            continue
        if usableRun(runs, first, first + nBursts - 1) is not None:
            starts[first] = nBursts
    return starts


def setupLengths(orbit, cache={}):
    '''
    Burst count of each piece already built, from its setup_ script.

    The directory name only carries the start, but setupSeveralTopsImages
    writes setup_<orbit>_<start> alongside it recording the first and last
    burst it cut, which is where the length survives.
    '''
    if orbit in cache:
        return cache[orbit]
    lengths = {}
    for setupFile in glob.glob(f'setup_{orbit}_*'):
        start = os.path.basename(setupFile).split('_')[-1]
        if not start.isdigit():
            continue
        bounds = {}
        for line in open(setupFile):
            for key in ('firstBurst', 'lastBurst'):
                if line.startswith(f'set {key}'):
                    value = line.split('=')[-1].strip()
                    if value.isdigit():
                        bounds[key] = int(value)
        if len(bounds) == 2:
            lengths[int(start)] = (bounds["lastBurst"] -
                                   bounds["firstBurst"] + 1)
    cache[orbit] = lengths
    return lengths


def builtLengths(orbit, runs, frames, orbframes):
    '''
    Burst count of each piece already built.

    The directory name carries only the start, so the length is taken from
    the setup_ script that cut it; those get cleaned up, so `frames` plus
    `orbitframes` -- which is what specified the cut in the first place --
    fills in the rest.  The setup_ script wins where both exist, being what
    was actually run.
    '''
    lengths = dict(realizedStarts(orbit, runs, frames, orbframes))
    lengths.update(setupLengths(orbit))
    return lengths


def clippedPieces(runs, spanFirst, spanLast, minBursts):
    '''
    Pieces available for one frame index, clipped to its span.

    The span always comes from the `frames` entry, never from the drifted
    anchor, so every piece stays inside the frame it belongs to: a piece is
    never extended past the nominal end, and a run spanning two frames yields
    one piece in each rather than one straddling piece.  Downstream
    autocleaning works on per-frame bounded regions, so a piece outside its
    frame produces results outside the expected region.
    '''
    pieces = []
    for runFirst, runLast in runs:
        first = max(runFirst, spanFirst)
        last = min(runLast, spanLast)
        if last - first + 1 >= minBursts:
            pieces.append((first, last))
    return pieces


def historicalRange(built, orbframes, acquisitions):
    '''
    Furthest the track has ever been cut, as (firstBurst, lastBurst).

    Coverage grows over the years and a new acquisition can reach bursts that
    have never been processed.  Extending a frame into them would produce a
    piece covering ground nothing else covers, so it could never pair and
    would fall outside the autocleaning regions.  Everything proposed is held
    inside this envelope.
    '''
    firsts, lasts = [], []
    for _, _, start, nBursts in orbframes:
        if nBursts > 0:
            firsts.append(start)
            lasts.append(start + nBursts - 1)
    for acq in acquisitions:
        lengths = setupLengths(acq['orbit'])
        for start in built.get(acq['orbit'], ()):
            firsts.append(start)
            #
            # A directory whose setup_ script has been cleaned up says where
            # a piece started but not how far it ran, so it must not be taken
            # as a one-burst piece -- that would collapse the envelope
            #
            if start in lengths:
                lasts.append(start + lengths[start] - 1)
    if not firsts or not lasts:
        return None
    return min(firsts), max(lasts)


def splitLong(first, last, maxBursts):
    '''
    Break a run into pieces of no more than maxBursts, sharing a seam burst.

    Neighbouring pieces already overlap by one burst -- 398-45 ends on 442 and
    442-54 starts on it -- so a seam repeats a burst and the pieces are cut
    the same way.  They are made as equal as they divide rather than filling
    maxBursts and leaving a stub at the end: a stub is a short piece like any
    other and pairs no better for having been an afterthought.
    '''
    span = last - first
    if span + 1 <= maxBursts or span <= 0:
        return [(first, last)]
    nPieces = -(-span // (maxBursts - 1))
    pieces, edge = [], first
    for piece in range(1, nPieces + 1):
        end = first + -(-span * piece // nPieces)
        pieces.append((edge, end))
        edge = end
    return pieces


def readVelocityStats():
    '''
    Burst range of each autocleaning box, from velocityStats/<lo>-<hi>.

    These are the bounded regions the autocleaning works in.  They do not say
    how the track should be framed -- that is carried forward from what was
    built -- but a piece invented here is cut to sit inside them, since a
    piece straddling two is cleaned against bounds that are not its own.
    '''
    boxes = []
    for statDir in glob.glob('velocityStats/*-*'):
        if not os.path.isdir(statDir):
            continue
        lo, _, hi = os.path.basename(statDir).partition('-')
        if lo.isdigit() and hi.isdigit():
            boxes.append((int(lo), int(hi)))
    return sorted(boxes)


def mergeSpans(spans):
    '''Sort (first, last) spans and join the ones that meet or overlap'''
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def clipToSpans(first, last, spans):
    '''The parts of a piece that lie inside spans, as (first, last) pairs'''
    parts = []
    for spanFirst, spanLast in spans:
        lo, hi = max(first, spanFirst), min(last, spanLast)
        if lo < hi:
            parts.append((lo, hi))
    return parts


def splitAtBoxes(first, last, boxes, overrun, minBursts):
    '''
    Break a piece where it crosses out of its velocityStats box.

    The autocleaning works box by box, so a piece straddling two of them is
    cleaned against bounds that are not its own: where the track has been cut
    at 0-50 and 51-100, two pieces of 25-50 and 51-100 sit inside their boxes
    and one piece of 25-75 does not.  A piece that runs past a boundary by no
    more than overrun is left whole -- 25-55 is still essentially inside its
    box, and cutting there would leave a stub rather than a piece.

    Boundaries that would leave either side shorter than minBursts are passed
    over for the same reason.
    '''
    edges = sorted({hi for _, hi in boxes if first < hi < last} |
                   {lo - 1 for lo, _ in boxes if first < lo - 1 < last})
    pieces, edge = [], first
    for boundary in edges:
        if last - boundary <= overrun:
            break
        if boundary - edge + 1 < minBursts or last - boundary + 1 < minBursts:
            continue
        pieces.append((edge, boundary))
        edge = boundary
    pieces.append((edge, last))
    return pieces


def cutToFit(first, last, boxes, args):
    '''A piece broken to sit inside its boxes and to keep inside maxBursts'''
    pieces = []
    for start, end in splitAtBoxes(first, last, boxes, args.boxOverrun,
                                   args.minBursts):
        pieces += splitLong(start, end, args.maxBursts)
    return pieces


def dropContained(pieces, keep=()):
    '''
    Drop a piece another piece already covers, unless it is an established key.

    Overlap is not redundancy in itself.  Where 6-day data is being paired,
    the key shared with the acquisition either side gives the 6-day pair and
    the key only the alternating acquisition carries gives the 12-day one, and
    the second can sit inside the first -- track-31 pairs S1A against S1C on
    442 while 398 and 413 each pair their own satellite twelve days apart.  So
    a start the framing was carried from is kept whatever covers it, and only
    the pieces this run invented -- a seam picking up surplus coverage -- are
    dropped where something else already carries that ground.

    Two pieces on the same start always collapse to the longer: that is one
    directory, cut twice.
    '''
    kept = []
    for first, last in sorted(pieces, key=lambda piece: (piece[0], -piece[1])):
        if any(first == otherFirst for otherFirst, _ in kept):
            continue
        if first in keep:
            kept.append((first, last))
            continue
        if any(otherFirst <= first and last <= otherLast
               for otherFirst, otherLast in kept):
            continue
        kept.append((first, last))
    return kept


def doubledSpans(pieces):
    '''
    The burst ranges more than one piece of an acquisition covers.

    Neighbouring pieces meet on a shared burst by design, so a one-burst
    overlap is a seam rather than a doubling; anything wider is the same
    ground being cut, processed and mosaicked twice.  Taken as (first, last)
    pairs and returned the same way.
    '''
    spans = []
    ordered = sorted(pieces)
    for position, (first, last) in enumerate(ordered):
        for otherFirst, otherLast in ordered[position + 1:]:
            lo, hi = max(first, otherFirst), min(last, otherLast)
            if hi - lo >= 1:
                spans.append((lo, hi))
    return spans


def builtFraming(acq, built, frames, orbframes):
    '''
    What was actually cut from an acquisition, as {first: nBursts}.

    The <orbit>_<firstBurst> directories give the starts and builtLengths
    gives each one its length, from the setup_ script that cut it where that
    still exists and from the frames/orbitframes entry that specified it
    otherwise.  A start recorded in neither is carried at the length its own
    run allows, so that it still counts as a key.
    '''
    lengths = builtLengths(acq['orbit'], acq['runs'], frames, orbframes)
    framing = {}
    for start in built.get(acq['orbit'], ()):
        if lengths.get(start):
            framing[start] = lengths[start]
            continue
        run = usableRun(acq['runs'], start, start)
        if run is not None:
            framing[start] = run[1] - start + 1
    return framing


class TrackFraming(object):
    '''
    The pieces proposed for each orbit, and how each orbit may be written.

    An acquisition that has been built already is closed: its orbitframes
    lines describe directories that exist and pair, and rewriting them would
    orphan what was built from them.  Its framing is still derived, because
    that is what the comparison is read against, but the only thing that can
    be written for it is an extra piece appended to what it already has --
    where a key introduced further along needs a partner to pair with.
    '''

    def __init__(self, frames):
        self.frames = frames
        # orbit -> [(first, nBursts)], the framing derived for that orbit
        self.pieces = {}
        # orbit -> [(first, nBursts)] appended to an orbit already built
        self.appended = defaultdict(list)
        #
        # The same, for a key belonging to an acquisition whose SLCs have been
        # cleaned away.  Drawn, so the plot shows the pairing the rules would
        # have produced, but never written: the partner it would pair with is
        # one nothing can be cut from, so building it would be work for
        # nothing
        #
        self.hypothetical = defaultdict(list)
        # orbit -> the built acquisition its keys were taken from
        self.reference = {}
        #
        # orbit -> lines a built acquisition is missing altogether, so that
        # the default `frames` entries stop being read for it.  The one thing
        # written for an acquisition already built besides a mate piece, and
        # only where it has no lines of its own (repairLines)
        #
        self.repair = {}
        #
        # Orbits whose SLCs have been cleaned away.  A framing is still
        # derived for them, since that is what the comparison is read
        # against, but there is nothing left to cut so none of it is written
        #
        self.noData = set()
        # lines for the report, in the order they were raised
        self.notes = []

    def setPieces(self, orbit, pieces):
        self.pieces[orbit] = list(pieces)

    def appendPiece(self, orbit, first, nBursts, real=True):
        '''
        Add a piece to an orbit already built, if it has not got one.

        real=False records it as hypothetical: drawn, never written.
        '''
        store = self.appended if real else self.hypothetical
        if first in {piece[0] for piece in store[orbit]}:
            return False
        store[orbit].append((first, nBursts))
        return True

    def starts(self, orbit):
        '''Every start this orbit would carry, proposed or appended'''
        return ({first for first, _ in self.pieces.get(orbit, [])} |
                {first for first, _ in self.appended.get(orbit, [])} |
                {first for first, _ in self.hypothetical.get(orbit, [])})

    def orbits(self):
        '''Orbits with anything to say, whether a framing or an addition'''
        return set(self.pieces) | {orbit for orbit, pieces
                                   in self.appended.items() if pieces}


def referenceFraming(acquisitions, iAcq, built, frames, orbframes, args,
                     allowed=(0, 100000), established=()):
    '''
    The keys and lengths to carry into this acquisition, as {first: nBursts}.

    The keys come from the newest built acquisition whose burst coverage
    matches this one.  Matching matters because two satellites interleave and
    they do not cover the same bursts -- on track-31 S1C holds 398-495 and S1A
    413-494 -- so the keys of whichever acquisition happens to be most recent
    can be starts this one's data cannot reach.  It can mean looking back more
    than one acquisition, which is what refBack is for, and where nothing
    recent matches it means looking back years: an old framing of the same
    ground beats a recent framing of different ground.

    Only acquisitions this one could actually pair with are candidates for
    the lengths (pairable: 12 days normally, 24 or 36 where a cycle has been
    missed and nothing lies between).  A framing carried forward from cycles
    ago carries nothing with it -- the series it belonged to stopped long
    since -- so beyond that an acquisition can still say how this ground is
    cut, but is not treated as something to pair with.

    The lengths are reconciled across the built acquisitions that match in the
    same way, and only those.  An acquisition covering different bursts cut
    the key shorter because its data ran out there, not because the track is
    framed that way -- on track-148 S1A stops at 408 and cuts 373 to 36 bursts
    where S1C runs it to 50 -- and reading that as a framing decision shortens
    the key for everyone and leaves a gap to be filled with stubs.

    Among the acquisitions that do match, a difference of no more than snapTol
    is jitter: the longest is taken and any acquisition that cannot reach it
    is shortened, since only the start names the directory and a piece a burst
    or two short still pairs.  A systematic difference (54, 34, 54, 34) is a
    framing decision: the shortest is taken, so that every acquisition pairs
    over the whole of its piece, and the surplus is picked up as a piece of
    its own at the seam (framePieces).

    Returns (reference, match, ground): the framing to apply, the acquisition
    the keys came from, and the ground those acquisitions were actually cut
    over -- which is as far as anything invented here may go.
    '''
    here = acquisitions[iAcq]
    #
    # The acquisitions that could actually pair with this one: a framing
    # carried forward from cycles ago carries nothing with it, since the
    # series it belonged to has long since stopped.  Kept in date order,
    # newest first
    #
    near, older = [], []
    for jAcq in range(iAcq - 1, -1, -1):
        acq = acquisitions[jAcq]
        if not built.get(acq['orbit']):
            continue
        if len(near) < args.refBack and pairable(acquisitions, iAcq, jAcq,
                                                 args):
            near.append(acq)
        else:
            older.append(acq)
    if not near and not older:
        #
        # Nothing has been built before this one, so `frames` is all there is
        # to go on -- a track being set up for the first time
        #
        frame = {first: nBursts for first, nBursts in frames}
        return frame, None, [(first, first + nBursts - 1)
                             for first, nBursts in frames]
    #
    # The keys come from whichever built acquisition frames the most of this
    # one's data (carriedCoverage).  The recent ones are tried first and an
    # older one only wins where it frames materially more: a framing that
    # leaves half the acquisition uncut is not the one to carry on, however
    # freshly it was built -- on track-163 the acquisition six days before
    # 63410 was 22 bursts long and carried a single 12-burst key.
    #
    scored = []
    for rank, acq in enumerate(near + older[:args.refSearch]):
        framing = builtFraming(acq, built, frames, orbframes)
        scored.append((carriedCoverage(here['runs'], framing, allowed, args,
                                       established),
                       -rank, acq, framing))
    #
    # Among equals the most recent wins: the framing in current use is the
    # one to carry on, and letting a burst or two of extra coverage send it
    # back through the record drops keys that are still being built.
    #

    def pick(entries):
        best = max(entry[0] for entry in entries)
        margin = max(args.minBursts, best // 10)
        return max((entry for entry in entries if entry[0] >= best - margin),
                   key=lambda entry: entry[1])

    nearScored, olderScored = scored[:len(near)], scored[len(near):]
    #
    # Where nothing is close enough to pair with, the most recent built
    # acquisitions are still the framing in use and are tried first all the
    # same.  Picking on coverage alone across the whole record hands the
    # framing to whichever old one happens to cut the most ground: track-61
    # stopped cutting bursts 355-374 in 2025, so a framing from before that
    # covers more of an acquisition than the one actually in use, and reached
    # back 275 days to drop the 429 key the rest of the track pairs on
    #
    if not nearScored:
        nearScored, olderScored = (olderScored[:args.refBack],
                                   olderScored[args.refBack:])
    best = pick(nearScored)
    #
    # Reaching past the neighbours happens only where their framing leaves
    # most of this acquisition uncut, and an older one does better.  That is
    # the track-163 case: the acquisition six days before 63410 is 22 bursts
    # long and frames 12 of its 82, where the 2022 framing frames 79 and,
    # trimmed, is exactly what 63410 was built as.  Measuring it against this
    # acquisition's own data rather than against the best framing on offer is
    # what keeps it from firing on a track whose framing is simply in the
    # middle of changing.
    #
    if nearScored and olderScored:
        available = len({burst for first, last in here['runs']
                         for burst in range(max(first, allowed[0]),
                                            min(last, allowed[1]) + 1)})
        reach = max(entry[0] for entry in olderScored)
        if best[0] < REACHBACKFRACTION * available and reach > best[0]:
            best = pick(olderScored)
    match, matchFraming = best[2], best[3]
    refs = [acq for acq in near] or [match]
    if match not in refs:
        refs = [match] + refs
    framings = [builtFraming(acq, built, frames, orbframes) for acq in refs]
    reference = {}
    for first in matchFraming:
        #
        # Only a piece that stops short of its own data says anything about
        # how long the key should be.  One that runs to the end of its
        # acquisition was simply trimmed there -- track-163's 6459 covers 22
        # bursts and cuts 358 at 12, track-148's S1A stops at 408 and cuts 373
        # at 36 -- and reading that as a framing decision shortens the key for
        # everyone and leaves a gap behind it to be filled with stubs
        #
        seen = [matchFraming[first]]
        for acq, framing in zip(refs, framings):
            if first not in framing or framing is matchFraming:
                continue
            run = usableRun(acq['runs'], first, first + framing[first] - 1)
            if run is not None and first + framing[first] - 1 >= run[1]:
                continue
            seen.append(framing[first])
        reference[first] = (max(seen) if max(seen) - min(seen) <= args.snapTol
                            else min(seen))
    #
    # The ground these acquisitions were cut over.  Coverage outside it has
    # been left out deliberately -- ocean, or somewhere of no interest -- and
    # the record is emphatic about it: picking it up invents keys the track
    # has never carried and that nothing else will ever pair with.  Inside it
    # is different: ground a neighbour cut and this framing does not reach is
    # the surplus of a key that has been shortened, and that does want a piece
    #
    ground = mergeSpans([(first, first + nBursts - 1) for framing in framings
                         for first, nBursts in framing.items()])
    return reference, match, ground


def carriedPieces(runs, reference, allowed, args, established=()):
    '''
    The reference keys as this acquisition would cut them, [(first, last)].

    Each key is taken at the reference length and shortened where the data
    runs out; a key the data cannot carry at all is dropped.
    '''
    pieces = []
    for first in sorted(reference):
        end = min(first + reference[first] - 1, allowed[1])
        run = usableRun(runs, first, first + args.minBursts - 1)
        start = first
        if run is None:
            #
            # The key falls before the data.  Where the frame still reaches
            # into it, the piece is moved onto the first burst there is rather
            # than abandoned: a start that has to move makes a new key, but
            # losing the piece altogether loses the ground for good, and the
            # acquisitions around it move the same way, so the new key pairs.
            # On track-45 the coverage stepped from 419 to 421 and everything
            # since has been cut on a key of its own.
            #
            ahead = [runFirst for runFirst, _ in runs
                     if first < runFirst <= end - args.minBursts + 1]
            if not ahead:
                continue
            start = max(min(ahead), allowed[0])
            #
            # A start the track already uses is worth a burst or two of
            # give: it is a key pieces exist on and pair with, where the
            # first burst of the data is a key of nobody's.  Track-45's
            # coverage begins at 421 and every acquisition of that run was
            # cut at 422
            #
            near = [key for key in established
                    if abs(key - start) <= args.snapTol and key >= start and
                    usableRun(runs, key, key + args.minBursts - 1) is not None]
            if near:
                start = min(near, key=lambda key: (abs(key - start), key))
            run = usableRun(runs, start, start + args.minBursts - 1)
            if run is None:
                continue
        last = min(end, run[1])
        if last - start + 1 >= args.minBursts:
            pieces.append((start, last))
    return pieces


def carriedCoverage(runs, reference, allowed, args, established=()):
    '''
    How many of this acquisition's bursts a reference framing would cut.

    This is what decides which acquisition the framing comes from.  Coverage
    matching alone is too strict: on track-163 orbit 63410 covers 355-436 and
    the framing that fits it is the 2022 one, 358-60 alongside 417-62, whose
    own acquisitions ran to 478 -- trimmed to 436 it is exactly what 63410 was
    built as.  Requiring the same coverage rejected it in favour of an older
    acquisition that happened to stop in the same place.

    Scoring the ground covered instead keeps what coverage matching was for.
    A framing from the other satellite leaves a hole where its own coverage
    starts later -- track-31 S1A cutting 413 and 442 covers 82 of S1C's 98
    bursts, against 98 for S1C's own 398 and 442 -- so it loses on the score
    rather than on a pattern test.
    '''
    covered = set()
    for first, last in carriedPieces(runs, reference, allowed, args,
                                     established):
        covered.update(range(first, last + 1))
    return len(covered)


def framePieces(runs, reference, allowed, args, boxes=(), ground=None,
                established=()):
    '''
    The pieces to cut from one acquisition, as [(first, nBursts)].

    Every key the coverage can carry is cut at the reference length, shortened
    where the data runs out.  A key is left exactly as history cut it, boxes
    and all, unless it is longer than maxBursts and has to be broken.

    Coverage the keys do not reach is then taken up as pieces of its own,
    seamed onto the piece either side so that they share a burst the way the
    established pieces do -- that is what picks up the surplus of an
    acquisition whose neighbours are shorter, and the early bursts of a
    satellite whose keys have not been seen yet.  These are cut to sit inside
    the velocityStats boxes, since nothing established them anywhere else.

    Nothing is proposed outside the allowable range, no piece is longer than
    maxBursts, and a piece another piece already covers is dropped unless it
    is a key in its own right.
    '''
    carried = carriedPieces(runs, reference, allowed, args, established)
    invented = []
    for runFirst, runLast in runs:
        lo, hi = max(runFirst, allowed[0]), min(runLast, allowed[1])
        edge = lo
        for first, last in sorted(piece for piece in carried
                                  if lo <= piece[0] and piece[1] <= hi):
            if first - edge + 1 >= args.minBursts:
                invented.append((edge, first))
            edge = max(edge, last)
        if hi - edge + 1 >= args.minBursts:
            invented.append((edge, hi))
    cut = []
    for first, last in carried:
        #
        # An established key is left as it was cut, so the framing stays the
        # one the track has been processed on; only the cap can break it
        #
        cut += ([(first, last)] if last - first + 1 <= args.maxBursts
                else cutToFit(first, last, boxes, args))
    for first, last in invented:
        #
        # Only as far as the neighbours were cut.  Coverage past that has been
        # left out on purpose and the record is emphatic about it
        #
        for part, partLast in (clipToSpans(first, last, ground)
                               if ground is not None else [(first, last)]):
            if partLast - part + 1 >= args.minBursts:
                cut += cutToFit(part, partLast, boxes, args)
    return [(first, last - first + 1)
            for first, last in dropContained(cut,
                                             keep={first for first, _
                                                   in carried})
            if last - first + 1 >= args.minBursts]


def repairLines(acq, built, frames, orbframes):
    '''
    The orbitframes lines a built acquisition is missing, or [].

    `frames` is only the default, and a track's default is edited over the
    years -- widened as the coverage grows, moved when the datatake does.  An
    acquisition built under an older one and never given lines of its own is
    then read against a default it does not fit, and the setup step stops on
    it: on track-133 every acquisition is cut 400-12 and 180 of them say so,
    but the six that do not are checked against `frames` 402-62, which their
    22 bursts cannot carry.

    Writing the lines is the whole point of the file, so they are written --
    from what was actually built, which is the plan that produced the
    directories on disk.  Only an acquisition with no lines at all is given
    any: one that has them was framed deliberately, and a default it cannot
    cover is then a genuine thing to fix by hand rather than to guess at.
    '''
    orbit = acq['orbit']
    if not built.get(orbit) or acq.get('noData'):
        return []
    if any(entry[0] == orbit for entry in orbframes):
        return []
    #
    # Only where the default really cannot be cut.  A built acquisition the
    # default fits needs no line: it is already described by `frames`
    #
    if not any(nBursts > 0 and
               usableRun(acq['runs'], first, first + nBursts - 1) is None
               for first, nBursts in frames):
        return []
    framing = builtFraming(acq, built, frames, orbframes)
    if not framing:
        return []
    return orbitLines(sorted(framing.items()), len(frames))


def frameTrack(acquisitions, frames, orbframes, built, allowed, args,
               fromAcq=0, boxes=()):
    '''
    Frame every acquisition from fromAcq on, each from the ones before it.

    Walked in date order, so an acquisition framed here is the reference for
    the next one and a whole run of new acquisitions comes out framed alike.
    An acquisition already built has its framing derived just the same, for
    the comparison to be read against, but nothing of it is ever written --
    see TrackFraming.
    '''
    framing = TrackFraming(frames)
    #
    # Every start the track has been cut on: where a key has to move, one of
    # these is worth a burst or two of give over the first burst of the data
    #
    established = {start for starts in built.values() for start in starts}
    for iAcq in range(fromAcq, len(acquisitions)):
        acq = acquisitions[iAcq]
        if acq.get('noData'):
            #
            # Its SLCs have been cleaned away, so there is nothing left to cut
            # from it however well its coverage fits.  It is still framed,
            # because that is what the comparison is read against, but
            # proposedEntries writes nothing for it
            #
            framing.noData.add(acq['orbit'])
        reference, match, ground = referenceFraming(acquisitions, iAcq,
                                                    built, frames, orbframes,
                                                    args, allowed,
                                                    established)
        framing.setPieces(acq['orbit'],
                          framePieces(acq['runs'], reference, allowed, args,
                                      boxes, ground, established))
        framing.reference[acq['orbit']] = match
        framing.repair[acq['orbit']] = repairLines(acq, built, frames,
                                                   orbframes)
    addMates(framing, acquisitions, built, args)
    return framing


def pairable(acquisitions, iA, iB, args):
    '''
    Whether two acquisitions are close enough in time to pair at all.

    Twelve days is the repeat, and where a cycle or two has been missed the
    24- and 36-day pairs are still worth forming -- but only where nothing
    lies between them.  With intervening data, that is what each of them pairs
    with, and reaching past it gains nothing; without it, the alternative is
    no pair at all.

    An intervening acquisition whose SLCs have been cleaned away does not
    count: nothing can be cut from it, so it is not a partner for either.
    '''
    lo, hi = sorted((iA, iB))
    days = abs((acquisitions[iB]['date'] - acquisitions[iA]['date']).days)
    intervening = any(not acquisitions[j].get('noData')
                      for j in range(lo + 1, hi))
    return days <= (args.maxMateDays if intervening else args.maxPairDays)


def addMates(framing, acquisitions, built, args):
    '''
    Give a key that nothing else carries a partner it can pair with.

    A start only pairs where a neighbouring acquisition carries the same
    start, so a key the new framing introduces -- the seam piece of a coverage
    that has grown, say -- pairs with nothing until the acquisition after it.
    Where the acquisition before it was built and its data reaches, it is
    given that piece as an addition: appended to what it already carries and
    never a change to it, since the directories it was cut into are what
    everything around it already pairs with.

    mateSpan of 2 covers both the 6-day nearest-in-time pair and the 12-day
    same-mission pair used by the Sentinel1-S1A/-S1C clone directories.
    '''
    for iAcq, acq in enumerate(acquisitions):
        orbit = acq['orbit']
        if built.get(orbit) or orbit not in framing.pieces:
            continue
        #
        # An acquisition with nothing left to cut still gets its partners
        # worked out, so the plot shows the pairing the rules would have
        # produced; they are recorded as hypothetical and never written
        #
        real = orbit not in framing.noData
        for first, nBursts in framing.pieces[orbit]:
            #
            # Each side in turn, nearest first.  A piece pairs forwards and
            # backwards independently, so a key the acquisition before it
            # carries still wants one in the acquisition after it -- that is
            # a second pair, and on a key this new it is the difference
            # between one pair and two
            #
            for direction in (-1, 1):
                steps = [iAcq + direction * step
                         for step in range(1, args.mateSpan + 1)]
                steps = [jAcq for jAcq in steps
                         if 0 <= jAcq < len(acquisitions) and
                         pairable(acquisitions, iAcq, jAcq, args)]
                if any(first in built.get(acquisitions[jAcq]['orbit'], set())
                       or first in framing.starts(acquisitions[jAcq]['orbit'])
                       for jAcq in steps):
                    #
                    # Something this side carries it already
                    #
                    continue
                for jAcq in steps:
                    mate = acquisitions[jAcq]
                    if not built.get(mate['orbit']):
                        continue
                    if mate.get('noData') and real:
                        #
                        # Nothing can be cut from it, so it cannot carry a
                        # partner that would actually be built
                        #
                        continue
                    run = usableRun(mate['runs'], first,
                                    first + args.minBursts - 1)
                    if run is None:
                        continue
                    if framing.appendPiece(mate['orbit'], first,
                                           min(run[1], first + nBursts - 1) -
                                           first + 1, real=real):
                        framing.notes.append(
                            f'    {mate["orbit"]} is given an extra piece at '
                            f'{first} so the new key has something to pair '
                            'with')
                    break


def orbitLines(pieces, nDefault, fromIndex=0):
    '''
    One orbit's pieces as orbitframes entries, [[index, first, nBursts]].

    determineFraming starts from `frames` and applies the overrides on top, so
    every default index has to be stated whenever an orbit is listed: one left
    out quietly falls back to its `frames` entry, and where the acquisition
    cannot cover that entry checkRange aborts the whole track.  Indices above
    the number of frames are appended by determineFraming rather than
    overriding anything, which is what fromIndex is for -- a piece added to an
    acquisition already framed has to take an index of its own instead of
    overwriting one of the lines it already has.
    '''
    if fromIndex:
        return [[fromIndex + n + 1, first, nBursts]
                for n, (first, nBursts) in enumerate(sorted(pieces))]
    lines = [[n + 1, first, nBursts]
             for n, (first, nBursts) in enumerate(sorted(pieces))]
    for index in range(len(lines) + 1, nDefault + 1):
        #
        # A default frame this acquisition has no piece for: stated as zero
        # rather than left out, or determineFraming puts the `frames` entry
        # back and the track aborts on it
        #
        lines.append([index, 0, 0])
    return lines


def highestIndex(orbit, orbframes, nDefault):
    '''Highest orbitframes index an orbit uses, the default frames included'''
    return max([entry[1] for entry in orbframes if entry[0] == orbit] +
               [nDefault])


def proposedEntries(framing, built, orbframes, nDefault, onlyOrbits=None):
    '''
    The orbits that need orbitframes lines, and the lines each one needs.

    An acquisition already built gets only the pieces it is being given,
    indexed above everything it already carries so that determineFraming adds
    them to its framing rather than replacing part of it, and the lines it is
    missing altogether where it has none (repairLines).  One not yet built
    gets its whole framing, which -commit writes in place of anything it has.
    '''
    entries = {}
    for orbit in sorted(framing.orbits()):
        if onlyOrbits is not None and orbit not in onlyOrbits:
            continue
        if orbit in framing.noData:
            continue
        if built.get(orbit):
            #
            # The repair lines take the default indices, which this orbit has
            # none of, and a mate piece is indexed above them as always
            #
            lines = list(framing.repair.get(orbit, []))
            if framing.appended.get(orbit):
                lines += orbitLines(
                    framing.appended[orbit], nDefault,
                    fromIndex=highestIndex(orbit, orbframes, nDefault))
            if lines:
                entries[orbit] = lines
            continue
        if framing.pieces.get(orbit):
            entries[orbit] = orbitLines(framing.pieces[orbit], nDefault)
    return entries


def formatEntries(framing, acquisitions, built, orbframes, nDefault,
                  onlyOrbits=None):
    '''Format the framing as orbitframes lines, grouped by orbit and date'''
    entries = proposedEntries(framing, built, orbframes, nDefault, onlyOrbits)
    lines = []
    for acq in acquisitions:
        orbit = acq['orbit']
        if orbit not in entries:
            continue
        for slcDir, dirRuns in sorted(acq['dirs']):
            span = (f'{dirRuns[0][0]}.{dirRuns[-1][1]}' if dirRuns
                    else 'gap.gap')
            lines.append(f'# {slcDir}/frames.{span}')
        for index, first, nBursts in entries[orbit]:
            lines.append(f'{orbit}-{index}-{first}-{nBursts}')
    return lines


def orbitOfLine(line):
    '''
    The orbit an orbitframes line sets, or None if the line is not an entry.

    Follows getFramesAndOrbits exactly: a line without a '-' or with a '#'
    anywhere in it is not an entry, and an entry has 3 or 4 fields.
    '''
    if '-' not in line or '#' in line:
        return None
    fields = line.split('-')
    if len(fields) not in (3, 4):
        return None
    if not all(field.strip().isdigit() for field in fields):
        return None
    return int(fields[0])


def commentOrbit(line):
    '''
    The orbit of a provenance comment this program wrote, or None.

    formatEntries heads each orbit with `# <orbit>-<seq>/frames.<first>.<last>`.
    Recognising them is what lets a commit replace its own comments instead of
    stacking up a fresh copy each time, while leaving hand-written comments
    alone.
    '''
    text = line.strip()
    if not text.startswith('#'):
        return None
    name, separator, rest = text.lstrip('#').strip().partition('/')
    if not separator or not rest.startswith('frames.'):
        return None
    orbitStr, _, seqStr = name.partition('-')
    if not (orbitStr.isdigit() and seqStr.isdigit()):
        return None
    return int(orbitStr)


def commitEntries(framing, orbits, acquisitions, built, orbframes, nDefault):
    '''
    Merge the framing into orbitframes, orbit by orbit.

    An acquisition not yet built has every line of its own replaced;
    everything else -- hand comments, blank lines, entries for orbits not
    proposed -- is kept as it was.  The file has been maintained by hand for
    years and most of it is still the best description of those orbits, so the
    merge touches only what the framing has something to say about.

    An acquisition already built is never rewritten.  Its lines describe
    directories that exist and pair, so they stay exactly where they are and
    the piece it is being given is added after them -- the one edit a built
    acquisition takes.
    '''
    lines = ([] if framing is None or not orbits else
             formatEntries(framing, acquisitions, built, orbframes, nDefault,
                           onlyOrbits=orbits))
    if not lines:
        print('\nnothing to commit')
        return
    replace = {orbit for orbit in orbits if not built.get(orbit)}
    with open('orbitframes', 'r') as fin:
        existing = [line.rstrip('\n') for line in fin]
    kept, replaced = [], 0
    for line in existing:
        orbit = orbitOfLine(line)
        if orbit is not None and orbit in replace:
            replaced += 1
            continue
        #
        # Provenance comments are rewritten with the entries they head, for
        # every orbit written; the entries of a built orbit are not
        #
        if commentOrbit(line) in orbits:
            continue
        kept.append(line)
    os.makedirs(BACKUPDIR, exist_ok=True)
    backup = f'{BACKUPDIR}/orbitframes.bak.{stamp()}'
    atomicCopy('orbitframes', backup)
    atomicWrite('orbitframes', kept + lines)
    written = sum(1 for line in lines if orbitOfLine(line) is not None)
    added = len(orbits) - len(replace)
    print(f'\ncommitted {written} entr{"y" if written == 1 else "ies"} for '
          f'{len(orbits)} orbit{"" if len(orbits) == 1 else "s"}, replacing '
          f'{replaced} existing line{"" if replaced == 1 else "s"}')
    if added:
        print(f'    {added} of them already built and left as they were, with '
              'a piece added after their entries')
    print(f'    previous orbitframes saved as {backup} -- -undo restores it')


def mergedOrbframes(framing, orbits, orbframes, built, nDefault):
    '''
    The orbitframes setupSeveralTopsImages would read after a commit.

    commitEntries replaces every line of the unbuilt orbits it proposes for,
    adds to the built ones and leaves the rest alone, so the same merge done
    in memory is what the setup step has to be checked against.  Without it a
    run that has not committed would check the framing it is replacing.

    Indexed against the `frames` file on disk, which is what
    setupSeveralTopsImages reads and expands the entries over.
    '''
    entries = ({} if framing is None else
               proposedEntries(framing, built, orbframes, nDefault, orbits))
    replace = {orbit for orbit in entries if not built.get(orbit)}
    merged = [list(entry) for entry in orbframes if entry[0] not in replace]
    for orbit in sorted(entries):
        for index, first, nBursts in entries[orbit]:
            merged.append([orbit, index, first, nBursts])
    return merged


def describeError(error):
    '''One line for a setup failure, naming what it applies to'''
    where = error.slc if error.slc else ''
    if not where and error.orbit is not None:
        where = f'{error.orbit}-{error.seq}'
    if error.frameRange is not None:
        where = f'{where} frame {error.frameRange[0]}-{error.frameRange[1]}'
    message = ' '.join(error.message.split())
    return f'    {where}: {message}' if where else f'    {message}'


def whyBlocked(error, orbits, built):
    '''
    What kind of failure this is, and so where the fix belongs.

    The setup step covers more of the record than any one run proposes for, so
    a failure is not necessarily in the framing just derived: it can be an
    acquisition already built, whose lines are never rewritten here, or one
    with no burst times to frame from at all.  Nothing is written either way --
    a run that cannot set up what it checked is not one to commit -- but which
    file to reach for is not the same in the three cases.
    '''
    kind = getattr(error, 'kind', None)
    if kind == 'gap':
        return ('        a frame is cut over a burst that is missing, which '
                'is the one thing a hole does stop -- reframe it to sit '
                'either side of the hole, or re-assemble the acquisition')
    if kind == 'btimes':
        return ('        it has no burst times to frame from -- the '
                'directory needs repairing, not the framing')
    if kind in ('ascnode', 'ascdesc'):
        return ('        it cannot be dated or its pass direction cannot be '
                'read, so nothing can be framed from it -- the directory '
                'needs repairing, not the framing')
    if error.orbit in orbits:
        return ('        this is the framing proposed here -- fix `frames` or '
                '`orbitframes` and run again')
    if error.orbit is not None and built.get(error.orbit):
        #
        # Nothing was built for this frame -- one that was is not range
        # checked at all -- so it is a frame the acquisition never covered and
        # nothing says so.  Where a whole run of built orbits reports the same
        # frame, it is the `frames` entry that has gone stale, not each of them
        #
        frame = ('' if error.frameRange is None else
                 f' {error.frameRange[0]}-{error.frameRange[1]}')
        return ('        already built, so segmentTrack never rewrites its '
                f'lines -- either the `frames` entry{frame} no longer fits '
                'this track, or this orbit needs a line of its own zeroing it '
                f'(<orbit>-<index>-{error.frameRange[0]}-0)')
    return ('        nothing is proposed for it, so it is checked against '
            '`frames` as it stands -- give it orbitframes lines by hand, or '
            'reach it with -firstdate/-lookBack')


def summarizeSetup(result, verb):
    '''Counts from one setupSeveralTopsImages pass'''
    made = len(result['setupFiles'])
    skipped = len(result['skipped'])
    print(f'    {made} piece{"" if made == 1 else "s"} {verb}, '
          f'{skipped} already built')
    if result['runfile'] is not None:
        print(f'    scripts listed in {result["runfile"]}')
    outOfRange = result.get('outOfRange')
    if outOfRange:
        #
        # The SLCs behind these were re-downloaded after the piece was cut and
        # came back covering less ground.  Nothing is wrong with the piece --
        # it exists and pairs -- but it could not be cut again from what is
        # there now, which is worth knowing before anything is deleted
        #
        shown = ' '.join(f'{outDir}({first}-{last})'
                         for outDir, first, last in outOfRange[:6])
        more = ('' if len(outOfRange) <= 6 else
                f' ... and {len(outOfRange) - 6} more')
        one = len(outOfRange) == 1
        print(f'    {len(outOfRange)} piece{"" if one else "s"} already built '
              f'{"is" if one else "are"} no longer covered by the SLCs now on '
              'disk, which have been re-downloaded since; left as '
              f'{"it is" if one else "they are"}, but '
              f'{"it" if one else "they"} could not be cut again: '
              f'{shown}{more}')
    noAscNode = result.get('noAscNode')
    if noAscNode:
        #
        # Cannot be dated, so cannot be framed or cut -- a partial assembly
        # leaves these behind.  Reported rather than passed over, since the
        # directory looks assembled, but not as a problem with the framing:
        # nothing is being proposed for it
        #
        shown = ' '.join(noAscNode[:8])
        more = ('' if len(noAscNode) <= 8 else
                f' ... and {len(noAscNode) - 8} more')
        print(f'    {len(noAscNode)} director'
              f'{"y has" if len(noAscNode) == 1 else "ies have"} no readable '
              f'ascending node time and cannot be dated, so nothing is set up '
              f'for {"it" if len(noAscNode) == 1 else "them"}: {shown}{more}')
    pending = result['pending']
    if pending:
        #
        # Scripts waiting on pieces the dates did not cover: nothing replaced
        # them, so they are left where they are rather than cleaned away
        #
        shown = ' '.join(pending[:8])
        more = '' if len(pending) <= 8 else f' ... and {len(pending) - 8} more'
        print(f'    {len(pending)} script'
              f'{"" if len(pending) == 1 else "s"} for pieces never built lie '
              f'outside these dates and were left alone: {shown}{more}')
    removed = result['removed']
    if removed:
        shown = ' '.join(removed[:8])
        more = '' if len(removed) <= 8 else f' ... and {len(removed) - 8} more'
        print(f'    removed {len(removed)} superseded file'
              f'{"" if len(removed) == 1 else "s"}: {shown}{more}')
    for runFile, outstanding in result['keptRunFiles']:
        #
        # An earlier run file is the only record of what it lists, so one
        # still naming unbuilt pieces this run does not cover is kept
        #
        shown = ' '.join(outstanding[:6])
        more = ('' if len(outstanding) <= 6 else
                f' ... and {len(outstanding) - 6} more')
        one = len(outstanding) == 1
        print(f'    kept {runFile}: {len(outstanding)} piece'
              f'{"" if one else "s"} it lists {"is" if one else "are"} '
              f'neither built nor in this run: {shown}{more}')


def setupWindow(args, built, acquisitions):
    '''
    Dates the setup step covers, as (firstDate, lastDate).

    SETUPWINDOWDAYS back from the last acquisition built, so what is checked is
    the stretch still being worked on.  -firstdate/-lastdate override it: they
    already say which acquisitions to consider, and giving one is how the whole
    record, or an older stretch of it, is deliberately taken in.
    '''
    if args.firstdate is not None:
        return args.firstDate, args.lastDate
    dates = [acq['date'] for acq in acquisitions if built.get(acq['orbit'])]
    if not dates:
        #
        # A track with nothing built yet is being set up from scratch, so
        # there is no recent stretch to narrow to
        #
        return args.firstDate, args.lastDate
    return max(dates) - timedelta(days=SETUPWINDOWDAYS), args.lastDate


def frameFrom(acquisitions, built, args, windowFirst, frames, orbframes):
    '''
    Where the derivation has to start, as an index into acquisitions.

    -lookBack says how many recent acquisitions to re-derive, but the setup
    step checks every acquisition in its own window, which reaches back
    further.  One of those that has never been framed is checked against an
    orbitframes that says nothing about it, and determineFraming then hands it
    every default `frames` entry -- entries its coverage usually cannot carry,
    since a track that has been reframed is normally one whose datatakes have
    moved.  So an acquisition nobody has looked at blocks the whole commit,
    and the way out is to frame it rather than to step over it.

    An acquisition already built is reached back to only where it has no
    lines the check can read -- `frames` is a default, and one it does not fit
    leaves the acquisition described by nothing (repairLines).  A built one
    the default does fit is left where it is, so the window widens only when
    there is something to write.

    Taking these in changes nothing about the acquisitions -lookBack already
    covered: referenceFraming carries a framing forward only from
    acquisitions that have been built, and those were candidates before this
    reached back to them.
    '''
    fromAcq = max(0, len(acquisitions) - args.lookBack)
    unframed = [iAcq for iAcq in range(fromAcq)
                if acquisitions[iAcq]['date'] >= windowFirst and
                not acquisitions[iAcq].get('noData') and
                (not built.get(acquisitions[iAcq]['orbit']) or
                 repairLines(acquisitions[iAcq], built, frames, orbframes))]
    return min(unframed) if unframed else fromAcq


def gapReport(slcDir, acquisitions, frames, orbframes):
    '''
    What a directory with a missing burst means for the pieces cut from it.

    Returns (runs, holes, framing, spanning): the sound stretches either side
    of the hole, the bursts that are not there, what this orbit is framed as,
    and the frames that run over a hole.  A frame clear of the hole is cut as
    it always was -- makeSetupFile takes the burst positions from the burst
    times, so a piece never slides past a hole it does not cross -- and a frame
    running over one cannot be cut at all.
    '''
    runs, _ = burstRuns(slcDir)
    holes = [burst for first, last in zip(runs, runs[1:])
             for burst in range(first[1] + 1, last[0])]
    orbit = int(slcDir.split('-')[0])
    framing = [frameRange for frameRange in
               determineFraming(orbit, 0, frames, orbframes)
               if frameRange[1] > 0]
    spanning = [frameRange for frameRange in framing
                if any(frameRange[0] <= hole <=
                       frameRange[0] + frameRange[1] - 1 for hole in holes)]
    return runs, holes, framing, spanning


def plotBurstGaps(reports):
    '''
    Draw the hole against the frames being cut, so the warning can be seen.

    One row per directory: the bursts it holds, the ones that are missing, and
    under them the frames this track cuts -- which is the whole question, since
    a frame either runs over the hole or it does not.  Drawn before the prompt
    and closed after it; a run with no display says so and carries on with the
    text.
    '''
    try:
        import matplotlib.pyplot as plt
    except ImportError as exception:
        print(f'    (no plot: {exception})')
        return None
    figure, axes = plt.subplots(figsize=(9, 1.8 + 0.9 * len(reports)))
    seen = set()

    def once(label):
        '''One legend entry per kind, however many rows draw it'''
        if label in seen:
            return None
        seen.add(label)
        return label

    for row, (slcDir, runs, holes, framing, spanning) in enumerate(reports):
        base = -row
        for first, last in runs:
            axes.plot([first, last], [base, base], linewidth=8,
                      solid_capstyle='butt', color='#9ecae1',
                      label=once('bursts held'))
        for hole in holes:
            axes.plot([hole, hole], [base - 0.42, base + 0.18],
                      linewidth=1.2, linestyle='--', color='#D55E00',
                      zorder=3)
            axes.plot([hole], [base], marker='x', markersize=10,
                      markeredgewidth=2.2, color='#D55E00', zorder=4,
                      label=once('missing'))
        for frameRange in framing:
            over = frameRange in spanning
            axes.plot([frameRange[0], frameRange[0] + frameRange[1] - 1],
                      [base - 0.3, base - 0.3], linewidth=6,
                      solid_capstyle='butt',
                      color='#D55E00' if over else '#31a354',
                      label=once('frame cut over the hole' if over
                                 else 'frame cut'))
    axes.set_yticks([-row for row in range(len(reports))])
    axes.set_yticklabels([report[0] for report in reports], fontsize=9)
    axes.set_ylim(-len(reports) + 0.4, 0.85)
    axes.set_xlabel('burst')
    axes.set_title('missing bursts against the frames being cut', fontsize=10)
    axes.legend(loc='upper center', frameon=False, fontsize=8, ncol=4,
                bbox_to_anchor=(0.5, 1.0))
    axes.grid(axis='x', color='#dddddd', linewidth=0.6)
    axes.set_axisbelow(True)
    for side in ('top', 'right', 'left'):
        axes.spines[side].set_visible(False)
    figure.tight_layout()
    try:
        plt.show(block=False)
        plt.pause(0.1)
    except Exception as exception:
        print(f'    (no plot shown: {exception})')
    return figure


def acceptBurstGaps(gappy, acquisitions, frames, orbframes, args):
    '''
    Report the directories a burst is missing from, and ask to go on.

    A missing burst used to stop the track outright.  It only matters where a
    frame is cut over it: the numbering steps over the hole and the burst
    positions are read back out of the burst times, so a piece either side of
    it is cut exactly as it always was.  That is a rare enough thing to be
    worth seeing rather than passing over, hence the report, the plot and the
    question -- and a run with no terminal to ask on stops, so nothing is
    built on the back of it unnoticed (-acceptGaps says yes in advance).
    '''
    reports, blocked = [], []
    for slcDir in gappy:
        runs, holes, framing, spanning = gapReport(slcDir, acquisitions,
                                                   frames, orbframes)
        reports.append((slcDir, runs, holes, framing, spanning))
        shown = ' '.join(str(hole) for hole in holes[:8])
        one = len(holes) == 1
        print(f'\n{BOLD}*** {slcDir}: burst{"" if one else "s"} {shown} '
              f'{"is" if one else "are"} missing from the assembled SLC'
              f'{PLAIN}')
        print('    the numbering steps over the hole, which is the record of '
              'what is not there; it is the frames cut over it that cannot be '
              'built')
        for frameRange in framing:
            first, last = frameRange[0], frameRange[0] + frameRange[1] - 1
            if frameRange in spanning:
                blocked.append(slcDir)
                print(f'    {ERROR}frame {first}-{last} runs over it and '
                      f'cannot be cut{PLAIN}')
            else:
                print(f'    frame {first}-{last} is clear of it and is cut as '
                      'it always was')
    figure = plotBurstGaps(reports)
    if args.acceptGaps:
        print('\n-acceptGaps: going on')
        answer = True
    else:
        answer = confirm('\ngo on with these acquisitions?')
    if figure is not None:
        try:
            import matplotlib.pyplot as plt
            plt.close(figure)
        except Exception:
            pass
    return answer


def plotFrom(acquisitions, built, start, nBuilt):
    '''
    Where the plot starts: `start`, backed up over nBuilt built acquisitions.

    The pieces already built are what the proposal is read against -- the
    ticks lining up down the plot is what a well framed track looks like --
    and the stretch being proposed for is by definition the stretch nothing
    has been built from, so on a track with a backlog it holds none of them.
    Backing up over as many built acquisitions as referenceFraming counts as
    neighbours puts the framing being carried forward on the screen.  They are
    only context: an acquisition already built is drawn faded and is never
    written, whatever the plot shows.
    '''
    found = start
    for iAcq in range(start - 1, -1, -1):
        if built.get(acquisitions[iAcq]['orbit']):
            found = iAcq
            nBuilt -= 1
            if nBuilt <= 0:
                break
    return found


def repairSlippedBursts(slcDirs):
    '''
    Renumber the burst times of the directories whose sequence is broken.

    A hole in the burst numbering is usually not a hole at all:
    computeBurstTimes used to round every burst on its own, and a swath whose
    times sit near a half-period boundary would number two bursts the same, or
    step over one.  Recomputing from the .tops_par files -- which is all the
    numbering was ever derived from -- puts it right.

    Done before the coverage is used, so a repaired acquisition is framed in
    the same run.  Only the directories given are touched.  Where recomputing
    changes nothing the times really do have a hole in them, and where it would
    move the first burst number the piece key would move with it, so both are
    reported and left alone.

    Returns the directories actually repaired.
    '''
    repaired, holes, keyed, failed = [], [], [], []
    for slcDir in slcDirs:
        try:
            preview = repairBurstTimes(slcDir, check=True)
        except (FileNotFoundError, ValueError) as exception:
            failed.append((slcDir, str(exception)))
            continue
        if not preview['changed']:
            holes.append(slcDir)
        elif preview['firstChanged']:
            keyed.append(slcDir)
        else:
            repaired.append((slcDir, repairBurstTimes(slcDir)))
    for slcDir, result in repaired:
        print(f'{BOLD}*** {slcDir}: the burst numbering was wrong and has been '
              f'corrected{PLAIN}')
        for name, note in result['notes']:
            print(f'    {name}: {note.strip()}')
        for name, correction in result['corrections']:
            print(f'    {name}: {correction.strip()}')
        for warning in result['warnings']:
            print(f'    swaths disagree: {warning.strip()}')
    for slcDir in holes:
        print(f'    {slcDir}: recomputing gives the same numbering, so the '
              'bursts really are missing -- not a numbering slip; only the '
              'frames cut over the hole are stopped by it')
    for slcDir in keyed:
        print(f'{BOLD}    {slcDir}: recomputing moves the first burst number, '
              f'which is the piece key, so it has been left alone -- run '
              f'computeBurstTimes.py there by hand if that is what you '
              f'want{PLAIN}')
    for slcDir, why in failed:
        print(f'    {slcDir}: cannot recompute the burst times ({why})')
    return [slcDir for slcDir, _ in repaired]


def setupStep(args, proposal, orbits, orbframes, origFrames, acquisitions,
              built, window):
    '''
    Run setupSeveralTopsImages over the framing just proposed.

    A dry pass first, over the whole track, because setupSeveralTopsImages on
    its own aborts on the first bad frame and everything behind it stays
    hidden.  Nothing is written while anything is wrong -- not orbitframes, not
    the scripts -- since a framing the setup step cannot realize is not one to
    commit.  What to do about a failure is left to the user for now: the run
    reports it, says which kind it is (whyBlocked), and stops.

    Returns (ok, write): ok is False if the check failed, and write is the call
    that commits and builds the scripts, or None where there is nothing left to
    do -- either it has just been done under -commit, or the check failed.  The
    plot hands `write` to its commit button, so what the plot commits is the
    framing that was just checked.
    '''
    merged = mergedOrbframes(proposal, orbits, orbframes, built,
                             len(origFrames))
    firstDate, lastDate = window
    since = ('' if args.firstdate is not None else
             f' (acquisitions since {firstDate:%Y-%m-%d}, '
             f'-firstdate reaches further back)')
    print(f'\nChecking the proposed framing with '
          f'setupSeveralTopsImages{since}:')
    dry = setupSeveralImages(check=True, firstDate=firstDate,
                             lastDate=lastDate, frames=origFrames,
                             orbframes=merged, strict=False, quiet=True,
                             requireSLCs=True)
    summarizeSetup(dry, 'would be set up')
    if dry['errors']:
        count = len(dry['errors'])
        print(f'\n{ERROR}*** setupSeveralTopsImages reports {count} '
              f'problem{"" if count == 1 else "s"} with this framing:{PLAIN}')
        for error in dry['errors']:
            print(describeError(error))
            print(whyBlocked(error, orbits, built))
        print(f'\n{ERROR}nothing written -- neither orbitframes nor any setup '
              f'script.{PLAIN} Fix `frames` or `orbitframes` and run again.')
        return False, None

    def write():
        '''Commit the entries just checked, and build their setup scripts'''
        commitEntries(proposal, orbits, acquisitions, built, orbframes,
                      len(origFrames))
        if args.noSetup:
            print('\n-noSetup: orbitframes written, no setup scripts')
            return True
        print('\nRunning setupSeveralTopsImages:')
        run = setupSeveralImages(check=False, firstDate=firstDate,
                                 lastDate=lastDate, frames=origFrames,
                                 orbframes=merged, strict=False,
                                 cleanStale=True, quiet=True,
                                 requireSLCs=True)
        summarizeSetup(run, 'set up')
        #
        # The dry pass passed, so anything here is setuptopsimage itself
        # failing
        #
        for error in run['errors']:
            print(describeError(error))
        return not run['errors']

    if args.commit:
        return write(), None
    return True, write


def undoCommit(prompt=True):
    '''
    Restore the newest orbitframes backup, and drop it from the stack.

    What is being put back is stated before anything is written, since the
    stack goes back as far as the commits do and the newest backup is only
    this session's work if this session is what last committed.
    '''
    backup = newestBackup('orbitframes.bak.*')
    if backup is None:
        print(f'no {BACKUPDIR}/orbitframes.bak.* backup -- nothing to undo')
        return
    when = backupTime(backup)
    print(f'{BOLD}-undo puts back the orbitframes as it stood at '
          f'{when:%Y-%m-%d %H:%M:%S}, {describeAge(when)}{PLAIN}')
    print(f'    everything committed since then is discarded ({backup})')
    if prompt and not confirm('    restore it?'):
        print('    orbitframes left as it is')
        return
    atomicCopy(backup, 'orbitframes')
    os.remove(backup)
    print(f'orbitframes restored from {backup}')
    remaining = len(glob.glob(f'{BACKUPDIR}/orbitframes.bak.*'))
    if remaining:
        print(f'    {remaining} earlier backup'
              f'{"" if remaining == 1 else "s"} left -- run -undo again to '
              'step further back')


def builtStarts():
    '''
    Pieces that were actually built, from the <orbit>_<firstBurst> directories.

    This is the ground truth for what the existing files produced, and is more
    reliable than re-expanding them, because some old entries refer to bursts
    the current SLCs no longer contain (the data was re-downloaded since).
    '''
    built = defaultdict(set)
    for outDir in glob.glob('*_*'):
        if not os.path.isdir(outDir):
            continue
        orbitStr, _, burstStr = outDir.partition('_')
        if orbitStr.isdigit() and burstStr.isdigit():
            built[int(orbitStr)].add(int(burstStr))
    return built


def compareStarts(acquisitions, frames, orbframes, proposal, built,
                  window=None, origFrames=None):
    '''
    Compare the proposal against the existing files on the pairing keys.

    Only firstBurst matters -- nBursts does not affect the directory name and
    so cannot affect pairing.
    '''
    rows = []
    for iAcq, acq in enumerate(acquisitions):
        if window is not None and iAcq < window:
            continue
        orbit, runs = acq['orbit'], acq['runs']
        #
        # The existing column has to be expanded through the `frames` file as
        # written, not the segmentation derived from the overrides, or it
        # would not describe what is actually on disk
        #
        truth = realizedStarts(orbit, runs,
                               frames if origFrames is None else origFrames,
                               orbframes)
        mine = set()
        lengths = {}
        #
        # The pieces derived for this acquisition, plus anything appended to
        # it as a mate.  A built acquisition is derived for the comparison but
        # only its appended pieces would ever be written (proposedEntries)
        #
        for first, nBursts in (proposal.pieces.get(orbit, []) +
                               proposal.appended.get(orbit, [])):
            if usableRun(runs, first, first + nBursts - 1) is not None:
                mine.add(first)
                lengths[first] = nBursts
        onDisk = built.get(orbit, set())
        truthSet = set(truth)
        diskLengths = builtLengths(orbit, acq['runs'],
                                   frames if origFrames is None
                                   else origFrames, orbframes)
        #
        # One row per <orbit>-<seq> directory, not per orbit.  Each is a
        # separate SLC that a piece has to be cut from, so showing its own
        # coverage and its own pieces is what makes a split orbit readable.
        #
        for slcDir, dirRuns in sorted(acq['dirs']):
            inDir = {x for x in onDisk
                     if usableRun(dirRuns, x,
                                  x + diskLengths.get(x, 1) - 1) is not None}
            mineDir = {x for x in mine
                       if usableRun(dirRuns, x,
                                    x + lengths[x] - 1) is not None}
            truthDir = {x for x in truthSet
                        if usableRun(dirRuns, x,
                                     x + truth[x] - 1) is not None}
            rows.append({'orbit': orbit, 'label': slcDir, 'date': acq['date'],
                         'truth': truthDir, 'mine': mineDir, 'built': inDir,
                         'lengths': lengths, 'truthLengths': truth,
                         'diskLengths': diskLengths, 'runs': dirRuns,
                         'pctDisk': percentIncluded(inDir, diskLengths,
                                                    dirRuns),
                         'pctTruth': percentIncluded(truthDir, truth, dirRuns),
                         'pctMine': percentIncluded(mineDir, lengths, dirRuns),
                         'extra': mineDir - truthDir,
                         'missing': truthDir - mineDir,
                         'extraDisk': mineDir - inDir,
                         'missingDisk': inDir - mineDir})
    return rows


def chunkPattern(acq, tol):
    '''
    How an acquisition is chunked into datatakes, to within tol bursts.

    Boundaries are quantised so that the routine jitter of a burst or two
    does not read as a different chunking.
    '''
    return tuple((round(first / tol), round(last / tol))
                 for first, last in acq['runs'])


def chunkingChanged(acquisitions, window, tol):
    '''
    Recent acquisitions whose chunking is not shared by any other.

    Where the datatakes keep to a pattern the track uses repeatedly, the
    segmentation built up by hand is a good guide: the acquisitions sharing a
    pattern pair with each other.  What cannot be framed from history is a
    chunking that stands alone -- there is nothing for it to pair with, so it
    needs its own treatment.

    Recurrence is the test rather than age.  A pattern that only started a
    year ago but has held since is established, and comparing the recent
    window against all earlier history would wrongly call it new.
    '''
    recent = acquisitions[-window:]
    counts = Counter(chunkPattern(acq, tol) for acq in recent)
    for acq in acquisitions[:-window]:
        pattern = chunkPattern(acq, tol)
        if pattern in counts:
            counts[pattern] += 1
    return [acq for acq in recent if counts[chunkPattern(acq, tol)] < 2]


def countPairs(startsByAcq, acquisitions, sixDay=7, twelveDay=13):
    '''
    Pairs a segmentation would produce, which is what it is all for.

    A pair forms between the next two acquisitions carrying the same
    firstBurst, so for each key the acquisitions holding it are walked in date
    order and consecutive gaps are counted -- up to sixDay days in one column
    and up to twelveDay in the other.  The gaps are not only multiples of 6 or
    12: with S1A, S1C and S1D all operating (April-June 2026) pairs a day
    apart turn up, and they count in the first column.  Agreement with what was built by
    hand is only a sanity check; this is the number being maximised.
    '''
    byKey = defaultdict(list)
    for iAcq, acq in enumerate(acquisitions):
        for start in startsByAcq[iAcq]:
            byKey[start].append(acq['date'])
    six = twelve = 0
    for dates in byKey.values():
        for earlier, later in zip(sorted(dates), sorted(dates)[1:]):
            days = (later - earlier).days
            if days <= sixDay:
                six += 1
            elif days <= twelveDay:
                twelve += 1
    return six, twelve


def framePairs(first, last, acquisitions, sixDay=7, twelveDay=13):
    '''Pairs one candidate frame would produce across these acquisitions'''
    dates = [acq['date'] for acq in acquisitions
             if usableRun(acq['runs'], first, last) is not None]
    six = twelve = 0
    for earlier, later in zip(dates, dates[1:]):
        days = (later - earlier).days
        if days <= sixDay:
            six += 1
        elif days <= twelveDay:
            twelve += 1
    return six, twelve, len(dates)


def percentIncluded(starts, lengths, runs):
    '''
    Percentage of the available bursts the pieces actually take in.

    Counted as a union, so the overlap between neighbouring pieces is not
    double counted, and measured against everything the acquisition holds --
    so a segmentation that cannot reach some of the data shows below 100.
    '''
    available = set()
    for first, last in runs:
        available.update(range(first, last + 1))
    if not available:
        return None
    covered = set()
    for start in starts:
        covered.update(range(start, start + lengths.get(start, 1)))
    return 100. * len(covered & available) / len(available)


#
# Okabe-Ito, in fixed order.  Colourblind-safe and assigned by sequence number
# rather than cycled, so a sequence keeps its colour from one track to the next
#
SEQCOLOURS = ['#0072B2', '#E69F00', '#009E73', '#D55E00',
              '#56B4E9', '#CC79A7', '#8C6D31', '#000000']
#
# Alternating fill so that pieces cut back to back stay separable: where two
# abut, a shared edge alone reads as one long piece
#
SEGMENTHATCH = ['', '///', '...', '\\\\']
#
# Height of a band of segments where nothing in it doubles, and the height and
# spacing of the lanes used where something does.  Only a row that doubles is
# thinned to make room: the lanes are there to separate two pieces, and
# thinning every row for a lane that is usually empty loses more than it gains
#
BANDHEIGHT = 0.26
LANEHEIGHT = 0.18
LANESTEP = 0.21


def assignLanes(spans):
    '''
    Lane for each span, so that spans covering the same bursts are separated.

    Neighbouring pieces meet on a shared burst by design, so a one-burst seam
    stays on the same lane; anything wider is the same ground cut twice and
    goes on the next lane up.  Taken and returned in sorted order.
    '''
    lanes, ends = [], []
    for first, last in sorted(spans):
        for lane, end in enumerate(ends):
            if first >= end:
                ends[lane] = last
                lanes.append(lane)
                break
        else:
            ends.append(last)
            lanes.append(len(ends) - 1)
    return lanes


def plotTrack(acquisitions, built, frames, origFrames, orbframes,
              proposal, args, onCommit=None):
    '''
    Show the track: burst along x, orbit up y, one line per datatake.

    Each <orbit>-<seq> directory draws its burst coverage, coloured by its
    sequence number, so a split datatake is visible as two lines of different
    colour on the same row.  The pieces already built are drawn over the top,
    darker and thinner, with a tick at each start -- the start is the pairing
    key, so the ticks lining up down the plot is what a well framed track
    looks like.

    onCommit is the write step of a run that has not committed: the plot is
    where the proposal is judged, so the answer is given there rather than by
    reading the plot, closing it and running the whole thing again with
    -commit.  Returns what it returned, or None if it was never pressed.
    '''
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.widgets import Button

    figure, axes = plt.subplots(figsize=(11, 8.5))
    seqs = sorted({int(slcDir.split('-')[1])
                   for acq in acquisitions for slcDir, _ in acq['dirs']})
    colours = {seq: SEQCOLOURS[i % len(SEQCOLOURS)]
               for i, seq in enumerate(seqs)}
    #
    # Rows are evenly spaced in date order and labelled with the orbit.  A
    # linear orbit axis is unreadable, because the satellites number their
    # orbits in ranges tens of thousands apart and the rows collapse together.
    #
    width = max(0.6, min(5.0, 260. / max(len(acquisitions), 1)))
    seen = set()
    for row, acq in enumerate(acquisitions):
        orbit = acq['orbit']
        for slcDir, dirRuns in sorted(acq['dirs']):
            seq = int(slcDir.split('-')[1])
            for first, last in dirRuns:
                axes.plot([first, last], [row, row], linewidth=width * 3,
                          solid_capstyle='butt', alpha=0.30,
                          color=colours[seq],
                          label=f'-{seq}' if seq not in seen else None)
                seen.add(seq)
        #
        # What was built sits above the row, what would be cut sits below, so
        # the two can be read against each other and against the coverage
        #
        lengths = builtLengths(orbit, acq['runs'], origFrames,
                               orbframes)
        pieces = [(first, nBursts) for first, nBursts
                  in (proposal.pieces.get(orbit, []) +
                      proposal.appended.get(orbit, []) +
                      proposal.hypothetical.get(orbit, []))
                  if usableRun(acq['runs'], first, first + nBursts - 1)]
        #
        # What was built sits above the coverage, what would be cut sits
        # below, each growing away from it so the two stay apart.  A piece
        # covering bursts another piece of the same acquisition already covers
        # is drawn on a lane of its own rather than on top of it: the ground
        # is cut twice either way, and two rectangles superimposed only look
        # like one
        #
        #
        # Every acquisition is framed, and seeing that against what was built
        # is the point of the plot.  Only the ones not yet built would
        # actually be written, though, and a faded band on its own does not
        # say which of the two reasons it is, so each is named: an acquisition
        # already built keeps its own lines, and one whose SLCs have been
        # cleaned away has nothing left to cut whatever its lines say.  Where
        # both are true the band above the row already says it was built, so
        # the one that is not obvious is the one named.
        #
        if acq.get('noData'):
            label, colour, opacity = ('proposed, SLCs cleaned away',
                                      '#777777', 0.45)
        elif built.get(orbit):
            label, colour, opacity = 'proposed, already built', '#C2185B', 0.35
        else:
            label, colour, opacity = 'proposed', '#C2185B', 1.0
        bands = [('built', '#404040', 1, 1.0,
                  [(start, start + lengths[start] - 1)
                   for start in sorted(built.get(orbit, ()))
                   if lengths.get(start)]),
                 (label, colour, -1, opacity,
                  [(first, first + nBursts - 1)
                   for first, nBursts in sorted(pieces)])]
        for label, colour, side, opacity, spans in bands:
            lanes = assignLanes(spans)
            #
            # A band with nothing doubled in it is drawn exactly as it always
            # was, full height in its own place
            #
            height = LANEHEIGHT if max(lanes, default=0) else BANDHEIGHT
            for i, ((first, last), lane) in enumerate(zip(sorted(spans),
                                                          lanes)):
                base = (row + 0.06 + lane * LANESTEP if side > 0 else
                        row - 0.06 - height - lane * LANESTEP)
                axes.add_patch(Rectangle(
                    (first, base), last - first, height, alpha=opacity,
                    facecolor=colour, edgecolor='white', linewidth=0.7,
                    hatch=SEGMENTHATCH[i % len(SEGMENTHATCH)], zorder=3,
                    label=label if label not in seen else None))
                seen.add(label)
                if lane:
                    #
                    # Outlined as well as offset, so the doubling reads at a
                    # glance without having to compare the two lanes
                    #
                    axes.add_patch(Rectangle(
                        (first, base), last - first, height,
                        facecolor='none', edgecolor='#D55E00', linewidth=1.1,
                        zorder=4, label='same bursts twice'
                        if 'twice' not in seen else None))
                    seen.add('twice')
        for start in sorted(built.get(orbit, ())):
            if not lengths.get(start):
                #
                # Built, but nothing records how far it ran
                #
                axes.plot([start], [row + 0.19], marker='|', markersize=5,
                          color='#404040', zorder=3)
    #
    # The frames themselves -- where the track is being cut
    #
    for level, (first, nBursts) in enumerate(sorted(frames)):
        axes.axvline(first, color='#C2185B', linewidth=0.8, alpha=0.45,
                     linestyle='--', zorder=0)
        axes.annotate(f'{first}', xy=(first, 1.004 + 0.022 * (level % 2)),
                      xycoords=('data', 'axes fraction'), ha='center',
                      va='bottom', fontsize=7, color='#C2185B')
    step = max(1, len(acquisitions) // 25)
    axes.set_yticks(range(0, len(acquisitions), step))
    axes.set_yticklabels([f'{acquisitions[r]["orbit"]}'
                          for r in range(0, len(acquisitions), step)],
                         fontsize=8)
    axes.set_ylim(-1, len(acquisitions))
    axes.set_xlabel('burst')
    axes.set_ylabel('orbit (rows in date order)')
    axes.set_title(f'{os.path.basename(os.getcwd())}: '
                   f'{len(acquisitions)} acquisitions, '
                   f'{acquisitions[0]["date"]:%Y-%m-%d} to '
                   f'{acquisitions[-1]["date"]:%Y-%m-%d}')
    axes.grid(axis='x', color='#dddddd', linewidth=0.6)
    axes.set_axisbelow(True)
    for side in ('top', 'right'):
        axes.spines[side].set_visible(False)
    axes.legend(title='datatake', loc='upper left', frameon=False,
                bbox_to_anchor=(1.01, 1.0))
    figure.tight_layout()
    if args.plotFile:
        figure.savefig(args.plotFile, dpi=140)
        print(f'\nplot written to {args.plotFile}')
        return None
    outcome = {}
    if onCommit is not None:
        #
        # Figure coordinates, added after tight_layout so the plot itself is
        # laid out as it always was
        #
        figure.subplots_adjust(bottom=0.14)
        commitButton = Button(figure.add_axes([0.72, 0.02, 0.13, 0.05]),
                              'commit', color='#F8BBD0',
                              hovercolor='#C2185B')
        exitButton = Button(figure.add_axes([0.86, 0.02, 0.10, 0.05]),
                            'exit', color='#e8e8e8', hovercolor='#bbbbbb')

        def onCommitPressed(event):
            #
            # Closing afterwards puts the terminal back in front, which is
            # where what was written is reported
            #
            commitButton.label.set_text('committing')
            figure.canvas.draw_idle()
            outcome['ok'] = onCommit()
            plt.close(figure)

        def onExitPressed(event):
            plt.close(figure)

        commitButton.on_clicked(onCommitPressed)
        exitButton.on_clicked(onExitPressed)
        figure.text(0.02, 0.02, 'commit writes the entries just checked and '
                                'builds their setup scripts', fontsize=8,
                    color='#666666')
    plt.show()
    return outcome.get('ok')


def pct(value):
    '''Render a percentage, or a dash where there is nothing to measure'''
    return '-' if value is None else f'{value:.0f}%'


def startsWithLengths(starts, lengths):
    '''Render a set of starts as start(nBursts), where the length is known'''
    return ','.join(f'{s}({lengths[s]})' if s in lengths else f'{s}(?)'
                    for s in sorted(starts))


def printComparison(rows, label, showOnly=False):
    '''
    Print the per-orbit comparison table and a summary.

    The summary always counts every row; showOnly limits what is printed.
    Scored twice: against the expanded frames+orbitframes, and against the
    <orbit>_<firstBurst> directories that were actually built.
    '''
    print(f'\n{label}')
    print(f'{"date":11s} {"orbit-seq":>9s}  {"available":15s}  '
          f'{"onDisk  start(nBursts)":26s} {"%":>4s}  '
          f'{"proposed  start(nBursts)":26s} {"%":>4s}  verdict')
    counts = {'exact': 0, 'extra': 0, 'missing': 0,
              'exactDisk': 0, 'extraDisk': 0, 'missingDisk': 0}
    for row in rows:
        ok = not row['extra'] and not row['missing']
        counts['exact'] += ok
        counts['extra'] += len(row['extra'])
        counts['missing'] += len(row['missing'])
        okDisk = not row['extraDisk'] and not row['missingDisk']
        counts['exactDisk'] += okDisk
        counts['extraDisk'] += len(row['extraDisk'])
        counts['missingDisk'] += len(row['missingDisk'])
        if showOnly and ok:
            continue
        bits = []
        if row['extra']:
            bits.append('extra ' +
                        ','.join(str(x) for x in sorted(row['extra'])))
        if row['missing']:
            bits.append('missing ' +
                        ','.join(str(x) for x in sorted(row['missing'])))
        verdict = '; '.join(bits) if bits else 'match'
        #
        # onDisk is what was built; the orbitframes expansion almost always
        # agrees with it, so it is only mentioned when it does not
        #
        if row['truth'] != row['built']:
            verdict += ('  [orbitframes expands to '
                        f'{",".join(str(x) for x in sorted(row["truth"]))}]')
        available = ','.join(f'{a}-{b}' for a, b in row['runs'])
        print(f'{row["date"].strftime("%Y-%m-%d"):11s} {row["label"]:>9s}  '
              f'{available:15s}  '
              f'{startsWithLengths(row["built"], row["diskLengths"]):26s} '
              f'{pct(row["pctDisk"]):>4s}  '
              f'{startsWithLengths(row["mine"], row["lengths"]):26s} '
              f'{pct(row["pctMine"]):>4s}  {verdict}')
    #
    # The point of the exercise: what each segmentation would actually pair
    #
    byAcq = defaultdict(lambda: (set(), set()))
    order = []
    for row in rows:
        if row['orbit'] not in byAcq:
            order.append(row)
        disk, mine = byAcq[row['orbit']]
        byAcq[row['orbit']] = (disk | row['built'], mine | row['mine'])
    acqs = [{'date': r['date'], 'orbit': r['orbit']} for r in order]
    diskSix, diskTwelve = countPairs(
        [byAcq[a['orbit']][0] for a in acqs], acqs)
    mineSix, mineTwelve = countPairs(
        [byAcq[a['orbit']][1] for a in acqs], acqs)
    print(f'\npairs formed   built: {diskSix} within a week + {diskTwelve} '
          f'within two = {diskSix + diskTwelve}'
          f'   proposed: {mineSix} + {mineTwelve} = {mineSix + mineTwelve}')
    n = max(len(rows), 1)
    print(f'vs frames+orbitframes: {counts["exact"]}/{len(rows)} orbits '
          f'match ({100. * counts["exact"] / n:.1f}%), '
          f'{counts["extra"]} extra, {counts["missing"]} missing starts')
    print(f'vs directories on disk: {counts["exactDisk"]}/{len(rows)} orbits '
          f'match ({100. * counts["exactDisk"] / n:.1f}%), '
          f'{counts["extraDisk"]} extra, {counts["missingDisk"]} missing')
    return counts


def assembledDirs(directory):
    '''
    Names of the assembled <orbit>-<seq> directories in directory.

    The same acceptance test as getAcquisitions -- the setupSeveralTopsImages
    patterns, plus a digit orbit and sequence.  That is what rejects the odd
    hand-made entry that turns up in some track directories (a stray `ln`, an
    old .metalink), which must not be taken for an assembled directory when
    the assembly directory is being inferred from the links.
    '''
    names = set()
    for pattern in SLCPATTERNS:
        for path in glob.glob(os.path.join(directory, pattern)):
            name = os.path.basename(path)
            orbitStr, _, seqStr = name.partition('-')
            if orbitStr.isdigit() and seqStr.isdigit():
                names.add(name)
    return sorted(names)


def findAssemblyDir(args):
    '''
    The assembly track directory holding the <orbit>-<seq> directories.

    -assemblyDir wins, then the project.yaml assemblyDir key joined with this
    track's name, and failing both the existing links are followed back to
    where they point.  Inference is the normal case: a track directory that
    has been linked once already records its own answer.

    Where the links point into more than one place, the project.yaml
    defaultAssemblyDirectory key settles it if one of them is under that key.
    Unlike assemblyDir it is not an answer on its own -- it only says which of
    the directories a track is actually linked into is the current one.
    '''
    trackName = os.path.basename(os.path.abspath('.'))
    if args.assemblyDir is not None:
        return args.assemblyDir
    projectYaml = nearestProjectYaml()
    fromYaml = projectYaml.get('assemblyDir')
    if fromYaml:
        return os.path.join(fromYaml, trackName)
    #
    # Different links can name different paths for the same place -- some of
    # track-112's go through insar8 and some through insar9, which is itself a
    # link to insar8 -- so they are resolved before being compared.  A link
    # whose target has gone is ignored rather than counted as a second root:
    # it says nothing reliable about where the assembly directory is now, and
    # one of them should not be able to stop the whole run.
    #
    roots = defaultdict(list)
    for name in assembledDirs('.'):
        if os.path.islink(name) and os.path.isdir(name):
            target = os.path.realpath(name)
            roots[os.path.dirname(target)].append(os.stat(target).st_mtime)
    if not roots:
        sys.exit('*** no <orbit>-<seq> links to infer the assembly directory '
                 'from -- pass -assemblyDir')
    if len(roots) == 1:
        return next(iter(roots))
    #
    # A track that has been moved between volumes over the years links into
    # both, so there is no single answer to infer.  defaultAssemblyDirectory
    # says which volume the project is on now; without it the most recently
    # assembled one is taken, since new assemblies land wherever the last one
    # did and that is the only place -refreshLinks has anything to find.  Loud
    # rather than fatal, since stopping here means the track cannot be
    # refreshed at all, and -assemblyDir says otherwise where it picks wrong.
    #
    fromDefault = projectYaml.get('defaultAssemblyDirectory')
    wanted = (os.path.join(fromDefault, trackName) if fromDefault else None)
    if wanted in roots:
        chosen, why = wanted, 'the project.yaml defaultAssemblyDirectory'
    else:
        chosen = max(roots,
                     key=lambda root: (max(roots[root]), len(roots[root])))
        why = 'the one assembled most recently'
    print(f'{BOLD}*** the existing links point into more than one assembly '
          f'directory; taking {why} -- pass -assemblyDir to use '
          f'another:{PLAIN}')
    for root in sorted(roots, key=lambda root: -max(roots[root])):
        newest = datetime.fromtimestamp(max(roots[root]))
        print(f'    {"-->" if root == chosen else "   "} {root} '
              f'({len(roots[root])} links, newest {newest:%Y-%m-%d})')
    return chosen


def refreshLinks(args):
    '''
    Link the assembled directories that have no link in this track directory.

    setupTrack builds <orbit>-<seq> under the assembly directory and nothing
    links it back here, so segmentTrack cannot see an acquisition until the
    link is made.  Only directories assembled within maxLinkAgeDays are taken:
    where a track was cut short years ago its assembled directories were
    deliberately never linked, and restoring those would put long-dead
    acquisitions back into the record.  -linkAll ignores the age.

    The age is taken from the mtime of the assembled directory -- when it was
    put together -- rather than from the acquisition date, so a run over ten
    year old data for a new region links normally.

    A directory whose SLCs have been cleaned away is never linked whatever its
    age -- see hasSwathSLCs().
    '''
    assemblyDir = findAssemblyDir(args)
    if not os.path.isdir(assemblyDir):
        sys.exit(f'*** assembly directory {assemblyDir} does not exist')
    print(f'\nlinking assembled directories from {assemblyDir}')
    cutoff = datetime.now().timestamp() - args.maxLinkAgeDays * 86400
    created, tooOld, noSLC = [], [], []
    for name in assembledDirs(assemblyDir):
        #
        # lexists, not exists: a link whose target has gone is still a link
        # that was made on purpose, and must not be replaced
        #
        if os.path.lexists(name):
            continue
        source = os.path.join(assemblyDir, name)
        if not args.linkAll and os.path.getmtime(source) < cutoff:
            tooOld.append(name)
            continue
        #
        # After the age test, so the directories are only opened when they
        # would otherwise be linked
        #
        if not hasSwathSLCs(source):
            noSLC.append(name)
            continue
        os.symlink(source, name)
        created.append((name, source))
    for name, source in created:
        ascFile = f'{source}/ascendingNodeTime'
        when = (parseAscNodeTime(ascFile).strftime('%Y-%m-%d')
                if os.path.isfile(ascFile) else 'no ascendingNodeTime')
        print(f'    linked {name:12s} {when}')
    if tooOld:
        print(f'    {len(tooOld)} assembled '
              f'director{"y" if len(tooOld) == 1 else "ies"} older than '
              f'{args.maxLinkAgeDays} days not linked '
              '(use -linkAll to include them)')
    if noSLC:
        #
        # Reported rather than passed over quietly: an acquisition missing
        # from the proposal because its SLCs were cleaned away looks the same
        # as one that was never assembled
        #
        shown = ' '.join(noSLC[:8])
        more = '' if len(noSLC) <= 8 else f' ... and {len(noSLC) - 8} more'
        print(f'    {len(noSLC)} assembled '
              f'director{"y" if len(noSLC) == 1 else "ies"} hold only '
              'metadata, their SLCs having been cleaned away, and cannot be '
              f'built from: {shown}{more}')
    if not created:
        print('    nothing new to link')
        return
    os.makedirs(BACKUPDIR, exist_ok=True)
    manifest = f'{BACKUPDIR}/links.{stamp()}'
    atomicWrite(manifest, [f'{name} {source}' for name, source in created])
    print(f'    {len(created)} link{"" if len(created) == 1 else "s"} created '
          f'-- -undoLinks removes them ({manifest})')


def undoLinks():
    '''Remove the links made by the last -refreshLinks'''
    manifest = newestBackup('links.*')
    if manifest is None:
        print(f'no {BACKUPDIR}/links.* record -- no links to undo')
        return
    removed, kept = [], []
    with open(manifest, 'r') as fin:
        for line in fin:
            name, _, source = line.strip().partition(' ')
            if not name:
                continue
            #
            # Only remove a link that is still the one that was made.  One
            # repointed by hand since then is somebody's deliberate change.
            #
            if os.path.islink(name) and os.readlink(name) == source:
                os.unlink(name)
                removed.append(name)
            elif os.path.lexists(name):
                kept.append(name)
    os.remove(manifest)
    print(f'removed {len(removed)} link{"" if len(removed) == 1 else "s"} '
          f'recorded in {manifest}')
    if kept:
        print(f'    {len(kept)} left alone, changed since being made: '
              + ' '.join(kept))


def processArgs():
    parser = argparse.ArgumentParser(
        epilog='Part of the s1setup package.',
        description='Propose orbitframes entries from burst coverage. Writes '
                    'nothing unless -commit, -refreshLinks, -undo, '
                    '-undoNoPrompt or -undoLinks is given.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-lookBack', '--lookBack', type=int, default=12,
                        help='re-derive the last N acquisitions, ignoring '
                             'their existing orbitframes entries, and print '
                             'the comparison; 0 instead proposes for the '
                             'acquisitions not yet framed.  An older '
                             'acquisition the setup step checks and nothing '
                             'has framed is taken in whatever N says, since '
                             'it would otherwise block the commit')
    parser.add_argument('-compare', '--compare', action='store_true',
                        help='re-derive the whole record and compare against '
                             'the existing orbitframes')
    parser.add_argument('-firstdate', '--firstdate', default=None,
                        help='only consider acquisitions on/after YYYY:MM:DD')
    parser.add_argument('-lastdate', '--lastdate', default=None,
                        help='only consider acquisitions on/before YYYY:MM:DD')
    parser.add_argument('-minBursts', '--minBursts', type=int, default=4,
                        help='shortest piece worth building')
    parser.add_argument('-mateSpan', '--mateSpan', type=int, default=2,
                        help='acquisitions each side to give matching pieces')
    parser.add_argument('-maxMateDays', '--maxMateDays', type=int, default=13,
                        help='furthest a matching piece is worth adding where '
                             'there is intervening data to pair with instead, '
                             'which covers the 6- and 12-day pairs')
    parser.add_argument('-maxBursts', '--maxBursts', type=int,
                        default=DEFAULTMAXBURSTS,
                        help='longest piece worth cutting; a run longer than '
                             'this is broken into equal pieces sharing a '
                             'burst at each seam')
    parser.add_argument('-boxOverrun', '--boxOverrun', type=int, default=5,
                        help='how far a piece invented here may run past a '
                             'velocityStats box boundary before it is broken '
                             'there instead')
    parser.add_argument('-refSearch', '--refSearch', type=int, default=40,
                        help='how many older built acquisitions to consider '
                             'where the recent ones frame little of this one; '
                             'the best framing of its data wins')
    parser.add_argument('-maxPairDays', '--maxPairDays', type=int, default=36,
                        help='longest gap that still pairs where no data lies '
                             'between, so a missed cycle or two still forms '
                             'the 24- and 36-day pairs; beyond it no pair is '
                             'attempted')
    parser.add_argument('-refBack', '--refBack', type=int, default=3,
                        help='how many built acquisitions back count as this '
                             'one\'s neighbours; three covers S1A, S1C and '
                             'S1D interleaving, so a coverage that comes '
                             'round every third acquisition still finds its '
                             'match.  An older acquisition can still supply '
                             'the framing where none of these covers the '
                             'same bursts')
    parser.add_argument('-snapTol', '--snapTol', type=int, default=3,
                        help='burst jitter treated as the same segmentation '
                             'rather than a new one; also how far two '
                             'lengths for one key may differ before the '
                             'shorter is taken and the surplus given its own '
                             'piece')
    parser.add_argument('-deriveWindow', '--deriveWindow', type=int,
                        default=40,
                        help='how many recent acquisitions the report '
                             'describes as the current state of the track')
    parser.add_argument('-dump', '--dump', action='store_true',
                        help='print the proposed entries for the whole '
                             'record, as a complete replacement orbitframes')
    parser.add_argument('-plot', '--plot', action='store_true',
                        help='show the track as a plot: burst along x, orbit '
                             'up y, a line per datatake coloured by sequence, '
                             'with the pieces already built drawn over it')
    parser.add_argument('-plotFile', '--plotFile', default=None,
                        help='save the plot to this file instead of showing '
                             'it')
    parser.add_argument('-quiet', '--quiet', action='store_true',
                        help='comparison summary only, no per-orbit table')
    parser.add_argument('-commit', '--commit', action='store_true',
                        help='write the proposed entries into orbitframes, '
                             'replacing the lines of the orbits proposed and '
                             'leaving every other line alone; the previous '
                             f'version is saved in {BACKUPDIR}/')
    parser.add_argument('-noSetup', '--noSetup', action='store_true',
                        help='stop after orbitframes: the proposed framing is '
                             'still checked with setupSeveralTopsImages, but '
                             'no setup_ scripts and no runSetup driver are '
                             'written')
    parser.add_argument('-acceptGaps', '--acceptGaps', action='store_true',
                        help='go on without asking where a burst is missing '
                             'from an assembled SLC; the frames clear of the '
                             'hole are cut as they always were, the ones over '
                             'it still cannot be built')
    parser.add_argument('-undo', '--undo', action='store_true',
                        help='restore the orbitframes from before the last '
                             '-commit, saying how far back that is and asking '
                             'before writing; repeat to step further back')
    parser.add_argument('-undoNoPrompt', '--undoNoPrompt', action='store_true',
                        help='-undo without the confirmation prompt, for a '
                             'run with nobody at the terminal')
    parser.add_argument('-refreshLinks', '--refreshLinks',
                        action='store_true',
                        help='link the assembled <orbit>-<seq> directories '
                             'that have no link here yet, so their '
                             'acquisitions become visible; done before '
                             'anything is proposed')
    parser.add_argument('-linkAll', '--linkAll', action='store_true',
                        help='with -refreshLinks, link every unlinked '
                             'directory whatever its age')
    parser.add_argument('-undoLinks', '--undoLinks', action='store_true',
                        help='remove the links made by the last '
                             '-refreshLinks')
    parser.add_argument('-maxLinkAgeDays', '--maxLinkAgeDays', type=int,
                        default=None,
                        help='-refreshLinks skips assembled directories older '
                             'than this; taken from the nearest project.yaml '
                             f'maxLinkAgeDays key, else {DEFAULTLINKAGEDAYS}')
    parser.add_argument('-assemblyDir', '--assemblyDir', default=None,
                        help='assembly track directory holding the '
                             '<orbit>-<seq> directories; taken from the '
                             'nearest project.yaml assemblyDir key, else '
                             'inferred from the existing links')
    args = parser.parse_args()
    args.firstDate = datetime(1900, 1, 1) if args.firstdate is None else \
        datetime.strptime(args.firstdate, "%Y:%m:%d")
    args.lastDate = datetime(2100, 1, 1) if args.lastdate is None else \
        datetime.strptime(args.lastdate, "%Y:%m:%d")
    #
    # The write modes each undo a different thing, so combining them would
    # only be confusing about what came back
    #
    if (args.undo or args.undoNoPrompt) and (args.commit or args.refreshLinks
                                             or args.undoLinks):
        sys.exit('*** -undo restores orbitframes on its own -- run it alone')
    if args.undoLinks and (args.commit or args.refreshLinks):
        sys.exit('*** -undoLinks removes links on its own -- run it alone')
    if args.linkAll and not args.refreshLinks:
        sys.exit('*** -linkAll only applies to -refreshLinks')
    if args.assemblyDir is not None and not args.refreshLinks:
        sys.exit('*** -assemblyDir only applies to -refreshLinks')
    if args.maxLinkAgeDays is None:
        args.maxLinkAgeDays = nearestProjectYaml().get('maxLinkAgeDays',
                                                       DEFAULTLINKAGEDAYS)
    return args


def main():
    args = processArgs()
    if not os.path.isfile('frames'):
        sys.exit('*** no frames file -- run from a track directory')
    #
    # The undo modes need neither the framing nor the acquisitions, so they
    # run on their own and return
    #
    if args.undo or args.undoNoPrompt:
        undoCommit(prompt=not args.undoNoPrompt)
        return
    if args.undoLinks:
        undoLinks()
        return
    #
    # Before the acquisitions are collected, so anything linked now is
    # proposed for in the same run
    #
    if args.refreshLinks:
        refreshLinks(args)
    frames, orbframes = getFramesAndOrbits()
    if not frames:
        sys.exit('*** frames file has no frame-FIRST-N entries')
    acquisitions, noData = getAcquisitions(args.firstDate, args.lastDate)
    if not acquisitions:
        sys.exit('*** no assembled <orbit>-<seq> directories with SLCs found')
    built = builtStarts()
    #
    # The dates the setup step covers, and so how far back the derivation has
    # to reach: whatever that step is going to check, this run has to have
    # framed, or an acquisition nothing has proposed for stops the commit
    #
    setupDates = setupWindow(args, built, acquisitions)
    reached = frameFrom(acquisitions, built, args, setupDates[0], frames,
                        orbframes)
    #
    # Before anything is derived from the coverage.  A broken burst sequence is
    # usually a numbering slip rather than missing data, and repairing it here
    # means the acquisition is framed in this run rather than the next one --
    # everything below reads the corrected numbering.  -dump is left alone, so
    # it stays a clean replacement orbitframes on stdout.
    #
    # Only what this run reads, though: a decade of acquisitions lies behind
    # the window the setup step covers, and a hole in one of those -- or in a
    # directory whose SLCs have been cleaned away, which nothing can be cut
    # from anyway -- is neither new nor this run's business.
    #

    def stillGappy(acqs):
        return [d for a in acqs for d in a['gappy']
                if not a.get('noData') and a['date'] >= setupDates[0]]

    gappy = stillGappy(acquisitions)
    if gappy and not args.dump:
        if repairSlippedBursts(gappy):
            acquisitions, noData = getAcquisitions(args.firstDate,
                                                   args.lastDate)
            gappy = stillGappy(acquisitions)
    if not args.dump:
        print(f'{len(acquisitions)} acquisitions, '
              f'{acquisitions[0]["date"].strftime("%Y-%m-%d")} to '
              f'{acquisitions[-1]["date"].strftime("%Y-%m-%d")}')
        print('frames: ' + ' '.join(f'{f}-{n}' for f, n in frames))
        if noData:
            #
            # Stale links: the assembly directory has been cleaned since, so
            # they still describe what was built but cannot carry new work
            #
            stale = sum(1 for a in acquisitions if a.get('noData'))
            print(f'{len(noData)} linked director'
                  f'{"y" if len(noData) == 1 else "ies"} hold only metadata, '
                  'their SLCs having been cleaned away; the '
                  f'{stale} acquisition{"" if stale == 1 else "s"} with '
                  'nothing left to cut still count towards the history, but '
                  'nothing is proposed or set up for them')
        if gappy:
            #
            # Whatever is left after the repair above: the bursts really are
            # missing.  What that stops is only the frames cut over the hole,
            # so it is put to the user rather than ending the run outright
            #
            if not acceptBurstGaps(gappy, acquisitions, frames, orbframes,
                                   args):
                sys.exit('*** stopped on the missing bursts -- nothing '
                         'written')

    origFrames = [list(f) for f in frames]
    #
    # `frames` sets the floor: the allowable range may be wider than it, from
    # what the track has actually been cut to, but never narrower.  Widening a
    # `frames` entry is how a track that really has been extended -- southern
    # Greenland out to northern Greenland -- is opened up.
    #
    history = historicalRange(built, orbframes, acquisitions)
    framesLo = min(f[0] for f in origFrames)
    framesHi = max(f[0] + f[1] - 1 for f in origFrames)
    if history:
        allowed = (min(history[0], framesLo), max(history[1], framesHi))
    else:
        allowed = (framesLo, framesHi)
    if not args.dump:
        cutTo = (f'cut to date {history[0]}-{history[1]}, ' if history else '')
        print(f'\nallowable range {allowed[0]}-{allowed[1]} ({cutTo}'
              f'`frames` {framesLo}-{framesHi}); nothing is proposed outside '
              'it -- widen `frames` to extend the track')
        #
        # Report what is left out, but not as a problem: the range is normally
        # where the track was deliberately cut short, to keep ocean and other
        # areas of no interest out of the processing.
        #
        before = after = 0
        reach = None
        for acq in acquisitions:
            for first, last in acq['runs']:
                before = max(before, min(last, allowed[0] - 1) - first + 1)
                beyond = last - max(first, allowed[1] + 1) + 1
                if beyond > after:
                    after, reach = beyond, last
        if before > 0:
            print(f'    up to {before} bursts before {allowed[0]} are left '
                  'out (normally deliberate -- ocean or other areas of no '
                  'interest)')
        if after > 0:
            print(f'    up to {after} bursts beyond {allowed[1]} (coverage '
                  f'reaches {reach}) are left out (normally deliberate)')
        #
        # Acquisitions nearly always overrun the range, so overrunning is not
        # the signal.  A track being extended shows up as the coverage now
        # reaching further than it ever used to.
        #
        split = max(len(acquisitions) - args.deriveWindow, 1)
        wasHigh = max((last for a in acquisitions[:split]
                       for _, last in a['runs']), default=None)
        nowHigh = max((last for a in acquisitions[split:]
                       for _, last in a['runs']), default=None)
        wasLow = min((first for a in acquisitions[:split]
                      for first, _ in a['runs']), default=None)
        nowLow = min((first for a in acquisitions[split:]
                      for first, _ in a['runs']), default=None)
        if nowHigh is not None and wasHigh is not None and nowHigh > wasHigh:
            print(f'    *** coverage now reaches {nowHigh}, against {wasHigh} '
                  'over the rest of the record -- if the track has been '
                  'extended, widen `frames` to take it in')
        if nowLow is not None and wasLow is not None and nowLow < wasLow:
            print(f'    *** coverage now starts at {nowLow}, against '
                  f'{wasLow} over the rest of the record -- if the track has '
                  'been extended, widen `frames` to take it in')
    boxes = readVelocityStats()
    if boxes and not args.dump:
        print(f'\n{len(boxes)} velocityStats box'
              f'{"" if len(boxes) == 1 else "es"} '
              + ' '.join(f'{lo}-{hi}' for lo, hi in boxes) +
              '; an established key is cut as it always has been, and a piece '
              f'invented here is broken to sit inside them (up to '
              f'{args.boxOverrun} bursts past a boundary is left whole)')
    #
    # Whether the history is a good guide at all
    #
    if not args.dump and len(acquisitions) > args.deriveWindow:
        changed = chunkingChanged(acquisitions, args.deriveWindow,
                                  max(args.snapTol, 1))
        window = min(args.deriveWindow, len(acquisitions))
        if changed:
            print(f'\n{len(changed)}/{window} recent acquisitions are chunked '
                  'into datatakes shared by no other acquisition, so there is '
                  'nothing for them to pair with as framed:')
            for acq in changed[:6]:
                runs = ','.join(f'{f}-{l}' for f, l in acq['runs'])
                print(f'    {acq["date"]:%Y-%m-%d} {acq["orbit"]:6d}  {runs}')
            if len(changed) > 6:
                print(f'    ... and {len(changed) - 6} more')
        else:
            print(f'\nevery one of the last {window} acquisitions shares its '
                  'datatake chunking with another, so the framing built up by '
                  'hand is a good guide')

    def describeFraming(proposal, orbits):
        '''
        Where the framing came from, and any key it introduces.

        The framing is carried forward from the last built acquisition that
        matches, so which one that was is the whole explanation of what is
        being proposed, and a key that is not in it starts a new series --
        which pairs with nothing until a second acquisition carries it.
        '''
        if args.dump:
            return
        for note in proposal.notes:
            print(note)
        carried = {}
        for orbit in sorted(orbits):
            match = proposal.reference.get(orbit)
            carried.setdefault(match['orbit'] if match else None,
                               []).append(orbit)
        byOrbit = {acq['orbit']: acq for acq in acquisitions}
        for source, users in sorted(carried.items(),
                                    key=lambda item: -len(item[1])):
            where = (f'orbit {source}' if source is not None else
                     'the `frames` file, nothing having been built yet')
            shown = ' '.join(str(orbit) for orbit in users[:8])
            more = '' if len(users) <= 8 else f' ... and {len(users) - 8} more'
            gaps = [(byOrbit[orbit]['date'] - byOrbit[source]['date']).days
                    for orbit in users if source is not None]
            age = f' ({min(gaps)} days back)' if gaps else ''
            print(f'\nframing carried forward from {where}{age}: '
                  f'{shown}{more}')
            if gaps and min(gaps) > args.maxPairDays:
                #
                # Nothing recent covers these bursts, so the framing had to
                # come from the last acquisition that did.  Worth saying: the
                # series on it stopped that long ago, and these pieces pair
                # with each other rather than with anything already built
                #
                print(f'    {BOLD}*** nothing within {args.maxPairDays} days '
                      f'covers these bursts, so this carries on a series that '
                      f'stopped {min(gaps)} days ago and pairs only within '
                      f'itself{PLAIN}')
            if source is not None:
                match = proposal.reference[users[0]]
                keys = ','.join(f'{first}({nBursts})' for first, nBursts
                                in sorted(builtFraming(match, built,
                                                       origFrames,
                                                       orbframes).items()))
                print(f'    it was built as {keys}')
        inUse = set()
        for acq in acquisitions:
            if built.get(acq['orbit']):
                inUse |= built[acq['orbit']]
        introduced = sorted({first for orbit in orbits
                             for first in proposal.starts(orbit)} - inUse)
        if introduced:
            print('\n*** these keys have not been built before, so they start '
                  'new series that pair with nothing until a second '
                  'acquisition carries them: ' +
                  ' '.join(str(x) for x in introduced))

    def committable(proposal, onlyOrbits=None):
        '''
        The orbits with lines to write, which is what -commit writes.

        An acquisition already built is in here only where it is being given
        an extra piece: its own lines are never rewritten (proposedEntries).
        '''
        return set(proposedEntries(proposal, built, orbframes,
                                   len(origFrames), onlyOrbits))

    def report():
        '''
        The terminal output, whichever mode was asked for.

        Returns the proposal and the orbits it covers, which is what -commit
        writes: whatever was shown is what gets committed -- including, where
        the setup check reads further back than -lookBack, the older unframed
        acquisitions it had to take in.
        '''
        nDefault = len(origFrames)
        if args.dump:
            proposal = frameTrack(acquisitions, origFrames, orbframes, built,
                                  allowed, args, boxes=boxes)
            for line in formatEntries(proposal, acquisitions, built,
                                      orbframes, nDefault):
                print(line)
            return proposal, committable(proposal)

        if args.compare:
            proposal = frameTrack(acquisitions, origFrames, orbframes, built,
                                  allowed, args, boxes=boxes)
            rows = compareStarts(acquisitions, origFrames, orbframes, proposal,
                                 built, origFrames=origFrames)
            printComparison(rows, 'Whole record, existing vs derived',
                            showOnly=args.quiet)
            orbits = committable(proposal)
            describeFraming(proposal, orbits)
            return proposal, orbits

        if args.lookBack > 0:
            fromAcq = max(0, len(acquisitions) - args.lookBack)
            proposal = frameTrack(acquisitions, origFrames, orbframes, built,
                                  allowed, args, fromAcq=reached,
                                  boxes=boxes)
            shownOrbits = {a['orbit'] for a in acquisitions[reached:]}
            #
            # Anything reached past -lookBack is an acquisition the setup step
            # checks and nothing has framed, so it is listed apart: what is
            # committed is what was shown, and these were not asked for
            #
            backlog = committable(proposal,
                                  {a['orbit'] for a
                                   in acquisitions[reached:fromAcq]})
            if backlog:
                one = len(backlog) == 1
                print(f'\n{len(backlog)} acquisition{"" if one else "s"} '
                      f'older than the last {args.lookBack} '
                      f'{"has" if one else "have"} no lines the setup step '
                      'can read -- never framed, or built under a `frames` '
                      'default that no longer fits -- so they are framed here '
                      f'as well (back to '
                      f'{acquisitions[reached]["date"]:%Y-%m-%d}):')
                for line in formatEntries(proposal, acquisitions, built,
                                          orbframes, nDefault,
                                          onlyOrbits=backlog):
                    print(line)
                #
                # Some of them have lines already and were simply never built
                # from.  Those lines are replaced, which is allowed -- nothing
                # pairs against an acquisition that was never cut -- but it is
                # not what -lookBack asked for, so it is said out loud
                #
                held = sorted(orbit for orbit in backlog
                              if any(entry[0] == orbit for entry in orbframes))
                if held:
                    print(f'    {len(held)} of them already have orbitframes '
                          'lines, which are replaced by what is derived here; '
                          'nothing was ever built from them, so no key that '
                          'pairs can move: ' +
                          ' '.join(str(orbit) for orbit in held))
            print('\nFraming for the last '
                  f'{len(acquisitions) - fromAcq} acquisitions '
                  '(only additions are written for the ones already built):')
            for line in formatEntries(proposal, acquisitions, built, orbframes,
                                      nDefault,
                                      onlyOrbits=shownOrbits - backlog):
                print(line)
            rows = compareStarts(acquisitions, origFrames, orbframes, proposal,
                                 built, window=reached, origFrames=origFrames)
            printComparison(rows, f'Last {len(acquisitions) - reached} '
                                  'acquisitions, existing vs derived',
                            showOnly=args.quiet)
            #
            # Only the orbits shown, and of those only the ones that may be
            # written -- an acquisition already built keeps its own lines
            #
            orbits = committable(proposal, shownOrbits)
            describeFraming(proposal, orbits)
            return proposal, orbits
        #
        # Default: frame the acquisitions not yet built, which is the same
        # test setupSeveralTopsImages uses to decide what is left to do.
        # Everything before them is what the framing is carried from.
        #
        unbuilt = [i for i, a in enumerate(acquisitions)
                   if not built.get(a['orbit']) and not a.get('noData')]
        if not unbuilt:
            print('\nNothing to do -- everything has been framed already')
            return None, set()
        proposal = frameTrack(acquisitions, origFrames, orbframes, built,
                              allowed, args, fromAcq=unbuilt[0],
                              boxes=boxes)
        newOrbits = {acquisitions[i]['orbit'] for i in unbuilt}
        print(f'\n# Proposed entries for {len(newOrbits)} unframed '
              'acquisitions')
        for line in formatEntries(proposal, acquisitions, built, orbframes,
                                  nDefault, onlyOrbits=newOrbits):
            print(line)
        #
        # A key introduced here can also need a partner in the acquisition
        # before it.  That is an addition to what it already carries, never a
        # change to it, but it does mean building another directory, so it is
        # listed apart.
        #
        orbits = committable(proposal)
        older = orbits - newOrbits
        if older:
            print('\n# Pieces added to acquisitions already built, so the new '
                  'keys pair')
            for line in formatEntries(proposal, acquisitions, built,
                                      orbframes, nDefault, onlyOrbits=older):
                print(line)
        describeFraming(proposal, newOrbits)
        return proposal, orbits

    proposal, orbits = report()
    #
    # -dump prints a replacement orbitframes and nothing else, so it stays
    # pipeable and skips the setup step
    #
    ok, write = True, None
    if args.dump:
        if args.commit:
            commitEntries(proposal, orbits, acquisitions)
    else:
        ok, write = setupStep(args, proposal, orbits, orbframes, origFrames,
                              acquisitions, built, setupDates)
    if args.plot or args.plotFile:
        #
        # After the report, not instead of it.  -lookBack narrows the plot the
        # same way it narrows the comparison; a decade on one axis is
        # unreadable
        #
        #
        # The stretch the report covered, and enough of what came before it to
        # show the framing being carried forward
        #
        shown = (acquisitions[plotFrom(acquisitions, built, reached,
                                       args.refBack):]
                 if args.lookBack > 0 else acquisitions)
        #
        # Seed up to the start of what is shown, so every plotted acquisition
        # carries a proposal to draw
        #
        plotProposal = frameTrack(acquisitions, origFrames, orbframes, built,
                                  allowed, args,
                                  fromAcq=len(acquisitions) - len(shown),
                                  boxes=boxes)
        #
        # The commit button is the same write step the terminal run would have
        # taken, so what is committed is the framing the check just passed
        #
        clicked = plotTrack(shown, built, frames, origFrames, orbframes,
                            plotProposal, args, onCommit=write)
        if clicked is not None:
            ok = clicked
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
