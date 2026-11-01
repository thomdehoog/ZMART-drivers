# What the plain Navigator driver could break, and what protects against it

This is a review of the driver at `zmart_drivers/leica/stellaris/navigator/`, made on
1 November 2026 from its code and from the run on the LAS X simulator on 30 October. It
answers one question: before this driver drives the real STELLARIS, what could go wrong
physically, and what already stops it?

A simulator shows whether LAS X takes a command. It cannot show whether that command is
safe for the instrument. Nothing below has been tried on the STELLARIS.

## How the driver reaches the microscope

The driver sends its commands to LAS X's hardware server, the same server LAS X's own
window sends them to. LAS X's command log shows the window's commands and the driver's in
the same form. So whatever the hardware server itself checks applies to the driver too.

The hardware server is `LCS.EXE` in LAS X's installation: the driver reaches it through
its COM interface `ITCS`, and the registry names `LCS.EXE` as the program behind the class
`TCS.Application` (checked on the LAS X computer, 1 November 2026). That the driver's
running object is that same program is very likely, but was not traced to the process.

The same server does not mean the same safety. What the driver skips is whatever the window
checks **before** it sends a command, in its own buttons and sliders, and whatever the
window does around a command: the order it sends things in, the state it starts from, and
values it fills in without showing them. Which of these matter is not known. Only Leica can
say.

## Where each finding stands (1 November 2026, on the branch)

| Finding | State |
|---|---|
| A tile scan drove z-wide by itself | **fixed**: the tiles carry the z of the drive LAS X's z-stack uses |
| Only the target of a move was checked | **fixed**: a largest step per axis (z-galvo 50 um, z-wide 100 um by default); a z-stack's ends too |
| Any command could reach LCS through the transfer | **fixed**: only the commands on the driver's list are sent |
| An objective change turned before it checked | **fixed**: immersion and calibration are checked before the turret turns |
| Every OK-only message box was answered | **fixed**: only notices known to change nothing are answered |
| z-wide's limits are the whole travel | **open**: set z-wide's safe range for the holder in use (wizard, Define limits) |
| What LAS X's window checks before it sends | **open**: a question for Leica |
| Laser power | at most 10 % per line by default; to be decided per instrument |

## Confirmed on the simulator: a tile scan can drive z-wide by itself

With LAS X's z-stack on z-wide, the ZMART Driver's tile scan drove z-wide from 1522.7 µm
to 0 µm during the scan (simulator, 1 November 2026): LCS reads a tile's `ZPos` as the
position of the z-stack's drive, and the driver always sends z-galvo's. This must be
fixed before any tile scan at a real microscope. See
`SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md`, section 7.

## The three things to fix or decide first

### 1. A z move is limited by where it ends, not by how far it goes

The limits gate checks the target position of every move. It does not check how large the
move is. On the simulator, z-wide stood at -7200 µm (outside the limits), and the driver
moved it to +1000 µm in one step of **8200 µm**, because the target was inside the limits.
On the STELLARIS a step like that, upwards, could drive the objective into the sample.

Fix: a largest allowed step per z drive, and per stage axis, in the limits file, checked
by the gate beside the target. This catches an unexpected jump only. It does not prevent a
collision: a small step can still reach the sample, and a large move cut into small steps
is as dangerous as the large move. What prevents a collision is a safe focus window for the
objective and holder in use (2 below). Both are needed.

### 2. The z-wide limits are the whole travel of the drive, not a safe focus window

The limits allow z-wide from 0 to 8000 µm, which is the measured travel of the drive. The
setup wizard can narrow it, but the default is the whole travel. Nothing knows where the
sample is. A focus far above the sample's plane is inside the limits.

Fix or decide: set z-wide's upper limit at the setup wizard to the highest focus that is
safe with the sample holder in use, and say so in the wizard.

### 3. An objective change has no immersion check, and turns before it checks the calibration

- The driver will turn from a dry objective to an oil or water objective, or back. Nothing
  checks the immersion. (The hardware check only uses objectives of the same immersion.)
- The driver's README says a change to an objective the calibration does not cover is
  refused. In the code it is not: the objective turns first, and only afterwards does the
  driver find the calibration missing and report that the stage was not moved.
- Z-wide is not lowered before the turret turns. Whether LAS X or the stand does this by
  itself is not known.

Fix or decide: refuse a change of immersion; check the calibration before the turret
turns; find out from Leica what the stand does with the focus during a turret change.

## Every command the driver sends, and what guards it

| Command | What it does | Guarded by the driver | Guarded by LAS X (seen on the simulator) | Open question |
|---|---|---|---|---|
| `SetStagePositionInHwOnly` | moves the stage in x and y | the measured stage area (wizard), then the physical travel | not tried | Can the stage hit anything (incubator, a holder) inside the measured area? |
| `SetZPosition` | moves z-galvo or z-wide by a step | the target against the limits | not seen | **The size of the step is not checked (1 above).** |
| `SetZoom`, `SetScanSpeed`, `SetConfocalScanFieldRotation`, `SetLineAverage`, `SetPinholeAU` | the five scan settings | nothing by default (the limits file leaves them open) | not tried with values outside LAS X's ranges; the driver's README says LAS X adjusts a pinhole above its maximum | Are some combinations (speed with zoom, resonant scanner) left to the window? |
| `SetSlotPosition` | turns the objective turret | the slot (open by default) | not seen | **Immersion; z during the turn (3 above).** Not tried, even on the simulator. |
| `CaptureSingleFrame` | takes one picture | sent only when LAS X is idle, never sent twice | refused to scan with no active channel (seen) | The light on the sample is what LAS X is set to. |
| Tile scan (`SetStageIrregularTileScanPositions`, `ScanMode`, `StartAcquiring` ...) | a picture over several fields | every position against the limits | not seen | The fields' z is z-galvo, as the window sends it (seen in LAS X's log). |
| After the tile scan (`StageMoveToCoordinates` ...) | stage back, scan mode xyz, autofocus on | the position against the limits | not seen | It switches autofocus on, as the window does. |
| Data Container delete | lets go of a saved picture | only a picture the driver saved, and refused when the element is no longer that picture | not tried | None physical. |

## What the driver does not send

These are on ChatGPT's list, and the plain Navigator driver does not touch them:

- **Jobs.** The plain Navigator has none, and the driver refuses to connect while Navigator
  Expert's jobs are shown.
- **Pan, the resonant scanner, beam paths, filters, detectors and their gain.** No command
  for any of them is in the driver.
- **Lasers and shutters.** Not in the driver. Laser power was changed once, by hand, on
  the simulator, for the 405 line (2 % to 10 % and back). Before it goes into the driver it
  needs a highest allowed power per line, decided with whoever is responsible for the
  instrument and its users' samples.

## The state tree is read, never written

LCS's COM interface can also write values straight into its state tree
(`ServerNodes.ChangeNodes`), and LCS marks 1,348 values as writable on the simulator,
among them laser intensities, detector settings, the z-stack and calibration flags. LAS
X's window seems to use this for a few controls (the z-stack's number of steps changed
with no command in LCS's log). The ZMART Driver never does, because:

1. A written value gets no command check: LCS does not accept, refuse or adjust it.
2. One call can reach any writable value, including laser output power and calibration
   flags.
3. Whether LCS drives the hardware to match a written value is not known; the read-back
   would only read back what was written.
4. Node numbers change after a restart or an experiment load, so a write could land on a
   different value.

Every change goes through a command (`SendCommand2`), which LCS checks and answers.

**Decided exceptions (Thom, 1 November 2026):** five values that neither a command nor a
loaded setting reaches. The transfer refuses every other node; each node is looked up by its
path at each write, and each value is read back. None of them moves the stage or the objective
or makes light.

| Value | Node | What may be written |
|---|---|---|
| Bidirectional X | `Confocal/ScanningParameter/ScanDirectionX` | 1 or 2 |
| Phase X | `Confocal/HWParameter/PhaseX` | within `PhaseXMin` to `PhaseXMax`, read at the write |
| Pinhole (metres) | `Confocal/HWParameter/Pinhole` | within `PinholeMin` to `PinholeMax`, read at the write |
| Pinhole unit | `Confocal/HWParameter/PinholeUnit` | 1 (µm) or 2 (AU) |
| Emission for the Airy unit | `Confocal/HWParameter/EmissionWaveLengthForAiryAnddzCalculation` | whole nm, 380 to 800 |

Point 1 above is why the ranges are checked by the driver: LCS does not keep to its own range
for a written value. A pinhole of 2000 µm raised an error from the pinhole's motor and still
opened the pinhole to its maximum; an emission of 2000 nm was kept as 800 (simulator, 1 November
2026). A value outside its range is refused by the driver, and nothing is written.

## One more thing the driver does on its own

The driver presses OK on LAS X's message boxes that have an OK button and nothing else, and
writes each one into its running log. A warning that matters could be closed this way
without anyone seeing it. Which boxes LAS X shows with OK only on the STELLARIS is worth a
list, so that the driver closes only the ones known to be harmless.

## What to ask Leica, or someone who knows LAS X's control software

1. Which checks does LAS X's window make before it sends a command to the hardware server,
   for the stage, z, the objective turret, the scan settings and the lasers?
2. Does the stand or LAS X lower the focus by itself when the objective turret turns?
3. Does the hardware server, or the firmware, refuse a z move into a known obstacle, or
   only past the drive's travel?
4. Are there combinations of scan speed, zoom and format that the window prevents and the
   hardware server accepts?
5. Which of LAS X's OK-only message boxes are safety warnings?

## Order of work

Before the driver drives the STELLARIS:

1. Have the wizard ask for z-wide's safe focus window for the objective and holder in use
   (finding 2), and add the largest step per z drive and per stage axis as a second guard
   (finding 1).
2. Check the calibration and refuse a change of immersion before the turret turns
   (finding 3), and make the README match the code.
3. Answer only the message boxes known to be harmless; leave every other one for the
   person at the microscope.
4. Ask Leica the five questions above.

Then, at the STELLARIS, with someone watching and small values first: the hardware check,
the objective change between two dry objectives, then the rest.
