# segmentTrack

Proposes `orbitframes` entries for a track from the burst coverage that
`catMultipleTops`/`computeBurstTimes` already record in each `<orbit>-<seq>` directory, then runs
`setupSeveralTopsImages.py` over the framing it proposed — checking it in every mode, and building
the setup scripts when the entries are committed.

**Nothing is written unless a write mode is asked for** — `-commit`, `-refreshLinks`, `-undo` or
`-undoLinks`. Without one, every mode only prints to the terminal, so it is safe to run against
live track directories. See [Writing the result](#writing-the-result) and
[The setup step](#the-setup-step).

Run from the track directory (the one holding `frames`, `orbitframes`, `ascending`/`descending`
and the `<orbit>-<seq>` directories).

---

## Usage

```
segmentTrack.py [options]
```

No positional arguments. With no options it re-derives the last 12 acquisitions, ignoring their
existing `orbitframes` entries, and prints the proposal next to what was built. Only the
acquisitions not yet built can be written; the rest are derived for the comparison alone.

An older acquisition that has never been framed is taken in as well, however far past those 12
it is, as long as the setup check reads it — see
[Reaching back over what was never framed](#reaching-back-over-what-was-never-framed). It is
listed in a section of its own, so what is committed is still what was shown.

`-lookBack 0` gives the other behaviour: propose only for the acquisitions that have not been
framed yet — the same "no `<orbit>_<firstBurst>` directory" test `setupSeveralTopsImages` uses —
and list separately any matching pieces needed in acquisitions already framed.

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-lookBack N` | 12 | Re-derive the last N acquisitions, ignoring their existing `orbitframes` entries, and print the proposal next to what was built; `0` proposes for the not-yet-framed acquisitions instead. An older acquisition the setup check reads and nothing has framed is taken in whatever N says |
| `-compare` | False | Re-derive the **whole** record and compare against the existing files |
| `-dump` | False | Print the proposed entries for the whole record, as a complete replacement `orbitframes` |
| `-plot` | False | Show the track as a plot (see below) |
| `-plotFile NAME` | — | Save the plot to a file instead of showing it |
| `-quiet` | False | Comparison summary only, no per-orbit table |
| `-firstdate YYYY:MM:DD` | — | Only consider acquisitions on or after this date |
| `-lastdate YYYY:MM:DD` | — | Only consider acquisitions on or before this date |
| `-minBursts N` | 4 | Shortest piece worth building |
| `-maxBursts N` | 76 | Longest piece worth cutting; a longer run is broken into equal pieces sharing a burst at each seam |
| `-refBack N` | 3 | How many built acquisitions back count as this one's neighbours; 3 covers S1A/S1C/S1D interleaving. An older acquisition can still supply the framing where none of these covers the same bursts |
| `-maxPairDays N` | 36 | Longest gap that still pairs where no data lies between, so a missed cycle or two still forms the 24- and 36-day pairs |
| `-refSearch N` | 40 | How many older built acquisitions to consider where the neighbours frame little of this one |
| `-mateSpan N` | 2 | Acquisitions each side that get a matching piece; 2 covers the 6-day and 12-day pairs |
| `-maxMateDays N` | 13 | Furthest a matching piece is worth adding **where there is intervening data to pair with instead** |
| `-snapTol N` | 3 | Burst jitter treated as the same segmentation; also how far two lengths for one key may differ before the shorter is taken and the surplus given its own piece |
| `-boxOverrun N` | 5 | How far a piece invented here may run past a velocityStats box boundary before it is broken there instead |
| `-deriveWindow N` | 40 | How many recent acquisitions the report describes as the current state of the track |

### Write modes

| Option | Default | Description |
|--------|---------|-------------|
| `-commit` | False | Write the entries just proposed into `orbitframes` — replacing the lines of the not-yet-built orbits, appending to the built ones, leaving every other line alone — then build the setup scripts for them |
| `-noSetup` | False | Stop after `orbitframes`: the framing is still checked with `setupSeveralTopsImages`, but no `setup_` scripts and no `runSetup` driver are written |
| `-acceptGaps` | False | Go on without asking where a burst is missing from an assembled SLC (see [A burst that really is missing](#a-burst-that-really-is-missing)); the frames clear of the hole are cut as they always were, the ones over it still cannot be built |
| `-undo` | False | Restore the `orbitframes` from before the last `-commit`, saying how far back that is and asking before writing; repeat to step further back |
| `-undoNoPrompt` | False | `-undo` without the confirmation prompt, for a run with nobody at the terminal |
| `-refreshLinks` | False | Link the assembled `<orbit>-<seq>` directories that have no link here yet |
| `-linkAll` | False | With `-refreshLinks`, link every unlinked directory whatever its age |
| `-undoLinks` | False | Remove the links made by the last `-refreshLinks` |
| `-maxLinkAgeDays N` | 365 | `-refreshLinks` skips assembled directories older than this; from the nearest `project.yaml` `maxLinkAgeDays` key if there is one |
| `-assemblyDir PATH` | inferred | Assembly track directory; from the nearest `project.yaml` `assemblyDir` key, else inferred from the existing links — where those point into more than one tree, the `project.yaml` `defaultAssemblyDirectory` key settles it |

---

## Writing the result

The routine update is three steps:

```
segmentTrack.py -refreshLinks     # take in newly assembled acquisitions
segmentTrack.py                   # read the proposal and its setup check
segmentTrack.py -commit           # write it, and build the setup scripts
```

### `-refreshLinks`

`setupTrack` builds `<orbit>-<seq>` under the **assembly** directory
(`/Volumes/insar8/ian/Data/SentinelGreenland/track-N`, the `assemblyDir` in `autoupdate.yaml`),
and the processing track directory reaches it through a per-orbit symlink. Nothing else creates
those links, and segmentTrack cannot see an acquisition until one exists — a missing link
silently shrinks the proposal rather than reporting anything.

Only directories assembled within `-maxLinkAgeDays` (365 by default) are linked. Where a track
was cut short years ago its old assembled directories were deliberately never linked, and
linking them now would put long-dead acquisitions back into the record; the count skipped is
reported, and `-linkAll` overrides it. The age comes from the **mtime of the assembled
directory** — when it was put together — not the acquisition date, so processing ten-year-old
data for a new region links normally.

**Only directories that still hold their swath SLCs are linked.** Cleaning the SLCs away leaves
the metadata behind — `.slc.par`, `.tops_par`, `.btimes`, `ascendingNodeTime`, the burst files —
so the directory goes on looking assembled long after there is anything to cut a piece from.
Linking one would put an acquisition into the record that no setup can build. The count is
reported rather than passed over, since an acquisition missing from the proposal because its SLCs
were cleaned looks exactly like one that was never assembled:

```
    337 assembled directories hold only metadata, their SLCs having been cleaned away, and cannot be built from: 10091-0 10137-0 ...
```

This applies to `-linkAll` too — that only overrides the age cutoff.

An existing link is never replaced, including a broken one: it was made on purpose. Broken links
are ignored when the assembly directory is inferred, so one cannot stop a run.

A track moved between volumes over the years has links into both, and there is then no single
directory to infer. Rather than stopping, which would leave the track unrefreshable, the choice is
reported in bold and made from the nearest `project.yaml` **`defaultAssemblyDirectory`** key —
joined with the track name, the tree the project is linked into now:

```
*** the existing links point into more than one assembly directory; taking the project.yaml defaultAssemblyDirectory -- pass -assemblyDir to use another:
    --> /Volumes/insar8/ian/Data/SentinelGreenland/track-90 (300 links, newest 2026-08-11)
        /Volumes/insar3/ian/Data/Sentinel1Greenland/track-90 (39 links, newest 2024-09-21)
```

Without that key, or where it names none of the directories this track is linked into, the **most
recently assembled** one is taken instead: new assemblies land wherever the last one did, and that
is the only place `-refreshLinks` has anything to find. `-assemblyDir` overrides either.

`defaultAssemblyDirectory` is not an answer on its own, unlike `assemblyDir` — it only settles
which of the directories a track is *already* linked into is the current one.

### `-commit`

Merges by orbit. Every line for an orbit the proposal covers is replaced; hand comments, blank
lines and entries for orbits not proposed are kept exactly as they were. The provenance comments
segmentTrack itself writes (`# <orbit>-<seq>/frames.<first>.<last>`) are replaced along with
their entries, so committing twice leaves the file unchanged rather than stacking up copies.

**An orbit framed exactly as `frames` gets no lines at all.** `determineFraming` starts from
`frames` and applies the overrides on top, so the default already produces that framing and
saying it again in `orbitframes` is noise. Such an orbit is left out of the proposal entirely and
its part of the file is not touched.

**An orbit that is listed states every `frames` index.** Once any one frame differs the whole
default is in play, and a frame left unlisted quietly falls back to its `frames` entry — where
the acquisition cannot cover that entry, `checkRange` finds no override and aborts the whole
track. So if `frames` has 3 entries and only 2 chunks can be cut, the third is written as
`<orbit>-3-<first>-0`; `NumberFrames` of 0 is how `setupSeveralTopsImages` is told to skip a
frame. Extra frames (index above the number in `frames`) keep their own index and always force
the orbit to be listed.

The two rules together are framing-preserving: what `determineFraming` returns for every orbit is
identical to what it returned before them, with the redundant lines gone.

What gets committed is what was shown: the default `-lookBack 12` writes the re-derived tail,
`-lookBack 0` writes the unframed acquisitions plus the matching pieces, and `-dump -commit`
writes the whole record — still keeping hand entries for any orbit the proposal has nothing to
say about. Because the default re-derives, a default `-commit` can **replace** entries for
acquisitions already framed, not just add new ones; read the comparison table before committing.

### The setup step

Every mode but `-dump` finishes by running `setupSeveralTopsImages` over the framing just
proposed. The proposal is merged into `orbitframes` **in memory** first — exactly the merge
`-commit` would write — so the framing being checked is the one being proposed, whether or not it
is committed.

That first pass is a check: it writes nothing, and it runs the whole track rather than stopping at
the first bad frame the way `setupSeveralTopsImages` does on its own, so one broken orbit no
longer hides everything behind it. It reports how many pieces would be set up, how many are
already built, and then every failure it found:

```
Checking the proposed framing with setupSeveralTopsImages (acquisitions since 2025-12-27, -firstdate reaches further back):
    12 pieces would be set up, 30 already built
    3 scripts for pieces never built lie outside these dates and were left alone: setup_57773_618 ...
```

**The setup step covers the last 3 months of the record**, counted back from the most recent
acquisition that has been built. A track carries a decade of acquisitions, and the old ones were
framed against SLCs that have been re-downloaded since, so checking them reports problems that are
neither new nor worth fixing — and one of them would block the commit. `-firstdate` (with
`-lastdate`) overrides the window: it already says which acquisitions to consider, so giving it is
how an older stretch, or the whole record, is deliberately taken in. A track with nothing built
yet is not narrowed at all.

### Reaching back over what was never framed

That window is normally wider than the stretch `-lookBack` re-derives, and the two have to
agree on one thing: everything the check reads must have been framed by somebody. An
acquisition with no `orbitframes` lines of its own is not skipped by `determineFraming` — it
falls back to **every** default `frames` entry, and a default its coverage cannot carry raises
`not in burst time range` and stops the commit for every other acquisition too.

So the derivation reaches back to the earliest acquisition in the check window that still has its
SLCs and has nothing the check can read — unbuilt, or built with no `orbitframes` lines of its
own (see [Acquisitions already built are never
rewritten](#acquisitions-already-built-are-never-rewritten)) — and frames those as well:

```
10 acquisitions older than the last 12 have never been framed, and the setup step checks them
too, so they are framed here as well (back to 2026-04-06):
# 63957-0/frames.669.724
63957-1-669-5
63957-2-674-44
63957-3-0-0
...
    6 of them already have orbitframes lines, which are replaced by what is derived here;
    nothing was ever built from them, so no key that pairs can move: 7181 7356 7531 ...
```

The zero-length lines are the fix: `checkRange` skips a frame of no bursts, so a `frames` entry
this acquisition cannot cover stops being asked of it.

Nothing already framed is disturbed by the wider reach. Only built acquisitions carry a framing
forward (`referenceFraming`), so an acquisition pulled in here can never become the reference
for another, and the entries proposed for the last N are identical either way. A built
acquisition inside the reach keeps its own lines as always. On a track with no backlog the
reach changes nothing at all.

Setup scripts waiting on pieces outside those dates are reported and left alone — nothing in the
window replaced them, so they are not cleaned away either.

**A piece already built is not range checked.** There is nothing to cut, so whether it *could* be
cut is not a question worth asking — and the SLCs are re-downloaded from time to time and come
back covering fewer bursts than the piece was cut from, which used to stop the run over a
directory that exists and pairs. It is reported instead, since it does mean the piece could not
be produced again from what is on disk now:

```
    18 pieces would be set up, 39 already built
    6 pieces already built are no longer covered by the SLCs now on disk, which have been
    re-downloaded since; left as they are, but they could not be cut again:
    7108_675(596-713) 7283_675(596-713) ...
```

**If anything failed, nothing is written at all** — not `orbitframes`, not a single script — and
the run exits non-zero:

```
*** setupSeveralTopsImages reports 1 problem with this framing:
    4223-0 frame 9000-40: ***** Frames 9000 to 9039 for 4223-0 not in burst time range 623 to 668 ...
        this is the framing proposed here -- fix `frames` or `orbitframes` and run again

nothing written -- neither orbitframes nor any setup script. Fix `frames` or `orbitframes` and run again.
```

The failures are reported in red, never worked around: the fix belongs in `frames` or
`orbitframes` and is the user's call. The kinds are the ones `setupSeveralTopsImages` has always
raised — a frame outside the burst range, a gap in the burst times, a missing `.btimes` file, an
unreadable `ascendingNodeTime`.

The check reads more of the record than any one run proposes for, so the second line says which
kind of failure it is and therefore where the fix belongs:

| what it says | what it means |
|---|---|
| this is the framing proposed here | the derivation is wrong for an orbit this run would write |
| already built, so its orbitframes lines are never rewritten here | fix that orbit's lines by hand — segmentTrack will not touch them |
| a frame is cut over a burst that is missing | reframe it either side of the hole, or re-assemble the acquisition — a hole no frame crosses does not get here |
| it has no burst times to frame from | a data problem; the directory needs repairing, no framing answers it |
| nothing is proposed for it | it is checked against `frames` as it stands — give it lines by hand, or reach it with `-firstdate`/`-lookBack` |

A burst-numbering slip never gets this far: it is repaired before the coverage is used — see
[Slipped burst numbering](#slipped-burst-numbering).

With a clean check and `-commit`, the entries are written and then the scripts:

```
    15 pieces set up, 109 already built
    scripts listed in runSetup.Aug:17:26:08:31:09
    removed 7 stale files: setup_57773_618 ... runSetup.Oct:16:25:16:28:46
```

**Repeat runs leave one current set.** After writing, the superseded setup scripts are removed —
those of pieces never built (no `<orbit>_<firstBurst>` directory), for the orbits this run covered
and not just written. Scripts for pieces already built are left alone: they are the record of how
that piece was cut, and segmentTrack reads them for the burst count the directory name does not
carry. Running twice therefore gives the same scripts, not a pile of them for framings that were
superseded.

An earlier `runSetup.*` is removed **only when this run has made it redundant** — every script it
lists is either being written again now or has since been built. Age is not the test: a run file
still pointing at a piece nobody has built is the only record of that work, so it is kept and the
reason printed:

```
    removed 3 superseded files: runSetup.Feb:08:26:14:30:29 runSetup.Feb:23:26:08:35:12 ...
    kept runSetup.Oct:16:25:16:28:46: 3 pieces it lists are neither built nor in this run: setup_57773_618 ...
```

A file that overlaps this run only partly is kept for the same reason — the overlapping half is
regenerated, and re-running a setup script is harmless, but the rest of it exists nowhere else.

`-noSetup` stops after `orbitframes`: the check still runs, so the entries are still validated
before they are written, but no scripts are produced.

### `-undo` and `-undoLinks`

Each `-commit` first copies `orbitframes` to `orbitframesBackup/orbitframes.bak.<stamp>`, and
`-refreshLinks` records what it created in `orbitframesBackup/links.<stamp>`. `-undo` restores
the newest backup and drops it, so repeating it steps further back; `-undoLinks` removes the
links from the last refresh, leaving alone any that have been repointed by hand since. The two
are separate: `-undo` never touches links.

`-undo` names the backup it is about to put back and dates it before writing anything —

```
-undo puts back the orbitframes as it stood at 2026-08-25 09:14:02, 2 days 5 hours ago
    everything committed since then is discarded (orbitframesBackup/orbitframes.bak.20260825T091402)
    restore it? [y/N]
```

— because the stack goes back as far as the commits do, and the newest backup is only this
session's work if this session is what last committed. Anything but `y`/`yes` leaves
`orbitframes` alone. `-undoNoPrompt` does the same restore without asking; a run with no
terminal to ask on answers no, so that is the flag for a script.

---

## Slipped burst numbering

A directory whose burst numbers do not run consecutively is usually not missing anything:
`computeBurstTimes` used to round each burst separately, so a swath whose times sit near a
half-period boundary numbered two bursts the same, or stepped over one (see
[computeBurstTimes](computeBurstTimes.md#how-bursts-are-numbered)).

Those are repaired **before anything is derived from the coverage**, so the acquisition is framed
in the same run:

```
*** 7342-0: the burst numbering was wrong and has been corrected
    20260423-0_iw1_hh.btimes: corrected from burst 8: burst number 647 was counted twice, so 647 -> 648 and +1 to the end (last burst 677 -> 678)
4 acquisitions, 2026-04-11 to 2026-05-17
...
committed 4 entries for 4 orbits
```

Only directories this run would actually read are touched — those inside the
[setup step's window](#the-setup-step), never a sweep of the tree, and never one whose
SLCs have been cleaned away. A track carries a decade of acquisitions and the old ones were
assembled from SLCs long since deleted, so a hole in one of those is neither new nor anything
this run can act on. Three cases are reported and left alone:

- recomputing gives the same numbering, so a burst really is missing — see
  [A burst that really is missing](#a-burst-that-really-is-missing);
- recomputing would move the *first* burst number, which is the piece key;
- the `.tops_par` files or `ascendingNodeTime` are not there to recompute from.

`-dump` skips the repair, so it stays a clean replacement `orbitframes` on stdout.

### A burst that really is missing

A burst lost during assembly is rare, and the numbering records it correctly: the frame counter
steps over the burst that is not there (`... 566, 568 ...`). What it costs is only the frames cut
**over** the hole. A frame wholly on either side of it is cut exactly as it always was, because
`makeSetupFile` reads the burst positions back out of the `.btimes` rather than trusting
`setuptopsimage`, which works a position out as `frame − firstFrame + 1` and so slides everything
past a hole one burst late:

```
setup_65023_573: 1 burst missing before this piece, so the burst positions were
corrected: firstBurst 17 -> 16, lastBurst 61 -> 60
```

On an unbroken acquisition the two agree and nothing is rewritten.

So the hole is reported, drawn against the frames being cut, and put to the user rather than
ending the run:

```
*** 65023-0: burst 567 is missing from the assembled SLC
    the numbering steps over the hole, which is the record of what is not there; it is the
    frames cut over it that cannot be built
    frame 573-617 is clear of it and is cut as it always was

go on with these acquisitions? [y/N]
```

Anything but `y`/`yes` stops the run and writes nothing, and a run with no terminal to ask on
answers no — so `-acceptGaps` is how an unattended run says yes in advance. A frame that *does*
run over the hole is still refused by `checkBurstTimes`, and reframing it to sit either side of
the hole, or re-assembling the acquisition, is the fix.

---

## Stale links

Most of a mature track has been cleaned: the SLCs are deleted once the pieces are built, leaving
the `.slc.par`, `.tops_par`, `.btimes`, `ascendingNodeTime` and burst files behind. Those
directories still parse, so they used to be framed like any other — and the setup step then wrote
`setup_` scripts for pieces nothing could build.

A linked directory with no swath SLCs left is now **kept as history but never proposed for**:

- its burst coverage still feeds `historicalRange`, the framing carried forward and the
  recent-acquisitions window, which is what the whole proposal is learned from;
- no piece is proposed for it and no matching piece is given to it;
- `-lookBack 0` does not count it as an unframed acquisition to do work for;
- the setup step skips it, so no `setup_` script is written for it.

The count is reported up front:

```
80 acquisitions, 2015-01-18 to 2026-03-27
77 linked directories hold only metadata, their SLCs having been cleaned away; the 77 acquisitions
with nothing left to cut still count towards the history, but nothing is proposed or set up for them
```

`-refreshLinks` applies the same test before making a link at all (see above), so a track linked
from scratch does not take in hundreds of empty directories.

---

## Where the segmentation comes from

The framing is **carried forward from the last built acquisition that matches**, not derived from
the record at large. What matters is that a new acquisition pairs with the ones around it, and a
pair only forms where two acquisitions carry the same `firstBurst`, so the acquisition just before
this one is the only thing that can define its keys.

**Which one** is decided by `carriedCoverage`: how many of this acquisition's bursts each
candidate's keys would actually cut, once each key is trimmed to the data. Among candidates within
a margin of the best, the **most recent wins** — the framing in current use is the one to carry on.
The satellites interleave and do not cover the same bursts, so this is what keeps them apart: on
track-31 S1A's keys (413, 442) cut 82 of S1C's 98 bursts where S1C's own (398, 442) cut all 98, so
S1C keeps its own framing. Its `-refBack` neighbours are all considered, which is what lets a
coverage that comes round every second or third acquisition find its match.

Coverage *equality* is the wrong test, and was the first thing tried: track-163's orbit 63410
covers 355-436 and the framing that fits it is the 2022 one, `358-60` alongside `417-62`, whose own
acquisitions ran to 478. Trimmed to 436 that is exactly what 63410 was built as, but an equality
test rejected it in favour of an older acquisition that happened to stop in the same place.

Where nothing is close enough to pair with at all, the most recent built acquisitions are tried
first all the same — they are still the framing in use. Picking on coverage across the whole record
instead hands the framing to whichever old one cuts the most ground: track-61 stopped cutting bursts
355-374 in 2025, so a framing from before that covers *more* of an acquisition than the one actually
in use, and reaching back for it dropped the `429` key the rest of the track pairs on.

Where the neighbours frame **less than `REACHBACKFRACTION` (0.5) of this acquisition's own data**
and an older framing covers more, the framing is taken from the older one instead. On track-163 the
acquisition six days before 63410 is 22 bursts long and frames 12 of its 82; without this, 63410
came out as five pieces, four of them stubs. The test is against the acquisition's own data rather
than against the best framing available, so a track whose framing is merely in the middle of
changing does not trip it. How far back it reached is reported, and where that is beyond
`-maxPairDays` it is called out — the series it carries on stopped that long ago, so the new pieces
pair only with each other.

**How close counts as a pair** is `pairable()`: 12 days is the repeat, and where a cycle or two
has been missed the 24- and 36-day pairs are still worth forming, but only where nothing lies
between — with intervening data, that is what each of them pairs with. The gaps are not only
multiples of 6 and 12: with S1A, S1C and S1D all operating (April–June 2026) pairs a day apart
turn up.

This is what makes the two pairings work together. On track-31 the shared key `442` gives the
6-day S1A↔S1C pair over 442-494, while `398` (S1C only) and `413` (S1A only) each pair their own
satellite twelve days apart over 398-441 and 413-441. The 12-day keys sit **inside** the ground a
6-day key covers, which is why an overlap is not treated as redundancy (below).

**Lengths** are reconciled across the neighbouring framings, but **only a piece that stopped
short of its own data counts**. One that ran to the end of its acquisition was simply trimmed
there and says nothing about how the key is framed: on track-148 S1A stops at 408 and cuts `373` to
36 bursts where S1C runs it to 50, and on track-163 a 22-burst acquisition cuts `358` to 12.
Reading either as a framing decision shortens the key for everyone and leaves a gap behind it to be
filled with stubs — which is exactly what both tracks showed. Among the pieces that do count:

- Differing by no more than `snapTol` — `442-54` next to a `442-53` — the longer is taken, and an
  acquisition whose data cannot reach it is simply shortened. Only the start names the directory,
  so a piece a burst or two short still pairs.
- Differing systematically — `54, 34, 54, 34` — the **shorter** is taken, so every acquisition
  pairs over the whole of its piece, and the surplus becomes a piece of its own at the seam
  (`442-34` on all of them, plus `475-21` on the long ones).

An established key is otherwise left exactly as it was cut, boxes and all: the framing stays the
one the track has been processed on.

### A key that falls before the data

Coverage steps around over the years, and a key can end up before the first burst an acquisition
holds — track-45 was cut on 419 for years until the coverage began at 421 instead. Dropping the
key loses that ground for good, so where the frame still reaches into the data the piece is
**moved onto the first burst there is**. A start that moves makes a new key, but the acquisitions
around it move the same way, so the new key pairs; on track-45 every acquisition since has been
cut on one.

The moved start then **snaps to a start the track already uses** where one is within `snapTol`,
in preference to the first burst of the data: that is a key pieces exist on and pair with, where
the first burst is a key of nobody's. Track-45's data begins at 421 and its run was cut at 422,
which is what the snap reproduces.

Without this the key was simply unreachable, every recent acquisition scored zero coverage, and
the reach-back below fired and framed orbit 41067 off an acquisition from 2016.

### What may be invented, and how far

A piece this run invents — surplus at a seam, or the early bursts of a satellite whose keys have
not been seen yet — is held inside **the ground the reference acquisitions were actually cut
over**. Coverage past that has been left out on purpose (ocean, or somewhere of no interest) and
the record is emphatic about it: over the 30 Greenland tracks, picking surplus coverage up
invented keys that were proposed 120 times and built never. Ground a neighbour *did* cut is the
opposite case — that is the surplus of a key that has been shortened, and it does want a piece.

Invented pieces are also cut to sit inside the **velocityStats boxes**. The autocleaning works box
by box, so a piece straddling two is cleaned against bounds that are not its own: where the track
is cut at 0-50 and 51-100, two pieces of 25-50 and 51-100 sit inside their boxes and one piece of
25-75 does not. A piece running past a boundary by no more than `-boxOverrun` is left whole, since
cutting there would leave a stub rather than a piece.

No piece is longer than `-maxBursts` (76). A longer run is broken into equal pieces sharing a
burst at each seam — the same one-burst overlap the tracks already carry, where `398-45` ends on
442 and `442-54` starts on it.

A piece another piece already covers is dropped, **unless it is a key in its own right**: the
12-day keys above are exactly the case where the same ground is cut twice on purpose. Two pieces
on the same start always collapse to the longer, being one directory cut twice.

### Acquisitions already built are never rewritten

Only the acquisitions after the last built one are framed for writing. An acquisition that has
been built already has `orbitframes` lines describing directories that exist and pair, and
rewriting them would orphan what was built from them. Its framing is still derived — that is what
the comparison table is read against — but the only thing ever written for it is an **extra piece
appended** after its existing lines, where a key introduced further along needs a partner to pair
with. `-commit` reports these separately, and they take an index above everything that orbit
already uses so `determineFraming` adds them rather than replacing part of its framing.

**The one other thing written for a built acquisition is the lines it has none of.** `frames` is
only a default, and a track's default is edited over the years — widened as the coverage grows,
moved when the datatake does. An acquisition built under an older one and never given lines of
its own is then read against a default it does not fit, and the setup step stops on it:

```
track-133   frames 402-62        every acquisition covers 391-412
            180 orbitframes entries, every one 400-12
            six orbits built at 400 with no entry at all
      ->    63905-0 frame 402-62: Frames 402 to 463 not in burst time range 391 to 412
```

Writing those lines is what the file is for, so they are written — taken from what was actually
built (`setup_63905_400` cut bursts 10-21, so `63905-1-400-12`), which is the plan that produced
the directories on disk. Only an acquisition with **no lines at all** is given any: one that has
them was framed deliberately, and a default it cannot cover is then a real thing to fix by hand
rather than to guess at.

### How well the rules reproduce the record

Scored over the whole record of all 30 Greenland tracks — 2500 built acquisitions — the rules
reproduce the set of keys actually built for **92.0%** of them exactly, and the framing they derive
would form **3279 pairs against the 3011** the built segmentation forms. The residual divergences
are 3-13 occurrences on a single key of a single track, which is the same order as the occasional
hand slip in the record itself; the deliberate one is the shortened shared key above, which
departs from history in order to pair over the whole piece.

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

What is being maximised is the pairs: the nearest-neighbour (6-day) pair, and the alternating
(12-day) pair where there is 6-day data — the two can be framed differently, and a long 12-day
piece overlapping a 6-day one is perfectly viable. Against that, the framing is kept close to the
historical one, because the autocleaning boxes were set up around it.

Rules applied, in the order they matter:

1. **Carry the keys forward** from the neighbour whose framing cuts the most of this
   acquisition's data, most recent among equals; reaching back past the `refBack` neighbours only
   where they frame less than half of it. `frames` is never modified, and is only the starting
   point for a track with nothing built yet.
2. **Trim, don't move.** If only the tail is missing, shorten the piece and keep the start — the
   start is the pairing key, the length is free. A key that falls *before* the data is the one
   case where a start does move: onto the first burst available, snapped to an established start
   within `snapTol`.
3. **Skip** (`nBursts=0`) where the data cannot carry a key at all.
4. **Reconcile the lengths**, counting only pieces that stopped short of their own data: the longer where they differ by no more
   than `snapTol`, the shorter where the difference is systematic, with the surplus taken up as
   its own piece at the seam.
5. **Invent only inside cut ground**, and cut it to the velocityStats boxes (`boxOverrun`).
6. **Cap at `maxBursts`**, breaking with a shared burst at the seam.
7. **Drop a piece another already covers**, unless it is a key in its own right — the 6-day and
   12-day keys overlap on purpose.
8. **Matching pieces.** A key that nothing else carries is given a partner in the built
   acquisitions **either side** of it, within `mateSpan`/`pairable`, appended to what those already
   have. Each side is a pair of its own, so a key the acquisition before it carries still wants one
   in the acquisition after — on a new key that is the difference between one pair and two. Where
   the key belongs to an acquisition whose SLCs have been cleaned away the partners are still
   worked out and drawn, so the plot shows the pairing the rules would have produced, but they are
   **never written**: the acquisition they would pair with is one nothing can be cut from.
9. **Never rewrite a built acquisition.** Its lines stay as they are; only additions are written.

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
- the **pieces that would be cut**, in crimson, below it. Every acquisition is framed and drawn,
  since reading the derivation against what was built is most of the point of the plot, but only
  the acquisitions not yet built would actually be written: the rest are faded and labelled
  *proposed, outdated*. An acquisition already built keeps its own lines whatever the derivation
  says, and one whose SLCs have been cleaned away has nothing left to cut. On a mature track the
  faded rows are most of the record, and the solid ones are the work in hand;
- **bursts an acquisition cuts twice**, drawn on a lane of their own — offset above the built band
  or below the proposed one — and outlined in orange. Superimposing them only looked like a single
  piece, so the piece covering ground another piece of the same acquisition already covers is
  stepped clear of it instead. Only a band that doubles is thinned to make room for the lane —
  everywhere else the segments are drawn full height, in the place they always were. A one-burst
  seam is not treated as a doubling, being how
  neighbouring pieces are meant to meet; anything wider is the same ground cut, processed and
  mosaicked twice. That is legitimate where a 12-day key sits inside a 6-day one, and is what a
  stray nested piece looks like otherwise.

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

The plot covers the stretch the report covered, backed up over the last few built acquisitions
(`-refBack`, 3 by default — the same number `referenceFraming` counts as neighbours). Without
that it would show none of them: the stretch being proposed for is the stretch nothing has been
built from, so on a track with a backlog the built pieces — the dark bands and ticks the
proposal is read against — all lie before it. Those extra rows are context only; an acquisition
already built is drawn faded and is never written.

### Committing from the plot

An interactive `-plot` run that was not given `-commit` carries two buttons under the axes:

| Button | What it does |
|--------|--------------|
| **commit** | Writes the entries just checked and builds their setup scripts — the same write step `-commit` would have taken — then closes the window so the terminal reports what happened |
| **exit** | Closes the window, writing nothing |

The plot is where a proposal is judged, so the answer is given there rather than by reading it,
closing it and running the whole thing again with `-commit`. What the button commits is the
framing whose check has already passed: a run whose check reported a problem has nothing to
commit, so it gets no buttons. `-plotFile` gets none either, being a saved image.

---

## Diagnostics

- **Directories with a hole in their burst sequence.** `checkBurstTimes` aborts the whole track on
  these whatever `orbitframes` says, so they are excluded from the coverage and listed for repair.
- **Starts in use that no frame can produce.** segmentTrack works only within the frames it has and
  will not invent new ones, so these report as "missing" until a region or `frames` entry covers
  them.

---

## Part of the s1setup package.
