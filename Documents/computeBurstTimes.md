# computeBurstTimes

Computes burst times and frame numbers from Gamma `.tops_par` files in the current directory. For
each `.tops_par` file, reads `burst_date` fields, computes elapsed seconds from the ascending node
time, and numbers the bursts (see below). Writes a `.btimes` file per `.tops_par`, touches
`missing.N` if a burst gap > 3 s is detected, and writes `frames.FIRST.LAST`. Called by
`setupTrack` and `catMultipleTops` after assembly.

Rerunning it in an assembled directory renumbers from the `.tops_par` files, which is how a
slipped `.btimes` is repaired — see [Repairing a slip](#repairing-a-slip).

Run from within the merged orbit output directory (which must contain `ascendingNodeTime`).

---

## Usage

```
computeBurstTimes.py [--check]
```

| Option | Description |
|--------|-------------|
| `--check` | Report what the numbering would become and what would change, writing nothing |

Operates on `*.tops_par` files in the current directory.

---

## How bursts are numbered

A burst number counts burst periods (2.759 s) from the ascending node. The numbering is anchored
once and then **counted**:

```
period = the burst period fitted to this swath's own times
n₀     = round(fitted time at burst offset 0 / 2.759)
nᵢ     = nᵢ₋₁ + max(1, round((tᵢ - tᵢ₋₁) / period))
```

Two things follow from counting rather than rounding each burst separately:

- **A swath sitting on a half-period boundary cannot slip.** Rounding each burst independently
  breaks when a swath's times land near `x.5`: track-171's iw1 fitted ratios read 640.5016,
  641.5013, … and drift −0.00024 per burst, so partway down the file they cross `.5` and two
  bursts get the same number (or one is stepped over). The old rule did exactly that and produced
  duplicated burst numbers, which `setupSeveralTopsImages.checkBurstTimes` reports as a "gap in
  burst times" that aborts the whole track.
- **A dropped burst stays visible.** The step comes from the time between bursts, so a real hole
  produces a jump in the numbering instead of being absorbed into a contiguous count. The old rule
  numbered by row index and hid them.

Every correction and every hole is printed:

```
20260423-0_iw1_hh.btimes:
  corrected from burst 8: burst number 647 was counted twice, so 647 -> 648 and +1 to the end (last burst 677 -> 678)
20260103-0_iw2_hh.btimes:
  bursts 1->2 are 5.517 s apart = 2 burst periods, so 1 burst went missing; numbering steps 630 -> 632
```

**Each swath keeps its own anchor.** The three swaths of one burst are acquired about a third of a
period apart, so their numbers legitimately differ by one depending on where in the period they
fall — that is the established convention and the whole archive is numbered that way. The swaths
are compared only as a check: they should start and end within one burst of each other and have
the same number of gaps. A disagreement is reported, never renumbered away.

---

## Repairing a slip

`.btimes` is derived entirely from `*.tops_par` + `ascendingNodeTime`, and both survive the SLCs
being cleaned away, so a directory can be renumbered without re-assembling anything:

```
cd <assembly>/track-N/7342-0
computeBurstTimes.py --check     # what would change
computeBurstTimes.py             # do it
```

`segmentTrack` does this itself for the directories it is actively segmenting when the setup check
trips over a slip — see [segmentTrack](segmentTrack.md#the-setup-step).

The first burst number is the piece key (`<orbit>_<firstBurst>`), and the anchor is unchanged by
the new rule, so a repair normally moves only the *last* number. Where recomputing would move the
first as well, `segmentTrack` leaves the directory alone and says so.

---

## Output

| File | Content |
|------|---------|
| `<name>.btimes` | One line per burst: `burstIndex timeSec frameNumber` |
| `missing.N` | Touch file indicating a gap before burst N |
| `frames.FIRST.LAST` | Touch file recording the first and last burst numbers of the **first** `.tops_par`. Any earlier `frames.*.*` is removed first, so a renumbered directory does not end up holding two. (It used to take FIRST from the first swath and LAST from whichever was processed last, so it described no single swath.) |

---

## Part of the s1setup package.
