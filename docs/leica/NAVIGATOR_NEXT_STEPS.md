# The plain Navigator driver: what is still to do

Written on 1 November 2026, at the end of the day's work on the simulator. Everything the
ZMART Driver can change today is listed in `NAVIGATOR_WHAT_WE_CAN_CHANGE.md`; how each was
learned is in `SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md`. This is what is left, in the
order we meant to take it.

Each is learned the way the rest was: one control used once in LAS X's window while LCS's log
and state tree are compared before and after, then built with LCS's own command (UI Automation
only where nothing reaches LCS), tests first, tried on the simulator through the driver, and
put back.

## To learn and build in LAS X's window

1. **The AOBS.** LCS has it (`Confocal/AOBSLineList`, up to 8 lines; it follows the white-light
   laser by itself) and the window has "Show AOBS Settings". Not learned.
2. **Autofocus, what is left.** Running it once and its settings are built (2 November 2026).
   Still to learn: the Reflection method's coverslip offset (`ReflectionOffset`) and its "Find
   Coverslip Reflection" button, "Auto Sync" with its scan options, and the hardware autofocus
   (AFC, `Microscope/AFC`, its offset). On the STELLARIS: an autofocus that finds a focus (only the
   failing case, "ValidateFocus failed", has been seen).
3. **ROIs.** Drawing a region and scanning only inside it. The window's Zoom In keeps its
   rectangle as an ROI in LAS X's data container (`Confocal/ZoomIn/CurrentDCROISetID`); a way to
   write an ROI there is not learned.
4. **Bleaching and FRAP.** LCS has the bleach parameters (`SequentialBleachPointParameter`); the
   bleach points, regions and the FRAP sequence are not learned.
5. **The z-stack panel's other controls.** Number of Steps (step mode 0 is not allowed yet), the
   Begin and End buttons (the same can be done with `set_z_stack`), the bin (remove the stack),
   Re-Center, the button under the bin, Z-Compensation, and "Same Stack Size for all Regions".
6. **The Navigator's Stage settings.** Tile overlap, separate images per region, the acquisition
   order of regions, saving and clearing the overview, merging images (raw data, 3D, blend) and
   the Fast Scan Settings.
7. **The carrier.** None, Slide, Dish, Chamber Slide, Multi Slide, Well Plate. The Navigator
   Expert sent `StageSetCarrierAttachment` in October; the plain Navigator's is to be seen.
   Choosing a carrier in the window also sent `StageClearAllPoints` and tile positions.
8. **Live, Fast Live and Spiral.** Live is learned (`<AcquireControl Mode="0"
   Command="StartLiveAcquiring" DCParentElementID="..."/>`, stopped with `StopAcquiring`), not
   built. Fast Live and Spiral are not learned in the plain Navigator.
9. **The notch filter** (`Confocal/HWParameter/IsNotchFilterAutoSelectionEnabled`) and the
   window's Dye Assistant, "Apply to hardware" and Bulk Options buttons.
10. **The dye library.** Reading it and putting a dye on a detector work. Creating a dye of one's
    own in LAS X's library, and the window's "+" above the dye bar, are not learned.
11. **Removing a line in another setting than the first.** The window let lines be deleted only
    in Setting 1; how LAS X removes one in Setting 2 is not known.
12. **The changes through a saved setting** (`set_detector`, the excitation line, the spectra
    shown) are refused with several settings. With the current setting now known by its place,
    they could change that setting in the file.

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

## Decided not to do

- The detector offset: LCS holds it, but LAS X's window offers no control for it, and the driver
  changes only what the window offers (Thom, 1 November 2026).
- Anything learned by decompiling LAS X: never.

## Housekeeping

- The setup wizard does not yet show the new limits entries (`set_phase_x`, `set_pinhole_um`,
  `set_collar` and the others added this day); they are taken from the bundled defaults.
- A version bump, when Thom decides.
