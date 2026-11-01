# Everything a scanning template holds, and how far the ZMART Driver covers it

A Navigator Expert scanning template (`.lrp`, in
`%APPDATA%\Leica Microsystems\LAS X\MatrixScreener6\User_0\ScanningTemplates\`) holds
LAS X's whole acquisition setup, job by job. It is used here as the list of everything
that can be set in LAS X's software. This page was made from
`{ScanningTemplate}_ZMART_z.lrp` (three jobs) on 1 November 2026, on the LAS X simulator.

For each setting it says what the ZMART Driver reads and writes today, and which LCS
command writes it, where LCS's log has shown LAS X's window sending it. "Not yet learned"
means the command has not been seen yet; it is learned by using the control once in LAS
X's window.

## Scan

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `Zoom` | yes | yes | `SetZoom` |
| `ScanSpeed` | yes | yes | `SetScanSpeed` |
| `RotatorAngle` (scan field rotation) | yes | yes | `SetConfocalScanFieldRotation` |
| `LineAverage` | yes | yes | `SetLineAverage` |
| `FrameAverage` | yes | no | `SetFrameAverage` (seen in the log) |
| `Line_Accumulation` | yes | no | `SetLineAccu` (seen in the log) |
| `FrameAccumulation` | yes | no | `SetFrameAccu` (seen in the log) |
| `InDimension`, `OutDimension`, `Lines` (format) | yes | no | `SetFormat` (seen in the log) |
| `ScanMode` | yes | only inside a tile scan | `ScanMode` (seen in the log) |
| `ScanDirectionX` (uni- or bidirectional) | no | no | not yet learned |
| `PanFirstDim`, `PanSecondDim` (pan) | no | no | not yet learned |
| `PhaseX` | no | no | not yet learned |
| `FlipX`, `FlipY`, `SwapXY` | no | no | not yet learned |
| `BitSize` | yes | no | not yet learned |
| `IsResonantScanner` | yes | no | `SetScannerToResonant` (seen in the log) |
| `PixelDwellTime`, `LineTime`, `FrameTime` | no | no | follow from speed, format and averaging |

## Z and the z-stack

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `ZPosition` (focus) | yes | yes, as a step | `SetZPosition` |
| `ZUseMode` (the stack's drive) | yes | no | `SetZUseMode` (seen in the log) |
| `Begin`, `End` | yes | no | `SetZStackDefinition` (seen in the log) |
| `Sections`, step size | yes | no | `SetStackStepSize` (seen in the log) |
| `StackCalculationMode` (system optimized) | no | no | not yet learned |
| `ZStackDirectionMode` | no | no | not yet learned |
| `AdditionalZPosition` (Super Z) | no | no | not yet learned |

## Objective and stage

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `ObjectivePos`, `ObjectiveName`, `Immersion`, `NumericalAperture` | yes | yes (the slot) | `SetSlotPosition` (from the code, not yet sent) |
| `MotCorrPosition` (correction collar) | yes | no | not yet learned |
| `StagePosX`, `StagePosY` | yes | yes | `SetStagePositionInHwOnly` |
| `IsAutofocusActive` | no | only inside a tile scan | `ActivateAutoFocus` (seen in the log) |

## Pinhole

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `PinholeAiry` | yes | yes | `SetPinholeAU` |
| `Pinhole` (in metres) | yes | no | not yet learned |
| `EmissionWavelengthForPinholeAiryCalculation` | no | no | not yet learned |

## Lasers

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `LaserLineSetting` `IntensityDev` (power per line) | yes | no | `SetLaserLineIntensity`, tried by hand for the 405 line |
| `Shutter` `IsActive` (per light source) | yes | no | `ActivateShutter` (seen in the log) |
| `LineDeactivationFlags` (line on or off) | no | no | not yet learned |
| `Aotf` settings | no | no | not yet learned |

## Detectors

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `IsActive` (the channel on or off) | yes | no | not yet learned |
| `Gain` | yes | no | not yet learned |
| `Offset` | yes | no | not yet learned |
| `DetectionRangeBegin`, `DetectionRangeEnd` (the band) | yes | no | not yet learned |
| `AcquisitionMode` (photon counting, ...) | no | no | not yet learned |
| `DyeName` | yes | no | not a hardware setting |
| detector overload | no | no | never sent by the ZMART Driver |

## Filters and sequences

| Setting in the template | Reads today | Writes today | LCS command |
|---|---|---|---|
| `Wheel` `FilterIndex` (per filter wheel) | yes | no | `FilterWheel_ApiSetSlot` (seen in the log) |
| `IsEnabledForSequentialScanning`, sequential mode | yes | no | `SetConfocalSequentialMode` (seen in the log) |
| several sequential settings in one job | one only | no | not known |

## STED, FLIM, dye separation, bleach and ROI, triggers, light sheet

These are in the template too, and in scope. None is read or written today, and none of
their commands has been learned yet. The STELLARIS simulator can do neither STED
(`CanDoSTED="0"`) nor FLIM (no FLIM detector); the ZMB's SP8s do both, so these are
learned there, through the same LCS route once it is checked on an SP8. The STED beam
alignment is service work and stays out.

| Setting in the template | Where it is learned |
|---|---|
| STED (`STED_DepletionLine`, `IsSTEDActive`, STED detector modes) | an SP8 with STED |
| FLIM and lifetime (`TauScanDef`, `IsFlimDetector`, time gate) | an SP8 with FLIM |
| Online dye separation (`OnlineDyeSeparation`) | to be seen |
| Bleach points and ROI scans (`BleachPoints`, `ROISet`, `IsRoiScanEnable`) | to be seen |
| Triggers (`InTrigger`, `OutTrigger`) | to be seen |
| Light sheet (`SpimCACompensationParameter`) | to be seen |

The job sequence itself belongs to Navigator Expert, not to this driver.

## What this means

- The ZMART Driver reads most of the template already: the scan, z, the objective, the
  lasers, the detectors and the filters.
- It writes 8 of the template's settings today (zoom, speed, rotation, line average,
  pinhole, focus, stage, objective), and sets the scan mode and autofocus only inside a
  tile scan.
- 14 more have a command seen in LCS's log. The rest are not yet learned: each is learned
  in LCS's log while LAS X's window makes the change, before it can be built.
