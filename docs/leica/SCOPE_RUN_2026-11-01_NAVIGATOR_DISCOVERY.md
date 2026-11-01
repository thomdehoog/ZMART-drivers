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
