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
| Collar reference stored or cleared (Set, Reset) | Learned, not built: it writes into LAS X's objective list |

## Taking pictures

| Setting | State |
|---|---|
| One picture with the current settings (Capture Image) | Works |
| A tile scan over several fields (Preview) | Works |
| The whole z-stack in one picture | Works: `acquire()` takes it when a stack is set |
| Letting go of saved pictures in LAS X | Works |

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
| Sequential mode | Tried; not built, because it also rearranges which detectors and shutters are on |
| Scan direction (uni- or bidirectional) | Works: written into LAS X's state tree |
| Phase X | Works: written into LAS X's state tree, within LAS X's own range |
| Pan | Works: read in micrometres, moved by a distance in micrometres (the window's pan arrows pressed by the ZMART Driver), and set back to the centre (through the smallest zoom) |
| Bit depth | Works: written into LAS X's state tree, one of LAS X's own list (8, 12, 16) |
| Autofocus on or off | Works |

## Z-stack

| Setting | State |
|---|---|
| Which drive carries it (z-galvo or z-wide) | Works |
| Begin and end | Works |
| Step size | Works |
| Number of sections | Works: it follows from the range and the step |
| System-optimized step | Works: written into LAS X's state tree; LAS X takes its optimal step and may stretch the end |
| Direction | Works: begin above end runs the stack downwards |

## Pinhole

| Setting | State |
|---|---|
| Pinhole in Airy units | Works |
| Pinhole in micrometres | Works: written into LAS X's state tree, within LAS X's own range |
| Unit LAS X shows the pinhole in (µm or AU) | Works: written into LAS X's state tree |
| Emission wavelength for the Airy unit | Works: written into LAS X's state tree, 380 to 800 nm |

## Lasers

| Setting | State |
|---|---|
| Laser power per line | Works (at most 10 % unless the limits say otherwise; the white-light laser's tunable lines not) |
| Shutter per light source | Works |
| A line on or off | Works: a power above 0 switches it on, 0 off |

## Detectors

| Setting | State |
|---|---|
| A channel on or off | Works (through a saved setting) |
| Gain | Works (through a saved setting; fixed by LAS X in photon counting) |
| Offset | Not yet learned |
| Detection band (begin and end) | Works (through a saved setting) |
| Operating mode (Fast Mode, Counting, Analog, Reflection) | Works (through a saved setting) |
| Tau mode (Intensity, TauGating, TauScan, TauSeparation, TauInteraction; with a pulsed WLL line) | Works (through a saved setting) |
| TauContrast | Works (through a saved setting) |
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
| Works | 55 (the objective among them, not yet tried) |
| Tried | 1 |
| Command known | 0 |
| Not yet learned | 7 |
