# The plain Navigator driver: what is still to do

Written on 1 November 2026, at the end of the day's work on the simulator. Everything the
ZMART Driver can change today is listed in `NAVIGATOR_WHAT_WE_CAN_CHANGE.md`; how each was
learned is in `SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md`. This is what is left, in the
order we meant to take it.

Each is learned the way the rest was: one control used once in LAS X's window while LCS's log
and state tree are compared before and after, then built with LCS's own command (UI Automation
only where nothing reaches LCS), tests first, tried on the simulator through the driver, and
put back.

## The way to parity (from 3 November 2026)

The aim: everything a user can do with the mouse in LAS X's **Acquire** tab, the driver can do through LCS
(Configuration, Process, Quantify and Analysis are left out for now). The checklist of every Acquire control,
from LAS X's own help (69 pages, 409 rows: 83 built, 182 to learn, 23 display only, 121 out of scope), is in
`D:\claude-scratch\navigator-run\help\CONTROL_CHECKLIST.md`; LAS X's window shows 894 controls to UI
Automation (`help\ui_tree.txt`).

How each is learned, without Thom's clicks, on the simulator: `D:\claude-scratch\navigator-run\auto_learn.py`
records LCS's state tree and logs, takes a screenshot of the left screen, uses the control (UI Automation; a real
mouse click where that does not reach LCS; never keypresses), reads the commands and state-tree values that
changed, and puts it back. Then: LCS's command first, the state tree only when no command does it, the range
checked by the driver; tests first; tried through the driver; documented; committed. A control that does not
give way after a few honest tries is noted here and left, not chased. Hard-to-undo controls (deleting, files,
calibration, the objective) are asked about first.

Order: the checklist's "to learn" items that change what is acquired first (ROI scan, bleach points,
Z-compensation, autofocus in a series, tile-scan options, the tau gates, the "apply to all settings" boxes,
Dynamic Signal Enhancement, Online Dye Separation, spectral scans), cosmetic ones last.

## To learn and build, in order of use (2 November 2026)

The rule: LAS X's own command first; the state tree only when no command does it; LAS X's range
checked by the driver either way (`transfer.CHECKED_COMMANDS`, `transfer.STATE_TREE_WRITES`).

**Autofocus, what is left**
- "Find Coverslip Reflection" finding the coverslip: only "not found" was seen (the simulator has none).
- The simulator's Best Focus Setting kept a 499 nm line at 2 % after the Auto Sync trial (Auto Sync off copies
  Setting 1 into it); a line cannot be taken out of it without redefining the whole Best Focus Setting.
- The hardware autofocus (AFC, `Microscope/AFC`) and its offset.
- The autofocus's Stage tab (at which positions: `WorkflowXY`, `WorkflowXYIterator`, `WorkflowXYPositions`);
  it is greyed out without stage positions, so it comes with the Navigator.

**Acquisition**
- Saving a time series: `acquire()` takes it (LAS X's Start), but the files are written for x, y and z
  only (`save_data/metadata.py` refuses LAS X dimension 4).
- Spiral: not learned.

**Region and experiment types**
- The regions of an ROI scan and the bleach points: drawn in LAS X's viewer and kept in its data container
  (`Confocal/ROIScan/CurrentDCROISetID`), not in LCS; the laser lines per region (ROI Configuration).
- Bleaching and FRAP (LCS has `SequentialBleachPointParameter`).
- Z-compensation's "Move to and Apply" (`WithAotfAndPMTSetting="1"`, not seen from the window) and Set to Begin
  (sent nothing when the reference was the begin already).
- The carrier: slide, dish, well plate (the Navigator Expert sent `StageSetCarrierAttachment`).

**Commands seen in LCS's log but not built**
- The z-stack's Set Focus (no logged command; the state tree ignored it; only the window, perhaps by UI
  automation), and the Number of Steps mode as such (the count is set through the step).
- `ResetConfocalSetting`, `ResetAutoFocusToAutoSync`.
- The mounting medium (`ATL_HyVolution_SetMountingMedium`).

**Other**
- The Dye Assistant, "Apply to hardware"; Bulk Options' Reset System and Reset ImageCompass (hard to undo: Thom decides).
- A dye of one's own in LAS X's library; the dye bar's "+".
- The Best Focus Setting changed during the dye trials (its scan zoom to 48, a 488 nm line at 1 %); the zoom was put back.

## To check on the STELLARIS

The simulator cannot show these; each needs one careful run on the real microscope.

- The objective change: its two refusals tried on the simulator (an uncalibrated pair, another
  immersion; 2 November 2026). The turn itself needs an objective-pair calibration first, on the
  simulator and then on the STELLARIS.
- The pan: that one click is 3 % of the field and the conversion to micrometres (Thom's 0.667 of
  the field at zoom 1 for the largest pan) holds; that the smallest zoom centres it; `move_pan`
  landing on a real sample.
- The correction collar turning, SmartCORR on a real sample, and GoTo.
- The white-light laser: tuning, adding and removing lines, and the 8 nm and 8-line limits.
- That a change goes into the current setting with several settings, and that the white-light
  slots are shared between settings as on the simulator.
- An autofocus that finds a focus (only the failing case has been seen), and the Best Focus Setting.
- That the hardware follows the state-tree values (for example, bidirectional halving the frame time).

- The λ scan: why LCS sometimes moves begin and end by a few nm (380 to 385, 650 to 655, 800 to 775) and
  sometimes keeps them; the Λλ scan's reverse direction (greyed out on the simulator) and its light source list.

- The Laser Overview's power switch (a laser on or off) and its Options: Thom to decide, as switching a laser
  is hard to undo on the STELLARIS. Online Dye Separation: needs a separation matrix from the Process tab.

- Not on the simulator, so not learned: Z Flow, Dynamic Signal Enhancement, the Fluo Turret, Environmental Control
  (no incubator), and Triggering (needs the Sequencer Advanced board, set in the Project Settings).

## Decided not to do

- Removing a laser line in another setting than the first: the window allowed it only in Setting 1
  (Thom, 1 November 2026). A line is switched off in any setting by a power of 0.

- The detector offset: LCS holds it, but LAS X's window offers no control for it, and the driver
  changes only what the window offers (Thom, 1 November 2026).
- Anything learned by decompiling LAS X: never.

## Housekeeping

- The setup wizard does not yet show the new limits entries (`set_phase_x`, `set_pinhole_um`,
  `set_collar` and the others added this day); they are taken from the bundled defaults.
- A version bump, when Thom decides.
