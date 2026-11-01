# How the ZMART Driver talks to the microscope

This page is for the ZMART Driver for LAS X's plain Navigator, at
`zmart_drivers/leica/stellaris/navigator/`. What it says was seen on the LAS X simulator on
30 October 2026, in LAS X's own command log
(`C:\ProgramData\Leica Microsystems\LAS X\lcsCommand.log`).

## 1. The short version

LAS X's window does not move the microscope itself. It sends commands to a program inside
LAS X called LCS, LAS X's hardware server (`LCS.EXE`), and LCS drives the hardware. The
ZMART Driver sends the same commands to the same LCS.

```
  LAS X window ──► LCS ◄── ZMART Driver
                    │
                    ▼
                hardware
```

## 2. What the ZMART Driver writes and what it reads

### Writes: commands that change something

The ZMART Driver sends each of these with LCS's `SendCommand2`. The full command is shown
as LCS's log recorded it on the simulator; the numbers are those of that run. Positions
and lengths are in metres. "LCS answered" is what LCS's log shows it answered.

| Command | Full command, as sent | What it does | Sent when | LCS answered |
|---|---|---|---|---|
| `SetStagePositionInHwOnly` | `<Scanner Command="SetStagePositionInHwOnly" StagePosX="0.0635000000" StagePosY="0.0415000000" RelativeCoordinates="0" MoveStageX="1" MoveStageY="1"/>` | moves the stage to x, y | a stage move | `Error="0"` (accepted), every time |
| `SetZPosition` | `<Scanner Command="SetZPosition" PosZ="0.0000020000" RelativeCoordinates="1" ZUseMode="2" BlockId="0"/>` | moves a z drive by a step (here z-wide, +2 µm; `ZUseMode="1"` is z-galvo) | a z move | `Error="0"`, every time |
| `SetZoom` | `<Scanner Command="SetZoom" Zoom="1.5" BlockId="0"/>` | sets the zoom | `change_setting("zoom", ...)` | `Error="0"`, every time |
| `SetScanSpeed` | `<Scanner Command="SetScanSpeed" Speed="600" BlockId="0"/>` | sets the scan speed | `change_setting("scan_speed", ...)` | `Error="0"`, every time |
| `SetConfocalScanFieldRotation` | `<Scanner Command="SetConfocalScanFieldRotation" Rotation="5" BlockId="0"/>` | sets the scan field rotation | `change_setting("scan_field_rotation", ...)` | `Error="0"`, every time |
| `SetLineAverage` | `<Scanner Command="SetLineAverage" LineAverage="2" SetMode="2" SingleSettingIndex="0" BlockId="0"/>` | sets the line average | `change_setting("line_average", ...)` | `Error="0"`, every time |
| `SetPinholeAU` | `<Scanner Command="SetPinholeAU" PinholeAU="1.5" SetMode="2" SingleSettingIndex="0" BlockId="0"/>` | sets the pinhole, in Airy units | `change_setting("pinhole_airy", ...)` | `Error="0"`, every time |
| `SetSlotPosition` | `<Microscope Command="SetSlotPosition" SlotPosition="1"/>` (from the ZMART Driver's code) | turns the objective turret | `change_objective(...)` | not sent yet |
| `StopAcquiring` | `<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>` | the window's stop, sent before every picture; LAS X is already idle then | a capture, a tile scan | `<Result/>` (no verdict) |
| `CaptureSingleFrame` | `<AcquireControl Command="CaptureSingleFrame" DCParentElementID="24" IsSingleImageCapture="1" SingleScanMode="0" AdditionalValue="1.0000000000" AdditionalValue2="0"/>` | takes one picture with the current settings (Capture Image), into the LAS X project 24 | `acquire(...)` | `Error="0"`; once `Error="1"`: "Can not start scan! (Reason: No active channel in one or more Sequences)" |
| `SetStageUseMode` | `<Scanner Command="SetStageUseMode" StageUseMode="7"/>` (after: `StageUseMode="0"`) | switches the stage to tile scanning (7), and back to normal (0) | a tile scan, and after it | `<Result/>` |
| `SetStageIrregularTileScanPositions` | `<Scanner Command="SetStageIrregularTileScanPositions" FocusOffset="0.0000000000"><Tile XPos="0.0635000000" YPos="0.0415000000" ZPos="-0.0000000002" FocusStabilizerOffset="0.0000000000"/><Tile XPos="0.0647787500" YPos="0.0415000000" ZPos="-0.0000000002" FocusStabilizerOffset="0.0000000000"/><Tile XPos="0.0660575000" YPos="0.0415000000" ZPos="-0.0000000002" FocusStabilizerOffset="0.0000000000"/></Scanner>` | the fields of a tile scan, one `Tile` each | a tile scan | `<Result/>` |
| `ATL_SuppressAtlasAttachment` | `<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="1"/>` (after: `EnableSuppression="0"`) | as the window sends it around a Preview | a tile scan, and after it | `<Result/>` |
| `ScanMode` | `<Scanner Command="ScanMode" ScanMode="xyt" BlockId="0"/>` (after: `ScanMode="xyz"`) | sets the scan mode | a tile scan, and after it; `change_setting("scan_mode", ...)` | `Error="0"` |
| `ActivateAutoFocus` | `<Scanner Command="ActivateAutoFocus" Activate="0" InitNew="0"/>` (after: `Activate="1"`) | autofocus off for the tile scan, on after | a tile scan, and after it; `change_setting("autofocus", ...)` | `<Result/>` |
| `StartAcquiring` | `<AcquireControl Command="StartAcquiring" DCParentElementID="24"/>` | starts the tile scan (the window's Preview), into the LAS X project 24 | `tile_scan(...)` | `<Result/>` |
| `StageMoveToCoordinates` | `<Scanner Command="StageMoveToCoordinates" PosX="0.0635000000" PosY="0.0415000000" PosZ="-0.0000000002"/>` | the stage back to the first field | after a tile scan | `<Result/>` |
| `SetZUseMode` | `<Scanner Command="SetZUseMode" UseMode="2"/>` | the drive the z-stack uses (1 z-galvo, 2 z-wide) | `set_z_stack(...)` | `<Result/>` |
| `SetZStackDefinition` | `<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="1" BeginValue="0.0009950000" EndValue="0.0010050000" BlockId="0"/>` | the z-stack's begin and end | `set_z_stack(...)` | `Error="0"` |
| `SetStackStepSize` | `<Scanner Command="SetStackStepSize" StepSizeInMeter="0.0000025000" BlockId="0"/>` | the z-stack's step | `set_z_stack(...)` | `Error="0"` |
| `StartAcquiring` (a z-stack) | `<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>`, then `<AcquireControl Command="StartAcquiring" DCParentElementID="24"/>` | takes the whole z-stack (the window's Start) | `acquire(...)` with a stack set | `<Result/>` |
| `SetLaserLineIntensity` | `<Scanner Command="SetLaserLineIntensity" LineIndex="0" LineIntensity="0.05" SetMode="2" SingleSettingIndex="0" BlockId="0" IsRoiBackground="0"><BeamRoute Version="0"><BeamPosition BeamPositionLevel="0" BeamPosition="10"/><BeamPosition BeamPositionLevel="1" BeamPosition="30"/></BeamRoute></Scanner>` | a laser line's power; above 0 switches the line on | `set_laser_power(...)` | `Error="0"` |
| `ActivateShutter` | `<Scanner Command="ActivateShutter" Activate="0" SetMode="2" SingleSettingIndex="0" BlockId="0"><BeamRoute Version="0"><BeamPosition BeamPositionLevel="0" BeamPosition="30"/></BeamRoute></Scanner>` | a light source's shutter | `set_laser_shutter(...)` | `Error="0"` |
| `SetFrameAverage` | `<Scanner Command="SetFrameAverage" FrameAverage="2" SetMode="2" SingleSettingIndex="0" BlockId="0"/>` | frame average | `change_setting("frame_average", ...)` | `Error="0"` |
| `SetLineAccu` | `<Scanner Command="SetLineAccu" LineAccu="2" SetMode="2" SingleSettingIndex="0" BlockId="0"/>` | line accumulation | `change_setting("line_accumulation", ...)` | `Error="0"` |
| `SetFrameAccu` | `<Scanner Command="SetFrameAccu" FrameAccu="2" SetMode="2" SingleSettingIndex="0" BlockId="0"/>` | frame accumulation | `change_setting("frame_accumulation", ...)` | `Error="0"` |
| `SetFormat` | `<Scanner Command="SetFormat" FormatX="256" FormatY="256" BlockId="0" IsAutoFocusDefinition="0"/>` | the image format | `change_setting("format", ...)` | `Error="0"` |
| `SetScannerToResonant` | `<Scanner Command="SetScannerToResonant" Enable="1" BlockId="0"/>` | the resonant scanner on or off | `change_setting("resonant_scanner", ...)` | `Error="0"` |
| `FilterWheel_ApiSetSlot` | `<Scanner Command="FilterWheel_ApiSetSlot" FilterWheelType="140" SlotIndex="1" SetMode="2" SingleSettingIndex="0" BlockId="0"><BeamRoute Version="0"/></Scanner>` | a filter wheel's slot | `set_filter_wheel(...)` | `Error="0"` |

The ZMART Driver sends only the commands in this table: its transfer refuses every other
one before it reaches LCS.

Letting go of a saved picture is the one write that does not go to LCS: the ZMART Driver
asks LAS X's Data Container to delete it (`clear_cache()`).

**A tile scan, in the order it is sent.** The ZMART Driver sends the window's Preview as
these commands, one after the other, and stops at the first one LCS refuses:

1. `<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>`
2. `<Scanner Command="SetStageUseMode" StageUseMode="7"/>`
3. `<Scanner Command="SetStageIrregularTileScanPositions" ...>` with one `Tile` per field
4. `<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="1"/>`
5. `<Scanner Command="ScanMode" ScanMode="xyt" BlockId="0"/>`
6. `<Scanner Command="ActivateAutoFocus" Activate="0" InitNew="0"/>`
7. `<Scanner Command="SetStageUseMode" StageUseMode="7"/>`
8. `<Scanner Command="SetStageIrregularTileScanPositions" ...>` again
9. `<AcquireControl Command="StartAcquiring" DCParentElementID="24"/>`

Once the scan is over, or when it never started, the ZMART Driver puts back what it
changed:

1. `<Scanner Command="StageMoveToCoordinates" PosX="..." PosY="..." PosZ="..."/>`
2. `<Scanner Command="SetStageUseMode" StageUseMode="0"/>`
3. `<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="0"/>`
4. `<Scanner Command="ScanMode" ScanMode="xyz" BlockId="0"/>`
5. `<Scanner Command="ActivateAutoFocus" Activate="1" InitNew="0"/>`

### Reads: nothing changes

Most readings come from LCS's state tree, where LCS keeps the current value of everything.
The ZMART Driver reads it through `ServerNodes`; LAS X's window reads the same tree.

| What | Read from | How |
|---|---|---|
| Stage x, y | `Stage/PositionX`, `Stage/PositionY` | state tree |
| z-galvo and z-wide | `ZPosZGalvo`, `ZPosZDrive` | LCS's `Hardware` |
| Scan status (idle or scanning) | `Acquire/AcquisitionState` | state tree |
| Scan mode | `Acquire/ScanDimensionList/ScanModeName` | state tree |
| Zoom, with its range | `Confocal/ScanningParameter/Zoom`, `ZoomMin`, `ZoomMax` | state tree |
| Scan speed, with its range | `Confocal/ScanningParameter/Speed`, `SpeedMin`, `SpeedMax` | state tree |
| Scan field rotation, with its range | `Confocal/HWParameter/ScanRotationAngle` (and `Min`, `Max`) | state tree |
| Line and frame average and accumulation | `Confocal/ScanningParameter/LineAverage`, `LineAccu`, `FrameAverage`, `FrameAccumulation` | state tree |
| Pinhole | `Confocal/HWParameter/PinholeAU` (and `Min`, `Max`), `Pinhole` (and `Min`, `Max`), `PinholeUnit`, `EmissionWaveLengthForAiryAnddzCalculation` | state tree |
| Phase X, with its range | `Confocal/HWParameter/PhaseX` (and `Min`, `Max`) | state tree |
| Format, pixel size, field of view | `Confocal/ScanningParameter/CurrentFormat`, `FormatX`, `FormatY`; `Acquire/AcquireImageSizeInfo` | state tree |
| Bit depth | `Confocal/ScanningParameter/Resolution` | state tree |
| Sequential mode | `Confocal/ScanningParameter/IsSequentialEnabled`, `SequentialMode` | state tree |
| Z-stack: begin, end, step, sections, drive | `Acquire/AcquireZStackInfo`, `Acquire/ZPositionMode` | state tree |
| Objective in use, and all objectives | `Microscope/SlotPosition`, `Microscope/ObjectiveList` | state tree |
| Laser lines: wavelength, intensity, shutter | `Confocal/LightSourceList` | state tree |
| Detectors: band, dye, gain, offset | `Confocal/SpectralDetection` | state tree |
| Filter wheels | `Confocal/HWParameter/FilterWheelList` | state tree |
| The stage's travel, as LAS X allows it | `Stage/DynamicLimitXmin` ... `DynamicLimitYmax` | state tree |
| Serial number, stand | `Acquire/ActiveDataSource/CurrentSerialNumber`, `Microscope/CurrentMicStandName` | state tree |
| Whether the window shows Navigator Expert's jobs | `Acquire/ATL/CurrentSequenceList` | state tree |
| The instrument's description | `<Scanner Command="GetConfocalHardwareInfoAsJson"/>` | `SendCommand2`; the one command that only reads |

## 3. What happens to one command

```
  ZMART Driver (Python)
      │
      │  SendCommand2("<Scanner Command=... />")      ← our only way in
      ▼
  LCS.EXE  (LAS X's hardware server)
      │
      ├─► 1. logs it ──────────────► lcsCommand.log
      │
      ├─► 2. checks it, answers at once
      │        │
      │        └──────────────────► back to the ZMART Driver:
      │                              Error="0"  accepted
      │                              Error="1"  refused, with a reason, nothing changes
      │                              Error="2"  accepted, value adjusted
      │                              <Result/>  no verdict (some commands)
      │
      ├─► 3. carries it out, often after answering
      │        settings  → at once
      │        stage move → arrives a second or more later
      │        capture    → scan runs after the answer
      │        message box in LAS X's window → SendCommand2 waits until it is closed
      │
      ├─► 4. drives the hardware through its device drivers
      │        │
      │        ▼
      │     stage · z drives · turret · scanner · lasers · detectors
      │     (on this PC: simulator drivers; on the STELLARIS: the real ones)
      │
      ├─► 5. updates its state tree
      │        │
      │        ├──────────────────► LAS X's window shows the new value
      │        │                     (your laser slider moved)
      │        │
      │        └──────────────────► the ZMART Driver reads it back (ServerNodes)
      │                              and confirms the change
      │
      └─► 6. a picture: pixels into the Data Container cache
               │
               └──────────────────► the ZMART Driver reads them, saves OME-Zarr,
                                     then has LAS X let go of the picture
```

An answer of `Error="0"` means LCS accepted the command, not that it is done. That is
why the ZMART Driver reads every change back from the state tree, and watches a picture's
scan until LAS X is idle again.

What LCS checks between step 2 and step 4, before it touches the hardware, cannot be seen
from outside. Only Leica can say.
