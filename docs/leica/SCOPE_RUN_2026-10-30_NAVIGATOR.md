# Run against LAS X: the plain Navigator driver, 2026-10-30

This is the record of the first run of the plain Navigator driver
(`zmart_drivers/leica/stellaris/navigator/`, 0.1.0b1) against a running LAS X.
The result in one line: **the hardware check passed on the LAS X simulator after two
fixes, the driver works from Python on its own, and Capture Image does not take a
z-stack; LAS X's Start does.** The orientation and objective steps of the setup wizard
cannot be done on the simulator and still need the STELLARIS.

- **Branch:** `claude/zmart-las-x-navigator-driver-jjxccf`, starting at `7d0a964`.
- **Computer:** `ZMB-LASX-PC`, with the **LAS X simulator** (serial number
  `STELLARIS SIMULATOR`), showing its plain Navigator. Not the real STELLARIS.
- **Python:** 3.12.16, in a fresh conda-forge environment, with the repository
  installed as `pip install -e ".[leica,test]"`.
- **Limits:** the driver's defaults for the hardware check; the setup wizard then
  saved this computer's own limits and origin.

---

## 1. Before anything moved

The simulator starts with its stage at (0, 0) and z-wide at -7200 µm. That is outside
the default limits (x 1000..130000, y 1000..100000, z-wide 0..8000 µm), so the hardware
check stopped at its preconditions, as it should, before anything moved. The stage and
z-wide were then placed inside the limits through the driver itself (x 63500, y 41500,
z-wide 1000 µm). The z-wide move was one relative step of +8200 µm and landed exactly.

LAS X's z-stack panel gives z-wide a travel range of 14400 µm, about -7200..+7200 µm.
The default limits allow only 0..8000 µm. They were not changed; this is for whoever
measures the STELLARIS's own limits.

## 2. The hardware check

`python zmart_drivers/leica/stellaris/navigator/testing/run_ci.py --hardware --out D:/ZMART-check/navigator-ci`

| Step | First run | Last run |
|---|---|---|
| 0 preconditions | pass, once the stage stood inside the limits | pass |
| 1 every reading (16) | pass | pass |
| 2 the five settings and back | pass | pass |
| 3 z-galvo, z-wide and the stage, and back | pass | pass |
| 4 objective | skipped (not asked for) | skipped |
| 5 capture, saved, let go of | **fail**: LAS X: "Can not start scan! (Reason: No active channel in one or more Sequences)" | pass, 3.8 s |
| 6 tile scan over three fields, saved, let go of | **fail**: the same reason | pass, 5.8 s from start to saved |

The first failures were LAS X's setup, not the driver: no detector channel was switched
on. The driver reported LAS X's reason in words and answered its message box. Once a
channel was on in LAS X, the last line was `ALL CHECKS PASSED`.

## 3. What was changed

1. **A tile scan that never starts now puts back what it changed.** In the first run
   LAS X took the tile scan's preparation (scan mode xyt, stage use mode, tile
   positions) and then refused to start. The put-back ran only after a successful tile
   scan, so LAS X was left in scan mode `xyNt`. The put-back now runs whenever the tile
   scan was sent (`procedures/changes.py`, test in `test_changes.py`).
2. **The z-stack's drive is read from the right node.** `Acquire/ZStackUseMode` stayed 1
   whether the stack was on z-galvo or on z-wide; `Acquire/ZPositionMode` followed the
   drive chosen in LAS X's z-stack panel (1 z-galvo, 2 z-wide). The driver called a
   z-wide stack a z-galvo stack before (`primitives/direct/reads.py`, test in
   `test_readings.py`).
3. **No z-stack is an answer at once.** The shared read loop takes a reading of None for
   no answer, so with no stack set every z-stack reading waited out its four windows,
   12 s, before it gave None. That slowed every `settings()`, every capture and every
   position read. The z-stack's row in the profile now answers `{"stack": None}`, and
   `procedures/readings.get_z_stack` hands on the stack unwrapped, so callers still get
   None. The shared dispatcher is unchanged.
4. **The offline tests draw without a window.** On this computer one calibration test
   failed now and then in the full suite, because matplotlib picked Tk and Tk could not
   start again. The test passed alone. `testing/conftest.py` now chooses matplotlib's
   Agg, as the Navigator Expert driver's tests do.
5. **The README says how the driver reaches LAS X:** the hardware server through
   Windows' COM (ActiveX), and the Data Container through Leica's .NET interface. It said
   ".NET" for both before.

## 4. The driver from Python on its own

The README's first example ran from start to end, near where the stage stood (no origin
was saved yet, so the example's (100, 250) would have been outside the limits): a move,
the zoom to 2 and back, a capture, a tile scan over `row_of_fields(3)`, `save()` (two
records, OME-Zarr, one store per field), `clear_cache()` (`Image001` and `Mosaic001`
let go of), the stage back. No change was left unconfirmed.

Two things to know about it:

- Without an `output_folder`, `save()` writes into `.staging/<session>/` under the
  data folder, as the Navigator Expert driver does. That is by design, but surprising
  the first time.
- A y move of 10, 30 or 250 µm on the simulator was sometimes reported as not confirmed:
  the stage read back 10 µm short, then arrived. A move of 100 µm and the moves back
  were confirmed. This was seen on the simulator only, and the driver said so each time.
  It should be watched on the STELLARIS.

## 5. Does Capture Image take the whole z-stack?

**No.** With a z-stack of five sections from 995 to 1005 µm set on z-wide in LAS X,
the window's Capture Image (`CaptureSingleFrame`) took one plane. LAS X's own
description of the picture in its cache has only x and y, and the saved store is
(1, 1, 1, 512, 512).

**LAS X's Start takes it.** `StopAcquiring` then `StartAcquiring` into the project, with
nothing else, took the whole stack: LAS X named the picture `Mosaic001`, with
dimension 3 (z) of five planes over 10 µm and dimension 10 (tiles) of one. Z-wide was
back at 1000 µm afterwards. The driver's existing tile-scan saving read it unchanged and
wrote one store of shape (1, 1, 5, 512, 512), with its projection, and the Data
Container let go of it.

**Proposal.** Add a third picture beside the capture and the tile scan: a z-stack,
sent as those two elements (a `Write` in `primitives/direct/writes.py` with its own
limits rule, never sent again, like a capture), watched like the others, and collected
as a one-field `Mosaic`. The objective-calibration step's focus measurements would then
take their stack with it instead of Capture Image. This was tried by hand on the
simulator only. On the STELLARIS it still needs a check that the Start honours the
stack's drive and range, and that it leaves the scan mode as it was.

## 6. The setup wizard

| Step | Outcome |
|---|---|
| 1 Connect | done; data folder `D:\ZMART-data` |
| 2 Define limits | done; saved and adopted |
| 3 Define coordinate system origin | done; saved and adopted |
| 4 Image-to-stage calibration | **cannot pass on the simulator**: "voting registration not trusted" |
| 5 Objective calibration | not reached: it opens once step 4 is done |

Step 4 refused correctly. The simulator's pictures do not move with the stage: the
three pictures were the same synthetic ramp, two of them identical byte for byte. The
wizard took the pictures, moved the stage and judged them as it should.

## 7. Offline suite

`python zmart_drivers/leica/stellaris/navigator/testing/run_ci.py` passed after every
change (the lint steps warn, as before; the tests inside the ZMART Controller were
skipped because the controller was not installed in this environment, and the wizard's
page test was skipped without Playwright).

## 8. What still needs a person at the STELLARIS

- The hardware check on the STELLARIS itself, with `--allow-objective`.
- Wizard steps 4 and 5, on a field with clear structure, and the objective step with a
  z-stack on z-wide. Until the z-stack proposal above is built, its focus measurements
  get one plane from Capture Image.
- The z-stack through Start (section 5), on the real instrument.
- The y moves that read back 10 µm short at first on the simulator (section 4).
- z-wide's real travel against the limits' 0..8000 µm (section 1).
