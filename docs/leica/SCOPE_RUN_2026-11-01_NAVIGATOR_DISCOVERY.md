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

## 23. The pan set back to the centre, and read in micrometres

Two more ways were tried. Zoom In on a rectangle drawn on the image, then Live: LCS was sent
`StartLiveAcquiring` and `StopAcquiring` only; LAS X had kept the rectangle as an ROI in its data
container (`Confocal/ZoomIn/CurrentDCROISetID` 346, `EnableZoomIn` True), and at the scan LCS set
the zoom (2.0 to 4.177) and the pan itself, then `EnableZoomIn` went False and
`CanDoUndoLastZoomIn` True. And a load with the pan at 0, followed by one click of Thom's: the pan
came out at the old value plus one click, so the load had not reached the pan the window uses.

The zoom does reach it. Set through the driver (`SetZoom`), zoom 1.0 left the pan as it was;
zoom 0.75, LAS X's smallest, pulled it from -0.000754 to -0.00000004, where it stayed when the
zoom went back to 4.177. One click at zoom 4.177 was 0.0000835, half of one at zoom 2: a click is
3 % of the field shown, and the conversion of section 22 gave 8.35 µm, 3 % of the 278.28 µm field.

Built: `pan()` reads it from a setting saved now, in LAS X's values and in micrometres;
`center_pan()` sets the smallest zoom and then the zoom it had, both read back, and reads the pan.
Through the driver, after Thom clicked 3 right and 2 down: 25.05 µm across and -16.71 µm down
(down is negative), then `center_pan()` confirmed, the zoom back at 4.177, the pan 0.00 and
-0.004 µm. That the smallest zoom centres the pan is seen on the simulator only; one check on the
STELLARIS is still to do.

## 24. The pan moved by a distance, with the window's arrows

Thom chose the window's pan arrows. In LAS X's main window, the pan pad is the element of class
`ScanPanControl` (automation id `PanControl`) in the XY panel, with nine unnamed buttons: four
arrows, four diagonals and the centre. Told apart by where they sit on the pad and pressed through
UI Automation's Invoke: right once moved the pan 8.35 µm at zoom 4.177 (one click, as by hand), and
the centre button set it to 0.

Built: `move_pan(x_um, y_um)`. Per axis, the zoom is raised just enough that a whole number of
presses lands on the distance (a press is `PanePercentage` 3 % of the field shown), the arrow is
pressed, and the zoom is set back. Through the driver: (20, -12) moved 20.00 and -12.01 µm; (5, 0)
5.00 and 0.00; (-33.3, 7.7) -33.32 and 7.70; each confirmed, the zoom back at 4.177 each time.
(2000, 0) was refused before any press: beyond the largest pan, 775 µm with the 10x.
`center_pan()` then set it to 0.00 and 0.00 µm.

## 25. The correction collar (motCORR) and SmartCORR

Learned with the window's motCORR panel, one control at a time, each marked and compared:

| Control | What LCS was sent | What changed |
|---|---|---|
| A value entered in the motCORR box (Enter) | `<Microscope Command="SetMotCorrPosition" MotCorrPosition="50" SetMode="1" SingleSettingIndex="0" BlockId="0"/>`, answered `<Result/>` | `Microscope/CurrentMotCorrPos` 0 to 50 |
| Set | nothing | the objective's `ObjectiveInfo/MotCorrRefPos` -1 (none) to the collar's position; the button then reads Reset |
| Reset | nothing | `MotCorrRefPos` back to -1 |
| GoTo | `SetMotCorrPosition` with the stored reference | `CurrentMotCorrPos` 30 to 0, the reference |
| Begin, End, Steps (Enter) | nothing | `Microscope/MotCorrAutomationInfo/AutomationBegin` 0 to 20, `AutomationEnd` 100 to 80, `AutomationSteps` 10 to 5 |
| Start SmartCORR | `<AcquireControl Command="StartMotCorrAutomation" DCParentElementID="24"/>` | a scan of about a second; LCS's event "motCorr Position found", with no message box |

Through UI Automation the box took a value (and its arrows changed it) without LAS X sending
anything, and the Start SmartCORR checkbox did nothing; a value entered by hand did. So these are
built with LCS's own commands, and the range as state-tree writes, as for Bidirectional X.

Built: `change_setting("collar", percent)`, 0 to 100; `change_setting("smartcorr_range", (begin,
end, steps))`; `smartcorr(project=None)`; `collar_to_reference()`, which reads `MotCorrRefPos` of
the objective in the light path and is refused when none is stored. Set and Reset were then built too
(Thom asked): `set_collar_reference()` writes the collar's position into `MotCorrRefPos` of the
objective in the light path, found at the write, and `reset_collar_reference()` writes -1. Through the driver: the collar 25 confirmed, 150 refused,
0 confirmed; the range (10, 90, 5) confirmed; SmartCORR confirmed, the collar left at 10; GoTo
refused without a reference; the range put back to (0, 100, 10) and the collar to 0. The Set
pressed during the learning was cleared with Reset, so the simulator's objective has no stored
reference, as before.

Set and Reset through the driver: the collar 40, `set_collar_reference()` stored 40; the collar 0,
`collar_to_reference()` brought it to 40; `reset_collar_reference()` cleared it; the collar back to 0.

## 26. The detector offset: not in LAS X's window

LCS holds an `Offset` for each detector (`DetectorHWInfo/Offset`, `OffsetMin` -100, `OffsetMax`
100, in %), all 0. No control in any of LAS X's windows is named Offset (searched with UI
Automation, visible and hidden), and a loaded setting does not apply the setting file's offset
(section 16). The STELLARIS's HyD detectors count photons or run in Fast Mode, where a black
level is not set. Thom decided: what the window does not offer, the driver does not change. Not
built.

## 27. The setting's checkbox and name, the two locks, the Trans PMT

Each control used once in LAS X's window, marked and compared:

| Control | What LCS was sent | What changed |
|---|---|---|
| The setting's checkbox, off | `<ATL SettingID="3" SetMode="0" EnableForScanning="0" Command="ATL_Setting_EnableDisableForScanning" Attribute="0"/>` | `IsEnabledForScanning` False; `CanDoScanSingle` False, reason 2: no picture can be taken |
| The setting's name, edited by hand | nothing | `Acquire/ATL/CurrentSetting/Name` and two copies, "Setting 1" to "ZMART test" |
| The detector lock ("Remove spectral range from optimization") | `BlockLightSinkDetectorForDyeAssistant` for HyD S 3 (40;3) and `BlockLightSourceLineForDyeAssistant` for line 0 of the white-light laser (10;10), the setting's excitation line at 497 nm; `BlockedState` 1 or 0 | the detector's `IsBlockedForDyeAssistant`, the line's `IsBlocked` |
| The colour lock (`LockLut`) | nothing | nothing in LCS |

The two locks were greyed out until Setting 1 was switched on (Thom). Writing "Setting 1" into
`CurrentSetting/Name` alone renamed all three copies. Setting 1 of a saved setting is now found as
the one setting in its list, whatever it is called.

Built: `enable_setting(on)`; `change_setting("setting_name", text)`; `lock_detector(name,
locked)`, with the line the setting excites at, among the lines that are on;
`lock_channel_colour(locked)`, pressed in LAS X's window. The Trans PMT was already switched by
`set_detector("Trans PMT", on=...)`. Through the driver: the setting off and on, renamed and back,
the detector lock on and off, the colour lock on and off, each confirmed; the Trans PMT on and off,
each confirmed, with nothing else changed.

## 28. Settings added and deleted

| Control | What LCS was sent | What changed |
|---|---|---|
| Add new Setting (`AddSettingButton`) | `<ATL CloneSetting="0" Command="ATL_Setting_Create" Attribute="2"/>`, answered `Error="0"` | "Setting 2" (id 113) in `CurrentBlock/SettingInfo`, and made the current setting |
| Delete active element (`DeleteActiveElementButton`), with Setting 2 current | `SetDetectorActive SetActive="0"` for HyD S 3 (40;3) | the button deletes the active *element*, here the detector selected in Setting 2, not the setting |
| Setting 2 deleted by hand | `<ATL SettingID="113" Command="ATL_Setting_Delete" Attribute="2"/>` | Setting 2 gone; Setting 1 current again. Its `SettingInfo` entry stays, with no values |

Built: `add_setting(copy=False)` (`CloneSetting` 1 for a copy) and `delete_setting(name)`, refused for
the job's last setting; `settings()["setting"]["all"]` lists every setting. With more than one
setting, the changes through a saved setting stay refused. Through the driver: a new setting added
(no detectors in use) and deleted; a copy added (HyD 2 and HyD S 3, as Setting 1) and deleted; the
last setting's deletion refused. Back to Setting 1 alone.

## 29. The sequential mode

With two settings (a copy of Setting 1 added), each of the window's three buttons pressed once:
LCS was sent no command; `Confocal/ScanningParameter/SequentialMode` went 0 to 1 (Between Frames),
1 to 0 (Between Lines) and 0 to 2 (Between Stacks), and LCS rearranged the detectors' channels in
both settings itself (`ChannelCount`, `IsEnabled`). That is what was seen in October and taken
then for the driver's doing: it is LCS's own answer to the mode, the same for the window.

Built: `change_setting("sequential_mode", "line" | "frame" | "stack")`, written into the state
tree, codes 0 to 2 only, read back; `settings()["sequential_mode"]`. Through the driver: frame,
stack and line, each confirmed, and LAS X's window showed Line pressed afterwards. Back to line,
and Setting 2 deleted.

## 30. The white-light laser: a line tuned, and its power

The white-light line that was on at 497 nm, changed to 510 nm in the window: LCS was sent
`<Scanner Command="SetLightSourceWavelength" LineIndex="0" Wavelength="510" SetMode="2"
SingleSettingIndex="0" DoAsync="1">` with the laser's route (10;10). `LightSource[2]/AOTF/AotfLine[0]/
WaveLength` went 497 to 510, and the AOBS's line followed by itself (`AOBSLineList/AOBSLine/
LaserLine`). In October a white-light command held LCS 33 hours behind the warning "Please keep
minimum distance of 8nm", so the driver checks the range and the distance before it sends.

Built: `tune_laser_line(from_nm, to_nm)`, for a white-light line that is on, refused outside the
laser's range (485 to 685 nm) or within 8 nm of its other lines that are on; `set_laser_power` now
finds a white-light line that is on at its wavelength. Through the driver: 510 to 520 nm, its power
5 % and 2 %, each confirmed; 700 nm refused; back to 497 nm at 2 %. Switching on a white-light line
that is off (it has no wavelength while off) is not yet learned.

## 31. A white-light line added and removed; its shutter

Thom added a white-light line with the window's Add Laser and set it to 600 nm. LCS was sent
`<Scanner IsBleachPointDefinition="0" IsAutoFocusDefinition="0" Command="SetConfocalLaserLinesDefinition">`
holding every line in use (route, `LineIndex`, `WaveLength`, `Locked`) with the new one on the
laser's next free line (1), and `SetLightSourceWavelength` while the line was moved to 600 nm.
The new line came on at 2 % with no power command in the log.

The same definition sent by the driver listed the new line at its wavelength but with no power, so
the driver sets its power after (`SetLaserLineIntensity`, through the limits). A line set to 0 %
stays listed in the window, dark; the definition sent without a line removes it
(`IsLineVisible` False).

Built: `add_laser_line(wavelength_nm, percent=2)` and `remove_laser_line(wavelength_nm)`, both
refused while a line is locked for the Dye Assistant (the definition carries each line's lock);
`add_laser_line` also outside the laser's range, within 8 nm of its other lines, or without a free
line. Through the driver: 650 nm added at 2 %; 600 nm added and removed, 497 and 650 staying on.
The white-light laser's shutter, one per light source, closed and opened by `set_laser_shutter(650,
...)`, 497 and 650 with it, 405 not.

## 32. A change for "setting index 0" lands in the first setting (a safety finding)

With Setting 2 added and current, three white-light lines were added through the driver for it.
They went into Setting 1: each `SetConfocalLaserLinesDefinition` (`<Sequence Index="0">`) and
`SetLaserLineIntensity` (`SingleSettingIndex="0"`) changed the job's first setting, and since the
definition held only Setting 2's lines, each one also took away Setting 1's others (its eight
white-light lines and the 405 line). Setting 2 got none. The readings show the current setting,
so they did not show it; LCS's per-setting entries (`SequentialSettingParameter[0]` and `[1]`) did.

The detector button had seemed to say index 0 means the current setting (section 28); for these
commands it is the first. So every change whose commands are addressed to index 0
(`SingleSettingIndex="0"`, `SequentialSettingIndex="0"`, `<Sequence Index="0">`: the laser lines,
powers and shutters, the averages, the pinhole in AU, the channel colour, the detector lock, the
collar) is now refused by the driver, with nothing sent, while another setting than the first is
current. With the first setting current, or with one setting, nothing changes. Through the driver:
`add_laser_line` refused with Setting 2 current; Setting 2 deleted; Setting 1's lines put back
(405 at 1.96 %, the eight white-light lines at 2 %).

How LAS X's window addresses a change to Setting 2 is still to be learned: a laser line added by
hand with Setting 2 current, watched in LCS's log. Until then, changes to a second setting are
refused rather than sent to the first.

## 33. The window addresses the current setting by its place; the white-light slots are shared

With Setting 2 (a copy) current, Thom's changes in the window: LCS was sent
`<ATL SettingID="120" Command="ATL_Setting_Activate" Attribute="2"/>` when Setting 2 was clicked
(and `SettingID="3"` for Setting 1), then `SingleSettingIndex="1"` and `<Sequence Index="1">` for
Setting 2. A ninth line was refused by LAS X with "You can not activate laser line '730'. The
maximum number of 8 visible laser lines is reached!". Lines could be deleted only in Setting 1.

Built: every change whose commands name a setting is addressed to the current one by its place
(`Request.setting`, filled by `changes.run`); `activate_setting(name)`. Through the driver: with
Setting 2 current, 600 and 650 nm went into Setting 2 and Setting 1 kept its 405; Setting 1 made
current, 497 nm went into it.

That last change also turned Setting 2's 600 nm line into 497: both settings had used slot 0 of the
white-light laser. LCS's per-setting entries (`ConfocalSequentialInfo/SequentialSettingParameter[n]/
LightSourceList_SequentialInfo/LightSource_SequentialInfo/UnifiedLaser_SequentialInfo/
UnifiedLaserLine_SequentialInfo`) show it: each slot has one wavelength in every setting, and each
setting its own power. Built: `get_laser_slots` reads the lines every setting uses; `add_laser_line`
takes a slot no setting uses and keeps 8 nm from every setting's lines; `tune_laser_line` refuses a
slot another setting uses; a ninth line in a setting is refused. Through the driver: with Setting 1
current, 600 nm went onto slot 2, Setting 2 untouched; 497 to 520 nm refused, slot 0 being in use in
Setting 2.

## 34. The objective change on the simulator (2 November 2026)

The turret: slot 0 10x/0.40 dry (in the light path), 1 20x/0.70 dry, 2 40x/0.75 dry, 3 40x/1.25
oil, 4 63x/1.40 oil, 5 100x/1.40 oil. Through the driver: 10x to 20x was refused before the turret
turned, because no adopted calibration covers the pair 0 to 1; 10x to the 40x oil was refused
before the turret turned, the immersion being another; to slot 0, where it already was, confirmed.
The turn itself and the stage following it need an objective-pair calibration
(`driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb`); Thom chose not to
run it yet.

## 35. The autofocus: run once, and its settings (2 November 2026)

The "Show autofocus panel" button is the autofocus on or off (`ActivateAutoFocus`); switched on,
LCS builds the Best Focus Setting itself (`ConfocalSequentialInfo/SequentialAutofocusSetting`,
64 x 64, bidirectional), edited like Setting 1. The configure button on the bottom bar's Autofocus
opens Best Focus Scan Options. Its fields are written by the window into LCS's state tree, with no
command (`Acquire/AutoFocusInfo`): "Focusdrive" `ZUseMode` (1 z-galvo, 2 z-wide); "Optimized for"
`AFAnalyseType` (1 Contrast Based Method 1, 2 Intensity, 3 Noise, 4 Contrast Based Method 2,
5 Reflection); "Capture range" `FocusRange` (a whole number of micrometres: 40.0 written was
ignored, 40 kept; the window took 1 and 5000); the slider `Precision` (1 speed, 5 accuracy; LCS
works out the steps: 5, 7 ... 25 at 60 µm); "Number of Steps" `UseFixSliceCount` and `FixSliceCount`
(odd, 3 to 1001: the window turned 1000 into 1001 and 1 into 3). Reflection adds a coverslip offset
(`ReflectionOffset`) and "Find Coverslip Reflection" (not learned).

The Autofocus button sent `<AcquireControl RestoreCameraRoi="1" Command="StartAutoFocus"
DCParentElementID="191"/>`: a 64 x 64 x 25 series "AutoFocus" into the experiment in 3.5 s, then
`ValidateFocus failed` in LCS.LOG and `Acquire/ScanUpdate/AFFoundUpdate` 0, the focus not moved (no
sample on the simulator). LAS X's per-plane scores are not exposed; the series is in LAS X's cache.
Thom chose that the driver reports what LAS X did and computes no scores of its own.

Built: `autofocus(project=None)`, the search's two ends checked against the z limits first, the
answer's `found` False when LCS wrote "ValidateFocus failed" during the run, with z before and after;
`settings()["autofocus_settings"]`; `change_setting` for `autofocus_method`, `autofocus_precision`,
`autofocus_steps`, `autofocus_range_um` and `autofocus_drive`. Through the driver: each setting set
and read back, 20 steps and an unknown method refused, the autofocus run (found False, z unchanged),
and the settings put back to z-galvo, 80 µm, Contrast Based Method 1, precision 5.

## 36. The Best Focus Setting: its scan, its optics and its laser power (2 November 2026)

The autofocus's own setting, the Best Focus Setting (`SettingId` 4, `.../SequentialAutofocusSetting`),
changed in the window: its Format sent `<Scanner Command="SetFormat" FormatX="128" FormatY="128"
BlockId="0" IsAutoFocusDefinition="1"/>`; its Speed, Bidirectional X and Zoom were written into the
state tree (`Speed`, `ScanDirectionX`, `Zoom`, with `SpeedMin`/`SpeedMax` 1 to 2600 and
`ZoomMin`/`ZoomMax` 0.75 to 48 beside them). A laser line and detector were brought in by dragging a
dye: `<Scanner IsAutoFocusDefinition="1" ActiveSettingIndex="-1"
Command="SetConfocalDefinitionFromDyeAssistant">` with the line (route, `LineIndex`, `WaveLength`,
`Intensity`) and the detector (route, `Begin`, `End`, `Dye`, `LutName`). Its power changed in the
window (8 %) went into the state tree with no command (`.../UnifiedLaserLine_SequentialInfo/Intensity`),
Setting 1's 405 staying at 2 %. Its checkbox sent `ATL_Setting_EnableDisableForScanning` for
`SettingID` 4; LCS answered and kept it on.

Built: `change_setting` for `autofocus_format`, `autofocus_speed`, `autofocus_bidirectional` and
`autofocus_zoom`; `set_autofocus_optics(laser_nm, percent, detector, band, dye)`; and, since that
command sets the power of a line only as it brings it in (sent again at another power it changed
nothing), `set_autofocus_laser_power(laser_nm, percent)`, a state-tree write to the line's place
(`At="source,line"`), checked against the laser limits. Through the driver: the scan set back to
64 x 64, 600, both ways, zoom 1; zoom 60 refused; the 405 on HyD S 3 brought in at 3 %, then 8 %
through the power; 50 % refused by the limit; Setting 1's 405 untouched at 1.96 %.

## 37. Commands before the state tree: the pinhole, and the autofocus's power (2 November 2026)

A check of LCS's command log since 22 October against the driver's commands found the window's own
`<Scanner Command="SetPinhole" PinholeDiameter="0.0001" SetMode="1" SingleSettingIndex="0"/>`, while the
driver wrote the pinhole in micrometres into the state tree. Sent by hand: 0.0002 m was set (199.9 µm,
0.50 AU, `<Result/>`); 0.002 m, twice LCS's maximum, raised `Pinhole (Sim)::Position = 16888: Value has to
be between 0 and 8000 !` and still set 0.0019999 m (5.02 AU). Put back to 100 µm. The normal power command
with `IsAutoFocusDefinition="1"` added (`SetLaserLineIntensity`, 405, 5 %) was answered `Error="0"` and
changed Setting 1's 405 to 5.1 %, the Best Focus Setting's staying at 8 %; Setting 1 put back to 1.96 %.

Thom's rule: a command first, the state tree only when no command does it. Built: `pinhole_um` with
`SetPinhole`, checked against `PinholeMin`/`PinholeMax` in the transfer before it is sent; the state-tree
write for the pinhole is gone. Through the driver: 200 µm confirmed; 2000 µm refused, not sent (LCS's log
shows only 0.0002 and 0.0001 from the driver); back to 100 µm. The autofocus's power stays a state-tree write.

## 38. A detector's on/off, gain, band and tau mode with LAS X's commands (2 November 2026)

Thom changed the detector panel in the window: HyD 2 off and on sent `<Scanner Command="SetDetectorActive"
SetActive="0|1" SequentialSettingIndex="0" SetMode="0" BlockId="0" SuppressUserInteraction="0">` with its
route; gain 100 sent `<Scanner Command="SetDetectorGain" DetectorGain="100" SetMode="2" SingleSettingIndex="0"
BlockId="0">`; the band `<Scanner Command="SetSpectrophotometerMultiBandRange" DetectorScanType="0"
SetterMode="2" SequentialSetMode="2" SingleSettingIndex="0" BlockId="0" BandLeft1="520" BandRight1="676">` with
`<BandBeamRoute1>`; a dye from the list `SetConfocalDefinitionFromDyeAssistant` (the whole setting, worked out
by the window). The operating mode (Analog) and the AOBS (Reflection, Enhanced Dynamics) sent no command: the
window wrote the setting's `OperatingModeIndex`/`OperatingMode` and `Confocal/AOBSLineList/AOBSLine`. The tau
modes are listed per detector and setting (`DigitalGatingModeList_SeqInfo`: places 1 to 5 hold 2, 3, 5, 6, 10);
`SetDigitalGatingModeIndex` names a mode by its place.

Built: `set_detector`'s on, gain (within the detector's own range), band and tau mode with these commands, in
the current setting; operating mode, TauContrast and dye still through a saved setting. Through the driver:
gain 50 and 100, band 530 to 650 and back, HyD 2 off and on, the Trans PMT on and off, each confirmed.

Two findings. A tau mode chosen in Analog was taken, and LCS switched the detector to Counting, its gain fixed
at 2.5 (as in the window). And the operating mode set back to Analog through the saved setting changed the
setting's own entry (`OperatingMode` 10, the gain 100 stored there) but not the detector as it runs
(`Confocal/.../AcquisitionMode` 1, `IsHighVoltageAllowed` False), so the gain stayed 2.5; making Setting 1
current again did not change it. The saved-setting route for the operating mode is not reliable; the window's
way (the state tree) is still to be built and tried.

## 39. The operating mode as the window sets it (2 November 2026)

HyD S 3's `OperatingModeIndex` in Setting 1's entry (`SequentialSettingParameter[0]/.../DetectorHWInfo_SequentialInfo[2]`)
written as the window writes it: 1 set Counting (`OperatingMode` 1), 2 set Analog (10), and the detector as it runs
followed: its gain went from 2.5 back to the 100 stored in the setting. The detector's list for the setting
(`DetectionUnitOperatingModeList_SeqInfo`) offers 4 Fast Mode, 1 Counting, 10 Analog, 11 Reflection by place.

Built: `set_detector(..., operating_mode=...)` writes the mode's place for the current setting and detector
(`At="setting,photometer,detector"`), before the gain; read back from LCS's per-setting entry. The saved-setting
route is gone for it. Through the driver: Counting (gain fixed at 2.5), Analog with gain 100, Fast Mode, Analog
with gain 100 again, each confirmed with the running detector following.

## 40. The z-stack panel: size, Begin and End, Re-Center, number of steps (2 November 2026)

With the window: Begin sent `BeforeBeginOrEndOfStackIsSet IsBegin="1"` (LCS set the begin at the focus);
End sent `IsBegin="0"` and `SetZStackDefinition SetBegin="2" SetEnd="1" EndValue="0.0009834"` (the focus);
Z Size sent `SetZStackSize ZStackSize="2E-05"`; Re-Center sent `ReCenterStackAroundCurrentZ`; Number of
Steps 7 sent nothing, `StackCalculationMode` going 2 to 0 and `SectionsCount` 1 to 7. By hand:
`SetZStackSize` 10 µm took 973.0 to 993.4 to 978.2 to 987.8 (around the middle), 20 µm back to 973.0 to
992.8; `SectionsCount` written into the state tree (1, 2, 50, 100000) was ignored. The focus was moved by
the driver (`SetZPosition`), the window not moving it on the simulator.

Built: `set_z_stack_size`, `set_z_stack_begin_here`, `set_z_stack_end_here`, `recenter_z_stack`, each with
the window's commands and the stack's new ends checked against the z limits first; `set_z_stack_sections`
by the step that gives the count (`SetStackStepSize`). Through the driver: size 10 and 20, 11 sections (2 µm),
re-centred to 973.4 to 993.4, the end set at the focus (983.4); the begin set there too left one section, no
stack, as the window's Begin would. Put back to 973.0 to 992.8, 7 sections.

## 41. The z-stack panel's Move to buttons, Set Focus and the bin (2 November 2026)

Pressed in the window, in this order: Move to Begin, Move to End, Move to Center, Set Focus, Move to Focus
(it can only be pressed once a focus is set), the bin. No LAS X log grew: none of them sends a command LCS
logs. The state tree afterwards: the focus at 982.9 µm (the middle of 973.0 to 992.8); the focus plane
`AcquireZStackInfo/AFPlanePosition` 0 to 0.0009829 with `IsAFPlanePosition` False to True (Set Focus); the
bin left `BeginValue` and `EndValue` both at the focus, one section, `IsBeginSet` and `IsEndSet` False.
Writing `AFPlanePosition` (980 µm) into the state tree was ignored by LCS, as `SectionsCount` was.

Built: `move_to_z_stack("begin" | "end" | "center" | "focus")`, the focus moved with `SetZPosition` as any
z move, within the z limits; "focus" goes to the plane set in the window and is refused when none is set.
`clear_z_stack()`, the End and Begin buttons' commands at the focus, which leaves one section as the bin
does. Through the driver: begin 973.0, end 992.8, center 982.9, from the begin to the focus 982.9; cleared,
no stack; put back to 973.0 to 992.8, 7 sections. Set Focus not built.

## 42. Live, started and stopped (2 November 2026)

The window's Live button sent `<AcquireControl Mode="0" Command="StartLiveAcquiring" DCParentElementID="24"/>`
and, pressed again, `<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>` (1 November, section 23).
Built: `start_live()` and `stop_live()` with these commands, read back from `Acquire/AcquisitionState`.
Through the driver: idle; started (busy, then `eScanContinuous` after 3 s); stopped, idle again in 1.1 s.
While Live runs, a picture waits for the scanner to be idle, so Live is stopped first.

## 43. The AOBS per line: Reflection and Enhanced Dynamics (2 November 2026)

In the AOBS Configuration window, the 497 nm line (the white-light laser's; only its lines go through
the AOBS) was put into Reflection and Enhanced Dynamics unticked. No LAS X log grew. The state tree:
`Confocal/AOBSLineList/AOBSLine[0]` `IsReflection` False to True, `IsDoubleModeEnabled` (Enhanced
Dynamics) True to False, `Intensity` 100 to 49.8 (the share of the line passed on).

Written into the state tree by hand, LCS took each value and kept the two apart as the window does:
Reflection on switched Enhanced Dynamics off and set 49.8; Reflection off, or Enhanced Dynamics on,
brought Fluorescence back at 100 with Enhanced Dynamics on; Enhanced Dynamics off alone changed only
itself. The window showed Reflection after the write (Thom).

Built: `set_aobs_line(wavelength_nm, reflection=, enhanced_dynamics=)`, written with the line's place
(`At`), read back from the AOBS's lines; a line the AOBS does not carry, Reflection with Enhanced Dynamics,
and Enhanced Dynamics on a microscope without it are refused. Through the driver: Fluorescence, Enhanced
Dynamics off, Reflection, Enhanced Dynamics on, each confirmed; 561 nm and Reflection with Enhanced
Dynamics refused. Left as found: Fluorescence, Enhanced Dynamics on.

## 44. Fast Live and its Scan Settings (2 November 2026)

The window's Fast Live button sent `<AcquireControl Mode="6" Command="StartLiveAcquiring" DCParentElementID="191"/>`
(Live is `Mode="0"`), and its stop the same `StopAcquiring` as Live. Its Scan Settings window sent no command;
the state tree holds them for each scanner, `Confocal/FastLiveParameters` (FOV scanner) and
`FastLiveParametersResonant`, with LCS's range beside the numbers:

| Window | Node | Range (FOV / resonant) |
|---|---|---|
| Maximal Format | `ScanFormat` | 16 to 512 / 16 to 512 |
| Minimal Scan Frequency | `ScanSpeed` | 100 to 2600 / 12000 fixed |
| Overview Zoom, box and value | `AdjustZoom`, `Zoom` | 0.75 to 48 / 2 to 48 |
| Bi-Directional | `Bidirectional` | |
| No Average/Accumulation | `NoAccumulationAverage` | |
| No Dynamic Signal Enhancement | `NoRollingAverage` | |
| Selected Setting Only (Between Lines Mode) | `SelectedSettingOnly` | |
| No τ-STED Xtend | `NoTauSTEDDeconvolution` | |

Written into the state tree by hand, LCS took each value, and the window showed them after reopening (Thom).
LCS does not hold the format to its range (300 and 1024 were taken) but held the frequency (3000 became 2600).
In the same minutes the autofocus's `FixSliceCount` went from 61 to 801 without Thom touching it; not explained.

Built: `start_fast_live()`; `set_fast_live(scanner, ...)` for all eight, every value checked before the first is
written (the format one of 16, 32, ... 512, each number against LCS's range beside it; the resonant scanner's
fixed frequency not changed), read back. Through the driver: format 128, 1000 Hz, bidirectional, overview zoom off,
dynamic signal enhancement on, each confirmed; 1024, 300, 3000 Hz and zoom 100 refused; Fast Live ran
(`eScanContinuous`) and stopped; the settings put back as Thom left them.

## 45. TauSeparation's channel colours, and a dye dropped onto a detector (2 November 2026)

In TauSeparation a detector has two tau channels, each with a colour (`TauSeparationDefinition_SeqInfo/
TauSeparationChannel[n]/LutName` in the setting's entry). Choosing TauSeparation sent the tau mode
(`SetDigitalGatingModeIndex`) and then `SetDetectorTauSeparationLut` for each channel (Cyan, Magenta).
Thom picked Red for HyD 2's first channel: the window sent `<Scanner Command="SetDetectorTauSeparationLut"
TauChannelIndex="0" SetMode="2" SingleSettingID="3" IsUserDefined="1" LutName="Red">` with the detector's
beam route, then moved the other tau channels on (HyD 2's second to Cyan; HyD S 3's to Magenta and Yellow),
each `IsUserDefined="0"`. The setting is named by its ID, not its place.

Built: `set_tau_channel_colour(detector, channel, colour)`, the one command, read back; refused outside
TauSeparation and for a third channel. Through the driver: HyD 2's first channel Green, then Cyan; LCS let
both channels be Cyan, so the second was set to Magenta. HyD 4 (not in TauSeparation) and channel 3 refused.

A dye dropped onto a detector (three times, Alexa 488 onto HyD 2, Alexa 568 and Alexa 532 onto HyD S 3)
sent `SetConfocalDefinitionFromDyeAssistant` with `MessageSource` DyeDragNDrop and `ActiveSettingIndex="-1"`,
carrying the whole setting as the window worked it out: each laser line (white-light 499 and 544 nm, at 2 %),
each detector's band, dye and colour. LAS X switched the 405 line off and HyD S 3 from Analog to Counting.
Not built yet.

## 46. TauSeparation's lifetime sliders (2 November 2026)

In the TauSeparation window, dragging HyD 2's second (magenta) slider sent `<Scanner Command="SetDetectorTauSeparationConfig"
TauChannelIndex="1" SeparationLine1="9.1266033279719E-09">` with the detector's beam route, 20 times while dragging. The
channel's `TauSeparationLine/TauLinePosition` went from 3 to 9.13 ns, and LCS moved the gate between the two channels
itself (`DetectorChannel[1]/Gate/GateEnd` 1.62 to 2.88 ns). LCS keeps no range for it; the window's scale runs 0.07 to 9.37 ns.

Built: `set_tau_channel_lifetime(detector, channel, ns)`, within that scale, read back within 0.01 ns. Through the driver:
HyD 2's channels 0.5, 3.0 and 1.0 ns, each confirmed; 20 ns refused. Left at 1 and 3 ns, LAS X's first values.

## 47. TauSeparation's Find (2 November 2026)

The Find button sent `<Scanner Command="FindTauSeparationLifeTimes">` with the detector's beam route, answered `<Result/>`.
LCS logged the outcome in `LifeTimeProcessTransfer.log`: "FindLifetimes seq 0 det 2, success 0", and nothing changed on
the simulator, which has no lifetime data. Built: `find_tau_lifetimes(detector)`, reporting `found` from that line and the
lifetimes afterwards (option A: what LAS X did, no lifetimes worked out by the driver). Through the driver: HyD 2, not
found, 1 and 3 ns kept; HyD 4, not in TauSeparation, refused. A search that finds them is to be seen on the STELLARIS.

## 48. The time series, learned by the driver's own clicks (2 November 2026)

Learned with `auto_learn.py`, no click of Thom's: the t panel sends LCS no command. Its Frames field changed
`Acquire/AcquireTStackInfo/RepeatCount` (and LCS the `Duration`); Acquire Until Stopped, Duration and Frames
(real mouse clicks; UI Automation's selection did not reach LCS) set `CalculationMode` 4, 2 and 3, until stopped
with `IsEnableAutomaticTSectionsCalculation` True and 4000 frames. Written by hand: `RepeatCount` and
`CalculationMode` were ignored; `DelayTime` (the interval) was taken, at least one frame long (0.5 s became
1.28 s, the frame time, `1/MaxFrameRate`); `Duration` was taken and LCS worked the frames out from it,
1 + duration / interval (no ceiling: 100001 frames); `IsEnableAutomaticTSectionsCalculation` switched until
stopped on and off, LCS setting the mode. The window's Frames field takes 1 to 10^9.

Built: `set_time_series(frames=, interval_s=, until_stopped=)`. Through the driver: 5 frames every 3 s (the
window showed 5), until stopped, 12 frames (until stopped off, the window showed 12); 0.2 s and 0 frames refused;
put back to 1 frame, xyz. A 3-frame series taken with `acquire()` (LAS X's Start) was not saved: the files are
written for x, y and z only.

## 49. ROI scan and the bleach mode (2 November 2026)

The Acquisition Mode panel's ROI button (pressed by the driver's own mouse click) sent LCS no command:
`Confocal/ROIScan/EnableRoiScan` went True, and LCS made `CanDoBleach` False, `CanDo3DLimitedRoiScan` and
`CanDoROIZoomIn` True, and Zoom In's `CanDoZoomIn` False. Written into the state tree, `EnableRoiScan` switched
ROI scan on and off, the window's button following; `EnableBleachScan` did the same for the bleach mode;
`Is3DLimitedRoiScanEnabled` with `First3DRoiSection` and `Last3DRoiSection` limited the ROI scan to sections of
the stack, LCS keeping them within it (50 became 7, the stack's last). The regions and the bleach points are
not in LCS: they are kept in LAS X's data container (`CurrentDCROISetID`, `CurrentDCBleachPointROISetID`).

Built: `set_roi_scan(on, sections=None)` and `set_bleach_mode(on)`, refused when the window greys the button
out, the sections checked against the stack first. Through the driver: ROI scan on with sections 2 to 5, then
every section, then off; the bleach mode refused while ROI scan was on; then on and off; the window's buttons
following each time.

## 50. Linear Z-compensation, learned by the driver's own clicks (2 November 2026)

The z-stack panel's Z-Compensation button opened the Linear Z-Compensation window (on the right monitor). Its
controls, each pressed by the driver's mouse:

| Window | LCS |
|---|---|
| Excitation Gain, Detector Gain, motCORR | `<Scanner Command="ActivateZCompensationMode" Mode="1" Activate="1"/>` (2, 4; `Activate="0"` off); `Confocal/ZCompensation/CurrentMode` the sum, 7 with all three |
| Add | `AddIntensityCompensationInterpolationPoint`: a point at the focus, holding every laser line's power, every detector's gain and the collar |
| Delete (a row selected) | `RemoveIntensityCompensationInterpolationPointAt ZPosition="1.98E-05"` |
| Remove All | `RemoveAllIntensityCompensationInterpolationPoints` |
| Move To (a row selected) | `ActivateIntensityCompensationPositions ZPosition="1.98E-05" WithAotfAndPMTSetting="0"`: the focus to 992.8 um |
| Reference Position | no command; `Confocal/ZCompensation/ReferencePosition` (metres) taken when written into the state tree |

A point's `ZPosition` is its offset from the reference (973 um, the stack's begin). Switched on with no points,
LCS put one at each end of the stack; while a mode is on, LCS offers no autofocus (`CanDoAutoFocus` False). A
point added at the height of another is not doubled. LCS lists the modes it has in `CompensationModeList` (0, 1, 2, 4).

Built: `set_z_compensation(excitation=, detector=, motcorr=, reference_um=)`, `add_z_compensation_point()`,
`remove_z_compensation_point(z_um)` (all with None), `move_to_z_compensation_point(z_um)` (a z move for the limits);
points named by their absolute z. Through the driver: excitation and detector on (points at 992.8 and 973.0), a point
added at 982.9, moved to 973.0, a point not there refused, all removed, the modes off, the focus put back at 982.9.

## 51. The autofocus in a time series (2 November 2026)

In xyt, the autofocus panel's Timelapse tab offers "First cycle only", "Every cycle" and "Every n-th cycle". Pressed
by the driver's mouse, no command: `Acquire/AutoFocusInfo/WorkflowTimelapse` 1, 2 and 3, n in
`WorkflowTimelapseIterator` (the window's field 1 to 10^9). Written into the state tree, both were taken and the
window followed. These changes had first been hidden by the record's own filter, which skipped any name with
"Time" in it (for clocks); the filter now looks at the node's own name and no longer skips "Time".

Built: `set_autofocus_in_time_series(when)`, `"first"`, `"every"` or n >= 2, read back. Through the driver: every,
5, first; 1 refused (that is "every"). The Stage tab was greyed out (no stage positions).

## 52. Auto Sync and its scan options; unsigned values in the state tree (2 November 2026)

The Best Focus Setting's X (`DeleteSetting`, pressed by the driver's mouse) sent `<Scanner Command="ResetAutoFocusToAutoSync"/>`:
`Acquire/AutoFocusInfo/IsAutoSyncActive` True, `IsSeriesScanAutofocusActive` False (as the help says). The
Autofocus button's own small button opened the Best Focus Scan Options window (inner window). Its Auto Sync box,
unticked, sent no command: `IsAutoSyncActive` False, and LCS copied Setting 1's light and detectors into the Best
Focus Setting. Auto Sync's options are in `Acquire/AutoFocusSettingConfocalScanOptions` (and `...Resonant`), each
with its own box: `ChangeFormat`/`WantedPixelSizeX` (the window's 16 to 512), `ChangeSpeed`/`WantedSpeed`
(`SpeedMin` to `SpeedMax`), `ChangeFieldOfView`/`SuggestedZoomReduction` (`ZoomMin` to `ZoomMax`), `UseBiDir`,
`NoAccuAndOrAverage`; LCS takes them only while Auto Sync is on.

Written into the state tree, the boxes, zoom and bidirectional were taken, but `WantedPixelSizeX` and `WantedSpeed`
were ignored as plain numbers and taken as LCS's unsigned number (`VT_UI4`). The z-stack's `SectionsCount` was
taken the same way (11, then back to 7); the time series' `RepeatCount` was ignored either way (the frames follow
the duration). State-tree writes can now be marked unsigned (`TreeWrite.unsigned`).

Built: `set_auto_sync(on)` (on with the window's command, off written), `set_auto_sync_options(scanner, ...)`,
a number ticking an option's box, False unticking it, refused while Auto Sync is off. Through the driver: on;
format 128, 1000 Hz, zoom 2 (the window showed 128); 1024 and 5000 Hz refused; back to 64 and 600, zoom off;
off; Series Scan Autofocus on again (`change_setting("autofocus", True)`).

## 53. The λ scan's detection (2 November 2026)

In xyλ, the λ panel's number fields took a value set through UI Automation only in the window; their arrows,
clicked by the driver's mouse, sent LCS commands: `<Scanner Command="SetLambdaDetectionBegin" Begin="390"/>`,
`SetLambdaDetectionEnd End=`, `SetLambdaDetectionStepsize Stepsize=`, `SetLambdaDetectionBandwidth Bandwidth=`,
whole nanometres. LCS keeps the values in `Confocal/LambdaDefinition` (`DetectionBegin`, `DetectionEnd`,
`DetectionStepSize`, `DetectionBandWidth`; `DetectionStepCount` did not follow). The window's begin and end run
380 to 800 nm.

Sent in the order step, bandwidth, begin, end, LCS kept 420 to 650 nm, 10, 15, and 380 to 800, 30, 30 exactly;
in other states (and through the driver just after switching to xyλ) it moved begin or end a few nm (650 to 655,
380 to 385, 800 to 775). Built: `set_lambda_scan(begin_nm=, end_nm=, step_nm=, bandwidth_nm=)`, in that order,
checked first, the result unconfirmed with what LAS X holds when it moved a value. Left at 385 to 775 nm, 30, 30;
the scan mode back at xyz.

## 54. The scan modes with z-galvo (2 November 2026)

Thom's hint. With z-wide, the Acquisition Mode list offered 12 scan modes (xyz, xyt, xt, xyzt, xyλ, xyλt, xyλz,
xyzλt, xyΛ, xyzΛ, xyΛt, xyΛλ); with z-galvo (`SetZUseMode UseMode="1"`) 19, the xz ones added: xzy, xzt, xzyt,
xzλ, xzλt, xzΛ, xzΛλ. Through the driver: z-galvo, `change_setting("scan_mode", "xzy")` and `"xzt"` taken; with
z-wide, LCS refused xzy ("invalid scan mode: xzy"), and the driver passed the refusal on. Built: `set_z_drive(drive)`,
the drive alone (before only with a whole z-stack). Left at z-wide, xyz.

## 55. The Laser Overview's output power (2 November 2026)

The Laser Overview (the beam path's laser button) lists Laser 405, the WLL, 488, 638 and 730; only the WLL's power
switch and slider are offered (85 %). Its slider set through UI Automation stayed in the window; a click of the
driver's mouse on its track (at the middle) set 49.4 % with no command: `LaserTargetOutputPower` on every WLL line
of the current setting's entry (`SequentialSettingParameter[n]/.../UnifiedLaserLine_SequentialInfo`). Written into
the state tree on one line, LCS set it on all eight and the window's slider followed (back to 85). The fixed lasers
read 0 there and are not offered.

Built: `set_laser_output_power(percent, source=None)`, the WLL unless another tunable source is named, with a new
limits entry `set_laser_output_power` (bundled default 0 to 85 %, LAS X's own value). Through the driver: 70 %
(the slider showed 0.7), 95 % refused by the limits, back to 85 %.

## 56. The Reflection autofocus's coverslip offset and its Find (2 November 2026)

With the method Reflection, the Best Focus Scan Options window shows "Coverslip offset [µm]" and "Find Coverslip
Reflection". The offset's arrows (the driver's mouse) sent no command: `Acquire/AutoFocusInfo/ReflectionOffset`
0 to 1e-06 and back (metres). Find sent `<AcquireControl Command="StartFindCoverSlipReflection" DCParentElementID="191"/>`;
LCS told LAS X "Cover glass reflex was not found (step 1)" (an information event in LAS X.log, no message box), and
the offset stayed.

Built: `set_coverslip_offset(um)` (within 5000 um either way, the autofocus's largest capture range) and
`find_coverslip_reflection()`, its search range checked against the z limits as the autofocus's, `found` read from
LAS X's log (option A: what LAS X did). Through the driver: 12.5 um, 9000 refused, Find not found (offset kept), back
to 0 and the method back to contrast 1.

## 57. "Apply to all settings", and the pinhole went into every setting (2 November 2026)

The XY panel's small boxes beside Line and Frame (averaging, accumulation) and the pinhole, pressed by the driver's
mouse: ticking one sent the current value again with `SetMode="1"` (`<Scanner Command="SetLineAverage" LineAverage="1"
SetMode="1" SingleSettingIndex="0" BlockId="0"/>`, `SetPinhole ... SetMode="1"`); unticking sent nothing. The pinhole's
arrows with the box unticked sent `SetPinhole ... SetMode="0"`. So `SetMode` 0 is this setting, 1 every setting (2,
the driver's form for the per-setting values, the setting at `SingleSettingIndex`).

The driver's `pinhole_um` had sent `SetMode="1"`, as an earlier log line (box ticked) showed: with several settings it
changed the pinhole in all of them. It now sends `SetMode="0"`, the current setting only, as the window. Built:
`apply_to_all_settings(name)`, the current value sent with `SetMode="1"`, through the limits as the setting's own value.
Through the driver: the pinhole 100.7 to 100 um (`SetMode="0"`), line average and pinhole applied to all settings.
The collar's `SetMotCorrPosition` keeps `SetMode="1"`, as the window sends it (it has no such box).

## 58. The time series' Minimize (2 November 2026)

The t panel's Minimize box (the driver's mouse) sent no command: `Acquire/AcquireTStackInfo/IsMinimized` True, and
back. Written into the state tree, LCS set the interval to the shortest (10 s to 1.28 s) and the window's box followed.
Built: `set_time_series(minimize=True/False)`; minimize with an interval refused. Through the driver: 10 s, minimized
(1.28 s), not minimized; frames put back to 1 and the scan mode to xyz. Dynamic Signal Enhancement, which the help
places in the XY panel, was not among the window's controls in xyt on the simulator.

## 59. TauGating's channels and gates (3 November 2026)

With HyD 2 in TauGating (set by the driver), its TauModes button opened "TauGating - HyD 2": one channel with one gate,
0.3 to 8.0 ns, on a scale from -1 to 11.5 ns. Pressed and dragged by the driver's mouse:

| Window | Command (with the detector's beam route) |
|---|---|
| A gate's handle dragged | `SetDigitalGateConfig ChannelIndex="0" SetMode="0" SingleSettingIndex="0" GateIndex1="0" GateBegin1="3E-10" GateEnd1="7.92E-09"` (seconds; sent while dragging) |
| + (a gate) | `AddDigitalGate ChannelIndex="0" GateBegin="8.125E-09" GateEnd="1.125E-08" SetMode="0" SingleSettingIndex="0"` |
| - (a gate) | `RemoveDigitalGate ChannelIndex="0" GateIndex="1" SetMode="0" SingleSettingIndex="0"` |
| Add Channel | `AddDetectorImageChannel IsChannelDigital="1"`, then `AddDigitalGate ChannelIndex="1" ...` |
| Remove Channel | `RemoveDetectorImageChannel ChannelIndex="1"` |

LCS holds them in the setting's entry: `DetectorChannelList_SeqInfo/DetectorChannel[i]/Gate[j]/GateBegin|GateEnd`,
`IsChannelActive`, and `FreeGatingChannelCount`. A click on the gate's track moved nothing (two handles share it),
and the only channel's on/off did not switch.

Built: `set_tau_gate`, `add_tau_gate` (at most three per channel), `remove_tau_gate` (a channel keeps one),
`add_tau_gate_channel`, `remove_tau_gate_channel` (one channel stays), only in TauGating, gates within -1 to 11.5 ns,
read back from the channels' gates (`get_detector_modes()[name]["gates"]`). Through the driver on HyD 2: the gate to
0.3 to 8.0, a gate 9 to 11 added, a channel 1 to 3 added and removed, the added gate removed, 20 ns refused; HyD 2
then back in TauSeparation (Cyan, Magenta).

## 60. The tau mode None, and greyed-out tau modes (3 November 2026)

The detector panel's TauModes list names, by place, exactly LCS's codes in `DigitalGatingModeList_SeqInfo`: None 0,
Intensity 2, TauGating 3, TauScan 5, TauSeparation 6, TauInteraction 10 (GateScan was not offered on the simulator).
Each entry has `IsEnabled`: for HyD 4 only Intensity was enabled; None needs TauContrast, the others a pulsed line in
counting. `SetDigitalGatingModeIndex` with a greyed-out place was answered `<Result/>` and changed nothing.

Built: "None" added to the driver's tau modes (one list, `writes.TAU_CODES`; `setting_files` derives its own from it);
`get_detector_modes()[name]["tau_available"]`; `set_detector(tau_mode=...)` refuses a greyed-out mode with nothing
sent. Through the driver: HyD 4's TauScan refused (only Intensity available).

## 61. The Fluorifier disc: notch filters and the polarizer (3 November 2026)

The beam path's Fluorifier button opened "Fluorifier Disc Settings": two notch filter wheels (left: Empty, NF 448/514/561,
NF 488/561/730, NF 448/514, NF 488/561/638; right: Empty, NF 685, NF 638, NF 488/638, NF 775, NF 660, NF 594), greyed out
while Auto Select is on, and the left wheel's other use as a polarizer, -90 to 90 degrees. Pressed by the driver's mouse,
no command: Auto Select `Confocal/HWParameter/IsNotchFilterAutoSelectionEnabled`; a notch filter the wheel's
`CurrentSlotPosition` (`PolarizationFW`, type 130; the right wheel type 131); the polarizer `PolarizationFW/IsSpectrumTurnMode`
and its angle `FilterSpectrumPos` (between `FilterSpectrumPosMin` and `Max`). Written into the state tree, each was taken
and the window followed.

The notch filters were already settable: `set_filter_wheel(130, "Empty")`, `set_filter_wheel(131, "NF 638")` confirmed.
Built: `set_notch_auto_select(on)` and `set_polarizer(angle_deg)` (None: the notch filters again). Through the driver:
Auto Select off, polarizer 45 degrees, 100 refused, back to the notch filters, Auto Select on (as found).

## 62. TauScan's images and range (3 November 2026)

With HyD 2 in TauScan, "TauScan - HyD 2" shows "No of Images" (1 to 7) and a lifetime range on a log scale. Its arrows
sent `<Scanner Command="SetDetectorTauScanCount" ImageCount="4">` (and 3); dragging the range's handle sent
`SetDetectorTauScanRange GateRangeBegin="2.11E-10" GateRangeEnd="9.366E-09"` (seconds), each with the detector's beam route.
LCS keeps them in the setting's entry (`TauScanImageCount`, `TauScanRangeBegin`, `TauScanRangeEnd`) and the most images in
the detector itself (`TauScanMaxImageCount`, 7 for HyD 2). Sent by hand: 10 images were ignored (3 kept), a range of 0.01 to
50 ns was clamped to 0.05 to 11.5 ns.

Built: `set_tau_scan(detector, images=, range_ns=)`, refused outside TauScan, above the detector's most, or outside 0.05 to
11.5 ns; `get_detector_modes()[name]["tau_scan"]`. Through the driver on HyD 2: 5 images, 0.5 to 8 ns; 9 refused; back to
3 and 0.1 to 10 ns; HyD 2 back in TauSeparation.

## 63. TauSeparation's + and -, and the colour lock (3 November 2026)

In "TauSeparation - HyD 2", + sent `<Scanner Command="AddDetectorTauSeparationChannel">` (with the beam route); LCS gave the new
channel 2 ns, and the window then sent its colour (`SetDetectorTauSeparationLut ... LutName="Yellow"`). - sent
`RemoveDetectorTauSeparationChannel ChannelIndex="2"`. A channel's lock sent its colour again with `IsUserDefined="0"`
(unlocked) or `"1"` (locked). Sent by hand, LCS took up to seven channels and ignored an eighth.

Built: `add_tau_channel(detector)` (at most seven), `remove_tau_channel(detector, channel)` (two stay), and `locked=` on
`set_tau_channel_colour`. Through the driver on HyD 2: a third channel (LCS gave it yellow, 2 ns), coloured green, removed;
removing a second refused; channel 1 unlocked and locked again.

## 64. TauInteraction's donor lifetime (3 November 2026)

"TauInteraction - HyD 2" shows "Average Donor Lifetime", 0.1 to 11.5 ns; its slider is not visible to UI Automation. Opening
the window sent `<Scanner Command="SetTauInteractionDonorLifetime" DonorLifetime="2.4E-09">` (with the beam route), LAS X's
default; LCS keeps it in the setting's entry (`TauInteractionDonorLifetime`). Built: `set_tau_donor_lifetime(detector, ns)`,
only in TauInteraction, 0.1 to 11.5 ns; `get_detector_modes()[name]["donor_lifetime_ns"]`. Through the driver on HyD 2: 3.1 ns,
20 refused, back to 2.4; HyD 2 back in TauSeparation.

## 65. The Λ (excitation) scan (3 November 2026)

In xyΛ, the Λ panel, by the driver's mouse: begin and end `<Scanner Command="SetLambdaExcitationBegin" Begin="495"/>`,
`SetLambdaExcitationEnd End="655"`, step `SetLambdaExcitationStepsize Stepsize="20"`, Automatic SP Movement
`SetLambdaAutomaticSPUnitMovement Movement="0|1"`, Method `SetLambdaDetectionBandwidthMode Mode="4"` (Right fixed) or
`"5"` (Constant), Gap to SP Unit `SetLambdaMinGap MinGap="11"`; SP Unit Lambda Max is the λ detection's end
(`SetLambdaDetectionEnd`); Lambda After Stage no command, `Confocal/LambdaDefinition/IsExcitationLambdaAfterStageDim`. The
window's begin and end ran 485 to 685 nm, the white-light laser's range on the simulator.

Built: `set_excitation_scan(begin_nm=, end_nm=, step_nm=, automatic_sp=, method=, min_gap_nm=, after_stage=)`, the step
first, begin and end within the white-light laser's range, what LAS X moved reported. Through the driver: 500 to 640 nm, 10,
constant, gap 15, after stage; 300 refused; back to 485 to 685, 20, right fixed, gap 10; the λ detection back to 385 to
775 nm, 30, 30; the scan mode back to xyz.

## 66. The Λλ scan's side and maximal distance (3 November 2026)

In xyΛλ, by the driver's mouse: the three option buttons for the detection's side sent `<Scanner Command="SetLambdaGeometry"
Geometry="0"/>` (detection behind, larger than the excitation), `"1"` (ahead) and `"2"` (independent); the maximal Λλ distance's
box `SetLambdaUseMaxGap UseMaxGap="1|0"` and its arrows `SetLambdaMaxGap MaxGap="50"`. The minimal gap is `SetLambdaMinGap`, as in
the Λ scan; the reverse direction was greyed out. Through the driver, 60 nm was taken with "behind" and "ahead", and LCS kept it
off with "independent". Built: `geometry=` and `max_gap_nm=` (False: none) on `set_excitation_scan`, the maximal distance refused
with an independent detection. Left behind, no maximal distance (40 nm stored); the scan mode back to xyz.

## 67. The xt line scan (3 November 2026)

In xt the t panel shows Line Interval (greyed), Page Interval, Minimize, Duration, Lines, Lines / Page and MAX. The Lines field (set
through UI Automation, as Frames) changed `Acquire/AcquireTStackInfo/Lines` and, with MAX on, `LinesPerPage`; no command. Written
into the state tree: `Lines` and `LinesPerPage` were ignored as plain numbers and taken as unsigned ones, `IsMaxLinesActive` and the
page interval (`DelayTime`) as plain ones; LCS works out `Pages` (4000 lines, 500 a page: 8) and the duration. With MAX, LCS puts
8192 lines on a page.

Built: `set_line_scan(lines=, max_lines_per_page=, lines_per_page=, page_interval_s=)`, only in xt, lines per page refused with MAX
on. Through the driver: 3000 lines, 600 a page (5 pages), 4 s; back to 2048000 lines, MAX, 20.48 s; the scan mode back to xyz.

## 68. Stacks, the X1 port and the MFP; what the simulator does not have (3 November 2026)

In xyzt the t panel's "Stacks" is the time series' frames: `set_time_series(frames=3)` showed 3 there. The X1 port is filter wheel
140 (---, Substrate, Mirror); `set_filter_wheel(140, "Substrate")` and back confirmed. The MFP (180: ---, Substrate, SD 470, DD
470/640) had been refused by the driver as one of LAS X's own wheels, but the window offers it: its Autoselect box writes
`HWParameter/IsMultiFunctionPortAutoSelectionEnabled`, and with it off its list writes `HWParameter/CurrentMultiFunctionPortPos`
(no command). Built: `set_mfp_auto_select(on)`, and `set_filter_wheel(180, name)` with Autoselect off (refused with it on), written
as the window does. Through the driver: DD 470/640, Substrate, Autoselect on again, a choice then refused.

Not found on the simulator, so not learned: Z Flow and Dynamic Signal Enhancement (not among the window's controls with z-galvo
in xyzt), the Fluo Turret (its list greyed out), Environmental Control (no such node in LCS), and Triggering (LCS lists 3 inputs
and 5 outputs and `Confocal/TriggerConfiguration`, but the plain Navigator shows no triggering window; the help says it needs the
Sequencer Advanced board, set in the Project Settings).

## 69. The white-light laser's Excitation Control (3 November 2026)

The Laser Overview's Excitation Control list for the WLL offers Maximum Power and Constant Power (its curve list greyed out). Chosen
in the window, no command: `Confocal/LightSourceList/LightSource[2]/AOTF/CurrentExcitationControlMode` 0 to 1, and LCS set
`CurrentConstantPowerCurve` 85 and `ExcitationControlBegin`/`End` 485 and 685 nm; back to 0, all reset. Written into the state tree,
LCS answered the same. Built: `set_excitation_control(mode, source=None)`. Through the driver: constant power, maximum power.

## 70. A setting's dyes, as the window's dye drop, in any setting (3 November 2026)

The window's dye drop sends `SetConfocalDefinitionFromDyeAssistant` (`IsAutoFocusDefinition="0"`, `DoAdjustSequenceCount` 1,
`MessageSource` DyeDragNDrop) with the setting's laser lines (route, line, wavelength, power) and its detectors (route, band, dye,
relation, colour and its lock); LCS rebuilds the setting's light and detection from it. Sent by the driver with one sequence while
the job had two settings, LCS cut the job down to one: Setting 2 was gone. A drop done with the driver's own mouse (ALEXA 514
dragged from the dye bar onto Setting 2's detectors) showed why: with two settings the window sends both, `<Sequence Index="0">`
for Setting 1 as it stands and `<Sequence Index="1">` for Setting 2 with the new dye.

LCS keeps each setting's lines (`IsLineVisible`, `Intensity`, `WaveLength`, `LineIndex`) and detectors (`IsActive`, `BeamRoute`,
`WaveLengthBegin`/`End`, `DyeName`, `DyeRelation`, `ImageChannelLut/LutName`, `IsUserDefinedLutName`) in its
`SequentialSettingParameter[n]`. Built: `get_setting_definitions()`, and `set_dyes(lines, detectors)` sending every setting, the
current one as asked and the others as they stand, then checking the job still has its settings and the others their dyes. Through
the driver with two settings: Setting 2's HyD 2 band 505 to 540 nm (HyD S 3 left out, so off), both settings kept. In "between
lines" sequential mode LCS gave Setting 1's HyD 2 the same band, as the window's own drop had (504 to 531): the settings share the
detectors' bands there, only the light switches between lines. Put back: Setting 2 deleted, Setting 1's HyD 2 485 to 527 nm, the
line 499 nm at 2 %.

## 71. TauContrast, as the detector panel's switch (3 November 2026)

With Setting 1's HyD 2 selected (the driver's mouse on its band), the detector panel showed TauContrast; its switch took
UI Automation's toggle (the mouse did not). No command for the switch itself: the setting's
`DetectorHWInfo_SequentialInfo[n]/IsLifetimeContrastActive` went True (beside `IsLifetimeContrastAllowed`), LCS added channels and
enabled the tau mode None, and the window sent the new channel's colour. (The record had hidden the flag: its noise filter skipped
names with "Lifetime".) Written into the state tree, LCS took it both ways and the window's switch followed. Built: `set_detector(
tau_contrast=...)` now writes it, in any setting, refused where LCS does not allow it; `get_detector_modes()[name]["tau_contrast"]`.
Through the driver: HyD 2 on and off (the switch followed), HyD 4 refused.

## 72. A detector's dye, as the detector panel's Dye list (3 November 2026)

The detector panel's Dye list (no id; beside Excitation Line) offers the dyes that fit the detector (for HyD 2, ALEXA 430 onwards).
Choosing ALEXA 514 with the driver's mouse sent no command: the setting's `DetectorHWInfo_SequentialInfo[n]/DyeName` changed, and
LCS followed on the detector itself. Written into the state tree, LCS took it, and took any text ("Not A Dye") as well. Built:
`set_detector(dye=...)` now writes it, in any setting, the name checked first against LAS X's dye library (Namespace/Name, the
namespace Leica or User:<name>); the saved-setting route is no longer used for the dye. Through the driver: HyD 2 to ALEXA 514, an
unknown dye refused, back to ALEXA 488.

## 73. A detector's excitation line (3 November 2026)

The detector panel's Excitation Line list offers "Auto (499)" and the setting's lines that are on ("499"). Choosing 499 with the
driver's mouse sent no command: the setting's `DetectorHWInfo_SequentialInfo[n]/TimegateReferenceLineIndex` went 0 to 1 (and the
detector's own), per detector, not one per setting as the driver had assumed. Written into the state tree, LCS took it, and any
number (5) as well. Built: `set_excitation_line(line, detector=None)` rewritten on that write, in any setting: "auto" is 0, a line
that is on is its place among the setting's lines plus one; a line that is not on is refused; with several detectors on, the
detector is named. The saved-setting route is gone. Through the driver: HyD 2 to 499, 561 refused, back to Auto.

## 74. The dye spectra shown (3 November 2026)

The settings toolbar's two spectrum buttons (`ShowExcitationButton`, `ShowEmissionButton`; the driver's mouse) sent no command:
the setting's `SequentialSettingParameter[n]/ShowExcitationSpectrum` and `ShowEmissionSpectrum` changed (and `SettingInfo`'s).
Written into the state tree, LCS took them and the buttons followed. `show_spectra` is rebuilt on that write, in any setting; with it
the saved-setting route's helpers no longer used were removed (`through_setting_1`, `with_detector`, `detector_shows`); `saved_now`
stays, for the detector lock's excitation line. Through the driver: excitation shown and emission hidden, then back.

## 75. Bulk Options: Lock All Settings, Disable All Settings (3 November 2026)

The settings toolbar's Bulk Options button opens a menu: Reset System, Reset ImageCompass, Lock All Settings and Disable All
Settings. Lock All Settings (the driver's mouse) sent `<Scanner Command="LockSetting" LockState="1" SetMode="1" .../>`: every
setting's detectors and lines were locked from the Dye Assistant (`IsBlockedForDyeAssistant`, `IsBlocked`). Pressed again it sends
the same, so the menu has no unlock; `LockState="0"` through LCS freed them all. Disable All Settings sent
`ATL_Setting_EnableDisableForScanning` with `SetMode="1"` and `EnableForScanning="0"`; `EnableForScanning="1"` switched them all
on again. Built: `lock_all_settings(locked=True)` and `enable_setting(on, all_settings=False)`. Through the driver: locked, freed,
every setting off and on again. Reset System and Reset ImageCompass were not pressed: they are hard to undo, and Thom decides.

## 76. The detector lock's excitation line, from the detector (3 November 2026)

The setting's HyD 2 lock (the driver's mouse; its excitation line on "Auto (499)", one line on) sent the two commands the driver
sends, the detector and the 499 line (`LineIndex="1"`, route 10;10), and LCS pinned the detector's `TimegateReferenceLineIndex`
from 0 to 1; freeing it left the pin. The Best Focus panel's lock (its HyD S 3, not on) sent only the detector's command. Built:
`lock_detector` takes the detector's own excitation line (section 73) instead of a saved setting, so it works in any setting; on
Auto it locks the one line that is on, and with several lines on Auto it is refused until the line is chosen, because LAS X's
choice cannot be read. `saved_now` and `read_back`, no longer used, were removed. Through the driver: HyD 2 locked on Auto and
on 499, freed each time, and its line put back to Auto.

## 77. Fast Live's Maximal Format steps (3 November 2026)

Fast Live's Scan Settings open with a right-click on the Fast Live button. The FOV scanner's Maximal Format slider and its box
read, through UI Automation, 16 to 512 in steps of 8; two clicks on the box's up arrow (the driver's mouse) wrote
`FastLiveParameters/ScanFormat` 256, 264, 272, and two on the down arrow put it back to 256. The driver took only powers of
two; it now takes the slider's steps of 8 within LCS's range (264 accepted, 260 refused). The window was closed.

Auto Sync's Maximal Format, in the Best Focus Scan Options window (a right-click on the Autofocus button opens it), reads the
same through UI Automation, 16 to 512 in steps of 8, and the driver now takes it the same way. Both windows' Minimal Scan
Frequency sliders step by 100 (100 to 2600 Hz); the driver checks the frequency against LCS's range only.

## 78. The y-stack of the xz scan modes; the zoom goes to 48 (3 November 2026)

With z-galvo, the scan mode xzy shows a Y-Stack panel instead of the Z-Stack panel, with the same controls. Its Begin and End
(the driver's mouse) sent the z-stack's own `BeforeBeginOrEndOfStackIsSet` and `SetZStackDefinition`, into `AcquireZStackInfo`.
Its Y-Position arrow and its Move to Begin sent no command: they wrote `Acquire/ZPosition` (the scan line, in metres;
`Confocal/YLineSelection/CurrentYLevel` followed), within `ZPositionMin` and `ZPositionMax` (±968.7 µm, the panel's Travel
Range of 1937.5 µm). Written into the state tree, LCS took it. The driver's size, sections and clear already worked on the
y-stack; its begin and end here and its moves used z-galvo instead of the line ("The current scan mode (xzy) do not support
a z positioning").

Built: `move_y_line(y_um)`, the Y-Position box, only in an xz scan mode (in the others the node is the focus), within LCS's
range; in an xz scan mode the stack's "here" is the line, and `move_to_z_stack` moves the line. The stack's ends still go
to the limits as z-galvo's, which is stricter than the line's range. Through the driver: the line to -8 and 6 µm, begin
and end set there, 8 sections, moved to the begin and the center; 2000 µm refused by LCS's range.

Choosing xzy set the zoom to 48, LCS's maximum, and back in xyz it stayed 48 (repeated once through the driver: 1, then 48
on xzy). This explains the zoom of 48 found earlier the same day. The stack was cleared, the line put at 0, xyz and z-wide
chosen again, and the zoom set back to 1.

## 79. The formula of the optical section; the autofocus's step count explained (3 November 2026)

The "+" beside the XY panel's Optical Section (`ToolbarButtonOptimizedMore`; the z-stack panel has the same) opens the
Formula of Optical Section Thickness. Its Emission λ arrow (the driver's mouse) wrote
`HWParameter/EmissionWaveLengthForAiryAnddzCalculation` 580 to 581, the node `change_setting("emission_wavelength")` already
writes; with it the pinhole's Airy range moved, and the autofocus's `FixSliceCount` went from 61 to 19047 and stayed there
when 580 was written back. That count is LCS's own, used only with the Best Focus "Number of Steps" box ticked
(`UseFixSliceCount`, off here: the autofocus runs by Precision), which explains the 61 to 801 seen on 2 November (section 44).
Its Apply to Step Size sent `<Scanner Command="SetOptimalStackStepSize" SamplingFactor="3"/>`: with no stack nothing changed;
with a z-wide stack of 980 to 986 µm in 1 µm steps the step became 1.3 µm, 6 sections, the end fitted to 986.5 µm. Built:
`apply_optical_section_step()`, its ends checked as the stack's. Refractive index, NA and Airy units are greyed out with
"Use from System" ticked, and only feed the window's own computation.

## 80. The Dye Assistant (3 November 2026)

The dye bar's `ButtonDyeAssistant` opened the Dye Assistant (on the right screen), with ALEXA 488 and one suggestion,
"None sequential". Its Apply sent `SetConfocalDefinitionFromDyeAssistant` with `MessageSource` FullDyeAssistant: the
command `set_dyes` sends for the dye drop, here with a 491 nm line and HyD S 3 from 496 to 738 nm. The suggestions
(Yield, Crosstalk) are the window's own computation, so the driver reaches the same result with `set_dyes`. The setting
was saved before and loaded again after (HyD 2, 499 nm).

## 81. The z-stack's Set Focus, pressed in the window (3 November 2026)

Set Focus sends LCS nothing, and LCS ignored the focus plane written into its state tree (section 50). Pressed in LAS X's
window through UI Automation (it is a toggle button, `ButtonSetFocus`), it cleared the plane that was set
(`AcquireZStackInfo/AFPlanePosition` and `IsAFPlanePosition`), and pressed again it set it where z-wide stood (982.9 µm).
Built: `set_z_stack_focus(on=True)` in `lasx_window`, beside the colour lock: on sets the plane where the focus is (a set
plane is pressed off first), off clears it; the plane is read back from LCS. The button was greyed out only before the
y-stack's begin was set; after a stack is cleared LAS X keeps it enabled. Through the driver: the formula's step (1.3 µm),
the plane cleared, set, and set again at 982.9 µm; the stack cleared after.

## 82. The Laser Overview's options; an image's settings applied (3 November 2026)

The Laser Overview (`ShowLaserOverviewButton`) lists the 405, 488, 638 and 730 nm lasers with their power switches greyed
out, and the white-light laser with its switch, its power (built: `set_laser_output_power`) and its options: Excitation
Control (built: `set_excitation_control`) and Excitation Curves (greyed out). The power switch was not pressed: whether the
driver switches a laser on or off is Thom's decision.

In the Projects tab, with Image001 of Project2 selected, the toolbar's Apply (`ButtonApply`, "Apply instrument parameters")
sent `<Scanner Command="ApplyImageSettings" DCImageID="194" RelightMode="0" WaitForSilenceHandle="0"/>`: the image's whole
setting came back (HyD S 3, 405 nm, 270 values changed). Not built: it would load laser powers and gains the driver cannot
check against the limits before they reach the microscope, and the driver clears LAS X's image cache after collecting its
pictures, so their IDs do not last. `save_setting` and `load_setting` (limits checked first) do the same. The setting saved
before was loaded again after (HyD 2, 499 nm).

## 83. The plain Navigator, opened and closed (3 November 2026)

The Acquisition Mode panel's `ButtonStageOverview` opened LAS X's Navigator full-screen on the right screen. Opening it
sent `SetStageUseMode` 7 and `SetStageIrregularTileScanPositions` with one tile where the stage stood (z-wide's 982.9 µm),
each three times, `GetCurrentZPosition`, and saved the job to a temporary file. Its window offers Live, Fast Live, Capture
Image and Spiral; Mark, the drawing tools, Hide and Focus Map; Preview and Start; and on its side Projects, Beam Path,
Scanmode, Z-Stack, Stage and Carrier. UI Automation shows only Mark, Hide and Close Navigator, so its other controls must be
learned by their place on the screen. Close Navigator sent `SetStageUseMode` 7, `StageClearAllPoints`, `SetStageUseMode` 1,
`StageClearAllPoints` and `SetStageUseMode` 0. Nothing was acquired with it open (section 9: LCS wrote dumps then).

## 84. Apply to hardware; the formula's typed inputs; the last unbuilt commands (3 November 2026)

The settings toolbar's last button (`ApplyToHardwareButton`, "Toggle apply to hardware") sent
`<ATL Activate="0" Command="ATL_ActivateHardwareConnectedMode" Attribute="0"/>` and set `Acquire/IsHwConnectedMode` from
True to False; pressed again, `Activate="1"` put it back. Off, LAS X keeps changes to a setting in its settings and leaves
the hardware as it is. Built: `apply_to_hardware(on)`, read back; tried through the driver, off and on, and the window's
button followed.

The Formula of Optical Section Thickness's "Use from System" box (`CheckBoxRefractiveIndexFromSystem`) sent LCS nothing,
unticked or ticked, and a refractive index typed in changed nothing in the state tree. Apply to Step Size then sent the
same `SetOptimalStackStepSize SamplingFactor="3"` as before, without the index: LCS computes the step from its own
values, so these inputs only change the window's own sum and are not built.

`ResetConfocalSetting` (`SetMode="0" SingleSettingIndex="0"`), seen on 1 November, came 2 s before an
`ATL_Setting_Delete`. It was taken here for part of the delete; section 91 shows it is the setting's own Reset (its
context menu), pressed just before the delete. A delete alone sends `ATL_Setting_Delete`, as `delete_setting` does. `ResetAutoFocusToAutoSync` is the Best Focus
Setting's X, built as `set_auto_sync(True)` (section 52). The mounting
medium (`ATL_HyVolution_SetMountingMedium`) has no control in the Acquire tab's window, so it is not built.

## 85. A laser line's own lock (3 November 2026)

Each row of the laser panel has its own lock, "Remove laser wavelength from optimization" (`LockLaser`, one per setting;
the first is the Best Focus Setting's). Setting 1's, on the white-light laser's 499 nm line, sent
`BlockLightSourceLineForDyeAssistant` with `LineIndex="1" SequentialSettingIndex="0" BlockedState="1"` alone, without the
detector's command, and set `IsBlocked` in the setting's `LineBlockedForAutomatic_SequentialInfo` and in the light source's
`LightSourceLineUsage` (beside its `LineIndex`); pressed again, `BlockedState="0"` freed it. The Best Focus Setting's lock
sent the same for its line index 0, which was not on, and LCS kept nothing. Built: `lock_laser_line(wavelength_nm,
locked)`, for a line on in the current setting, read back from `LightSourceLineUsage`; tried through the driver, locked and
freed, and 561 nm (not on) refused.

## 86. The window's last controls; the viewer takes no drawn point (3 November 2026)

Every control LAS X's Acquire window shows to UI Automation was compared with what is learned. Those left were these:

- The z panel's Sensitivity (`ButtonSensitivity`) shows a slider (`lMSSlider`) under the focus wheel. Moved from 0.8 to
  1.1 it sent LCS nothing: it sets how far the window's own wheel turns the focus, so it is not built. Put back to 0.8.
- `ComboWizards`, at the top left, switches LAS X to another application: Simulator (the plain one, in use), FRAP,
  FRAP XT, FRET AB, FRET SE, Lightning and Navigator Expert. Not built: the plain driver works in the plain application,
  and the Navigator Expert has its own driver.
- `lMSToolBarButtonToggle` is the Configuration, Acquire, Process, Quantify and Analysis bar, and `RaActivationButton`
  sits in the menu bar: outside the Acquire tab.

With the bleach mode on (`set_bleach_mode(True)`), the Bleach Point panel showed Load, Save, Calibrate, the list of
points (empty), the bleaching time (days to milliseconds), Add Laser and its own spectrum, and the viewer's toolbar gained
Draw point. Draw point was switched on through UI Automation and the image clicked with the driver's mouse, three times,
and no point appeared. Those clicks went to the wrong screen: LAS X's screen lies left of the main one, at negative
screen positions, and the clicks were placed as if it started at 0. Section 88 has the point drawn where it belongs.
Live was stopped, Draw point and the bleach mode switched off again.

## 87. Z-compensation's Set to Begin; no Move to and Apply (3 November 2026)

With a z-wide stack of 977.9 to 987.9 µm and the reference at 980 µm, the Linear Z-Compensation window's Set to Begin
(the driver's mouse) sent LCS no command and wrote `Confocal/ZCompensation/ReferencePosition` 0.00098 to 0.0009779, the
stack's begin; the points kept their offsets (7.9 and -2.1 µm), so they moved with it. `set_z_compensation(reference_um=)`
writes the same node, so nothing new is built. The window in LAS X 4.9 has Add, Delete, Move To, Remove All, the reference
and Set to Begin; the "Move to and Apply" that LAS X's help describes is not in it, so it is not built. The table only shows
each point's values. Put back: the points removed, the modes off, the stack cleared, the reference at 980 µm.

## 88. The Navigator's Stage, Carrier and Mark; a bleach point drawn, and its power (3 November 2026)

The clicks by screen position now take LAS X's screen where it is (left of the main screen, at x -1920 to 0). With them
the Navigator answered.

The Navigator's **Stage** tab opened its options: Tile Overlap (10 %), Acquire Separate Images per Region, Optimize
Acquisition Order of Regions, Save Overview, Clear Overview and Hide Region Shapes on Start of Acquisition, Load previous
experiment on startup, Merge Images during Acquisition with Keep Raw Data, 3D and Blend, and Fast Scan Settings. Each
switch, clicked and clicked back, sent LCS nothing and changed nothing in its state tree, except Optimize Acquisition Order,
which sent the tile positions again. They govern how the Navigator tiles, merges and saves its own pictures; the driver
places its fields itself (`tile_scan(positions)`), so they are not built. The **Carrier** tab offered None, Slide and Dish
(Chamber Slide, Multi Slide and Well Plate greyed out); a 75 x 25 mm glass slide drew the slide and offered Align, and sent
LCS nothing; put back to None. **Mark** put P1 in the Task List and sent `SetStageIrregularTileScanPositions` with the
stage's place, as `tile_scan` does with its positions. With the Navigator open, the scan mode was `xyzN` and LCS offered
the autofocus at the first position only (`CanDoFirstPositionOnly`); closing it cleared the points and put both back, so
the autofocus's Stage tab is not reached in the plain application: `tile_scan` and `autofocus()` per position do it.
Spiral, Preview, Start and the capture buttons acquire, and were not pressed (section 9).

In the Acquire window, with the bleach mode on, Draw point and one click in the image drew a bleach point ("ROI 0"; the
viewer marks it ROI 1). LCS got no command: `Confocal/ROIScan/CurrentBleachPoint` went from -1 to 0, and the bleach point's
setting (`SequentialBleachPointParameter`) took Setting 1's line, the white-light laser's 499 nm at 2 %. Its place and its
bleaching time (the panel's days to milliseconds: 2 s typed in sent nothing) LAS X keeps in its data container. The
panel's Intensity, one step up, wrote the line's `Intensity` 2.0 to 2.1 there with no command. Built:
`set_bleach_laser_power(laser_nm, percent)`, written into that node as the window does, checked against the laser limits,
refused with the bleach mode off or no point drawn; through the driver 4 % was taken and read back, 60 % refused by the
limits, and the power put back. The panel's bin removes the point's laser line (`SetConfocalLaserLinesDefinition` with
`IsBleachPointDefinition="1"` and no line); the viewer's Select All and bin removed the point. While the bleach panel is
in front, the light source's current values are the bleach point's. Put back: the point removed, Setting 1's 499 nm at 2 %,
Draw point and the bleach mode off.

## 89. An ROI scan's regions, drawn; ROI Configuration lists none on the simulator (3 November 2026)

With ROI scan on (`set_roi_scan(True)`), the viewer offered Draw rectangle, ellipse, polygon and range, and the ROI panel
Background, 3D (greyed out without a stack) and Show Roi Configuration Panel. Draw rectangle and a drag in the image drew a
region in both channels; LCS got nothing, as for a bleach point: the regions are kept in LAS X's data container. The ROI
Configuration window (on the right screen) showed a column for each laser line (405, 488, 638, 730 and the white-light
lines), but no row for the region, nor for a second one drawn with the window open; so the lines per region cannot be
learned on the simulator. Put back: the window closed, the regions removed (Select All and the bin), ROI scan off.

## 90. Bulk Options' Reset System and Reset ImageCompass (3 November 2026)

LAS X's help (Beam Path Settings: Adding, Loading, Renaming, Sorting, Resetting and Deactivating Settings) says Reset System
sets the ImageCompass and the global settings to their state when LAS X starts (xyz, 512 x 512, 400 Hz, resonant and
bidirectional off, zoom 1, z-galvo, rotation 0, Airy 1, no autofocus, ROI or bleach point, the z-stack's begin and end
reset, the filters and beam splitters at their start), the lasers staying on; Reset ImageCompass resets only the
ImageCompass (the setting's name, the spectral slider, the detectors' operating mode). Both are ordinary operator
controls, so they are built.

Each asks "Are you sure ...?" first (Yes, No). Answered Yes: Reset ImageCompass sent `<Scanner Command="ResetImageCompass"/>`;
the setting's line went off, the detectors and the Best Focus Setting went back to their start and the setting's name became
"Setting  1"; the zoom (2) stayed. Reset System sent `<Scanner Command="ResetSystem"/>`: the zoom went to 1, the z-stack's
ends were cleared, the step became System Optimized, the λ scan and the autofocus took their start values, and HyD 2's gain
went from 100 to 10. Built: `reset_system()` (read back on the zoom) and `reset_imagecompass()` (read back: no line on); the
driver sends them without the question. Through the driver: the setting saved, the zoom set to 2, Reset System (zoom 1, xyz,
no line on), Reset ImageCompass, and the saved setting loaded back (499 nm at 2 %, "Setting 1"). HyD 2 stays at gain 10:
in its tau mode LCS allows only 10.

## 91. A setting's context menu: Duplicate and Reset (3 November 2026)

LAS X's help (the same Beam Path Settings page) lists a right click on a setting: Duplicate and Reset; the menu also
offers "Copy to LDD setting". The driver's own right click on Setting 1's name opened it inside LAS X's main window.
Duplicate sent `<ATL CloneSetting="1" Command="ATL_Setting_Create" Attribute="2"/>`, as `add_setting(copy=True)` already
does: "Setting 2" came with Setting 1's 499 nm line and became the current setting. Reset, on Setting 2, sent
`<Scanner Command="ResetConfocalSetting" SetMode="0" SingleSettingIndex="0"/>`: the current setting's line went off and its
detectors went back to their start. This is the command seen on 1 November (section 84), so it is the setting's Reset,
not a part of deleting a setting. Built: `reset_setting()`, on the current setting, read back with no line on. Through the
driver: Setting 1 duplicated, the copy reset (Setting 1 kept 499 nm at 2 %), the copy deleted.

## 92. The Laser Overview's ON/OFF (3 November 2026)

LAS X's help (Activating Laser Lines) says to switch on the lasers needed before the first picture with the Laser
Overview's ON/OFF, and off again after the work; the solid-state lasers have a standby, ready again within a second, while
a laser switched on from off takes 1 to 2 minutes. So it is an ordinary operator control, and it is built.

In the Laser Overview (on the right screen) only the white-light laser's switch is enabled: LCS marks it
`SwitchablePowerState` 1, the 405, 488, 638 and 730 nm lasers 0. Clicked off, it sent
`<Scanner Command="SetLaserPowerState" PowerState="0">` with the laser's full route (10;10 and a third level 0, the laser in
its light source; LCS lists it as `LaserBeamRoute`), and `Laser/PowerState` went from 1 to 0; clicked on, `PowerState="1"`
put it back. Built: `switch_laser(name, on)`, refused for a laser LCS does not let be switched, read back from
`PowerState`. Through the driver: the white-light laser off and on, Laser 488 refused; the 499 nm line and the 85 % output
stayed.

## 93. A time series taken and saved (3 November 2026)

The layout follows what the driver's OME-Zarr writer already had: its arrays are t, c, z, y, x, with a time axis in
seconds, as the OME-Zarr (NGFF) specification orders them. So a time series is one store per field with every frame along
t, its t scale the time between frames, and `TimeIncrement` in its OME metadata; a tile scan stays one store per field.

`acquire()` with the scan mode xyt took one frame: it used Capture Image. The window's Start, in xyt with 3 frames, sent
`StartAcquiring` and LAS X wrote one picture, Series002: LAS X's dimension 4, 3 elements, in seconds, its byte step one
frame (both channels), and a time stamp for each channel of each frame. Built: with a time series set (a scan mode with t,
more than one frame), `acquire()` takes it with Start, as for a z-stack, the picture `"time_series"`; a series until stopped
is refused, as it would never finish. The reader takes dimension 4 (each frame from its own place, each plane its own
time), and the store holds them along t.

In HyD 2's tau mode LAS X also writes, for a moment, a second picture of the same name (three channels, its own pixels),
and lets go of its pixels soon after; this happens for a capture too. The reader now waits while two pictures share the
name and refuses only if two are still there at the end of its wait.

Through the driver: 3 frames, saved as one store of shape (3, 2, 1, 512, 512), its t scale 1.46 s (the frames' own times;
1.28 s was the interval set, LAS X's shortest), LAS X's cache let go of after.

## 94. Bleach points and ROI regions drawn by the driver (3 November 2026)

LCS gets nothing when a point or a region is drawn, so the driver draws them with the mouse in LAS X's viewer
(`procedures/lasx_viewer.py`). UI Automation does not say where the picture lies in the viewer (every layer covers the
whole viewer), but the viewer's status line does: "Cursor: Pos: 584.49 µm, 584.12 µm" from the picture's top-left corner,
with the picture's size ("x = 512 y = 512", "Size: 1162.50 µm x 1162.50 µm"). The driver puts the pointer on two places
40 screen pixels apart to learn the mapping (1.756 µm a screen pixel at 162 %), then on the target, and clicks only when
the status line shows the target pixel's centre within half a pixel. Learned on the way:

- The pointer is moved as a real mouse moves it (an absolute input event): put there only, the viewer did not always
  follow, and a stale reading once placed a rectangle's second corner wrongly. A reading that does not change after a
  move is now taken for none.
- A region drawn with a drag stayed unfinished: the status line froze, and LAS X dropped the region when the tool changed.
  A click at one corner and a click at the other finish it, and it stays.
- With two tau channels shown one above the other the picture is 2.52 µm a screen pixel, coarser than a picture pixel
  (2.27 µm); the driver refuses then, and says to zoom in or show one picture. With one panel (162 %) every target was hit.
- Select all ROIs is enabled while any region or point is drawn; LAS X shows no count. A bleach point reads back through
  LCS: `CurrentBleachPoint` went -1, 0, 1 with each point.

Built: `draw_bleach_point(x_px, y_px)` (bleach mode on, read back from LCS), `draw_roi_region(shape, corner, opposite)`
(ROI scan on, "rectangle" or "ellipse"), `clear_drawings()`. Through the driver on the live picture: a rectangle from
pixel (60, 60) to (200, 160) and an ellipse in (260, 300) to (450, 460), both kept and in place; a bleach point at (400,
120) in place; pixel 512 refused; everything cleared, the modes off, the viewer's display put back.

## 95. No LCS route for regions; the drawing sent as window messages (3 November 2026)

Thom asked whether regions can be added through LCS or another server instead of the mouse. LCS's whole command log
holds no command for ROIs or bleach points. The regions are LAS X's data container's objects (its log lists ROI sets,
`DefaultScanROISet`, `BleachPointROISet`, and no requests a client could send); it has no documented interface, and it is
not taken apart. A saved setting carries them in full (`<ROI>` with a `DCROISet` of `ROISingle` elements: `RoiType` 16 a
rectangle, 32 an ellipse of 64 vertices, the vertices in metres around the region's centre, a `Transformation` with the
scan field's rotation (270°) and the centre's `Translation` from the field's middle, and per region an `AOTF_SETTING`
with every laser line's intensity, which is ROI Configuration; the bleach points in `BleachPoints/BleachPointROISet`). But
a setting with a region in it, loaded through LCS (`load_setting`, `ATL_Block_Load_From_XML`), did not bring the region
back: the current ROI set (`CurrentDCROISetID` 177) stayed empty, also after Live.

So the window it is, but without the computer's pointer: mouse moves and clicks posted to LAS X's window
(`WM_MOUSEMOVE`, `WM_LBUTTONDOWN`, `WM_LBUTTONUP`) drew a bleach point, and the status line followed the posted moves
exactly. `lasx_viewer` now sends all its moves and clicks so. Through the driver: the rectangle (60, 60)-(200, 160), the
ellipse (260, 300)-(450, 460) and the bleach point (400, 120) drawn in place and cleared, and the pointer stood at the same
place before and after. The toolbar's tools are still pressed through UI Automation.

## 96. Points and regions belong to the scan field: two guards (3 November 2026)

Thom pointed to the region work done with the Navigator Expert (March to May 2026, `experimental/lrp_edits/roi.py` and
`scanfields/` in the earlier smart-microscopy repository; read for its lessons only, not used): a region's vertices are
metres around its centre and its `Translation` the offset from the field's middle (the stage centre, x negated), both in
the display frame; the pan moves the field, so it moves them; the picture's orientation must be the display's for pixels
to map onto regions; and with another objective the regions landed a little elsewhere on the sample. All of this says the
same: a bleach point or region is kept in the scan field, not on the sample.

Two guards follow. Before drawing, the driver compares the picture LAS X's viewer shows (by the name on its status line,
"Preview" for the last scan) with the field now, from the picture's own description in LAS X's cache: objective, zoom,
stage position, pan (`PanFirstDim`, `PanSecondDim`, the pan now read from a setting saved now) and rotation; a picture
taken with any of them otherwise is refused, and a new one is taken first. And `change_objective` is refused while a
bleach point or region is drawn: they are cleared, and drawn again on a picture taken with the new objective. (The
objective change itself already moves the stage by the measured offset between the two objectives, which keeps the field
on the same place of the sample; whether LAS X keeps a region's size in micrometres across the change is not yet
measured: the simulator's pair is not calibrated.)

On the simulator: the last scan's picture passed; with the zoom set to 2 a point was refused ("taken with the zoom 1
(now 2)"); at zoom 1 it was drawn; with it drawn, the objective change was refused before anything turned.

## 97. The pan and drawn regions; the pan set from the centre (3 November 2026)

A rectangle drawn at pan 0 kept its `Translation` (-12.69, -14.10 µm) in a setting saved after the pan moved 100 µm
across (`PanFirstDim` 0 to 0.001): in plain LAS X a region stays where it is in the scan field, so a pan moves it across
the sample. (The old Navigator Expert note, that the translation is measured from the stage's centre, does not hold here.)
The simulator's picture is a pattern that does not follow the pan, so how far the sample moved could not be seen. So
`move_pan`, `center_pan` and the new `set_pan` are refused while points or regions are drawn, as `change_objective` is.

`set_pan(x_um, y_um)` sets the pan to a place in micrometres from the centre, right and up positive, as Thom set it on the
Navigator Expert route: the pan is read now (from a setting saved now) and moved by the difference with the pan pad's
arrows. LCS's whole command log holds no pan command; the old route wrote `PanFirstDim` and `PanSecondDim` into the
Navigator Expert's template and had the Navigator Expert load it; in plain LAS X a loaded setting does not set the pan
(section 18). So the arrows, pressed through UI Automation (no mouse), stay the only way; `center_pan` alone needs no
window, as LAS X's smallest zoom pulls the pan to the centre.
