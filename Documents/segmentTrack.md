# segmentTrack

Proposes `orbitframes` entries for a track from the burst coverage that
`catMultipleTops`/`computeBurstTimes` already record in each `<orbit>-<seq>` directory. Runs before
`setupSeveralTopsImages.py`, which consumes `frames` and `orbitframes`.

**This program never writes anything.** Every mode prints to the terminal, so it is safe to run
against live track directories.

Run from the track directory (the one holding `frames`, `orbitframes`, `ascending`/`descending`
and the `<orbit>-<seq>` directories).

---

## Usage

```
segmentTrack.py [options]
```

No positional arguments. With no options it proposes entries for the acquisitions that have not
been framed yet — the same "no `<orbit>_<firstBurst>` directory" test `setupSeveralTopsImages`
uses. Everything earlier in the record is used to learn the current starts.

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-lookBack N` | 0 | Re-derive the last N acquisitions, ignoring their existing `orbitframes` entries, and print the proposal next to what was built |
| `-compare` | False | Re-derive the **whole** record and compare against the existing files |
| `-dump` | False | Print the proposed entries for the whole record, as a complete replacement `orbitframes` |
| `-plot` | False | Show the track as a plot (see below) |
| `-plotFile NAME` | — | Save the plot to a file instead of showing it |
| `-quiet` | False | Comparison summary only, no per-orbit table |
| `-firstdate YYYY:MM:DD` | — | Only consider acquisitions on or after this date |
| `-lastdate YYYY:MM:DD` | — | Only consider acquisitions on or before this date |
| `-minBursts N` | 4 | Shortest piece worth building |
| `-mateSpan N` | 2 | Acquisitions each side that get a matching piece; 2 covers the 6-day and 12-day pairs |
| `-maxMateDays N` | 13 | Furthest a matching piece is worth adding |
| `-splitTol N` | 0 (off) | If > 0, a satellite starting more than this many bursts before the shared key also gets a piece at its own start (try 4) |
| `-snapTol N` | 3 | Burst jitter treated as the same segmentation rather than a new one |
| `-runLength N` | 3 | Repeats before a changed start becomes the anchor |
| `-overrideFraction F` | 0.5 | Fraction of acquisitions overridden in `orbitframes` above which `frames` is treated as stale |
| `-keyFraction F` | 0.5 | How often a second start in a region must be built, relative to the main one, to count as a frame of its own |
| `-deriveWindow N` | 40 | How many recent acquisitions define the segmentation in use |
| `-useFrames` | False | Take the segmentation from `frames` alone, ignoring the velocityStats regions |
| `-keepBroken` | False | Propose pieces even when every frame of an acquisition missed its anchor |

---

## Where the segmentation comes from

`frames` is written once and goes stale as coverage is extended over the years, so it is not
trusted blindly:

- **Short `orbitframes`** (below `-overrideFraction`) means `frames` has been describing the track
  fine and is used as-is.
- **Most acquisitions overridden** means `frames` is the stale one. The segmentation is then taken
  from the `velocityStats/<lo>-<hi>` directories combined with the starts and lengths actually in
  use.

A velocityStats directory is how the data are **bunched for the autocleaning**, so its range can
hold **one or more** frames — the count is derived from the data, never assumed to be one. Within
each region:

- Every start with a real history there becomes a frame, not just the most used one. A region
  routinely carries several keys: the two satellites are staggered (track-127 runs 644 alongside
  649), or a frame is deliberately cut in two (track-83 runs 668 alongside 671). Dropping a
  long-standing key would orphan every piece already built on it. "Real history" means built at
  least `keyFraction` as often as the region's main start — a genuine second key belongs to one of
  the two satellites and so appears in roughly half the acquisitions, whereas a looser threshold
  turns one-offs into frames and over-produces.
- The **start** counts only the last 40 acquisitions, so one abandoned years ago does not come
  back.
- The **length** is the furthest that start has been taken **within the same window**, from the
  `setup_` scripts and the `orbitframes` entries of those acquisitions. The furthest ever is too
  generous: track-3 cut `452` to 24 bursts on four orbits years ago and to 19 or 20 ever since, so
  the all-time maximum would add five bursts of ocean to every new frame.
- A region nothing starts in any more gets no frame rather than an invented one.

Where a region carries several keys and one of them cannot be reached in an acquisition, that
frame is skipped rather than re-anchored: re-anchoring exists to rescue data that would otherwise
be lost, and a sibling key in the same region already covers it. For the same reason a re-anchor
that would land within `stubGap` of another frame's key is dropped — that is the same cut
jittered, not a new one.

### Keys no frame produces

Whichever way the segmentation was arrived at, a start still being built that **no frame produces**
is kept as a frame of its own, and the gap is reported:

```
(no frame covers it)  ->  frame-662-10  (built for 20 of the last 40)
```

This matters more than tidiness: abandoning such a key stops the series running on it, which on a
two-key track halves the pairs. It catches both a key falling outside every velocityStats region
(track-83 builds 576, below its first region) and a key inside a region that simply has no frame
cut there (track-68 builds 662 where `frames` names only 671). Note that "covered" means a frame
actually produces the start — a region merely bracketing it is not enough.

The derived segmentation is printed at the top of every run.

Nothing is proposed outside the **historical extent** — the furthest the track has ever been cut.
That boundary is normally a deliberate choice rather than an accident: acquisitions routinely run
on past the area of interest into ocean, and the track was cut short to keep that out of the
processing (track-39 stops at burst 720 although the data reaches 725). Coverage also grows over
the years, and a piece built on bursts nothing else covers could never pair anyway.

The extent is reported, with how many bursts per acquisition fall outside it, so that a track
which really has been extended is noticeable — not because the excluded bursts are a loss.

Once the derived segmentation differs from `frames`, every piece is written out explicitly and any
surplus `frames` index is skipped. `setupSeveralTopsImages` expands `orbitframes` against `frames`
as it stands, so a piece left implicit would fall back to a stale default and abort the run.

---

## What it is solving

The reframed output directory is `<orbit>_<firstBurst>`, and pairing (`grepdate.findPair`) matches
that name exactly. So **`firstBurst` is the pairing key and `nBursts` is free** — a piece can be
shortened as much as the data requires and still pair, but a shifted start makes a new key and
needs matching pieces in the neighbouring acquisitions before any pair can form.

Rules applied, in the order they matter:

1. **Trim, don't move.** If only the tail is missing, shorten the piece and keep the start.
2. **Skip** (`nBursts=0`) when nothing usable is left inside the frame.
3. **Stay inside the frame.** A piece never extends past its frame end, and a run covering two
   frames yields a piece in each rather than one straddling piece — the autocleaning works on
   per-frame bounded regions. Where the velocityStats regions are known they set how early a piece
   may start, since any start inside a region is legitimate.
4. **Learn the start**, as described above. `frames` is never modified.
5. **Re-anchor on a persistent change**, not a one-off: the next `runLength` acquisitions must
   agree to within `snapTol`. Bridging pieces are added across the changeover.
6. **Matching pieces.** A one-off shifted start is copied into the neighbouring acquisitions within
   `mateSpan`/`maxMateDays` so it can pair.
7. **Stagger** (opt-in, `-splitTol 4`). Where one satellite's bursts systematically start well
   before the shared key, it also gets a piece at its own start: the shared key pairs it across
   missions, its own start pairs it down its own series.
8. **Broken acquisitions.** If every frame of a multi-frame track missed its start, the acquisition
   is skipped and reported rather than rescued — it needs re-downloading, and rescuing it would add
   a new key to every neighbour.

---

## Reading the comparison table

```
date         orbit  available        onDisk  start(nBursts)        %  proposed  start(nBursts)      %  verdict
2026-03-27   63811  661-725          661(9),669(29)              57%  661(37)                     57%  missing 669
```

- **available** — burst coverage of the assembled SLCs, from the `.btimes` files (the same range
  the `frames.FIRST.LAST` sentinel records).
- **onDisk** — the pieces actually built, i.e. the `<orbit>_<firstBurst>` directories, with lengths
  read from their `setup_<orbit>_<start>` scripts. Where the `frames`+`orbitframes` expansion
  disagrees with what was built, the verdict says so in brackets; it normally agrees.
- **%** — percentage of the acquisition's available bursts the pieces take in, counted as a union
  so overlaps are not double counted. This is the column that says whether a difference matters:
  above, one piece covers exactly as much as two.
- **verdict** — differences in the pairing keys only, since lengths cannot affect pairing.

---

## The plot

`-plot` draws burst along x and orbit up y, and prints the usual report as well — it adds to the
text output rather than replacing it. Each row carries three things:

- the **datatake coverage**, one band per `<orbit>-<seq>` directory coloured by its sequence
  number with a legend, so a split datatake shows as two colours on the same row;
- the **pieces already built**, dark, above the coverage;
- the **pieces that would be cut**, in crimson, below it.

Built and proposed segments alternate their fill (plain, hatched, dotted) and carry a white edge,
because pieces cut back to back would otherwise read as one long piece. Dashed vertical lines mark
the frame starts, labelled along the top — that is the segmentation itself. Since the start is the
pairing key, cuts lining up down the plot is what a well framed track looks like, and a row missing
a crimson segment that its neighbours have is a pair that will not form.

Rows are evenly spaced in **date order** and labelled with the actual orbit number. The orbit is
not used as a linear axis because the satellites number their orbits in ranges tens of thousands
apart, which collapses the rows into two distant bands; the labels therefore run 4-digit and
5-digit numbers interleaved, which is correct.

Combine with `-lookBack N` to narrow the plot to the last N acquisitions — a decade on one axis is
unreadable.

---

## Diagnostics

- **Directories with a hole in their burst sequence.** `checkBurstTimes` aborts the whole track on
  these whatever `orbitframes` says, so they are excluded from the coverage and listed for repair.
- **Starts in use that no frame can produce.** segmentTrack works only within the frames it has and
  will not invent new ones, so these report as "missing" until a region or `frames` entry covers
  them.

---

## Part of the s1setup package.
