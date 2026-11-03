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
- Built on 3 November 2026 (sections 104, 105): the Stage tab (`set_autofocus_at_stage_positions`), Spiral
  (`spiral_of_fields`), Start (`tile_scan(button="start")`) and the Focus Map (`find_focus_map` through LCS's
  `StartNavigatorAutoFocus`, `focus_points`, `shift_focus_map`, `tile_scan(focus_map=)`). Not built: the Focus Map's own
  autofocus scan options (its "..." box; the driver searches with the autofocus's settings as they are), and the
  picture the Navigator takes at each focal point for its overview.

**Region and experiment types**
- The regions of an ROI scan, the bleach points and the Zoom In rectangle: drawn in LAS X's viewer and kept in its
  data container (`Confocal/ROIScan/CurrentDCROISetID`), not in LCS. The laser lines per region (ROI Configuration):
  its window lists no region on the simulator (section 89), so they are learned on the STELLARIS.
  A bleach point is drawn with Draw point and one click in the image; LCS gets nothing, and its place and bleaching
  time stay in LAS X's data container (section 88). Its laser power is built (`set_bleach_laser_power`). Points, rectangles, ellipses, polygons and ranges are drawn by the driver (sections 94, 102), and a point's bleaching
  time is set (`set_bleach_time`); the Zoom In rectangle is `zoom_in(corner, opposite)`, a zoom and a pan.
- Bleaching and FRAP (LCS has `SequentialBleachPointParameter`); the FRAP and FRET applications are LAS X's other
  wizards (section 86), outside the plain Acquire tab.
- The carrier (slide, dish, well plate): the Navigator's drawing aid only; choosing one sends LCS nothing and a Preview
  carries nothing of it (section 105). Nothing to build: the driver lays out wells and chambers as positions.
- The bleach points saved to and loaded from a file (the Bleach Point panel's Save and Load): a file, so asked first.

**Commands seen in LCS's log but not built**
- None: the Number of Steps mode is `z_stack_mode` (section 102).
- None left: `ResetAutoFocusToAutoSync` is `set_auto_sync(True)`; `ResetConfocalSetting` is a setting's Reset,
  built as `reset_setting()` (section 91); the mounting medium (`ATL_HyVolution_SetMountingMedium`) has no control in the Acquire tab
  (section 84).

**Other**
- The Dye Assistant's Apply is `set_dyes`'s command (section 80). Reset System and Reset ImageCompass are built (section 90).
- A dye of one's own in LAS X's library; the dye bar's "+".
- The Best Focus Setting changed during the dye trials (its scan zoom to 48, a 488 nm line at 1 %); the zoom was put back.

## To check on the STELLARIS

- `zoom_in` on a sample: that the box's middle comes to the centre (the pan's direction, from March to May).
- `measure_pan_window()` on the STELLARIS (by the window's pad): its largest pan and field fraction against the code's; the
  field fraction on a sample with pictures; the resonant scanner's window.
- The pan's window on the STELLARIS: that LAS X clips there too, at the largest pan times `1 - smallest zoom / zoom`
  (0.00775 on the simulator, a number in the code). The COM scanner route, allowed on the microscope (Thom, 3 November
  2026): first one step right, read back, compared with one press of the window's arrow, centred; then
  `measure_pan_window(route="scanner")` (section 101).
- Drawn regions across an objective change (after the pair's calibration): whether LAS X keeps a region's size in
  micrometres, and how far it lands from its place on the sample; the driver refuses the change while regions are drawn
  until this is measured (section 96).

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
- An autofocus that finds a focus (only the failing case has been seen), and the Best Focus Setting; the Navigator's
  autofocus (`find_focus_map`) finding a focus that differs from point to point on a tilted sample.
- That the hardware follows the state-tree values (for example, bidirectional halving the frame time).

- The λ scan: why LCS sometimes moves begin and end by a few nm (380 to 385, 650 to 655, 800 to 775) and
  sometimes keeps them; the Λλ scan's reverse direction (greyed out on the simulator) and its light source list.

- The Laser Overview's ON/OFF (`switch_laser`, section 92): how long the white-light laser needs before it is ready
  again after being switched on. Online Dye Separation: needs a separation matrix from the Process tab.

- Not on the simulator, so not learned: Z Flow, Dynamic Signal Enhancement, the Fluo Turret, Environmental Control
  (no incubator), and Triggering (needs the Sequencer Advanced board, set in the Project Settings).

## Decided not to do

- Removing a laser line in another setting than the first: the window allowed it only in Setting 1
  (Thom, 1 November 2026). A line is switched off in any setting by a power of 0.

- The detector offset: LCS holds it, but LAS X's window offers no control for it, and the driver
  changes only what the window offers (Thom, 1 November 2026).
- Anything learned by decompiling LAS X: never.
- The pan through LCS's older COM automation interface (`Scanner.MoveInDirection` with the pad's nine directions,
  `Scanner.PanPercentage`, seen only in LCS's registered type library): not used (Thom, 3 November 2026). It is another
  door into LCS than the commands the window sends, the window keeps its own copy of the pan, and a hand-made call held
  LCS for 33 hours once. The pan stays on the window's arrows; a pan command is a question for Leica.

## Housekeeping

- The setup wizard does not yet show the new limits entries (`set_phase_x`, `set_pinhole_um`,
  `set_collar` and the others added this day); they are taken from the bundled defaults.
- A version bump, when Thom decides.
