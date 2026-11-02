"""A stand-in for LAS X's hardware server with its plain Navigator shown: the state tree, and the commands it takes.

The offline suite runs the whole driver against this: the reads walk the
state tree stand-in (``state_tree_stand_in.py``, as on the simulator), and
the writes are the elements the plain Navigator route sends, applied to
the same tree as LAS X applies them (simulator and STELLARIS, 2026-10-15):

- ``SetZoom``, ``SetScanSpeed``, ``SetConfocalScanFieldRotation``,
  ``SetLineAverage`` and ``SetPinholeAU`` change the current setting's node;
- ``SetZPosition`` with ``RelativeCoordinates="1"`` steps the named z drive;
- ``SetStagePositionInHwOnly`` puts the stage at the position given;
- ``SetSlotPosition`` turns the turret;
- ``CaptureSingleFrame`` and ``StartAcquiring`` run a scan: the scan status
  reads ``eScanSingle`` for a few reads, then idle again;
- the other elements of a tile scan (``SetStageUseMode``, ``ScanMode``...)
  are taken and remembered.

A value a limit of the stand-in refuses (zoom above ``ZoomMax``) is
answered as LAS X answers it, ``Error="1"`` with a reason, and nothing
changes. Every element sent is kept in ``sent``, in order.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    state_tree,
    transfer,
)

SCANNING = ("Confocal", "ScanningParameter")
HARDWARE = ("Confocal", "HWParameter")

#: The hardware info the stand-in answers, as LAS X's JSON names it.
HARDWARE_INFO = {
    "SerialNumber": "STELLARIS SIMULATOR",
    "SystemType": "SIMULATOR",
    "Microscope": {
        "name": "DM Manual-6",
        "objectives": [
            {"slotIndex": 0, "objectiveNumber": 506511, "name": "HC PL APO  CS    10x/0.40 DRY ", "magnification": 10.0},
            {"slotIndex": 1, "objectiveNumber": 506513, "name": "HC PL APO CS  20.0x0.70 UV", "magnification": 20.0},
        ],
    },
}


#: The tau modes a detector offers, by place, as LCS lists them in a setting (simulator, 2026-11-02).
TAU_LIST = (0, 2, 3, 5, 6, 10)


class PlainNavigator:
    """LAS X with its plain Navigator shown: no jobs, a stage, two z drives, the current settings."""

    def __init__(self, *, x_um=63500.0, y_um=41500.0, z_galvo_um=0.0, z_wide_um=100.0, scan_s=0.15):
        self.x_um, self.y_um = x_um, y_um
        self.z_um = {1: z_galvo_um, 2: z_wide_um}
        self.sent: list[str] = []
        self.answers: dict[str, str] = {}  # a command name -> the answer to give instead
        self.refuse_all: str | None = None  # a reason: every element is refused with it
        self.scanning_until = 0.0
        self.scan_s = scan_s  # how long a scan lasts
        self.captures = 0
        self.loaded: list[str] = []  # the setting files loaded
        self.pan = [0.0, 0.0]  # the pan, as LAS X saves it in the master setting
        self.keeps_pan = False  # True: the smallest zoom does not pull the pan to the centre
        self.nodes = StandInNodes(
            dict,
            stage=lambda: (self.x_um * 1e-6, self.y_um * 1e-6),
            zdrives=lambda: {1: self.z_um[1] * 1e-6, 2: self.z_um[2] * 1e-6},
        )
        self.nodes.live[("Acquire", "AcquisitionState")] = self.scan_state
        for side in ("ImageSizeX", "ImageSizeY"):  # the field shown shrinks with the zoom, 1162.5 um at zoom 1
            self.nodes.live[("Acquire", "AcquireImageSizeInfo", side)] = lambda: 0.0011625 / self.live[(*SCANNING, "Zoom")]
        self.ServerNodes = self.nodes

    # --- what the reads see ----------------------------------------------------------

    def scan_state(self):
        if getattr(self, "live_on", False):
            return 1  # eScanContinuous
        return 2 if time.monotonic() < self.scanning_until else 0  # eScanSingle, or idle

    def take_StartLiveAcquiring(self, e):
        self.live_on = True

    def take_StopAcquiring(self, e):
        self.live_on = False

    @property
    def live(self):
        return self.nodes.live

    def setting(self, path):
        return self.live[path]

    # --- what the hardware server answers ------------------------------------------------

    def SendCommand2(self, xml, _):
        self.sent.append(xml)
        if xml == '<Scanner Command="GetConfocalHardwareInfoAsJson"/>':
            return f'<Result Error="">{json.dumps(HARDWARE_INFO)}</Result>'
        element = ET.fromstring(xml)
        command = element.get("Command")
        if self.refuse_all:
            return f'<Result Error="1">{self.refuse_all}</Result>'
        if command in self.answers:
            return self.answers[command]
        handler = getattr(self, f"take_{command}", None)
        if handler is None:
            return '<Result Error="0"/>'
        return handler(element) or '<Result Error="0"/>'

    def take_SetZoom(self, e):
        zoom = float(e.get("Zoom"))
        if e.get("BlockId") != "0":
            return '<Result Error="1">invalid block identifier</Result>'
        if not self.live[(*SCANNING, "ZoomMin")] <= zoom <= self.live[(*SCANNING, "ZoomMax")]:
            return f'<Result Error="1">Zoom {zoom:g} is out of range</Result>'
        self.live[(*SCANNING, "Zoom")] = zoom
        if zoom <= self.live[(*SCANNING, "ZoomMin")] and not self.keeps_pan:
            self.pan = [0.0, 0.0]  # the simulator pulled the pan to the centre at the smallest zoom

    def take_SetScanSpeed(self, e):
        self.live[(*SCANNING, "Speed")] = int(float(e.get("Speed")))

    def take_SetConfocalScanFieldRotation(self, e):
        self.live[(*HARDWARE, "ScanRotationAngle")] = float(e.get("Rotation"))

    def take_SetLineAverage(self, e):
        self.live[(*SCANNING, "LineAverage")] = int(float(e.get("LineAverage")))

    def take_SetPinholeAU(self, e):
        value = float(e.get("PinholeAU"))
        top = self.live[(*HARDWARE, "PinholeAUMax")]
        if value > top:
            self.live[(*HARDWARE, "PinholeAU")] = top
            return f'<Result Error="2">Pinhole set to {top:.3f}</Result>'
        self.live[(*HARDWARE, "PinholeAU")] = value

    def take_SetZPosition(self, e):
        drive = int(e.get("ZUseMode"))
        step_um = float(e.get("PosZ")) * 1e6
        if e.get("RelativeCoordinates") == "1":
            self.z_um[drive] += step_um
        else:
            self.z_um[drive] = step_um

    def take_SetStagePositionInHwOnly(self, e):
        x, y = float(e.get("StagePosX")) * 1e6, float(e.get("StagePosY")) * 1e6
        if e.get("RelativeCoordinates") == "1":
            self.x_um, self.y_um = self.x_um + x, self.y_um + y
        else:
            self.x_um, self.y_um = x, y

    def take_StageMoveToCoordinates(self, e):
        self.x_um, self.y_um = float(e.get("PosX")) * 1e6, float(e.get("PosY")) * 1e6

    def take_SetSlotPosition(self, e):
        slot = int(e.get("SlotPosition"))
        if not 0 <= slot < len(self.nodes.objectives):
            return f'<Result Error="1">slot {slot} is invalid</Result>'
        self.live[("Microscope", "SlotPosition")] = slot

    def take_CaptureSingleFrame(self, e):
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s

    def take_SetStageIrregularTileScanPositions(self, e):
        self.tiles = [float(tile.get("ZPos")) * 1e6 for tile in e.findall("Tile")]

    def take_StartAcquiring(self, e):
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s
        # As LCS did on the simulator (2026-11-01): a tile scan drives the drive the
        # z-stack uses (Acquire/ZPositionMode) to the tiles' ZPos.
        if getattr(self, "tiles", None):
            drive = self.live[("Acquire", "ZPositionMode")]
            self.z_um[drive] = self.tiles[0]
            self.tiles = None

    # The z-stack as LCS answered the window's commands (simulator, 2026-11-01):
    # 2 leaves that end as it is, and the sections follow from range and step.
    def stack_sections(self):
        stack = ("Acquire", "AcquireZStackInfo")
        step = self.live[(*stack, "StepSize")] or 1e-06
        self.live[(*stack, "StepSize")] = step
        size = abs(self.live[(*stack, "EndValue")] - self.live[(*stack, "BeginValue")])
        self.live[(*stack, "SectionsCount")] = int(round(size / step)) + 1

    def take_SetZStackDefinition(self, e):
        stack = ("Acquire", "AcquireZStackInfo")
        if e.get("SetBegin") == "1":
            self.live[(*stack, "BeginValue")] = float(e.get("BeginValue"))
        if e.get("SetEnd") == "1":
            self.live[(*stack, "EndValue")] = float(e.get("EndValue"))
        self.stack_sections()

    def take_SetZStackSize(self, e):
        """The stack's size around its middle, as LCS does (simulator, 2026-11-02)."""
        stack = ("Acquire", "AcquireZStackInfo")
        begin, end = self.live[(*stack, "BeginValue")], self.live[(*stack, "EndValue")]
        middle, half = (begin + end) / 2, float(e.get("ZStackSize")) / 2
        down = begin > end
        self.live[(*stack, "BeginValue")], self.live[(*stack, "EndValue")] = (middle + half, middle - half) if down else (middle - half, middle + half)
        self.stack_sections()

    def take_ReCenterStackAroundCurrentZ(self, e):
        """The stack moved, its size kept, around where its drive stands."""
        stack = ("Acquire", "AcquireZStackInfo")
        begin, end = self.live[(*stack, "BeginValue")], self.live[(*stack, "EndValue")]
        here, half = self.z_um[self.live[("Acquire", "ZPositionMode")]] * 1e-6, abs(end - begin) / 2
        self.live[(*stack, "BeginValue")], self.live[(*stack, "EndValue")] = (here + half, here - half) if begin > end else (here - half, here + half)
        self.stack_sections()

    # Linear Z-compensation, as LCS answered the window's commands (simulator, 2026-11-02).
    ZCOMP = ("Confocal", "ZCompensation")

    def zcomp_values(self):
        return [("AOTF 499", 2.0), ("HyD S 3", 100.0)]

    def take_ActivateZCompensationMode(self, e):
        mode, bit = self.live[(*self.ZCOMP, "CurrentMode")], int(e.get("Mode"))
        mode = mode | bit if e.get("Activate") == "1" else mode & ~bit
        if mode and not self.live[(*self.ZCOMP, "CurrentMode")] and not self.nodes.zcomp_points:
            # Switched on with no points: LCS puts one at each end of the stack.
            stack = ("Acquire", "AcquireZStackInfo")
            size = abs(self.live[(*stack, "EndValue")] - self.live[(*stack, "BeginValue")])
            self.nodes.zcomp_points = [{"z": size, "values": self.zcomp_values()}, {"z": 0.0, "values": self.zcomp_values()}]
        self.live[(*self.ZCOMP, "CurrentMode")] = mode

    def take_AddIntensityCompensationInterpolationPoint(self, e):
        here = self.z_um[self.live[("Acquire", "ZPositionMode")]] * 1e-6 - self.live[(*self.ZCOMP, "ReferencePosition")]
        kept = [p for p in self.nodes.zcomp_points if abs(p["z"] - here) > 1e-9]  # a point at that height is not doubled
        self.nodes.zcomp_points = sorted([*kept, {"z": here, "values": self.zcomp_values()}], key=lambda p: -p["z"])

    def take_RemoveIntensityCompensationInterpolationPointAt(self, e):
        z = float(e.get("ZPosition"))
        self.nodes.zcomp_points = [p for p in self.nodes.zcomp_points if abs(p["z"] - z) > 1e-9]

    def take_RemoveAllIntensityCompensationInterpolationPoints(self, e):
        self.nodes.zcomp_points = []

    def take_ActivateIntensityCompensationPositions(self, e):
        drive = self.live[("Acquire", "ZPositionMode")]
        self.z_um[drive] = (self.live[(*self.ZCOMP, "ReferencePosition")] + float(e.get("ZPosition"))) * 1e6

    def take_ResetAutoFocusToAutoSync(self, e):
        """The Best Focus Setting closed: Auto Sync on, Series Scan Autofocus off (simulator, 2026-11-02)."""
        self.live[("Acquire", "AutoFocusInfo", "IsAutoSyncActive")] = True
        self.live[("Acquire", "AutoFocusInfo", "IsSeriesScanAutofocusActive")] = False

    LAMBDA = ("Confocal", "LambdaDefinition")

    def lambda_steps(self):
        begin, end, step = (self.live[(*self.LAMBDA, k)] for k in ("DetectionBegin", "DetectionEnd", "DetectionStepSize"))
        self.live[(*self.LAMBDA, "DetectionStepCount")] = int((end - begin) // step) + 1 if step else 1

    def take_SetLambdaDetectionBegin(self, e):
        self.live[(*self.LAMBDA, "DetectionBegin")] = float(e.get("Begin"))
        self.lambda_steps()

    def take_SetLambdaDetectionEnd(self, e):
        self.live[(*self.LAMBDA, "DetectionEnd")] = float(e.get("End"))
        self.lambda_steps()

    def take_SetLambdaDetectionStepsize(self, e):
        self.live[(*self.LAMBDA, "DetectionStepSize")] = float(e.get("Stepsize"))
        self.lambda_steps()

    def take_SetLambdaDetectionBandwidth(self, e):
        self.live[(*self.LAMBDA, "DetectionBandWidth")] = float(e.get("Bandwidth"))

    def take_SetStackStepSize(self, e):
        self.live[("Acquire", "AcquireZStackInfo", "StepSize")] = float(e.get("StepSizeInMeter"))
        self.stack_sections()

    def take_SetZUseMode(self, e):
        self.live[("Acquire", "ZPositionMode")] = int(e.get("UseMode"))

    def light_source(self, e):
        route = tuple(int(p.get("BeamPosition")) for p in e.iter("BeamPosition"))
        return next(s for s in self.nodes.light_sources if tuple(s["BeamRoute"]) == route)

    def take_SetLaserLineIntensity(self, e):
        source = self.light_source(e)
        line = next(a for a in source["AOTF"]["AotfLine"] if a["LineIndex"] == int(e.get("LineIndex")))
        # LCS sets a line's power in steps of 1/255 (5 % became 5.098 %).
        line["Intensity"] = round(float(e.get("LineIntensity")) * 255) / 255 * 100

    def take_SetConfocalLaserLinesDefinition(self, e):
        """The lines listed are kept at their wavelength; a new one gets no power, one left out is off, as on the simulator."""
        kept = set()
        for listed in e.iter("LightSource"):
            kept.add((tuple(int(p.get("BeamPosition")) for p in listed.iter("BeamPosition")), int(listed.findtext("LineIndex"))))
            route = tuple(int(p.get("BeamPosition")) for p in listed.iter("BeamPosition"))
            source = next(s for s in self.nodes.light_sources if tuple(s["BeamRoute"]) == route)
            line = next(a for a in source["AOTF"]["AotfLine"] if a["LineIndex"] == int(listed.findtext("LineIndex")))
            line["WaveLength"] = float(listed.findtext("WaveLength"))
        for source in self.nodes.light_sources:
            for line in source["AOTF"]["AotfLine"]:
                if (tuple(source["BeamRoute"]), line["LineIndex"]) not in kept:
                    line["Intensity"] = 0.0
        return "<Result/>"

    def take_SetLightSourceWavelength(self, e):
        source = self.light_source(e)
        line = next(a for a in source["AOTF"]["AotfLine"] if a["LineIndex"] == int(e.get("LineIndex")))
        line["WaveLength"] = float(e.get("Wavelength"))
        return "<Result/>"

    def take_ActivateShutter(self, e):
        self.light_source(e)["Shutter"]["IsShutterActive"] = e.get("Activate") == "1"

    def take_SetFrameAverage(self, e):
        self.live[(*SCANNING, "FrameAverage")] = int(e.get("FrameAverage"))

    def take_SetLineAccu(self, e):
        self.live[(*SCANNING, "LineAccu")] = int(e.get("LineAccu"))

    def take_SetFrameAccu(self, e):
        self.live[(*SCANNING, "FrameAccumulation")] = int(e.get("FrameAccu"))

    def take_SetFormat(self, e):
        if e.get("IsAutoFocusDefinition") == "1":
            af = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialAutofocusSetting")
            self.live[(*af, "FormatX")], self.live[(*af, "FormatY")] = int(e.get("FormatX")), int(e.get("FormatY"))
            return
        x, y = int(e.get("FormatX")), int(e.get("FormatY"))
        self.live[(*SCANNING, "FormatX")], self.live[(*SCANNING, "FormatY")] = x, y
        self.live[(*SCANNING, "CurrentFormat")] = f"{x} x {y}"

    def take_StartAutoFocus(self, e):
        """A focus search on the autofocus's drive; it ends where the focus was found, or, failing, where it began."""
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s
        logged = getattr(self, "autofocus_log", None)
        if logged:
            path, line = logged
            path.write_text(path.read_text(encoding="utf-8") + line, encoding="utf-8")
        else:
            drive = self.live[("Acquire", "AutoFocusInfo", "ZUseMode")]
            self.z_um[drive] += getattr(self, "autofocus_finds_um", 0.0)

    def take_StartMotCorrAutomation(self, e):
        """A scan through the collar positions, leaving the collar where the picture was best."""
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s
        self.live[("Microscope", "CurrentMotCorrPos")] = getattr(self, "smartcorr_finds", 0.0)

    def detector_at(self, e, tag="BeamRoute"):
        route = tuple(int(p.get("BeamPosition")) for p in e.find(tag).iter("BeamPosition"))
        units = [u for s in self.nodes.detection["Spectrophotometer"] for u in s["DetectionUnitList"]["DetectorHWInfo"]]
        return next(u for u in units if tuple(u["BeamRoute"]) == route)

    def take_SetDetectorActive(self, e):
        self.detector_at(e)["IsActive"] = e.get("SetActive") == "1"

    def take_SetDetectorGain(self, e):
        self.detector_at(e)["HighVoltage"] = float(e.get("DetectorGain"))

    def take_SetSpectrophotometerMultiBandRange(self, e):
        unit = self.detector_at(e, "BandBeamRoute1")
        unit["WaveLengthBegin"], unit["WaveLengthEnd"] = float(e.get("BandLeft1")), float(e.get("BandRight1"))

    def take_SetDigitalGatingModeIndex(self, e):
        unit = self.detector_at(e)
        unit["DigitalGatingMode"] = TAU_LIST[int(e.get("GatingModeIndex"))]
        if unit["DigitalGatingMode"] == 6 and not unit.get("TauColours"):
            unit["TauColours"] = ["Cyan", "Magenta"]  # LAS X's first colours for the two channels

    def take_SetDetectorTauSeparationConfig(self, e):
        unit = self.detector_at(e)
        unit.setdefault("TauLifetimes", [1e-09, 3e-09])[int(e.get("TauChannelIndex"))] = float(e.get("SeparationLine1"))

    def take_FindTauSeparationLifeTimes(self, e):
        """LCS's search for the two lifetimes: logged as LCS logs it; found ones replace the channels'."""
        path, success, found = getattr(self, "lifetime_log", (None, 0, None))
        if path is not None:
            line = f"2026-11-02 21:35:57.572 20000012h  INFO 5653 ScanSettingsResponder.cpp 'General message:==> FindLifetimes seq 0 det 2, success {success}'\n"
            path.write_text((path.read_text(encoding="utf-8") if path.is_file() else "") + line, encoding="utf-8")
        if success and found:
            self.detector_at(e)["TauLifetimes"] = list(found)

    def take_SetDetectorTauSeparationLut(self, e):
        self.detector_at(e)["TauColours"][int(e.get("TauChannelIndex"))] = e.get("LutName")

    def take_SetPinhole(self, e):
        """The pinhole set as asked, beyond LCS's own range too, as the simulator did (2026-11-02)."""
        self.live[("Confocal", "HWParameter", "Pinhole")] = float(e.get("PinholeDiameter"))

    def take_SetMotCorrPosition(self, e):
        self.live[("Microscope", "CurrentMotCorrPos")] = float(e.get("MotCorrPosition"))

    def take_SetScannerToResonant(self, e):
        self.live[(*SCANNING, "IsResonantConfocalScanner")] = e.get("Enable") == "1"

    def take_FilterWheel_ApiSetSlot(self, e):
        wheel = next(w for w in self.nodes.filter_wheels["FilterWheel"] if w["FilterWheelType"] == int(e.get("FilterWheelType")))
        wheel["CurrentSlotPosition"] = int(e.get("SlotIndex"))


    # --- a setting saved and loaded as a file, as LAS X's Setting panel does (simulator, 2026-11-01)

    def setting_xml(self, name):
        """One setting as LAS X writes it: its lasers, detectors and bands."""
        lasers = "".join(
            f'<LaserLineSetting LaserLine="{line["WaveLength"]:g}" IntensityDev="{line["Intensity"]:g}" '
            f'LineIndex="{line["LineIndex"]}" SequenceIndex="0"/>'
            for source in self.nodes.light_sources
            for line in source["AOTF"]["AotfLine"]
        )
        units = [u for s in self.nodes.detection["Spectrophotometer"] for u in s["DetectionUnitList"]["DetectorHWInfo"]]
        detectors = "".join(
            f'<Detector Version="2" Name="{u["Name"]}" Channel="{u["BeamRoute"][1]}" IsActive="{int(u["IsActive"])}" '
            f'DyeName="{u["DyeName"]}" Gain="{u["HighVoltage"]:g}" DetectionRangeBegin="320" DetectionRangeEnd="910" '
            f'AcquisitionMode="{u.get("AcquisitionMode", 1)}" DigitalGatingMode="{u.get("DigitalGatingMode", 2)}" '
            f'IsLifetimeContrastActive="{u.get("IsLifetimeContrastActive", 0)}" OperatingType="{u.get("OperatingType", 2)}" '
            f'IsReflectionModeActive="{u.get("IsReflectionModeActive", 0)}"/>'
            for u in units
        )
        line = getattr(self, "reference_line", ("Laser 405", 405, 1))
        reference = f'<DetectionReferenceLine Version="0" LaserName="{line[0]}" LaserWavelength="{line[1]:g}" IsAutomaticAssigned="{line[2]}"/>'
        show = getattr(self, "show_spectra", (0, 1))
        bands = "".join(
            f'<MultiBand Version="3" Channel="{u["BeamRoute"][1]}" LeftWorld="{u["WaveLengthBegin"]:g}" '
            f'RightWorld="{u["WaveLengthEnd"]:g}" TargetWaveLengthBegin="{u["WaveLengthBegin"]:g}" '
            f'TargetWaveLengthEnd="{u["WaveLengthEnd"]:g}"/>'
            for u in units
        )
        pan = f'PanFirstDim="{self.pan[0]!r}" PanSecondDim="{self.pan[1]!r}" ' if name == "S2" else ""
        return (
            f'<ATLConfocalSettingDefinition UserSettingName="{name}" {pan}ScanDirectionX="1" '
            f'IsShowDyeEmissionSpectrum="{show[1]}" IsShowDyeExcitationSpectrum="{show[0]}">'
            f"<LaserArray>{lasers}</LaserArray><DetectorList>{detectors}</DetectorList><Spectro>{bands}</Spectro>"
            f"{reference}</ATLConfocalSettingDefinition>"
        )

    def take_ATL_Block_Save_To_XML(self, e):
        settings = "".join(self.setting_xml(f"Setting {n + 1}") for n in range(getattr(self, "sequential", 1)))
        Path(e.get("FileName")).write_text(
            '<?xml version="1.0"?><Configuration Type="1"><LDM_Block_Sequential BlockName="Job 1">'
            f"<LDM_Block_Sequential_Master>{self.setting_xml('S2')}</LDM_Block_Sequential_Master>"
            f"<LDM_Block_Sequential_List>{settings}"
            f'<Autofocus_Block BlockName="AutoFocus of Job 1">{self.setting_xml("Best Focus Setting")}</Autofocus_Block>'
            "</LDM_Block_Sequential_List></LDM_Block_Sequential></Configuration>",
            encoding="utf-8",
        )
        return "<Result/>"

    def take_ATL_Block_Load_From_XML(self, e):
        """Setting 1's lasers, detectors and bands applied, as LCS applied them on the simulator."""
        self.loaded.append(e.get("FileName"))
        root = ET.parse(e.get("FileName")).getroot()
        setting = next(s for s in root.iter("ATLConfocalSettingDefinition") if s.get("UserSettingName") == "Setting 1")
        for line in setting.iter("LaserLineSetting"):
            for source in self.nodes.light_sources:
                for aotf in source["AOTF"]["AotfLine"]:
                    if aotf["WaveLength"] == float(line.get("LaserLine")):
                        aotf["Intensity"] = round(float(line.get("IntensityDev")) / 100 * 255) / 255 * 100
        units = {u["Name"]: u for s in self.nodes.detection["Spectrophotometer"] for u in s["DetectionUnitList"]["DetectorHWInfo"]}
        by_channel = {str(u["BeamRoute"][1]): u for u in units.values()}
        for det in setting.iter("Detector"):
            unit = units[det.get("Name")]
            unit["IsActive"] = det.get("IsActive") == "1"
            unit["HighVoltage"] = float(det.get("Gain"))
            unit["DyeName"] = det.get("DyeName")
            for field in ("AcquisitionMode", "DigitalGatingMode", "IsLifetimeContrastActive", "OperatingType", "IsReflectionModeActive"):
                unit[field] = int(det.get(field))
        line = setting.find("DetectionReferenceLine")
        if line is not None:
            self.reference_line = (line.get("LaserName"), float(line.get("LaserWavelength")), int(line.get("IsAutomaticAssigned")))
        self.show_spectra = (int(setting.get("IsShowDyeExcitationSpectrum")), int(setting.get("IsShowDyeEmissionSpectrum")))
        for band in setting.iter("MultiBand"):
            unit = by_channel[band.get("Channel")]
            unit["WaveLengthBegin"], unit["WaveLengthEnd"] = float(band.get("LeftWorld")), float(band.get("RightWorld"))
        return "<Result/>"

    def take_ATL_Setting_Create(self, e):
        """A new setting after the others, made the current one, as LCS did on the simulator."""
        current = ("Acquire", "ATL", "CurrentSetting")
        for each in self.nodes.settings:
            if each["SettingID"] == self.live[(*current, "SettingID")]:
                each["Name"], each["IsEnabledForScanning"] = self.live[(*current, "Name")], self.live[(*current, "IsEnabledForScanning")]
        new = {"SettingID": 110 + len(self.nodes.settings), "Name": f"Setting {len(self.nodes.settings) + 1}", "IsEnabledForScanning": True}
        self.nodes.settings.append(new)
        self.live[(*current, "SettingID")], self.live[(*current, "Name")] = new["SettingID"], new["Name"]
        self.live[(*current, "IsEnabledForScanning")] = True
        return '<Result Error="0"/>'

    def take_ATL_Setting_Activate(self, e):
        """The setting made current, as LCS did for the window."""
        current = ("Acquire", "ATL", "CurrentSetting")
        for each in self.nodes.settings:
            if each["SettingID"] == self.live[(*current, "SettingID")]:
                each["Name"], each["IsEnabledForScanning"] = self.live[(*current, "Name")], self.live[(*current, "IsEnabledForScanning")]
        chosen = next(s for s in self.nodes.settings if s["SettingID"] == int(e.get("SettingID")))
        self.live[(*current, "SettingID")], self.live[(*current, "Name")] = chosen["SettingID"], chosen["Name"]
        self.live[(*current, "IsEnabledForScanning")] = chosen["IsEnabledForScanning"]
        return "<Result/>"

    def take_ATL_Setting_Delete(self, e):
        """The setting deleted; the first one becomes the current one."""
        self.nodes.settings = [s for s in self.nodes.settings if s["SettingID"] != int(e.get("SettingID"))]
        first, current = self.nodes.settings[0], ("Acquire", "ATL", "CurrentSetting")
        self.live[(*current, "SettingID")], self.live[(*current, "Name")] = first["SettingID"], first["Name"]
        self.live[(*current, "IsEnabledForScanning")] = first["IsEnabledForScanning"]
        return "<Result/>"

    def take_SetConfocalDefinitionFromDyeAssistant(self, e):
        """The Best Focus Setting's line and its power, as LCS keeps them (only the autofocus's is modelled)."""
        source = e.find(".//LightSource")
        route = ";".join(p.get("BeamPosition") for p in source.iter("BeamPosition"))
        self.nodes.autofocus_lines = [{"beamRoute": route, "lineIndex": int(source.findtext("LineIndex")),
                                 "wavelength": float(source.findtext("WaveLength")),
                                 "percent": round(float(source.findtext("Intensity")) * 100, 6)}]
        return "<Result/>"

    def take_ATL_Setting_EnableDisableForScanning(self, e):
        af = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialAutofocusSetting")
        if int(e.get("SettingID")) == self.live[(*af, "SettingId")]:
            self.live[(*af, "IsEnabledForScanning")] = e.get("EnableForScanning") == "1"
            return "<Result/>"
        if int(e.get("SettingID")) == self.live[("Acquire", "ATL", "CurrentSetting", "SettingID")]:
            self.live[("Acquire", "ATL", "CurrentSetting", "IsEnabledForScanning")] = e.get("EnableForScanning") == "1"
        return "<Result/>"

    def take_BlockLightSinkDetectorForDyeAssistant(self, e):
        route = tuple(int(p.get("BeamPosition")) for p in e.iter("BeamPosition"))
        units = [u for s in self.nodes.detection["Spectrophotometer"] for u in s["DetectionUnitList"]["DetectorHWInfo"]]
        next(u for u in units if tuple(u["BeamRoute"]) == route)["IsBlockedForDyeAssistant"] = e.get("BlockedState") == "1"
        return "<Result/>"

    def take_BlockLightSourceLineForDyeAssistant(self, e):
        return "<Result/>"

    def take_SetLut(self, e):
        channel = [int(p.get("BeamPosition")) for p in e.iter("BeamPosition")][1]
        next(lut for lut in self.nodes.luts if lut["Channel"] == channel)["LUTName"] = e.get("LutName")
        return "<Result/>"

    def take_ActivateAutoFocus(self, e):
        self.live[("Acquire", "AutoFocusInfo", "IsSeriesScanAutofocusActive")] = e.get("Activate") == "1"

    def take_ScanMode(self, e):
        self.live[("Acquire", "ScanDimensionList", "ScanModeName")] = e.get("ScanMode")

    # --- what was sent -------------------------------------------------------------------

    def commands(self) -> list[str]:
        """The ``Command`` of every element sent, in order, the reads left out."""
        found = []
        for xml in self.sent:
            match = re.search(r'Command="([^"]+)"', xml)
            if match and match[1] != "GetConfocalHardwareInfoAsJson":
                found.append(match[1])
        return found


def attach(monkeypatch, navigator: PlainNavigator | None = None) -> PlainNavigator:
    """Stand *navigator* in for LAS X's hardware server for the test, in every thread."""
    navigator = navigator or PlainNavigator()
    monkeypatch.setattr(hardware_server, "attach", lambda: navigator)
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    monkeypatch.setitem(transfer.senders, "current", None)
    return navigator
