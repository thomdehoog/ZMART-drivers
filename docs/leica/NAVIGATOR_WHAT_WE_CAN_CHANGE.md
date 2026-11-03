# What the ZMART Driver can change on a Leica confocal

The ZMART Driver sends its changes to LAS X's hardware server (LCS), the same program LAS
X's own window sends them to. Everything a person sets in LAS X's software goes through LCS
as a command, so each setting below can in principle be changed by the ZMART Driver too.

Each setting is in one of four states:

- **Works**: the ZMART Driver changes it, checks it against this microscope's limits
  first, and reads it back afterwards. Tried on the LAS X simulator.
- **Tried**: LAS X accepted the change, sent once by hand on the simulator, and it was
  read back. Not yet a ZMART Driver call.
- **Command known**: LCS's log shows the exact command LAS X's window sends for it. Not
  tried yet.
- **Not yet learned**: LAS X's window can change it, but no command for it has been seen in
  LCS's log, and changing it in a saved setting did not take (a loaded setting applies its
  lasers and detectors, not the scan parameters).

"Through a saved setting" means the ZMART Driver has LAS X save its current setting, changes
the one value in a copy, checks the copy against the limits, has LAS X load it, and reads the
value back.

Nothing has been tried on a real microscope yet (state of 1 November 2026, after the
discovery session in `SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md` and the build that followed it).

## Position and focus

| Setting | State |
|---|---|
| Stage x, y | Works |
| Focus (z-wide) | Works |
| z-galvo | Works |
| Objective | Works in the code; another command for it (by slot index) tried |
| Correction collar (motCORR) | Works, with LAS X's command |
| Collar to the stored reference (GoTo) | Works, with LAS X's command |
| SmartCORR range (Begin, End, Steps) | Works: written into LAS X's state tree |
| SmartCORR (automatic collar scan) | Works, with LAS X's command |
| Collar reference stored or cleared (Set, Reset) | Works: written into LAS X's state tree, for the objective in the light path only |
| Setting added (new, or a copy of the current one) | Works, with LAS X's command |
| The current setting chosen (with several settings) | Works, with LAS X's command; every change goes into the current setting |
| Setting deleted | Works, with LAS X's command; the last one never |
| Setting switched on or off for scanning | Works, with LAS X's command |
| Setting's name | Works: written into LAS X's state tree |
| Detector lock (kept from the Dye Assistant) | Works, with LAS X's commands |
| Channel colour lock | Works: pressed in LAS X's window (it sends LAS X's hardware server nothing) |
| Transmitted-light detector (Trans PMT) on or off | Works |

## Taking pictures

| Setting | State |
|---|---|
| One picture with the current settings (Capture Image) | Works |
| A tile scan over several fields (Preview) | Works |
| The whole z-stack in one picture | Works: `acquire()` takes it when a stack is set |
| Letting go of saved pictures in LAS X | Works |
| Live, started and stopped | Works, with LAS X's commands; stop it before taking pictures |
| Time series: frames, interval, Acquire Until Stopped, Minimize | Works: written into LAS X's state tree, as its t panel does; saving a series is not yet possible |
| The xt line scan: lines, MAX, lines per page, page interval | Works: written into LAS X's state tree (lines as unsigned numbers), as its t panel does in xt |
| ROI scan on or off, and the sections of a stack it is limited to (ROI 3D) | Works: written into LAS X's state tree, as its button does; the regions themselves are drawn in LAS X's viewer |
| The bleach mode on or off | Works: written into LAS X's state tree, as its button does; the bleach points are set in LAS X's window |
| Linear Z-compensation: excitation, detector gain, motCORR on or off; support points added, removed, moved to; reference position | Works, with LAS X's commands (the reference written into the state tree) |
| When the autofocus runs in a time series (first cycle, every cycle, every n-th) | Works: written into LAS X's state tree, as its Timelapse tab does |
| The Reflection autofocus's coverslip offset, and Find Coverslip Reflection | Works: the offset written into LAS X's state tree; Find with LAS X's command, reporting whether it found the coverslip |
| Auto Sync on or off, and its scan options (format, frequency, overview zoom, bidirectional, no averaging) for both scanners | Works: on with LAS X's command, off and the options written into its state tree, as the Best Focus Scan Options window does |
| The λ scan's detection: begin, end, step, bandwidth | Works, with LAS X's commands; LAS X sometimes moves begin or end by a few nm, which the driver reports |
| The Λ (excitation) scan: begin, end, step, Automatic SP Movement, method, minimal gap, Lambda After Stage; the Λλ scan's side and maximal distance | Works, with LAS X's commands (Lambda After Stage written into the state tree); within the white-light laser's range |
| The z drive on its own (z-galvo or z-wide), and with z-galvo the xz scan modes (xzy, xzt, xzλ ...) | Works, with LAS X's commands |
| A value given to every setting (the "apply to all settings" boxes beside line and frame averaging and accumulation, and the pinhole) | Works, with LAS X's commands |
| The X1 port and the MFP: the X1 port with `set_filter_wheel(140, ...)`; the MFP's Autoselect, and with it off the MFP with `set_filter_wheel(180, ...)` | Works: the X1 port with LAS X's command; the MFP written into LAS X's state tree, as its list does |
| The Fluorifier disc: notch filters by hand or with the laser (Auto Select), and the polarizer's angle | Works: the filters with `set_filter_wheel` (wheels 130, 131); Auto Select and the polarizer written into LAS X's state tree, as its window does |
| The white-light laser's Excitation Control (Maximum or Constant Power) | Works: written into LAS X's state tree, as the Laser Overview does |
| The white-light laser's own output power (the Laser Overview's slider) | Works: written into LAS X's state tree, as the window does; within the limits (`set_laser_output_power`, by default up to 85 %) |
| Fast Live, started and stopped | Works, with LAS X's commands |
| Fast Live's Scan Settings, both scanners | Works: written into LAS X's state tree, as its window does; each range checked first, since LAS X does not hold the format to it |

## Scan

| Setting | State |
|---|---|
| Zoom | Works |
| Scan speed | Works |
| Scan field rotation | Works |
| Line average | Works |
| Frame average | Works |
| Line accumulation | Works |
| Frame accumulation | Works |
| Format (pixels) | Works |
| Optimize XY Format (LAS X's optimal format) | Works |
| Scan mode (xyz, xyt, ...) | Works |
| Resonant scanner on or off | Works |
| Sequential mode (between lines, frames or stacks) | Works: written into LAS X's state tree; LAS X rearranges the detectors' channels itself, as for its window |
| Scan direction (uni- or bidirectional) | Works: written into LAS X's state tree |
| Phase X | Works: written into LAS X's state tree, within LAS X's own range |
| Pan | Works: read in micrometres, moved by a distance in micrometres (the window's pan arrows pressed by the ZMART Driver), and set back to the centre (through the smallest zoom) |
| Bit depth | Works: written into LAS X's state tree, one of LAS X's own list (8, 12, 16) |
| Autofocus on or off | Works |
| Autofocus run once now (the window's Autofocus button) | Works, with LAS X's command; reports whether LAS X found a focus and z before and after |
| Autofocus method, precision or steps, capture range, drive | Works: written into LAS X's state tree, as its Best Focus Scan Options do |
| Best Focus Setting: format | Works, with LAS X's command |
| Best Focus Setting: speed, bidirectional, zoom | Works: written into LAS X's state tree, speed and zoom within LAS X's own range |
| Best Focus Setting: its laser line and detector (band, dye) | Works, with LAS X's command (the window's drag-in) |
| Best Focus Setting: a line's power | Works: written into LAS X's state tree; within the laser limits |
| Best Focus Setting switched off | Not possible: LAS X accepts the command and keeps it on while the autofocus is on |
| Autofocus coverslip offset and Find Coverslip Reflection (Reflection method) | Not yet learned |
| Autofocus in a series: every n time points, at which positions (Timelapse, Stage tabs) | Not yet learned |

## Z-stack

| Setting | State |
|---|---|
| Which drive carries it (z-galvo or z-wide) | Works |
| Begin and end | Works |
| Step size | Works |
| Number of sections | Works: `set_z_stack_sections`, by the step that gives it |
| Size (around the middle) | Works, with LAS X's command |
| Begin and End where the focus is (the window's buttons) | Works, with LAS X's commands |
| Re-Center | Works, with LAS X's command |
| Move to Begin, End, Center, Focus | Works: the window's buttons send no logged command, so the focus moves as any z move does, within the z limits |
| The bin (Clear) | Works, with the End and Begin buttons' commands at the focus |
| Set Focus (the focus plane) | Not yet: no logged command, and LCS ignored the plane written into the state tree |
| System-optimized step | Works: written into LAS X's state tree; LAS X takes its optimal step and may stretch the end |
| Direction | Works: begin above end runs the stack downwards |

## Pinhole

| Setting | State |
|---|---|
| Pinhole in Airy units | Works |
| Pinhole in micrometres | Works, with LAS X's command (`SetPinhole`); LAS X's own range checked first, since LAS X does not keep to it |
| Unit LAS X shows the pinhole in (µm or AU) | Works: written into LAS X's state tree |
| Emission wavelength for the Airy unit | Works: written into LAS X's state tree, 380 to 800 nm |

## Lasers

| Setting | State |
|---|---|
| Laser power per line | Works (at most 10 % unless the limits say otherwise; a white-light line when it is on) |
| White-light laser: a line tuned to another wavelength | Works, with LAS X's command; within the laser's range, at least 8 nm from its other lines that are on |
| White-light laser: a line added (the window's Add Laser) | Works, with LAS X's commands; within its range, 8 nm from its other lines |
| A laser line removed from the setting | Works, with LAS X's command |
| Shutter per light source | Works |
| A line on or off | Works: a power above 0 switches it on, 0 off |
| AOBS per white-light line: Fluorescence or Reflection, Enhanced Dynamics | Works: written into LAS X's state tree, as its AOBS Configuration window does; LAS X has no Enhanced Dynamics in Reflection |

## Detectors

| Setting | State |
|---|---|
| A channel on or off | Works (through a saved setting) |
| Gain | Works (through a saved setting; fixed by LAS X in photon counting) |
| Offset | Not built: LCS holds a value per detector (-100 to 100 %), but LAS X's window offers no control for it, and the driver changes only what the window can |
| Detection band (begin and end) | Works (through a saved setting) |
| Operating mode (Fast Mode, Counting, Analog, Reflection) | Works (through a saved setting) |
| Tau mode (Intensity, TauGating, TauScan, TauSeparation, TauInteraction; with a pulsed WLL line) | Works (through a saved setting) |
| TauContrast | Works: written into LAS X's state tree, as the detector panel's switch does, in any setting |
| A detector's dye (the detector panel's Dye list) | Works: written into LAS X's state tree, as the window does, in any setting; checked against LAS X's dye library |
| A detector's excitation line (the detector panel's Excitation Line list) | Works: written into LAS X's state tree, as the window does, per detector, in any setting |
| A setting's dyes on its detectors, with its lines and bands (the window's dye drop), in any setting | Works, with LAS X's command; every setting is sent, the others as they stand |
| A TauSeparation channel's colour | Works, with LAS X's command; the other tau channels keep theirs (the window moves them on) |
| A TauSeparation channel's lifetime (the slider) | Works, with LAS X's command; within the window's scale, 0.07 to 9.37 ns |
| TauSeparation's Find | Works, with LAS X's command; reports whether LAS X found the lifetimes (only "none found" seen) |
| TauGating's gates and channels: moved, added, removed | Works, with LAS X's commands; within the window's -1 to 11.5 ns |
| TauScan's number of images and lifetime range | Works, with LAS X's commands; images up to the detector's most, range 0.05 to 11.5 ns |
| TauSeparation's channels added and removed (two to seven), and a channel colour's lock | Works, with LAS X's commands |
| TauInteraction's average donor lifetime | Works, with LAS X's command; 0.1 to 11.5 ns |
| Dye | Works (through a saved setting) |
| Excitation line (a line, or Auto) | Works with one detector on (through a saved setting) |
| Channel colour | Works |
| Show the dyes' excitation and emission spectra | Works (through a saved setting) |

## Whole settings and the dye library

| Setting | State |
|---|---|
| Save LAS X's current setting to a file | Works |
| Load a setting file (one made in LAS X), its values checked against the limits first | Works |
| The dye library: every dye's name, peaks and spectra | Works (read only) |

## Filters

| Setting | State |
|---|---|
| Filter wheel position | Works (LAS X's own scanner wheels are never turned) |

## STED, FLIM and other applications

These run on the ZMB's SP8s, which have STED and FLIM. The STELLARIS simulator has
neither, so they are learned on an SP8.

| Setting | State |
|---|---|
| STED (depletion line, STED detector modes) | Not yet learned |
| FLIM and lifetime (measurement, time gates, lifetime separation) | Not yet learned |
| Online dye separation | Not yet learned |
| Bleach points and FRAP | Not yet learned |
| Triggers in and out | Not yet learned |
| Light sheet | Not yet learned |

## What the ZMART Driver will not change

LAS X's calibration and service functions, the STED beam alignment, and resetting a
detector after an overload. These stay with Leica's
service and the person responsible for the instrument.

## In numbers

| State | Settings |
|---|---|
| Works | 109 (the objective among them, not yet tried) |
| Tried | 0 |
| Command known | 0 |
| Not yet learned | 8 |
