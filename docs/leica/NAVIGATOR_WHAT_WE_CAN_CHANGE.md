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
  LCS's log. Many of LAS X's controls change LCS without a logged command; those stay set in
  LAS X's window.

Nothing has been tried on a real microscope yet (state of 1 November 2026, after the
discovery session in `SCOPE_RUN_2026-11-01_NAVIGATOR_DISCOVERY.md` and the build that followed it).

## Position and focus

| Setting | State |
|---|---|
| Stage x, y | Works |
| Focus (z-wide) | Works |
| z-galvo | Works |
| Objective | Works in the code; another command for it (by slot index) tried |
| Correction collar | Not yet learned |

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
| Scan mode (xyz, xyt, ...) | Works |
| Resonant scanner on or off | Works |
| Sequential mode | Tried; not built, because it also rearranges which detectors and shutters are on |
| Scan direction (uni- or bidirectional) | Not yet learned |
| Pan | Not yet learned |
| Bit depth | Not yet learned |
| Autofocus on or off | Works |

## Z-stack

| Setting | State |
|---|---|
| Which drive carries it (z-galvo or z-wide) | Works |
| Begin and end | Works |
| Step size | Works |
| Number of sections | Not yet learned |
| System-optimized step | Not yet learned |
| Direction | Not yet learned |

## Pinhole

| Setting | State |
|---|---|
| Pinhole in Airy units | Works |
| Pinhole in micrometres | Not yet learned |

## Lasers

| Setting | State |
|---|---|
| Laser power per line | Works (at most 10 % unless the limits say otherwise; the white-light laser's tunable lines not) |
| Shutter per light source | Works |
| A line on or off | Works: a power above 0 switches it on, 0 off |

## Detectors

| Setting | State |
|---|---|
| A channel on or off | Not yet learned |
| Gain | Not yet learned |
| Offset | Not yet learned |
| Detection band (begin and end) | Command known, but not yet working as aimed |
| Operating mode (photon counting, ...) | Not yet learned |

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
| Works | 27 (the objective among them, not yet tried) |
| Tried | 1 |
| Command known | 1 |
| Not yet learned | 18 |
