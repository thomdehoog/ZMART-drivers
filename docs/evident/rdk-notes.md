# What the FV4000 driver relies on from the RDK manual

Notes taken from the *FLUOVIEW FV4000 Remote Development Kit (FV40S-RMTDK) Instructions* while
writing the driver, kept here so that a reader can see where each behaviour of the driver comes from
without the manual at hand. The manual itself is Evident's; it is not part of this repository.

## The interface

- FLUOVIEW serves XML-RPC at `http://<host>:8080/xmlrpc`. The port is changed in FLUOVIEW's
  `cellSens_FV.ini` with the option `-rdkport`. Every call takes one dictionary and answers one
  dictionary with a `result` code.
- The result codes: `OK`, `ERROR`, `UNAVAILABLE`, `OUT_OF_MEMORY`, `INVALID_PARAMETERS`, `NO_FRAME`,
  `UNSUPPORTED_IMAGE`, `UNSUPPORTED_PARAMETERS`, `FILE_NOT_FOUND`, `UNSUPPORTED_FILE_VERSION`. The
  driver treats `UNAVAILABLE` as temporary (sent again), `ERROR` and `OUT_OF_MEMORY` as permanent, and
  the rest as a wrong request (`primitives/rdk/parsing.py`).
- The call groups: Maintenance (version), Remote (LOCAL/REMOTE mode), Repeat (the live scan), Protocol
  (start, stop, progress, the files being written), Canvas (the live display), Parameter (every
  setting, by `settingId`), Processing, and IDA (reading a saved image).
- The scan states `Protocol.getProtocolProgress` reports: `IDLING`, `SCANNING`, `RESTING`, `WAITING`.
  Most settings and a new protocol are refused with `UNAVAILABLE` while a scan runs, so the driver waits
  for `IDLING` before every change.
- Troubleshooting: a flood of commands makes FLUOVIEW close the socket (Windows socket error 10054).
  The driver keeps at least 20 ms between calls and makes one call at a time. Python's standard
  XML-RPC transport would quietly send a request again after such a reset, which for a capture would
  be a second picture; the driver's transport sends every call exactly once, on its own connection,
  and reports a reset as "it is not known whether FLUOVIEW carried it out".
- Which call is allowed in which state is the manual's section 3.6. Two entries shape the driver:
  `Protocol.stopProtocol` is refused while IDLING, so `stop()` sends nothing when nothing runs; and
  `Repeat.stop` is allowed only in RESTING or WAITING during a live scan, so a live scan is counted as
  running in any state but IDLING. `MATL_ROI_ENABLE_SETTING` cannot be read back, so that change is
  confirmed by nothing.

## Positions and units

- Positions are in **nanometres**, as whole numbers: `XY_STAGE_POSITION_SETTING` (`x`, `y`, and
  `escapeEnabled` to lower the objective on the way), `Z_STAGE_POSITION_SETTING` (the focus motor,
  absolute, with a relative form), `LSM_Z_PIEZO_POSITION_SETTING` (the piezo). The driver speaks
  micrometres and converts at the edge (`primitives/rdk/parsing.py`).
- A z-stack is defined against FLUOVIEW's own Z origin (`LSM_Z_COORDINATE_BASE_SETTING`): start, end,
  step, range and slice count in nanometres, plus the method (`LSM_Z_COORDINATE_TYPE_SETTING`).
- The nosepiece: `LSM_MICROSCOPE_OBJECTIVE_LENS_LIST` names the objectives per slot;
  `LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING` reads and sets the slot in use. The manual's set takes
  the slot as a string while its get answers a number in an unnamed struct; the driver sends a string
  and looks for the number both in the reply and in any struct inside it.

## Settings the driver uses

Channels (`LSM_CHANNEL_*`: enable, HV, gain, offset, digital gain, the laser of a channel, by
`channelId`), lasers (`LSM_LASER_*`: enable and intensity in percent, by `laserId`; an intensity
outside the laser's range is clipped by FLUOVIEW), the z-stack (`LSM_Z_COORDINATE_*`), the time lapse
(`LSM_TIME_LAPS_*`: enable, repeat count, interval with a unit), the scanner (`LSM_SCANNER_TYPE_SETTING`
GALVANO or RESONANT, `LSM_SCANNER_MODE_SETTING` ONEWAY or ROUNDTRIP), brightness adjustment over depth
(`LSM_BRIGHTNESS_ADJUSTMENT_*`) and multi-area imaging (`MATL_ROI_*`: create a group of columns and
rows, duplicate a well, delete, enable, position in XY and Z, the Z origin, and per area delete, enable
and Z range). `XY_STAGE_WELL_POSITION_SETTING`, which moves to a named well, is deliberately not offered:
its destination is not known to the limits before the move.

## Taking and reading a picture

- `Protocol.startProtocol` with an `executionType` (the driver uses the manual main protocol) starts the
  imaging set up in FLUOVIEW and answers the file names it will write (`targetName`) and a wait hint.
  `Protocol.stopProtocol` stops it. `Protocol.getAcquiringFiles` lists what is being written.
- `Remote.setRemoteMode` with `REMOTE` locks FLUOVIEW's screen for remote use; `LOCAL` gives it back.
  `Repeat.setRepeatScan` starts and stops the live scan.
- `Parameter.loadParameter` with an image path recalls the imaging setup that picture was taken with.
- Reading a saved image (IDA): `open` the file, `getGroup`, `getArea`, then `getProperty` for
  `EnabledChannelIdList`, `ChannelInfo` (the one property answered under `structValue`), `Axes`, `AxisStartPosition`, `AxisEndPosition`, `AxisStep`,
  `AxisMaxSize` and `PixelLength` (micrometres per pixel), then `getImage` for one channel and position
  and `getImageBody` / `getNextImageBody` for the pixels in pieces (base64 binary), and `release` every
  id in reverse order. The driver does this in `primitives/rdk/image_data.py`. The manual's example
  also shows how a stage offset is computed from a centre of mass in pixels and the pixel length,
  which is the same arithmetic the orientation measurement uses.
