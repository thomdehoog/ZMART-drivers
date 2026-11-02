# The plain Navigator driver: what is still to do

Written on 1 November 2026, at the end of the day's work on the simulator. Everything the
ZMART Driver can change today is listed in `NAVIGATOR_WHAT_WE_CAN_CHANGE.md`; how each was
learned is in `SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md`. This is what is left, in the
order we meant to take it.

Each is learned the way the rest was: one control used once in LAS X's window while LCS's log
and state tree are compared before and after, then built with LCS's own command (UI Automation
only where nothing reaches LCS), tests first, tried on the simulator through the driver, and
put back.

## To learn and build, in order of use (2 November 2026)

The rule: LAS X's own command first; the state tree only when no command does it; LAS X's range
checked by the driver either way (`transfer.CHECKED_COMMANDS`, `transfer.STATE_TREE_WRITES`).

**Autofocus, what is left**
- The Reflection method's coverslip offset (`ReflectionOffset`) and its "Find Coverslip Reflection" button.
- Auto Sync and its scan options.
- The hardware autofocus (AFC, `Microscope/AFC`) and its offset.
- The Timelapse and Stage tabs: when the autofocus runs in a series (every n time points, at which
  positions; LCS has `WorkflowTimelapse`, `WorkflowXY`).

**Acquisition**
- Time series (xyt): number of frames and interval. The scan mode can be set; these two are not learned.
- Spiral: not learned.
- Fast Live's Maximal Format: the driver takes 16, 32, ... 512 (powers of two within LAS X's range); check
  which values the window's slider offers.

**Region and experiment types**
- ROI scan: scanning only inside a region (the Zoom In rectangle is an ROI in LAS X's data container).
- Bleaching and FRAP (LCS has `SequentialBleachPointParameter`).
- Z-compensation: laser and gain along depth.
- The carrier: slide, dish, well plate (the Navigator Expert sent `StageSetCarrierAttachment`).

**Commands seen in LCS's log but not built**
- The z-stack's Set Focus (no logged command; the state tree ignored it; only the window, perhaps by UI
  automation), Z-Compensation, and the Number of Steps mode as such (the count is set through the step).
- `SetDetectorTauSeparationLut`, `ResetConfocalSetting`, `ResetAutoFocusToAutoSync`.
- The mounting medium (`ATL_HyVolution_SetMountingMedium`).

**Other**
- The notch filter, the Dye Assistant, "Apply to hardware", Bulk Options.
- A dye of one's own in LAS X's library; the dye bar's "+".
- Removing a laser line in another setting than the first (the window allowed it only in Setting 1).
- The changes through a saved setting (`set_detector` and the like) with several settings: still refused.

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

## Decided not to do

- The detector offset: LCS holds it, but LAS X's window offers no control for it, and the driver
  changes only what the window offers (Thom, 1 November 2026).
- Anything learned by decompiling LAS X: never.

## Housekeeping

- The setup wizard does not yet show the new limits entries (`set_phase_x`, `set_pinhole_um`,
  `set_collar` and the others added this day); they are taken from the bundled defaults.
- A version bump, when Thom decides.
