# Discovery at the LAS X simulator: what LAS X's window sends, 2026-11-01

Thom used one LAS X control at a time, in LAS X's plain Navigator, while LCS's command log
(`C:\ProgramData\Leica Microsystems\LAS X\lcsCommand.log`) and LCS's state tree were
recorded before and after (`D:\claude-scratch\navigator-run\discover.py`). A command
counts as learned only when the window was seen sending it. Nothing was read from Leica's
code.

- **Computer:** `ZMB-LASX-PC`, LAS X simulator (`STELLARIS SIMULATOR`).
- **Branch:** `claude/zmart-las-x-navigator-driver-jjxccf`.

## Notes on the session

- The first two rounds were made with Navigator Expert open by mistake. There the z-stack
  panel's commands carry `Origin="NavigatorExpert"`, and Navigator Expert keeps the values
  in its job until the job runs: the panel showed a 2 µm step while LCS still held 3.2 µm,
  until a scan ran. Those two rounds are not counted; round 1 was done again in the plain
  Navigator.
- A test scan (`StartAcquiring`) was sent while Navigator Expert was open, before this was
  noticed; LAS X then restarted (Thom had started it again by accident). The minutes before
  the restart are not in LAS X's log. After the restart the stage stood at (0, 0) and
  z-wide at 1488.9 µm.
- At 14:28:01, while LAS X left Navigator Expert, the window itself sent tile positions with
  `ZPos="0.0010000000"`, z-wide's position, with the z-stack on z-wide. This supports the
  open finding that a tile's `ZPos` follows the z-stack's drive (see the safety review).

## 1. Z-stack begin and end

Set in the Z-Stack panel: End 1005 µm, then Begin 995 µm.

| What | Full command, as the window sent it | LCS answered |
|---|---|---|
| End | `<Scanner Command="SetZStackDefinition" SetBegin="2" SetEnd="1" BeginValue="0" EndValue="0.001005" BlockId="0" Origin="LMSApplication"/>` | `Error="0"` |
| Begin | `<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="2" BeginValue="0.000995" EndValue="0" BlockId="0" Origin="LMSApplication"/>` | `Error="0"` |

- Values in metres. `SetBegin="2"` or `SetEnd="2"` leaves that end as it is; `1` sets it.
- Read back from `Acquire/AcquireZStackInfo/BeginValue` and `EndValue`.
- Without a command in LCS's log, at the same time: `StackCalculationMode` 0 to 1,
  `StepSizeConstant` False to True, `StepSize` to 1 µm, `SectionsCount` 10 to 11.

## 2. Z-stack number of steps and step size

Set in the Z-Stack panel: Number of Steps 7; then Z-Step Size 2 µm.

**LCS logged no command for either.** The state tree changed all the same:

| What | Values that changed |
|---|---|
| Number of Steps 7 | `SectionsCount` 11 to 7, `StepSize` 1 to 1.6 µm, `StackCalculationMode` 1 to 0, `StepSizeConstant` True to False, `EndValue` to 1004.6 µm |
| Z-Step Size 2 µm | `StepSize` to 2 µm, `SectionsCount` 7 to 6, `StackCalculationMode` 0 to 1, `StepSizeConstant` False to True, `EndValue` to 1005 µm |

So the window changes these through a way that LCS's command log does not record. The
command log alone cannot teach them.

Navigator Expert's own log entries (30 October and earlier) show a command for the step
size, sent to its jobs: `<Scanner Command="SetStackStepSize" StepSizeInMeter="2.909E-06" BlockId="8"/>`.
Whether the same command sent to the current scan (`BlockId="0"`) works is still to be
tried. For the number of sections, no command has been seen anywhere.

**Tried on the current scan:** `<Scanner Command="SetStackStepSize" StepSizeInMeter="2.5E-06" BlockId="0"/>`,
sent once by hand. LCS answered `Error="0"`; the stack read back a 2.5 µm step and 5
sections over the same 995 to 1005 µm. So the step is set with this command, and the
number of sections follows from the range and the step.

## 3. Z-stack drive

Not repeated: on 30 October, in the plain Navigator, the window sent
`<Scanner Command="SetZUseMode" UseMode="1" Origin="LMSApplication"/>` and `UseMode="2"`
when the stack's drive was switched between z-galvo (1) and z-wide (2). Read back from
`Acquire/ZPositionMode`.

## 4. Z-stack Begin and End buttons (take the current focus)

Begin pressed, z-wide moved +5 µm by the ZMART Driver (`SetZPosition`), End pressed.

| What | Commands, as the window sent them | LCS answered |
|---|---|---|
| Begin button | `<Scanner Command="BeforeBeginOrEndOfStackIsSet" IsBegin="1"/>`, then `<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="2" BeginValue="0.00151771" EndValue="0" BlockId="0"/>` | `<Result/>`, `Error="0"` |
| End button | `<Scanner Command="BeforeBeginOrEndOfStackIsSet" IsBegin="0"/>`, then `<Scanner Command="SetZStackDefinition" SetBegin="2" SetEnd="1" BeginValue="0" EndValue="0.0015227" BlockId="0"/>` | `<Result/>`, `Error="0"` |

The buttons send the current focus as the value; no other command is needed to "use the
current focus".

Also seen in this round:

- **LCS accepted a stack from 3345 µm to -1727 µm** (`Error="0"` to both), sent by the
  window while the stack graphic was dragged. LCS does not keep a stack within a safe
  range; the ZMART Driver's limits have to.
- **z-wide moved from 1488.9 to 1517.7 µm with no command in LCS's log.** LAS X's window
  can move the focus in a way LCS's command log does not record. The log is not a full
  record of what moved.

## 5. Z-stack size and Re-Center

| What | Command, as the window sent it | LCS answered | Values that changed |
|---|---|---|---|
| Z Size 10 µm | `<Scanner Command="SetZStackSize" ZStackSize="1E-05" BlockId="0" Origin="LMSApplication"/>` | `Error="0"` | `BeginValue` 1517.7 to 1515.2 µm, `EndValue` 1522.7 to 1525.2 µm (same centre), `SectionsCount` 3 to 5 |
| Re-Center | none logged | | none: the stack stayed centred at 1520.2 µm, not at the focus (1522.7 µm) |

Re-Center is not learned.

## 6. Z-stack direction, System Optimized, removing the stack

| What | Command, as the window sent it | Values that changed |
|---|---|---|
| Direction down | `<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="1" BeginValue="0.00152521" EndValue="0.00151521" BlockId="0"/>` (`Error="0"`) | begin and end swapped: the direction is the order of begin and end, not a command of its own |
| System Optimized | none logged | `StackCalculationMode` 1 to 2, `UseSystemOptimizedCalculation` True, `StepSize` 2.4 µm (`OptimalStepSize`), `SectionsCount` 6 |
| Bin (remove the stack) | none logged | `BeginValue` = `EndValue` = the focus (1522.7 µm), `SectionsCount` 1, `IsBeginSet` and `IsEndSet` False |

- System Optimized is not learned. The same step is reached with `SetStackStepSize` and
  the value LCS publishes in `Acquire/AcquireZStackInfo/OptimalStepSize`, without the
  "optimized" flag.
- Removing the stack is not learned. `SetZStackDefinition` with begin and end both at the
  focus may do the same; still to be tried.

## Z-stack: where it stands

| Control | State |
|---|---|
| Begin, end (typed, or the Begin/End buttons with the focus) | learned: `SetZStackDefinition` |
| Direction | learned: the order of begin and end |
| Size | learned: `SetZStackSize` |
| Step size | learned and tried: `SetStackStepSize` |
| Number of sections | follows from range and step; the window's own way is not logged |
| Drive | learned: `SetZUseMode` (30 October) |
| Taking the whole stack | tried: `StartAcquiring` |
| System Optimized, Re-Center, removing the stack | not logged; not learned |

## 7. Safety finding: a tile scan drives z-wide when the z-stack is on z-wide

With the z-stack's drive on z-wide (`Acquire/ZPositionMode` 2), z-wide at 1522.7 µm and
z-galvo at 0, the ZMART Driver's own three-field tile scan was run, and z-wide was read
every 0.2 s throughout (43 readings).

- The driver sent each tile with `ZPos="-0.0000000002"`, z-galvo's position.
- **z-wide went from 1522.7 µm down to 0.0 µm during the scan**, and was back at 1522.7 µm
  afterwards. z-galvo stayed at 0.
- So LCS reads a tile's `ZPos` as the position of the drive the z-stack uses, not of
  z-galvo. The window itself sends z-wide's position in that case (seen at 14:28:01).

On the STELLARIS this would be an uncommanded move of 1.5 mm.
**Fixed the same day:** the tile scan now reads `ZPositionMode` first; with the z-stack on
z-wide every tile carries z-wide's position. Run again, z-wide stayed at 1522.7 µm in all 41 readings.

After this test LAS X showed an error report, and wrote an internal dump
(`C:\ProgramData\Leica Microsystems\LAS X\SubjectManagerDump.log`) at 14:42:38: 13 s after
the tile scan's put-back, at about the time the picture `Mosaic001` was let go of in the
Data Container. LAS X kept running, and its log shows no error. The cause is not known.

## 8. Move to Begin

With a stack from 1510 to 1531.6 µm (set by `SetZStackDefinition SetBegin="1" SetEnd="1"`
from the ZMART Driver; LCS answered `Error="0"` but stretched the end from the 1530 µm asked
to 1531.6 µm, to fit whole steps of the system-optimized 2.4 µm):

- Move to Begin moved z-wide from 1522.7 to 1510 µm, **with no command in LCS's log**.
- Not learned. The same result is a z move to the stack's begin, which the ZMART Driver
  already makes with `SetZPosition`.

## 9. LCS writes dumps when the ZMART Driver takes a picture with the Navigator open

After LAS X's restart at 14:28, every picture the ZMART Driver took made LAS X show
"Create Error Report", with LCS dumps in `C:\ProgramData\Leica Microsystems\LAS X\`
(`LCS_<date>_<time>.dmp`), 10 and 20 s after a tile scan, 14 s after a capture. LCS kept
running, and its own log (`LCS.LOG`) names no cause. Tried one thing at a time:

| Test | Dumps |
|---|---|
| Tile scan, z-stack on z-wide, with a second connection reading z and the picture let go of | 2 |
| Tile scan, z-stack on z-wide, nothing else | 2 |
| Tile scan, z-stack on z-galvo, nothing else | 2 |
| Capture, nothing else | 1 |
| Capture by LAS X's own window (Capture Image), nothing from the ZMART Driver | none |
| Capture by the ZMART Driver with LAS X's Navigator closed (normal acquisition view) | **none** |

So the dumps come from LAS X's Navigator window handling the ZMART Driver's pictures, not
from LCS taking the command. What the window does differently: its capture went into the
Data Container element 398, named `Hidden` and not open, and it added an attachment
(`DcAccess SetAttachment ... AttachmentName="Tempest"`). The ZMART Driver's pictures go into
the open project 24 (`ZMART_Experiment1`), chosen as on 30 October, when no dumps came. Why
the dumps began after today's restart is not known.

Until this is understood, the ZMART Driver takes its pictures with LAS X's Navigator closed.

## 10. Most of the acquisition panel leaves no trace in LCS's log

In LAS X's normal acquisition view (Navigator closed), switching Bidirectional X (it was the
Best Focus setting's) changed `.../SequentialAutofocusSetting/ScanDirectionX` 2 to 1 with no
command in LCS's log and nothing in `LAS X.log`. Looking back, nearly every command "seen
in LCS's log" was sent by Navigator Expert (its CAM API) or by the ZMART Driver, not by the
window; the window's own commands in the log are the z-stack panel's and the Navigator's
tile scan. So the window cannot teach most controls; the commands that can be used are the
ones LCS's log has shown from any sender.

## 11. Every logged command tried on the current scan

LCS's logs (`lcsCommand.log`, `lcsCommandOld.log`) hold 45 distinct changing commands: 16
the ZMART Driver sends, 6 learned above, 12 that are not settings (Navigator Expert's `ATL_`
job commands, LAS X's start-up and shutdown, attachments, the carrier, clearing points),
and these 11, each sent once by hand to the current scan (`BlockId="0"`) on the simulator,
then sent again with the value it had (full forms in
`D:\claude-scratch\navigator-run\discovery\try_logged.json`):

| Command | LCS answered | What changed | Back afterwards |
|---|---|---|---|
| `SetFrameAverage FrameAverage SetMode="2" SingleSettingIndex="0"` | `Error="0"` | `FrameAverage` 1 to 2 | yes |
| `SetFrameAccu FrameAccu ...` | `Error="0"` | `FrameAccumulation` 1 to 2 | yes |
| `SetLineAccu LineAccu ...` | `Error="0"` | `LineAccu` 1 to 2 | yes |
| `SetFormat FormatX FormatY IsAutoFocusDefinition="0"` | `Error="0"` | 512 x 512 to 256 x 256 | yes |
| `FilterWheel_ApiSetSlot FilterWheelType SlotIndex ...` with an empty `BeamRoute` | `Error="0"` | wheel 140, slot 2 to 1 | yes |
| `<Microscope Command="SetObjectiveSlotIndex" SlotIndex BlockId="0"/>` | `Error="0"` | the objective turned (field halved, 10x to 20x) | yes; LAS X readjusted the pinhole by 0.0003 AU |
| `ActivateShutter Activate ...` with the UV source's `BeamRoute` (30) | `Error="0"` | the UV shutter closed | yes |
| `SetScannerToResonant Enable` | `Error="0"` | the resonant scanner on (speeds 12000) | the setting yes; some derived values stayed different |
| `SetConfocalSequentialMode SequentialMode IsApiCommand="1"` | `Error="0"` | mode 0 to 1, with knock-on changes | **no**: a third detector was left enabled and one shutter off in the sequential information |
| `SetSpectrophotometerMultiBandRange ...` (aimed at HyD S 3, route 40;3) | `Error="0"` | not the band aimed at; small changes on other detectors | **unclear**: the detector's route is not known well enough |
| `SetLut ... LutName` (a display colour) | `<Result/>` | Gray to Green | put back by hand (first put back to the wrong colour) |

So frame average, line and frame accumulation, format, filter wheel, objective (by slot
index), shutter and resonant scanner can be set by command and read back. Sequential mode
works but rearranges the setting, so it needs a read-back of the detectors and shutters too.
The detection band is not learned. No LCS dump came during these tries.

## 12. Settings saved and loaded as files

LAS X's Setting panel saves and loads a setting with two commands LCS logged:

- `<ATL FileName="...settings.seq" Command="ATL_Block_Save_To_XML" Attribute="2"/>`
- `<ATL FileName="...settings.seq" CreateNewInstedOfReplace="0" ElementIDAfterLoadedBlock="0" Command="ATL_Block_Load_From_XML" Attribute="2"/>`

The file is the same XML as a block in a Navigator Expert scanning template (`.lrp`): the
setting's master, `Setting 1` and the `Best Focus Setting`, each with its scan, lasers,
detectors and filters. Values changed in a saved file and loaded again, on the simulator:

| Field changed in the file | Applied on load |
|---|---|
| Laser line power (`LaserLineSetting IntensityDev`) | yes: 405 nm 1.96 to 5.1 %, and back |
| Detector gain (`Detector Gain`), detector not in photon counting | yes: HyD S 3 2.5 to 50 (`HighVoltage` too) |
| Detector gain, detector in photon counting | no: LAS X fixes the gain in counting mode |
| Detector on (`Detector IsActive`) | yes: HyD 4 on, gain 10, band 676 to 695 nm |
| Detector range (`DetectionRangeBegin`, `DetectionRangeEnd`) | no: these seem to be the detector's whole range, not its band |
| Bidirectional X (`ATLConfocalSettingDefinition ScanDirectionX`) | no, when changed in every setting of the file |

Changing a value in every setting of the file also changed the Best Focus setting: with HyD 4
switched on there, its format went from 64 x 64 to 8192 x 8192. A change must be made in the
one setting meant, and LCS's values compared before and after the load.

Then changed in Setting 1 only (the master and the Best Focus setting left as they were),
loaded, LCS's values compared, the original loaded again:

| Field changed in Setting 1 | Applied on load |
|---|---|
| Detector band (`MultiBand` of the detector's channel: `LeftWorld`, `RightWorld`, `TargetWaveLengthBegin`, `TargetWaveLengthEnd`) | yes: HyD S 3 410-676 to 450-600 nm; LAS X moved the neighbours' limits itself |
| Bidirectional X (`ScanDirectionX` 1 to 2, 2 being bidirectional in the Best Focus setting) | no: nothing in LCS changed |
| Operating mode (`Detector OperatingType` 1 to 2) | yes: `OperatingMode` 10 to 1, `OperatingModeIndex` 2 to 1 |
| `Detector AcquisitionMode` 1 to 0 | no |
| TauContrast (`IsLifetimeContrastActive`) | yes; LAS X also switched the detector to counting |
| Dye (`DyeName` to Leica/ALEXA 488) | yes |
| Excitation line (`DyeExcitationMode` 0 to 1) | yes |
| Show the dye's excitation spectrum (`IsShowDyeExcitationSpectrum`, on the setting) | yes: `ShowExcitationSpectrum` True |

## 13. A channel's colour

`<Scanner Command="SetLut" DetectorOrCameraChannel="1" IsUserDefined="0" SequentialSettingIndex="0" LutName="Green">`
with the detector's route sets the colour of the detector's channel: `Acquire/LUT/LUT_Entry`
lists one colour per channel, and a detector of the spectral detection on route `40;n` is
channel `n`. HyD S 3 went to Green and back, read back each time.

## 14. The dye library

`C:\ProgramData\Leica Microsystems\DyeDataBase\db\dyes` is empty on this computer: LAS X
gets its dyes from a dye-database service. How to read it without Leica's code is still to
be found.

## 15. The detector panel's lists, as LAS X writes them

Each option chosen in LAS X's window, then the setting saved by LAS X and read (so the
numbers are LAS X's own, not guessed):

| Operating Mode | In the saved setting | LCS `OperatingMode` (index) |
|---|---|---|
| Fast Mode | `OperatingType="2"`, `AcquisitionMode="5"` | 4 (0) |
| Counting | `OperatingType="2"`, `AcquisitionMode="1"` | 1 (1) |
| Analog | `OperatingType="1"`, `IsReflectionModeActive="0"` | 10 (2) |
| Reflection | `OperatingType="1"`, `IsReflectionModeActive="1"` | 11 (3) |

| TauModes (offered with a pulsed WLL line, in counting) | `DigitalGatingMode`, in the file and in LCS (index) |
|---|---|
| Intensity | 2 (1) |
| TauGating | 3 (2) |
| TauScan | 5 (3) |
| TauSeparation | 6 (4) |
| TauInteraction | 10 (5) |

Excitation Line: each setting has one `DetectionReferenceLine` (`LaserName`, `LaserWavelength`,
`IsAutomaticAssigned`): "Auto (405)" is `Laser 405`, 405, `1`; choosing 497 made it
`WLL`, 497, `0`. One per setting, not one per detector: which detector it belongs to
with several on is not known.

## 16. What a loaded setting does not apply: the scan parameters

Changed in a saved setting and loaded, with nothing in LCS's values changing afterwards:
the detector offset (`Detector Offset`), the bit depth (`BitSize`), the pinhole in metres
(`Pinhole`), the step mode (`StackCalculationMode`), the pan (`PanFirstDim`, held only by the
master setting), the correction collar (`MotCorrPosition`, the same), and Bidirectional X.

Bidirectional X switched in LAS X's window changed LCS's `ScanningParameter/ScanDirectionX` 1 to
2 with no command in LCS's log, and the saved setting then had `ScanDirectionX="2"` and
`ScanDirectionXName="Bidirectional"` in both the master and Setting 1. The same two values
written back for unidirectional, in both, and loaded, left LCS at 2. A loaded setting applies
its lasers and detectors; the scan parameters stay as they are. These stay not learned.

## 17. Optimize XY Format

LAS X's "Optimize XY Format" button sets the format to the one LCS holds as optimal:
`Acquire/ATL/CurrentBlock/ConfocalSequentialInfo/OptimizedFormatX` and `OptimizedFormatY`
(2824 x 2824 at zoom 2 with the 10x), valid while `IsOptimizeFormatValid`. The ZMART Driver
reads them and sets the format with `SetFormat`: 512 x 512 to 2824 x 2824, read back, and back.

## 18. Phase X

Shown with Bidirectional X on. Typed in LAS X's window as -40: LCS's
`Confocal/HWParameter/PhaseX` went from -49.99 to -39.976 (LCS keeps it in steps, so -40
became -39.98), with no command in LCS's log. Written as -20 in the master and Setting 1 of a
saved setting and loaded: LCS stayed at -39.976. Like the other scan parameters, neither a
command nor a loaded setting sets it; it is written into the state tree instead (section 20).

## 19. Bidirectional X, written into the state tree (a decided exception)

With no command and no setting-file route, Thom decided one narrow exception to "the state
tree is read, never written": Bidirectional X only. Written with `ServerNodes.ChangeNodes` into
`Confocal/ScanningParameter/ScanDirectionX` (1 unidirectional, 2 bidirectional), the node
looked up by its path at each write. LCS read back the value, the frame rate halved and doubled
with it (0.78 and 1.56 per second), so the scan follows, and LAS X's window showed the switch.
Every other node is refused by the transfer, and only 1 or 2 is written.

## 20. Phase X and the pinhole, written into the state tree (decided exceptions)

Thom extended the exception of section 19 to Phase X and the pinhole. All under
`Confocal/HWParameter`, written with `ChangeNodes` and read back, then put back:

- `PhaseX` -20: read back -20 exactly. Its range is `PhaseXMin` -100 to `PhaseXMax` 100.
- `Pinhole` 0.0002 m: read back 0.0001999 m, and LCS worked out `PinholeAU` 0.502 from it.
  Its range is `PinholeMin` 0.0001 to `PinholeMax` 0.001 m (100 to 1000 µm; 0.25 to 2.51 AU
  at 580 nm).
- `EmissionWaveLengthForAiryAnddzCalculation` 520: the same 100 µm pinhole read 0.280 AU
  instead of 0.251. LCS gives it no minimum or maximum node.
- `PinholeUnit`: 1 shows the pinhole in µm in LAS X's window, 2 in AU; the pinhole stays.

Outside the range, LCS does not keep to it. `Pinhole` 0.002 m raised
`Pinhole (Sim)::Position = 16888: Value has to be between 0 and 8000 !` from the pinhole's
motor, and the pinhole still went to its maximum (2.51 AU in LAS X's window); it was put back to
100 µm. `EmissionWaveLengthForAiryAnddzCalculation` 2000 was kept as 800, and 50 as 380. So the
ZMART Driver checks each value itself: Phase X and the pinhole against LCS's own minimum and
maximum, read at the write; the emission against 380 to 800 nm. Outside, nothing is written.

Through the ZMART Driver (`change_setting`), each confirmed by its read-back: `phase_x` -20
(read -20.0), `pinhole_um` 200 (read 199.9 µm, 0.502 AU), `emission_wavelength` 520 (0.560 AU),
`pinhole_unit` "um". Refused, with nothing written and every value unchanged: `pinhole_um`
2000 and 50, `phase_x` 150, `emission_wavelength` 900. All put back: Phase X -39.976, 100 µm,
580 nm, AU.

## 21. The bit depth and the system-optimized step, written into the state tree

Thom asked for these two next. Neither has a command in LCS's log, and a loaded setting does
not change them (section 16). Both are written into the state tree and read back:

- `Confocal/ScanningParameter/Resolution`, the bit depth, beside `ResolutionList` (8, 12, 16).
  Written 12: kept. Written 10, not on the list: LCS kept 12 and said nothing.
- `Acquire/AcquireZStackInfo/StackCalculationMode` 2: LCS set `UseSystemOptimizedCalculation`
  True and the step to its `OptimalStepSize` itself, as the window's System Optimized does. The
  stack of 1505 to 1515 µm at 2.5 µm became 1.3 µm and 9 sections, the end stretched to 1515.4 µm
  to fit whole steps. Written 1 again: the flag went False and the step stayed at 1.3 µm.

Through the ZMART Driver (`change_setting`), each confirmed by its read-back: `bit_depth` 12,
16 and 8; `system_optimized_step` True and False. `bit_depth` 10 was refused, with nothing
written. The stack was put back to 1505 to 1515 µm at 2.5 µm with `set_z_stack`, and the bit
depth to 8.

## 22. The pan

Thom moved the pan to the right in LAS X's window, in the normal acquisition view. LCS received
no command, and none of its 12,525 state-tree values changed. A setting saved afterwards held
`PanFirstDim` 0.0001743713507108436 (it had been 8.67e-19, zero)
and `PanSecondDim` still zero. The same setting, its `PanFirstDim` written as 0 and loaded,
left the pan where it was. One capture through the ZMART Driver with the pan moved: LCS was sent
`StopAcquiring` and `StartAcquiring` only, and no pan value appeared in the state tree.

Tried again, wider: the whole state tree (all 14 roots, 16 levels deep, lists included:
15,201 values) and every LAS X log, marked before and compared after Thom clicked the pan five
times to the left. No value changed and no log grew. A setting saved afterwards held
`PanFirstDim` -0.00069757778991061652, so the clicks had moved the pan: one click is 0.00017437
and five are five times that.

So LAS X keeps the pan in its own setting and hands it to the hardware by a way that is not
seen here. The pan can be read from a saved setting; it cannot be set. It stays where Thom put it.

The same capture took the z-stack (1505 to 1515 µm) and left z-wide at 1510 µm, the middle of
the stack; before it, z-wide stood at 1495.6 µm. The detectors' `IsEnabled` went from True to
False during the scan, while `IsActive`, whether a detector is on, stayed True for HyD 2 and
HyD S 3.

The pan is not in metres. Thom worked out its unit in March to May 2026, on the Navigator
Expert route (ZMART-microscopy, `navigator_expert/core/utils.py`): a pan value is a fraction
of the galvo's deflection, at most `PAN_LIMIT` 0.00775 per axis, and at that maximum the
sample shifts by `GALVO_FIELD_FRACTION` 0.667 of the objective's field of view at zoom 1. So
the shift in micrometres is `pan x base FOV (um) x 0.667 / 0.00775` (86.06 times the base
FOV), the same at every zoom; the fraction 0.667 is measured per microscope. Here, with the
10x objective (base FOV 1162.5 µm, 581.25 µm at zoom 2), one click is 0.00017437 x 1162.5 x
86.06 = 17.4 µm, which is 3 % of the field at zoom 2: LCS's `ScanningParameter/PanePercentage`
is 3. Five clicks to the left left the pan at -0.000698, about 70 µm to the left.

On that route the pan was set by writing `PanFirstDim` and `PanSecondDim` into a Navigator
Expert template and loading it through Navigator Expert. In the plain Navigator, the loaded
setting (`ATL_Block_Load_From_XML`) does not apply the pan in any of its modes (above), so it
stays read-only here.

