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

This script never writes anything -- all modes print to the terminal.

Run from the track directory (the one holding `frames`, `orbitframes` and the
<orbit>-<seq>directories).

Part of the s1setup package.
"""
import argparse
import glob
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

from s1setup.setupSeveralTopsImages import getFramesAndOrbits, determineFraming

#
# Directory patterns used by setupSeveralTopsImages to find assembled SLCs
#
SLCPATTERNS = ['????-?', '?????-?']


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


def getAcquisitions(firstDate, lastDate):
    '''
    Collect the assembled SLCs as one entry per orbit, date ordered.

    An orbit split across several sequences (a split datatake) contributes one
    burst run per sequence, since setupSeveralTopsImages will realize a piece
    if any sequence of that orbit covers it.
    '''
    byOrbit = {}
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
        orbit = int(orbitStr)
        if orbit not in byOrbit:
            byOrbit[orbit] = {'orbit': orbit,
                              'date': parseAscNodeTime(ascFile),
                              'runs': [], 'dirs': [], 'gappy': []}
        byOrbit[orbit]['dirs'].append((slcDir, [] if gappy else
                                       [tuple(x) for x in runs]))
        if gappy:
            #
            # No framing can be built from this directory -- checkBurstTimes
            # aborts on it first -- so contribute no coverage and report it
            #
            byOrbit[orbit]['gappy'].append(slcDir)
        else:
            byOrbit[orbit]['runs'] += [list(x) for x in runs]
    acquisitions = []
    for acq in byOrbit.values():
        if acq['date'] < firstDate or acq['date'] > lastDate:
            continue
        acq['runs'] = mergeRuns(acq['runs'])
        acquisitions.append(acq)
    return sorted(acquisitions, key=lambda a: (a['date'], a['orbit']))


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


def alignRegions(frames, regions):
    '''
    One velocityStats region per frame, in frame order.

    deriveFrames produces a frame per region already, but when `frames` is
    kept the two lists need not line up, so each frame is matched to the
    region holding its start and falls back to its own extent.
    '''
    aligned = []
    for first, nBursts in frames:
        match = [r for r in regions if r[0] <= first <= r[1]]
        aligned.append(match[0] if match else (first, first + nBursts - 1))
    return aligned


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


def frameIndexFor(start, frames, regions):
    '''
    Which frame a built start belongs to, or None if it fits none.

    Where several frames share a region -- two staggered keys, or a frame cut
    in two -- the nearest start wins, so each key stays with its own frame.
    '''
    if regions:
        inRange = [p for p, (lo, hi) in enumerate(regions)
                   if lo <= start <= hi]
    else:
        inRange = [p for p, (first, nBursts) in enumerate(frames)
                   if first <= start <= first + nBursts - 1]
    if not inRange:
        return None
    return min(inRange, key=lambda p: abs(frames[p][0] - start))


def frameSpan(frames, p, anchor, regions=None, history=None):
    '''
    Burst range a frame's pieces must stay inside.

    Where the velocityStats regions are known they set how early a piece may
    start, since a region is precisely the bounded area the autocleaning works
    in and any start inside it is legitimate.  Without them the start is the
    `frames` start, opened up to take in an anchor that has drifted earlier.

    The end is always the frame end: a start that has drifted later gives a
    shorter piece, it does not push the frame outwards.  Pieces never leave
    this range, so a run covering two frames yields a piece in each rather
    than one long piece straddling both -- a piece outside its frame lands
    outside the expected autocleaning region.
    '''
    first, nBursts = frames[p]
    if regions:
        spanFirst, spanLast = min(anchor, regions[p][0]), first + nBursts - 1
    else:
        spanFirst, spanLast = min(anchor, first), first + nBursts - 1
    if history:
        spanFirst = max(spanFirst, history[0])
        spanLast = min(spanLast, history[1])
    return spanFirst, spanLast


def pieceAt(pieces, start, minBursts):
    '''The piece that can be built starting exactly on start, or None'''
    for first, last in pieces:
        if first <= start and last - start + 1 >= minBursts:
            return (start, last)
    return None


class Proposal(object):
    '''Accumulates the proposed orbitframes entries for one track'''

    def __init__(self, frames, existingOrbits, minBursts, nOriginal=0):
        self.frames = frames
        self.nframes = max(len(frames), nOriginal)
        self.existingOrbits = existingOrbits
        self.minBursts = minBursts
        # orbit -> list of [index, first, nBursts]
        self.entries = defaultdict(list)
        # orbit -> set of firstBursts this orbit will build
        self.starts = defaultdict(set)
        # (orbit, index) of deviant pieces needing mates: list of dicts
        self.deviants = []
        # orbits skipped entirely because every frame missed its anchor
        self.broken = set()

    def addPiece(self, orbit, index, first, nBursts):
        self.entries[orbit].append([index, first, nBursts])
        if nBursts > 0:
            self.starts[orbit].add(first)

    def addExtra(self, orbit, first, nBursts):
        '''Append an extra piece with the next free index above the defaults'''
        if first in self.starts[orbit]:
            return False
        used = {e[0] for e in self.entries[orbit]}
        used |= self.existingOrbits.get(orbit, set())
        index = self.nframes + 1
        while index in used:
            index += 1
        self.addPiece(orbit, index, first, nBursts)
        return True


def proposeTrack(acquisitions, frames, orbframes, args, seedThrough=None,
                 regions=None, built=None, history=None,
                 nOriginal=None):
    '''
    Walk the acquisitions in date order and propose orbitframes entries.

    seedThrough, if given, is the number of leading acquisitions whose
    existing orbitframes entries are used to seed the anchors instead of being
    recomputed (lookBack mode re-derives only the tail of the record).
    '''
    nframes = len(frames)
    #
    # setupSeveralTopsImages expands these against the `frames` file as it
    # stands, not the segmentation derived here, so once the two differ every
    # piece has to be stated explicitly and any surplus `frames` index has to
    # be skipped -- otherwise a stale default fires and the run aborts
    #
    nOriginal = nframes if nOriginal is None else nOriginal
    explicit = nOriginal is not None and nOriginal != 0 and \
        (nOriginal != nframes or regions is not None)
    built = {} if built is None else built
    existingOrbits = defaultdict(set)
    for orbit, index, first, nBursts in orbframes:
        existingOrbits[orbit].add(index)
    proposal = Proposal(frames, existingOrbits, args.minBursts,
                        nOriginal)
    seedThrough = 0 if seedThrough is None else seedThrough
    #
    # anchor[p] is the firstBurst currently in use for frame index p.  It is
    # seeded from `frames` and then learned, because the frames files have
    # drifted out of date (track-141 says 630 but everything uses 631, and
    # track-10 says 671 but everything uses 674).
    #
    anchor = [f[0] for f in frames]
    for iAcq, acq in enumerate(acquisitions):
        orbit, runs = acq['orbit'], acq['runs']
        if iAcq < seedThrough:
            #
            # Seed the anchors from the directories actually built.  This is
            # read rather than replayed through determineFraming because the
            # frame indices in orbitframes refer to whatever `frames` held
            # when each line was written, which is not the segmentation in
            # use once it has been derived from the velocityStats regions.
            #
            if iAcq < seedThrough - args.deriveWindow:
                #
                # Only the recent record seeds the anchors.  The segmentation
                # is derived from that same window, so letting a start that
                # was abandoned years ago set an anchor would put the two out
                # of step and resurrect a dead key.
                #
                continue
            if regions:
                #
                # The keys have been derived from this same window, so they
                # are the segmentation.  Letting a stray start that was built
                # a handful of times redefine an anchor would undo that.
                #
                continue
            byFrame = defaultdict(list)
            for start in built.get(orbit, ()):
                p = frameIndexFor(start, frames, regions)
                if p is not None:
                    byFrame[p].append(start)
            for p, starts in byFrame.items():
                #
                # A frame can carry more than one key (the two satellites are
                # staggered), so hold the one already anchored rather than
                # picking arbitrarily
                #
                if anchor[p] not in starts:
                    anchor[p] = max(starts)
            continue
        #
        # The established start is the floor: once directories exist under it
        # the key cannot be abandoned while the data still supports it
        #
        if iAcq == seedThrough:
            floor = list(anchor)
        nDeviant = 0
        firstEntry = len(proposal.entries[orbit])
        firstDeviant = len(proposal.deviants)
        #
        # Which frames can be built on their own key here.  Where a region
        # carries several keys and one of them cannot be reached, there is
        # nothing to rescue -- its sibling already covers that data -- so it
        # is skipped rather than re-anchored onto a start nothing else uses.
        #
        onKey = []
        for p in range(nframes):
            spanFirst, spanEnd = frameSpan(frames, p, anchor[p], regions,
                                           history)
            here = clippedPieces(runs, spanFirst, spanEnd, args.minBursts)
            onKey.append(pieceAt(here, anchor[p], args.minBursts) is not None)
        for p in range(nframes):
            spanFirst, spanEnd = frameSpan(frames, p, anchor[p],
                                           regions, history)
            found = clippedPieces(runs, spanFirst, spanEnd, args.minBursts)
            if not found:
                #
                # Nothing usable in this frame's span -- skip it
                #
                proposal.addPiece(orbit, p + 1, anchor[p], 0)
                continue
            #
            # A persistent change of start is a new segmentation rather than a
            # one-off, so adopt it as the anchor and bridge back to the
            # acquisitions still on the old one
            #
            #
            # Checked before any re-anchoring: re-anchoring exists to rescue
            # data that would otherwise be lost, and a sibling key already
            # covers this, so moving would only invent a start nothing uses
            #
            if not onKey[p] and regions and any(
                    onKey[q] for q in range(nframes)
                    if q != p and regions[q][1] == regions[p][1]):
                proposal.addPiece(orbit, p + 1, anchor[p], 0)
                continue
            newAnchor = resolveAnchor(found, acquisitions, iAcq, frames, p,
                                      anchor[p], floor[p], args, regions,
                                      history)
            if newAnchor != anchor[p]:
                bridgeAnchor(proposal, acquisitions, args, p, iAcq,
                             anchor[p], newAnchor, spanEnd)
                anchor[p] = newAnchor
            #
            # Build on the anchor where possible -- that keeps the pairing key
            #
            main = pieceAt(found, anchor[p], args.minBursts)
            if main is None:
                #
                # Re-anchoring here would land within a burst or two of
                # another frame's key: that is the same cut jittered, not a
                # new one, and the other frame already covers the data
                #
                if any(abs(found[0][0] - anchor[q]) <= args.stubGap
                       for q in range(nframes) if q != p):
                    proposal.addPiece(orbit, p + 1, anchor[p], 0)
                    continue
                main = found[0]
                nDeviant += 1
                proposal.deviants.append({'iAcq': iAcq, 'p': p,
                                          'first': main[0],
                                          'spanEnd': spanEnd})
            first, last = main
            nBursts = last - first + 1
            if explicit and first not in proposal.starts[orbit]:
                proposal.addPiece(orbit, p + 1, first, nBursts)
            elif first in proposal.starts[orbit]:
                #
                # Another frame already covers this start, so this one would
                # just build the same directory twice
                #
                proposal.addPiece(orbit, p + 1, anchor[p], 0)
            elif first == frames[p][0] and nBursts == frames[p][1]:
                # the default already works, no line needed
                proposal.starts[orbit].add(first)
            else:
                proposal.addPiece(orbit, p + 1, first, nBursts)
            #
            # Where one satellite's bursts start well before the shared key,
            # keep its own start as a second piece: the shared key pairs it
            # with the other satellite, its own start pairs it with the rest
            # of its own series (track-127 runs 644 alongside 649)
            #
            ownFirst, ownLast = found[0]
            if staggered(acquisitions, iAcq, spanFirst, spanEnd, ownFirst,
                         first, args):
                if proposal.addExtra(orbit, ownFirst, ownLast - ownFirst + 1):
                    proposal.deviants.append({'iAcq': iAcq, 'p': p,
                                              'first': ownFirst,
                                              'spanEnd': spanEnd})
            #
            # Other runs inside this frame's span are gap fragments
            #
            for extraFirst, extraLast in found:
                if extraFirst == main[0] or extraLast <= main[1]:
                    continue
                #
                # This run is another key's territory, not a gap to fill: a
                # sibling frame in the same region starts inside it and will
                # cut it properly, so a fragment here would just duplicate
                # that piece at a start nothing pairs with
                #
                if regions and any(
                        extraFirst <= anchor[q] <= extraLast
                        for q in range(nframes)
                        if q != p and regions[q][1] == regions[p][1]):
                    continue
                if proposal.addExtra(orbit, extraFirst,
                                     extraLast - extraFirst + 1):
                    proposal.deviants.append({'iAcq': iAcq, 'p': p,
                                              'first': extraFirst,
                                              'spanEnd': spanEnd})
        #
        # An acquisition where every frame missed its anchor is a broken
        # acquisition, not a new segmentation.  Rescuing it would mean adding
        # a new pairing key to every neighbour for one bad orbit, so skip it
        # and flag it for a re-download instead (track-90 10487).
        #
        for p in range(nframes, nOriginal):
            #
            # This index exists in `frames` but not in the segmentation in
            # use, so its default would build a piece nothing else has
            #
            proposal.addPiece(orbit, p + 1, frames[0][0], 0)
        if nframes > 1 and nDeviant == nframes and not args.keepBroken:
            del proposal.entries[orbit][firstEntry:]
            del proposal.deviants[firstDeviant:]
            proposal.starts[orbit] = set()
            proposal.broken.add(orbit)
            for p in range(nframes):
                proposal.addPiece(orbit, p + 1, anchor[p], 0)
    #
    # Mates: a deviant start only pairs if the neighbouring acquisitions
    # carry a piece with the same start
    #
    addMates(proposal, acquisitions, args)
    return proposal


def withinMateDays(acqA, acqB, args):
    '''
    A mate is only worth adding if the two acquisitions would actually pair.

    The neighbour by index can be months away when intervening acquisitions
    have had their SLC directories cleaned up, and a piece built to partner
    something that far off is wasted.
    '''
    return abs((acqB['date'] - acqA['date']).days) <= args.maxMateDays


def resolveAnchor(found, acquisitions, iAcq, frames, p, anchor, floor, args,
                  regions=None, history=None):
    '''
    Decide the start this frame should use from here on.

    The candidate is the latest start the next runLength acquisitions can all
    support, so a satellite whose bursts sit one late (S1A against S1C) does
    not knock the key back and forth.  The anchor only moves when that window
    is coherent -- if the starts in it disagree by more than snapTol the run
    is a one-off bad acquisition, not a new segmentation, and it is handled as
    a deviation with matching pieces instead.

    The anchor is kept whenever it is still reachable, which is what holds the
    pairing key steady.  It drifts back down when a smaller start has become
    available again, so a bad spell does not cost the earlier bursts of every
    acquisition thereafter -- but never below the established start, because
    directories already exist under that name.
    '''
    spanFirst, spanLast = frameSpan(frames, p, anchor, regions, history)
    window = []
    for jAcq in range(iAcq, min(len(acquisitions), iAcq + args.runLength)):
        ahead = clippedPieces(acquisitions[jAcq]['runs'], spanFirst, spanLast,
                              args.minBursts)
        if ahead:
            window.append(ahead[0][0])
    spread = max(window) - min(window) if window else 0
    if len(window) < args.runLength or spread > args.snapTol:
        return anchor
    candidate = max(window)
    if pieceAt(found, anchor, args.minBursts) is None:
        # anchor unreachable here, so it has to move
        if pieceAt(found, candidate, args.minBursts) is not None:
            return candidate
        return anchor
    if floor <= candidate < anchor:
        return candidate
    return anchor


def staggered(acquisitions, iAcq, spanFirst, spanEnd, ownFirst, anchor, args):
    '''
    True when this acquisition's bursts start systematically before the key.

    A satellite whose bursts sit well before the shared key loses that data
    every pass, so it is worth also building a piece at its own start: the
    shared key pairs it with the other satellite, its own start pairs it down
    its own series.  The test is that the same early start comes back in its
    own series -- a single acquisition that happens to start early is just a
    deviation, and creating a second key for it would never pay off.

    Off by default.  Measured over every track, splitting costs more than it
    gains: most tracks whose key has drifted later can still reach the frame
    start, and a second key there doubles the processing for a strip that only
    ever pairs at 12 days.  Only a few tracks are run this way (track-127
    carries 644 alongside 649), and those want -splitTol 4.
    '''
    if args.splitTol <= 0 or ownFirst + args.splitTol >= anchor:
        return False
    for offset in range(-args.mateSpan, args.mateSpan + 1):
        jAcq = iAcq + offset
        if offset == 0 or jAcq < 0 or jAcq >= len(acquisitions):
            continue
        if not withinMateDays(acquisitions[iAcq], acquisitions[jAcq], args):
            continue
        ahead = clippedPieces(acquisitions[jAcq]['runs'], spanFirst, spanEnd,
                              args.minBursts)
        if not ahead:
            continue
        if abs(ahead[0][0] - ownFirst) <= args.snapTol and \
                ahead[0][0] + args.splitTol < anchor:
            return True
    return False


def bridgeAnchor(proposal, acquisitions, args, p, iAcq, oldAnchor, newAnchor,
                 spanEnd):
    '''
    Carry both starts across a change of anchor so the changeover still pairs.

    The acquisitions just before the change are still on the old start, so
    give them the new one; and the first acquisitions on the new start keep
    the old one where the data is still there.
    '''
    for jAcq in range(max(0, iAcq - args.mateSpan), iAcq):
        if not withinMateDays(acquisitions[jAcq], acquisitions[iAcq], args):
            continue
        run = usableRun(acquisitions[jAcq]['runs'], newAnchor,
                        newAnchor + args.minBursts - 1)
        if run is not None:
            nBursts = min(run[1], spanEnd) - newAnchor + 1
            proposal.addExtra(acquisitions[jAcq]['orbit'], newAnchor, nBursts)
    for jAcq in range(iAcq, min(len(acquisitions), iAcq + args.mateSpan)):
        if not withinMateDays(acquisitions[jAcq], acquisitions[iAcq], args):
            continue
        run = usableRun(acquisitions[jAcq]['runs'], oldAnchor,
                        oldAnchor + args.minBursts - 1)
        if run is not None:
            nBursts = min(run[1], spanEnd) - oldAnchor + 1
            proposal.addExtra(acquisitions[jAcq]['orbit'], oldAnchor, nBursts)


def addMates(proposal, acquisitions, args):
    '''
    Give every deviant start a partner in the neighbouring acquisitions.

    mateSpan of 2 covers both the 6-day nearest-in-time pair and the 12-day
    same-mission pair used by the Sentinel1-S1A/-S1C clone directories.
    '''
    for deviant in proposal.deviants:
        iAcq, first, spanEnd = deviant['iAcq'], deviant['first'], \
            deviant['spanEnd']
        for offset in range(-args.mateSpan, args.mateSpan + 1):
            jAcq = iAcq + offset
            if offset == 0 or jAcq < 0 or jAcq >= len(acquisitions):
                continue
            mate = acquisitions[jAcq]
            if not withinMateDays(acquisitions[iAcq], mate, args):
                continue
            if first in proposal.starts[mate['orbit']]:
                continue
            run = usableRun(mate['runs'], first, first + args.minBursts - 1)
            if run is None:
                continue
            nBursts = min(run[1], spanEnd) - first + 1
            if nBursts >= args.minBursts:
                proposal.addExtra(mate['orbit'], first, nBursts)


def formatEntries(proposal, acquisitions, onlyOrbits=None):
    '''Format the proposal as orbitframes lines, grouped by orbit and date'''
    lines = []
    for acq in acquisitions:
        orbit = acq['orbit']
        if orbit not in proposal.entries:
            continue
        if onlyOrbits is not None and orbit not in onlyOrbits:
            continue
        for slcDir, dirRuns in sorted(acq['dirs']):
            span = (f'{dirRuns[0][0]}.{dirRuns[-1][1]}' if dirRuns
                    else 'gap.gap')
            lines.append(f'# {slcDir}/frames.{span}')
        for index, first, nBursts in sorted(proposal.entries[orbit]):
            lines.append(f'{orbit}-{index}-{first}-{nBursts}')
    return lines


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


def readVelocityStats():
    '''
    Burst range of each frame, from the velocityStats/<lo>-<hi> directories.

    These are the bounded regions the autocleaning works in, and they hold the
    real segmentation: each region contains the start burst of one frame.
    They stay current as coverage is extended, whereas `frames` is written
    once and goes stale, so they are the better description of the track.
    '''
    regions = []
    for statDir in glob.glob('velocityStats/*-*'):
        if not os.path.isdir(statDir):
            continue
        lo, _, hi = os.path.basename(statDir).partition('-')
        if lo.isdigit() and hi.isdigit():
            regions.append((int(lo), int(hi)))
    return sorted(regions)


def addLooseKeys(frames, regions, built, acquisitions, allowed, args):
    """
    Keep established keys that no frame or region brackets.

    Whichever way the segmentation was arrived at, abandoning a start that is
    still being built stops the series running on it -- continuity between
    runs matters more than tidiness, so the key is kept and the gap reported.
    This is what the `frames` path was missing: a track whose file names one
    frame while two keys are in use would lose half its pairs.
    """
    usage = defaultdict(int)
    for acq in acquisitions[-args.deriveWindow:]:
        for start in built.get(acq['orbit'], set()):
            usage[start] += 1
    if not usage or not allowed:
        return frames, regions, []
    #
    # Covered means a frame actually produces this start, not merely that a
    # region brackets it: a key inside a region that no frame cuts is exactly
    # the case being rescued
    #
    spans = [(f[0], f[0] + f[1] - 1) for f in frames]
    floor = max(usage.values()) * args.keyFraction
    lengths = defaultdict(int)
    for acq in acquisitions[-args.deriveWindow:]:
        for start, nBursts in setupLengths(acq['orbit']).items():
            lengths[start] = max(lengths[start], nBursts)
    notes = []
    for start, count in sorted(usage.items()):
        if not (allowed[0] <= start <= allowed[1]) or count < floor:
            continue
        if any(lo <= start <= hi for lo, hi in spans):
            continue
        if any(abs(start - f[0]) <= args.stubGap for f in frames):
            continue
        nBursts = lengths.get(start) or allowed[1] - start + 1
        frames = frames + [[start, nBursts]]
        if regions:
            regions = regions + [(start, allowed[1])]
        notes.append(f'    (no frame covers it)  ->  frame-{start}-{nBursts}'
                     f'  (built for {count} of the last {args.deriveWindow})')
    return frames, regions, notes


def deriveFrames(frames, orbframes, built, acquisitions, regions,
                 window=40, keyFraction=0.5, stubGap=6,
                 allowed=None):
    '''
    Work out the frame each velocityStats region is really being cut on.

    Takes the start that region has actually been built on most often, and the
    length that start has been given in `orbitframes` -- reusing the pattern
    already established rather than inventing one.  Falls back to the `frames`
    entry for the region, then to the region itself.

    Returns (derived, notes) with one frame per region, in region order.
    '''
    #
    # Counted over the recent record only.  A start that was in use years ago
    # and has since been dropped must not come back as the frame for its
    # region -- the pattern wanted is the one that fits the current data.
    #
    usage = defaultdict(int)
    for acq in acquisitions[-window:]:
        for start in built.get(acq['orbit'], set()):
            usage[start] += 1
    if not usage:
        for acq in acquisitions:
            for start in built.get(acq['orbit'], set()):
                usage[start] += 1
    #
    # A frame's length is the furthest it has been taken, not the most common
    # or the most recent.  Where a frame has been cut into two pieces the
    # individual pieces are shorter than the frame -- track-39 builds 661(9)
    # and 669(29), but the frame itself runs 661 to 697, and proposing a
    # single piece there has to be allowed to span the whole of it.
    #
    lengths = defaultdict(int)
    recentOrbits = {acq['orbit'] for acq in acquisitions[-window:]}
    for start, nBursts in frames:
        lengths[start] = max(lengths[start], nBursts)
    for orbit, _, start, nBursts in orbframes:
        if orbit in recentOrbits:
            lengths[start] = max(lengths[start], nBursts)
    for acq in acquisitions[-window:]:
        for start, nBursts in setupLengths(acq['orbit']).items():
            lengths[start] = max(lengths[start], nBursts)

    derived, kept, notes = [], [], []
    for lo, hi in regions:
        inRegion = {s: n for s, n in usage.items() if lo <= s <= hi}
        fromFrames = [f for f in frames if lo <= f[0] <= hi]
        #
        # A region can carry more than one key -- the two satellites are
        # staggered, or a frame is deliberately cut in two.  Every start with
        # a real history here is kept, not just the most used one: dropping a
        # long-standing key would orphan every piece already built on it.
        #
        if inRegion:
            floor = max(2, int(keyFraction * max(inRegion.values())))
            persistent = sorted(s for s, n in inRegion.items() if n >= floor)
        else:
            persistent = []
        for extra in persistent[1:]:
            kept.append((extra, hi))
            derived.append([extra, lengths.get(extra) or hi - extra + 1])
            notes.append(f'    {lo}-{hi}  ->  frame-{extra}-{derived[-1][1]}'
                         f'  (also built for {inRegion[extra]} of the last '
                         f'{window})')
        if inRegion:
            start = persistent[0]
            source = f'built for {inRegion[start]} of the last {window}'
        else:
            #
            # Nothing starts here any more, so this region is covered by a
            # neighbouring frame running through it.  Inventing a frame would
            # cut the track somewhere it is not being cut.
            #
            notes.append(f'    {lo}-{hi}  ->  no frame (nothing starts here)')
            continue
        kept.append((lo, hi))
        if lengths.get(start):
            nBursts = lengths[start]
        elif fromFrames:
            nBursts = fromFrames[0][1]
        else:
            nBursts = hi - start + 1
        derived.append([start, nBursts])
        notes.append(f'    {lo}-{hi}  ->  frame-{start}-{nBursts}  ({source})')
    #
    # Two keys a few bursts apart are one frame reached from two staggered
    # starts, not two frames: the earlier one is a stub running up to the
    # later, with the usual one burst of overlap.  Keys far apart really are
    # separate frames and keep their full extent.
    #
    order = sorted(range(len(derived)), key=lambda i: derived[i][0])
    for this, nextOne in zip(order, order[1:]):
        if kept[this][1] != kept[nextOne][1]:
            continue
        gap = derived[nextOne][0] - derived[this][0]
        if 0 < gap <= stubGap:
            derived[this][1] = gap + 1
            notes.append(f'    {kept[this][0]}-{kept[this][1]}  ->  '
                         f'frame-{derived[this][0]}-{gap + 1} trimmed: a stub '
                         f'up to {derived[nextOne][0]}')
    return derived, kept, notes


def checkFramesFile(frames, built, acquisitions, args, regions=None):
    '''
    Report starts in regular use that no frame in `frames` can produce.

    segmentTrack works within the frames it is given and will not invent new
    ones, so a track whose real segmentation has outgrown its `frames` file
    has to have that file corrected first -- otherwise most of the record is
    unreachable and the proposal will look empty.
    '''
    spans = [frameSpan(frames, p, frames[p][0], regions)
             for p in range(len(frames))]
    outside = defaultdict(int)
    for acq in acquisitions:
        for start in built.get(acq['orbit'], set()):
            if not any(lo <= start <= hi - args.minBursts + 1
                       for lo, hi in spans):
                outside[start] += 1
    return sorted(outside.items(), key=lambda x: -x[1])


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
        framing = determineFraming(orbit, 0, frames,
                                   [[orbit] + e for e in
                                    proposal.entries.get(orbit, [])])
        for first, nBursts in framing:
            if nBursts > 0 and usableRun(runs, first,
                                         first + nBursts - 1) is not None:
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
    order and consecutive gaps are counted -- up to sixDay days as a 6-day
    pair, up to twelveDay as a 12-day one.  Agreement with what was built by
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


def plotTrack(acquisitions, built, frames, origFrames, orbframes,
              proposal, args):
    '''
    Show the track: burst along x, orbit up y, one line per datatake.

    Each <orbit>-<seq> directory draws its burst coverage, coloured by its
    sequence number, so a split datatake is visible as two lines of different
    colour on the same row.  The pieces already built are drawn over the top,
    darker and thinner, with a tick at each start -- the start is the pairing
    key, so the ticks lining up down the plot is what a well framed track
    looks like.
    '''
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

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
        for i, start in enumerate(sorted(built.get(orbit, ()))):
            nBursts = lengths.get(start)
            if nBursts:
                axes.add_patch(Rectangle(
                    (start, row + 0.06), nBursts - 1, 0.26,
                    facecolor='#404040', edgecolor='white', linewidth=0.7,
                    hatch=SEGMENTHATCH[i % len(SEGMENTHATCH)], zorder=3,
                    label='built' if 'built' not in seen else None))
                seen.add('built')
            else:
                axes.plot([start], [row + 0.19], marker='|', markersize=5,
                          color='#404040', zorder=3)
        entries = [[orbit] + e for e in proposal.entries.get(orbit, [])]
        pieces = [(first, nBursts)
                  for first, nBursts in determineFraming(orbit, 0, frames,
                                                         entries)
                  if nBursts > 0 and usableRun(acq['runs'], first,
                                               first + nBursts - 1)]
        for i, (first, nBursts) in enumerate(sorted(pieces)):
            axes.add_patch(Rectangle(
                (first, row - 0.32), nBursts - 1, 0.26,
                facecolor='#C2185B', edgecolor='white', linewidth=0.7,
                hatch=SEGMENTHATCH[i % len(SEGMENTHATCH)], zorder=3,
                label='proposed' if 'proposed' not in seen else None))
            seen.add('proposed')
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
    else:
        plt.show()


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
    print(f'\npairs formed   built: {diskSix} six-day + {diskTwelve} '
          f'twelve-day = {diskSix + diskTwelve}'
          f'   proposed: {mineSix} + {mineTwelve} = {mineSix + mineTwelve}')
    n = max(len(rows), 1)
    print(f'vs frames+orbitframes: {counts["exact"]}/{len(rows)} orbits '
          f'match ({100. * counts["exact"] / n:.1f}%), '
          f'{counts["extra"]} extra, {counts["missing"]} missing starts')
    print(f'vs directories on disk: {counts["exactDisk"]}/{len(rows)} orbits '
          f'match ({100. * counts["exactDisk"] / n:.1f}%), '
          f'{counts["extraDisk"]} extra, {counts["missingDisk"]} missing')
    return counts


def processArgs():
    parser = argparse.ArgumentParser(
        description='Propose orbitframes entries from burst coverage. '
                    'Never writes -- all output goes to the terminal.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-lookBack', '--lookBack', type=int, default=0,
                        help='re-derive the last N acquisitions, ignoring '
                             'their existing orbitframes entries, and print '
                             'the comparison')
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
                        help='furthest a matching piece is worth adding, '
                             'which covers the 6- and 12-day pairs')
    parser.add_argument('-splitTol', '--splitTol', type=int, default=0,
                        help='if > 0, a satellite starting this many bursts '
                             'before the shared key also gets a piece at its '
                             'own start (try 4); off by default')
    parser.add_argument('-stubGap', '--stubGap', type=int, default=6,
                        help='bursts within which two starts are treated as '
                             'the same cut rather than separate frames')
    parser.add_argument('-snapTol', '--snapTol', type=int, default=3,
                        help='burst jitter treated as the same segmentation '
                             'rather than a new one')
    parser.add_argument('-runLength', '--runLength', type=int, default=3,
                        help='repeats before a deviation becomes the anchor')
    parser.add_argument('-keyFraction', '--keyFraction', type=float,
                        default=0.5,
                        help='how often a second start in a region must be '
                             'built, relative to the main one, to count as a '
                             'frame of its own rather than a one-off')
    parser.add_argument('-deriveWindow', '--deriveWindow', type=int,
                        default=40,
                        help='how many recent acquisitions define the '
                             'segmentation in use and the current coverage')
    parser.add_argument('-overrideFraction', '--overrideFraction', type=float,
                        default=0.5,
                        help='fraction of acquisitions overridden in '
                             'orbitframes above which `frames` is treated as '
                             'stale and the segmentation comes from the '
                             'overrides instead')
    parser.add_argument('-useFrames', '--useFrames', action='store_true',
                        help='take the segmentation from `frames` alone, '
                             'ignoring the velocityStats regions')
    parser.add_argument('-keepBroken', '--keepBroken', action='store_true',
                        help='propose pieces even when every frame of an '
                             'acquisition missed its anchor, instead of '
                             'skipping it as a broken acquisition')
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
    args = parser.parse_args()
    args.firstDate = datetime(1900, 1, 1) if args.firstdate is None else \
        datetime.strptime(args.firstdate, "%Y:%m:%d")
    args.lastDate = datetime(2100, 1, 1) if args.lastdate is None else \
        datetime.strptime(args.lastdate, "%Y:%m:%d")
    return args


def main():
    args = processArgs()
    if not os.path.isfile('frames'):
        sys.exit('*** no frames file -- run from a track directory')
    frames, orbframes = getFramesAndOrbits()
    if not frames:
        sys.exit('*** frames file has no frame-FIRST-N entries')
    acquisitions = getAcquisitions(args.firstDate, args.lastDate)
    if not acquisitions:
        sys.exit('*** no assembled <orbit>-<seq> directories found')
    gappy = [d for a in acquisitions for d in a['gappy']]
    if not args.dump:
        print(f'{len(acquisitions)} acquisitions, '
              f'{acquisitions[0]["date"].strftime("%Y-%m-%d")} to '
              f'{acquisitions[-1]["date"].strftime("%Y-%m-%d")}')
        print('frames: ' + ' '.join(f'{f}-{n}' for f, n in frames))
        if gappy:
            #
            # checkBurstTimes aborts the whole track on these no matter what
            # orbitframes says, so they need repairing rather than framing
            #
            print(f'\n*** {len(gappy)} directories have a hole in their burst '
                  'sequence and will stop setupSeveralTopsImages:')
            for slcDir in gappy:
                print(f'    {slcDir}')

    built = builtStarts()
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
    listed = {e[0] for e in orbframes}
    override = sum(1 for a in acquisitions if a['orbit'] in listed)
    fraction = override / len(acquisitions)
    regions = [] if args.useFrames else readVelocityStats()
    #
    # A short orbitframes means `frames` has been describing the track fine
    # for years and should be left alone.  Once most acquisitions need an
    # override, `frames` is the stale one and the segmentation actually in
    # use has to come from the overrides instead.  The regions are used for
    # containment either way -- they say how early a piece may start, which
    # `frames` alone cannot.
    #
    if regions and fraction >= args.overrideFraction:
        derived, regions, notes = deriveFrames(frames, orbframes, built,
                                               acquisitions, regions,
                                               args.deriveWindow,
                                               args.keyFraction,
                                               args.stubGap,
                                               allowed=allowed)
        if not args.dump:
            print(f'\n{override}/{len(acquisitions)} acquisitions '
                  f'({100 * fraction:.0f}%) are overridden in orbitframes, so '
                  'the segmentation is taken from it and the velocityStats '
                  f'regions rather than `frames` ({len(frames)} frame'
                  f'{"" if len(frames) == 1 else "s"}, {len(regions)} '
                  f'region{"" if len(regions) == 1 else "s"}):')
            for note in notes:
                print(note)
        frames = derived
    elif not args.dump:
        print(f'\nonly {override}/{len(acquisitions)} acquisitions '
              f'({100 * fraction:.0f}%) are overridden in orbitframes, so '
              '`frames` is taken as still describing the track'
              + (f', bounded by {len(regions)} velocityStats region'
                 f'{"" if len(regions) == 1 else "s"}' if regions else ''))
    if regions and len(regions) != len(frames):
        regions = alignRegions(frames, regions)
    frames, regions, looseNotes = addLooseKeys(frames, regions, built,
                                               acquisitions, allowed, args)
    if looseNotes and not args.dump:
        for note in looseNotes:
            print(note)
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
    #
    # Continuity between runs.  Whatever is proposed has to hook up with what
    # was built last time -- a key that appears one run and is gone the next
    # pairs with nothing, so any churn in the keys is reported up front.
    #
    if not args.dump:
        inUse = set()
        for acq in acquisitions[-args.deriveWindow:]:
            inUse |= built.get(acq['orbit'], set())
        proposedKeys = {f[0] for f in frames}
        introduced = sorted(proposedKeys - inUse)
        dropped = sorted(x for x in inUse - proposedKeys
                         if allowed[0] <= x <= allowed[1])
        if introduced:
            print('\n*** these keys are not among those recently built, so '
                  'they start new series that pair with nothing until they '
                  'have been built twice: ' +
                  ' '.join(str(x) for x in introduced))
        if dropped:
            print('\n*** these keys are in recent use but not in the proposed '
                  'segmentation, so the series built on them would stop: ' +
                  ' '.join(str(x) for x in dropped))
    outside = checkFramesFile(frames, built, acquisitions, args, regions)
    if outside and not args.dump:
        #
        # Nothing below can be proposed, so every piece built on one of these
        # starts will show up as "missing" in the comparison.  That is the
        # tool declining to invent a frame, not failing to find one.
        #
        print(f'\n*** `frames` describes {len(frames)} frame'
              f'{"" if len(frames) == 1 else "s"}, but '
              f'{len(outside)} start{"" if len(outside) == 1 else "s"} in use '
              f'{"lies" if len(outside) == 1 else "lie"} outside all of '
              'them. segmentTrack works only within the frames it is given '
              'and will not invent new ones, so these will report as '
              '"missing" until `frames` is corrected:')
        for start, count in outside[:10]:
            print(f'    burst {start} built for {count} orbits')
        if len(outside) > 10:
            print(f'    ... and {len(outside) - 10} more')

    def report():
        '''The terminal output, whichever mode was asked for'''
        if args.dump:
            proposal = proposeTrack(acquisitions, frames, orbframes, args,
                                    regions=regions, built=built,
                                    history=history, nOriginal=len(origFrames))
            for line in formatEntries(proposal, acquisitions):
                print(line)
            return

        if args.compare:
            proposal = proposeTrack(acquisitions, frames, orbframes, args,
                                    regions=regions, built=built,
                                    history=history, nOriginal=len(origFrames))
            rows = compareStarts(acquisitions, frames, orbframes, proposal,
                                 built, origFrames=origFrames)
            printComparison(rows, 'Whole record, existing vs proposed',
                            showOnly=args.quiet)
            if proposal.broken:
                print(f'\n{len(proposal.broken)} broken acquisitions skipped '
                      '(every frame missed its anchor): ' +
                      ' '.join(str(x) for x in sorted(proposal.broken)))
            return

        if args.lookBack > 0:
            seedThrough = max(0, len(acquisitions) - args.lookBack)
            proposal = proposeTrack(acquisitions, frames, orbframes, args,
                                    seedThrough=seedThrough, regions=regions,
                                    built=built, history=history,
                                    nOriginal=len(origFrames))
            print('\nProposed entries for the last '
                  f'{len(acquisitions) - seedThrough} acquisitions:')
            recent = {a['orbit'] for a in acquisitions[seedThrough:]}
            for line in formatEntries(proposal, acquisitions,
                                      onlyOrbits=recent):
                print(line)
            rows = compareStarts(acquisitions, frames, orbframes, proposal,
                                 built, window=seedThrough,
                                 origFrames=origFrames)
            printComparison(rows, f'Last {args.lookBack} acquisitions, '
                                  'existing vs proposed', showOnly=args.quiet)
            return
        #
        # Default: propose entries for the acquisitions not yet framed,
        # which is the same test setupSeveralTopsImages uses to decide what
        # is left to do.  Everything before that seeds the anchors.
        #
        unbuilt = [i for i, a in enumerate(acquisitions)
                   if not built.get(a['orbit'])]
        if not unbuilt:
            print('\nNothing to do -- everything has been framed already')
            return
        seedThrough = unbuilt[0]
        proposal = proposeTrack(acquisitions, frames, orbframes, args,
                                seedThrough=seedThrough, regions=regions,
                                built=built, history=history,
                                nOriginal=len(origFrames))
        newOrbits = {acquisitions[i]['orbit'] for i in unbuilt}
        print(f'\n# Proposed entries for {len(newOrbits)} unframed '
              'acquisitions')
        for line in formatEntries(proposal, acquisitions,
                                  onlyOrbits=newOrbits):
            print(line)
        #
        # A new deviation can also need a matching piece in an older
        # acquisition already framed.  Those are additions, never edits, but
        # they do mean building another directory, so they are listed apart.
        #
        older = set(proposal.entries) - newOrbits
        mateLines = formatEntries(proposal, acquisitions, onlyOrbits=older)
        if mateLines:
            print('\n# Matching pieces needed in acquisitions already framed')
            for line in mateLines:
                print(line)


    report()
    if args.plot or args.plotFile:
        #
        # After the report, not instead of it.  -lookBack narrows the plot the
        # same way it narrows the comparison; a decade on one axis is
        # unreadable
        #
        shown = (acquisitions[-args.lookBack:] if args.lookBack > 0
                 else acquisitions)
        #
        # Seed up to the start of what is shown, so every plotted acquisition
        # carries a proposal to draw
        #
        plotProposal = proposeTrack(acquisitions, frames, orbframes, args,
                                    seedThrough=len(acquisitions) - len(shown),
                                    regions=regions, built=built,
                                    history=history,
                                    nOriginal=len(origFrames))
        plotTrack(shown, built, frames, origFrames, orbframes,
                  plotProposal, args)

if __name__ == '__main__':
    main()
