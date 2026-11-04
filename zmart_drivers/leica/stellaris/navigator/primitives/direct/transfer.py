"""Sending a change to LAS X's hardware server: the direct route, the only route of this driver.

A change is a short list of XML elements (``writes.py``), the ones LAS X's
own window sends. Each goes to the hardware server with ``SendCommand2``,
from a thread of its own, because the server answers only once a dialog the
element caused is closed; the write loops answer LAS X's dialogs around the
send (``dispatcher/write.py``, handed ``procedures/answer_dialogs.py``).

LAS X answers ``<Result Error="0"/>`` accepted, ``Error="1"`` refused with
its reason inside, ``Error="2"`` accepted with the value adjusted, and a
bare ``<Result/>`` for the ATL commands. The answer to the last element
sent goes into the client's ``reply``, where the profile's ``check_reply``
reads it. The elements of one change are sent in order and stop at the
first LAS X refuses, so a tile scan whose preparation was refused is never
started.

When the hardware server cannot be reached nothing is sent and the route
says so (:class:`TransportUnavailable`). Once an element has gone out it is
never handed on or sent again by the route: LAS X may already have carried
it out; sending again is the write loops' decision, under the change's own
rules (a capture is never sent again).

Learned from LAS X's own command log; nothing here is guessed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from ...dispatcher.write import TransportUnavailable
from . import client as hardware_server
from .client import LASX_LOG, HardwareServerUnavailable

log = logging.getLogger(__name__)


class DirectTransport:
    """The direct route, as the write loops ask for a transport."""

    #: Named for the result's ``route``; there is one route in this driver.
    carried_by = "direct"

    def fill(self, request, setup_fn):
        """Write the change's elements into the request, afresh before every send."""
        setup_fn(request)

    def clear_reply(self, client):
        """Empty the reply, so that what is read after the send is LAS X's new answer."""
        client.reply.clear()

    def send(self, client, request, label, *, asynchronous):
        """Send the change's elements one after the other; return ``(arrived, notes)`` like any transport.

        Raises :class:`TransportUnavailable` while nothing has been sent.
        LAS X's refusal is not an error here: it goes into the reply, and
        the elements after it are not sent.
        """
        if not request.elements:
            raise TransportUnavailable(f"{request.name} has no element to send")
        try:
            hardware_server.connected_server()
        except HardwareServerUnavailable as why:
            raise TransportUnavailable(str(why)) from why
        refusal = outside_the_limits(client, request.elements)
        if refusal is not None:
            client.reply.update(accepted=False, text=refusal, answer=f'<Result Error="1">{refusal}</Result>',
                                element=request.elements[0])
            return True, [f"{label} | {refusal}"]
        notes = []
        for number, xml in enumerate(request.elements, start=1):
            answer = send(xml)
            client.reply.update(
                accepted=accepted(answer),
                text=answer_text(answer),
                answer=answer,
                element=xml,
            )
            if not client.reply["accepted"]:
                if number < len(request.elements):
                    notes.append(
                        f"{label} | LAS X refused element {number} of {len(request.elements)}; "
                        f"the rest were not sent"
                    )
                break
        return True, notes

    def await_reply(self, client):
        """LAS X's answer came with the send, so there is nothing more to wait for."""
        return True

    def dialog_warning_since(self, command_started_at, description):
        """A dialog LAS X's window opened since the change began, by its log; None when none did.

        Diagnostic only, asked when a change failed or stayed unconfirmed: a
        log that cannot be read must never add another way for a change to
        fail.
        """
        try:
            shown = last_dialog_shown()
        except Exception:
            log.debug("the dialog diagnostic could not read LAS X's log", exc_info=True)
            return None
        if shown is None or shown[0] < command_started_at:
            return None
        return f"{description} | LAS X dialog appears open: {shown[1]}"


DIRECT = DirectTransport()


# =============================================================================
# One element sent, and LAS X's answer
# =============================================================================


class Sender:
    """A thread with its own connection to the hardware server, sending one element at a time.

    The hardware server answers a command only once a dialog the command
    caused is closed, so the command is sent from here and waited for by
    :func:`send`, which can stop waiting.
    """

    def __init__(self):
        self.waiting = queue.Queue()
        threading.Thread(target=self.run, name="lasx-direct-sender", daemon=True).start()

    def run(self):
        while True:
            xml, outcome, done = self.waiting.get()
            try:
                tree = STATE_TREE.match(xml)
                if tree is not None:
                    outcome["answer"] = write_into_state_tree(tree[1], tree[2], tree[3])
                else:
                    outcome["answer"] = outside_lcs_range(xml) or str(hardware_server.connected_server().SendCommand2(xml, ""))
            except Exception as why:  # noqa: BLE001 -- handed to the caller as it is
                hardware_server.forget_connection()
                outcome["error"] = why
            done.set()


senders = {"current": None}
senders_lock = threading.Lock()

#: Every command the driver may send to LCS, by name. LCS takes many more,
#: calibration and service commands among them; the transfer refuses each
#: one that is not here, before anything reaches LCS. A new change adds its
#: commands here, in the same step as its builder in ``writes.py``.
ALLOWED_COMMANDS = frozenset(
    {
        "SetZoom",
        "SetScanSpeed",
        "SetConfocalScanFieldRotation",
        "SetLineAverage",
        "SetPinholeAU",
        "SetStagePositionInHwOnly",
        "SetZPosition",
        "SetSlotPosition",
        "StopAcquiring",
        "StartLiveAcquiring",
        "CaptureSingleFrame",
        "StartAcquiring",
        "SetStageUseMode",
        "SetStageIrregularTileScanPositions",
        "ATL_SuppressAtlasAttachment",
        "ScanMode",
        "ActivateAutoFocus",
        "ActivateExtendedAutoFocusInMarkAndFind",
        "StartNavigatorAutoFocus",
        "SaveBleachPoints",
        "DisableManualOperation",
        "StageMoveToCoordinates",
        "SetZUseMode",
        "SetZStackDefinition",
        "SetZStackSize",
        "SetOptimalStackStepSize",
        "ReCenterStackAroundCurrentZ",
        "BeforeBeginOrEndOfStackIsSet",
        "SetStackStepSize",
        "SetLaserLineIntensity",
        "SetPinhole",
        "SetDetectorActive",
        "SetDetectorGain",
        "SetSpectrophotometerMultiBandRange",
        "SetDigitalGatingModeIndex",
        "SetDetectorTauSeparationLut",
        "SetDetectorTauSeparationConfig",
        "FindTauSeparationLifeTimes",
        "SetDigitalGateConfig",
        "SetDetectorTauScanCount",
        "SetTauInteractionDonorLifetime",
        "AddDetectorTauSeparationChannel",
        "RemoveDetectorTauSeparationChannel",
        "SetDetectorTauScanRange",
        "AddDigitalGate",
        "RemoveDigitalGate",
        "AddDetectorImageChannel",
        "RemoveDetectorImageChannel",
        "ActivateZCompensationMode",
        "ResetAutoFocusToAutoSync",
        "StartFindCoverSlipReflection",
        "SetLambdaDetectionBegin",
        "SetLambdaExcitationBegin",
        "SetLambdaExcitationEnd",
        "SetLambdaExcitationStepsize",
        "SetLambdaAutomaticSPUnitMovement",
        "SetLambdaDetectionBandwidthMode",
        "SetLambdaMinGap",
        "SetLambdaGeometry",
        "SetLambdaUseMaxGap",
        "SetLambdaMaxGap",
        "SetLambdaDetectionEnd",
        "SetLambdaDetectionStepsize",
        "SetLambdaDetectionBandwidth",
        "AddIntensityCompensationInterpolationPoint",
        "RemoveIntensityCompensationInterpolationPointAt",
        "RemoveAllIntensityCompensationInterpolationPoints",
        "ActivateIntensityCompensationPositions",
        "SetLightSourceWavelength",
        "SetConfocalLaserLinesDefinition",
        "ActivateShutter",
        "SetFrameAverage",
        "SetLineAccu",
        "SetFrameAccu",
        "SetFormat",
        "SetScannerToResonant",
        "FilterWheel_ApiSetSlot",
        "ATL_Block_Save_To_XML",
        "ATL_Block_Load_From_XML",
        "SetLut",
        "SetMotCorrPosition",
        "StartMotCorrAutomation",
        "StartAutoFocus",
        "SetConfocalDefinitionFromDyeAssistant",
        "ATL_Setting_EnableDisableForScanning",
        "ATL_Setting_Create",
        "ATL_Setting_Delete",
        "ATL_Setting_Activate",
        "BlockLightSinkDetectorForDyeAssistant",
        "LockSetting",
        "ResetSystem",
        "SetLaserPowerState",
        "ResetImageCompass",
        "ResetConfocalSetting",
        "ATL_ActivateHardwareConnectedMode",
        "BlockLightSourceLineForDyeAssistant",
    }
)

@dataclass(frozen=True)
class TreeWrite:
    """One value the driver may write into LCS's state tree, and the values it may take.

    ``kind`` is ``int``, ``float``, ``bool`` (written as 0 or 1) or ``str`` (plain text, see :data:`PLAIN_TEXT`). A value is one of ``choices``, or lies
    within ``within`` (a fixed range; with ``choices`` too,
    either will do), or lies between the two nodes ``between`` names beside
    it: LCS's own minimum and maximum, read at the moment of the write; or
    it is one of the values in the node ``listed`` names beside it, LCS's
    own list, read at the write. ``of_objective``: the node belongs to the
    objective in the light path, found at the write. ``numbered``: the names
    in *path* that repeat; the element's ``At`` gives which of each, in order.
    ``unsigned``: written as LCS's unsigned number; these nodes ignore a plain one.
    """

    path: tuple[str, ...]
    kind: type = int
    choices: frozenset = frozenset()
    within: tuple[float, float] | None = None
    between: tuple[str, str] | None = None
    listed: str | None = None
    of_objective: bool = False
    numbered: tuple[str, ...] = ()
    unsigned: bool = False


HW_PARAMETER = ("Confocal", "HWParameter")
#: The autofocus's own setting, the Best Focus Setting, in LCS's state tree.
AUTOFOCUS_SETTING = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialAutofocusSetting")
BLEACH_POINT = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialBleachPointParameter")

#: The values the driver may write straight into LCS's state tree, by name.
#: LAS X's window changes these with no command LCS logs, and a loaded setting
#: does not change them; every other node is refused. None of them moves the
#: stage or the objective or makes light (simulator, 2026-11-01).
#:
#: LCS does not keep to its own range when a value is written here: a pinhole
#: of 2000 micrometres (its range is 100 to 1000) raised an error and still
#: opened the pinhole to its maximum, and an emission wavelength of 2000 nm
#: was kept as 800. So every value is checked here first, and one outside
#: the range is refused before anything is written.
STATE_TREE_WRITES = {
    # Bidirectional X: 1 one way, 2 both ways; the frame rate follows it.
    "ScanDirectionX": TreeWrite(("Confocal", "ScanningParameter", "ScanDirectionX"), choices=frozenset({1, 2})),
    # Phase X, which lines up the two directions of a bidirectional scan.
    "PhaseX": TreeWrite((*HW_PARAMETER, "PhaseX"), float, between=("PhaseXMin", "PhaseXMax")),
    # The unit LAS X shows the pinhole in: 1 micrometres, 2 Airy units.
    "PinholeUnit": TreeWrite((*HW_PARAMETER, "PinholeUnit"), choices=frozenset({1, 2})),
    # The emission wavelength the Airy unit is worked out for, in whole nm.
    # LCS gives no minimum or maximum, but keeps it within 380 to 800 nm.
    "EmissionWaveLengthForAiryAnddzCalculation": TreeWrite(
        (*HW_PARAMETER, "EmissionWaveLengthForAiryAnddzCalculation"), within=(380, 800)
    ),
    # The bit depth, one of LCS's list (8, 12, 16 on the simulator); LCS
    # ignored 10, which is not on it.
    "Resolution": TreeWrite(("Confocal", "ScanningParameter", "Resolution"), listed="ResolutionList"),
    # The z-stack's step mode: 2 is System Optimized, where LCS takes its own
    # optimal step (and may stretch the end to fit whole steps), 1 keeps the
    # step size. 0, the number of steps, is not written.
    # The sequential mode: settings taken in turn between lines (0), frames
    # (1) or stacks (2). The window writes it here, and LCS rearranges the
    # detectors' channels itself, as for the window (simulator, 2026-11-01).
    "SequentialMode": TreeWrite(("Confocal", "ScanningParameter", "SequentialMode"), choices=frozenset({0, 1, 2})),
    "StackCalculationMode": TreeWrite(("Acquire", "AcquireZStackInfo", "StackCalculationMode"), choices=frozenset({0, 1, 2})),
    # SmartCORR's range: the collar positions it scans through, in percent,
    # and how many. The window writes them here (simulator, 2026-11-01); LCS
    # gives them no range, the collar's is 0 to 100 percent.
    "AutomationBegin": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationBegin"), float, within=(0, 100)),
    "AutomationEnd": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationEnd"), float, within=(0, 100)),
    "AutomationSteps": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationSteps"), within=(2, 100)),
    # The collar position stored for the objective in the light path, which
    # GoTo goes to: the window's Set writes the collar's position here, its
    # Reset -1, none (simulator, 2026-11-01). Only this objective's is written.
    # The current setting's name, as the window writes it when the name is
    # edited; LCS copies it to the setting's other entries (simulator, 2026-11-01).
    "Name": TreeWrite(("Acquire", "ATL", "CurrentSetting", "Name"), str),
    # The autofocus's settings, as its Best Focus Scan Options write them
    # (simulator, 2026-11-02): the method (1 Contrast 1, 2 Intensity, 3 Noise,
    # 4 Contrast 2, 5 Reflection), the precision (1 speed to 5 accuracy) or a
    # fixed number of steps (odd, 3 to 1001, as the window keeps it), the
    # capture range in whole micrometres (the window takes 1 to 5000; LCS keeps
    # a whole number and ignored 40.0; the search's ends are checked against
    # the z limits when it runs), and the drive.
    "AFAnalyseType": TreeWrite(("Acquire", "AutoFocusInfo", "AFAnalyseType"), choices=frozenset({1, 2, 3, 4, 5})),
    "Precision": TreeWrite(("Acquire", "AutoFocusInfo", "Precision"), choices=frozenset({1, 2, 3, 4, 5})),
    "UseFixSliceCount": TreeWrite(("Acquire", "AutoFocusInfo", "UseFixSliceCount"), bool),
    "FixSliceCount": TreeWrite(("Acquire", "AutoFocusInfo", "FixSliceCount"), within=(3, 1001)),
    "FocusRange": TreeWrite(("Acquire", "AutoFocusInfo", "FocusRange"), within=(1, 5000)),
    "ZUseMode": TreeWrite(("Acquire", "AutoFocusInfo", "ZUseMode"), choices=frozenset({1, 2})),
    # The Best Focus Setting's own scan: speed and zoom within LCS's own range
    # beside them, read at the write, and the direction (1 one way, 2 both);
    # the window writes these there (simulator, 2026-11-02).
    "AutofocusSpeed": TreeWrite((*AUTOFOCUS_SETTING, "Speed"), between=("SpeedMin", "SpeedMax")),
    "AutofocusZoom": TreeWrite((*AUTOFOCUS_SETTING, "Zoom"), float, between=("ZoomMin", "ZoomMax")),
    "AutofocusScanDirectionX": TreeWrite((*AUTOFOCUS_SETTING, "ScanDirectionX"), choices=frozenset({1, 2})),
    # The power of one of the Best Focus Setting's lines, in percent: the
    # window writes it there with no command (simulator, 2026-11-02). Which
    # light source and line is the element's At; the power goes to the limits.
    "AutofocusLaserPower": TreeWrite(
        (*AUTOFOCUS_SETTING, "LightSourceList_SequentialInfo", "LightSource_SequentialInfo", "UnifiedLaser_SequentialInfo",
         "UnifiedLaserLine_SequentialInfo", "Intensity"),
        float, within=(0, 100), numbered=("LightSource_SequentialInfo", "UnifiedLaserLine_SequentialInfo"),
    ),
    # The power of a line of the bleach point the Bleach Point panel shows, as its Intensity writes it with no command
    # (simulator, 2026-11-03). Which light source and line is the element's At; the power goes to the limits.
    "BleachPointLaserPower": TreeWrite(
        (*BLEACH_POINT, "LightSourceList_SequentialInfo", "LightSource_SequentialInfo", "UnifiedLaser_SequentialInfo",
         "UnifiedLaserLine_SequentialInfo", "Intensity"),
        float, within=(0, 100), numbered=("LightSource_SequentialInfo", "UnifiedLaserLine_SequentialInfo"),
    ),
    # A detector's operating mode in a setting, as the window writes it: the
    # mode's place in the detector's list (Fast Mode, Counting, Analog,
    # Reflection on the simulator, 2026-11-02); LCS sets the mode itself and
    # applies it to the detector. Which setting, photometer and detector is At.
    # A detector's TauContrast in the setting, as the detector panel's switch writes it (simulator, 2026-11-03).
    # A detector's dye in the setting (``Namespace/Name``), as the detector panel's Dye list writes it; LCS takes any
    # text, so the driver checks the name against LAS X's dye library first (simulator, 2026-11-03).
    "DyeName": TreeWrite(
        ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter", "SpectralDetection_SequentialInfo",
         "Spectrophotometer_SequentialInfo", "DetectionUnitList_SequentialInfo", "DetectorHWInfo_SequentialInfo", "DyeName"),
        str, numbered=("SequentialSettingParameter", "Spectrophotometer_SequentialInfo", "DetectorHWInfo_SequentialInfo"),
    ),
    # A detector's excitation line in the setting, the detector panel's list: 0 Auto, then the setting's lines that are on, by place;
    # LCS takes any number, so the driver keeps to the list (simulator, 2026-11-03).
    "TimegateReferenceLineIndex": TreeWrite(
        ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter", "SpectralDetection_SequentialInfo",
         "Spectrophotometer_SequentialInfo", "DetectionUnitList_SequentialInfo", "DetectorHWInfo_SequentialInfo", "TimegateReferenceLineIndex"),
        within=(0, 64), numbered=("SequentialSettingParameter", "Spectrophotometer_SequentialInfo", "DetectorHWInfo_SequentialInfo"),
    ),
    # Whether the setting panel draws the dyes' spectra, as the settings toolbar's buttons write it (simulator, 2026-11-03).
    "ShowExcitationSpectrum": TreeWrite(("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter",
                                         "ShowExcitationSpectrum"), bool, numbered=("SequentialSettingParameter",)),
    "ShowEmissionSpectrum": TreeWrite(("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter",
                                       "ShowEmissionSpectrum"), bool, numbered=("SequentialSettingParameter",)),
    "IsLifetimeContrastActive": TreeWrite(
        ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter", "SpectralDetection_SequentialInfo",
         "Spectrophotometer_SequentialInfo", "DetectionUnitList_SequentialInfo", "DetectorHWInfo_SequentialInfo", "IsLifetimeContrastActive"),
        bool, numbered=("SequentialSettingParameter", "Spectrophotometer_SequentialInfo", "DetectorHWInfo_SequentialInfo"),
    ),
    "OperatingModeIndex": TreeWrite(
        ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter", "SpectralDetection_SequentialInfo",
         "Spectrophotometer_SequentialInfo", "DetectionUnitList_SequentialInfo", "DetectorHWInfo_SequentialInfo", "OperatingModeIndex"),
        within=(0, 7), numbered=("SequentialSettingParameter", "Spectrophotometer_SequentialInfo", "DetectorHWInfo_SequentialInfo"),
    ),
    # The AOBS's lines (white-light lines only): Reflection, and Enhanced Dynamics. The window
    # writes them here with no command, and LCS keeps the two apart (simulator, 2026-11-02).
    # Which line is the element's At.
    "IsReflection": TreeWrite(("Confocal", "AOBSLineList", "AOBSLine", "IsReflection"), bool, numbered=("AOBSLine",)),
    "IsDoubleModeEnabled": TreeWrite(("Confocal", "AOBSLineList", "AOBSLine", "IsDoubleModeEnabled"), bool, numbered=("AOBSLine",)),
    # Fast Live's Scan Settings, for the FOV scanner and the resonant one ("Resonant" at the end of
    # the name). The window writes them here with no command (simulator, 2026-11-02); LCS keeps the
    # format and the zoom to no range of its own, so the range beside each is checked here. The resonant
    # scanner's speed is not among them: LCS holds it at one value (min = max = 12000 Hz) and the window
    # offers no control for it (simulator, 2026-11-03).
    **{
        f"FastLive{name}{suffix}": TreeWrite(("Confocal", parent, node), kind, between=between)
        for suffix, parent in (("", "FastLiveParameters"), ("Resonant", "FastLiveParametersResonant"))
        for name, node, kind, between in (
            ("Format", "ScanFormat", int, ("ScanFormatMin", "ScanFormatMax")),
            ("Frequency", "ScanSpeed", int, ("ScanSpeedMin", "ScanSpeedMax")),
            ("OverviewZoom", "AdjustZoom", bool, None),
            ("Zoom", "Zoom", float, ("ZoomMin", "ZoomMax")),
            ("Bidirectional", "Bidirectional", bool, None),
            ("NoAverage", "NoAccumulationAverage", bool, None),
            ("NoDynamicSignalEnhancement", "NoRollingAverage", bool, None),
            ("SelectedSettingOnly", "SelectedSettingOnly", bool, None),
            ("NoTauStedXtend", "NoTauSTEDDeconvolution", bool, None),
        )
        if f"{name}{suffix}" != "FrequencyResonant"
    },
    # The time series: its interval, its duration (LCS works the frames out from it: writing the
    # frames is ignored), and "Acquire Until Stopped". The window writes them here with no command
    # (simulator, 2026-11-02); LCS keeps the interval at least one frame long.
    "DelayTime": TreeWrite(("Acquire", "AcquireTStackInfo", "DelayTime"), float, within=(0, 1e9)),
    "Duration": TreeWrite(("Acquire", "AcquireTStackInfo", "Duration"), float, within=(0, 1e15)),
    "IsMinimized": TreeWrite(("Acquire", "AcquireTStackInfo", "IsMinimized"), bool),
    # The xt line scan: its lines and lines per page (unsigned; plain ones are ignored), and MAX (simulator, 2026-11-03).
    "Lines": TreeWrite(("Acquire", "AcquireTStackInfo", "Lines"), within=(1, 1_000_000_000), unsigned=True),
    "LinesPerPage": TreeWrite(("Acquire", "AcquireTStackInfo", "LinesPerPage"), within=(1, 1_000_000_000), unsigned=True),
    "IsMaxLinesActive": TreeWrite(("Acquire", "AcquireTStackInfo", "IsMaxLinesActive"), bool),
    # The t panel's Acquire Until Stopped (4) and Frames (3): the mode the window writes; LCS sets the automatic flag
    # itself; only as an unsigned number, a plain one is ignored (simulator, 2026-11-03).
    "CalculationMode": TreeWrite(("Acquire", "AcquireTStackInfo", "CalculationMode"), choices=frozenset({3, 4}), unsigned=True),
    # ROI scan and the bleach mode (the Acquisition Mode panel's ROI and bleach buttons), and the ROI scan's
    # sections of a stack (ROI 3D). The window writes them here with no command (simulator, 2026-11-02);
    # LCS keeps the sections within the stack. The regions themselves are in LAS X's data container.
    "EnableRoiScan": TreeWrite(("Confocal", "ROIScan", "EnableRoiScan"), bool),
    "EnableBleachScan": TreeWrite(("Confocal", "ROIScan", "EnableBleachScan"), bool),
    "Is3DLimitedRoiScanEnabled": TreeWrite(("Confocal", "ROIScan", "Is3DLimitedRoiScanEnabled"), bool),
    "First3DRoiSection": TreeWrite(("Confocal", "ROIScan", "First3DRoiSection"), within=(1, 100000)),
    "Last3DRoiSection": TreeWrite(("Confocal", "ROIScan", "Last3DRoiSection"), within=(1, 100000)),
    # Linear Z-compensation's reference position, in metres: the window writes it here (simulator, 2026-11-02).
    "ReferencePosition": TreeWrite(("Confocal", "ZCompensation", "ReferencePosition"), float, within=(-1, 1)),
    # When the autofocus runs in a time series (its Timelapse tab): 1 the first cycle only, 2 every cycle,
    # 3 every n-th, n in the iterator. The window writes them here (simulator, 2026-11-02).
    "WorkflowTimelapse": TreeWrite(("Acquire", "AutoFocusInfo", "WorkflowTimelapse"), choices=frozenset({1, 2, 3})),
    "WorkflowTimelapseIterator": TreeWrite(("Acquire", "AutoFocusInfo", "WorkflowTimelapseIterator"), within=(1, 1_000_000_000)),
    # When the autofocus runs at stage positions (the Navigator's Autofocus Stage tab), the same way; the tab sets
    # IsFirstPositionOnly beside it (simulator, 2026-11-03).
    # The bleach point the Bleach Point panel shows: its dropdown writes it here, and LCS follows with the point's own
    # setting (simulator, 2026-11-03). The driver checks it against the points the panel lists.
    # The z-stack's Set Focus: True puts the focus plane where the stack's drive stands, False clears it; the plane's
    # position written itself is ignored (simulator, 2026-11-03).
    "IsAFPlanePosition": TreeWrite(("Acquire", "AcquireZStackInfo", "IsAFPlanePosition"), bool),
    # The z-stack panel's Z Flow: the window writes it here with no command; LCS turns frame accumulation, frame
    # averaging and z-compensation unavailable with it on, and back with it off (STELLARIS, 2026-11-04).
    "IsScanHeadControlledZStackActive": TreeWrite(("Confocal", "ScanningParameter", "IsScanHeadControlledZStackActive"), bool),
    "CurrentBleachPoint": TreeWrite(("Confocal", "ROIScan", "CurrentBleachPoint"), within=(0, 10_000)),
    "WorkflowXY": TreeWrite(("Acquire", "AutoFocusInfo", "WorkflowXY"), choices=frozenset({1, 2, 3})),
    "WorkflowXYIterator": TreeWrite(("Acquire", "AutoFocusInfo", "WorkflowXYIterator"), within=(1, 1_000_000_000)),
    "IsFirstPositionOnly": TreeWrite(("Acquire", "AutoFocusInfo", "IsFirstPositionOnly"), bool),
    # Auto Sync off (on is the window's command), and its scan options for the FOV scanner and the resonant one
    # ("Resonant" at the end): each with its own on/off. The window writes them here, taken only while Auto Sync
    # is on; the format and the speed only as unsigned numbers (simulator, 2026-11-02). The resonant scanner's
    # speed and its box are not among them: LCS holds the speed at one value (min = max = 12000 Hz) and the window
    # shows neither (simulator, 2026-11-03).
    "IsAutoSyncActive": TreeWrite(("Acquire", "AutoFocusInfo", "IsAutoSyncActive"), bool),
    **{
        f"AutoSync{name}{suffix}": TreeWrite(("Acquire", parent, node), kind, within=within, between=between, unsigned=unsigned)
        for suffix, parent in (("", "AutoFocusSettingConfocalScanOptions"), ("Resonant", "AutoFocusSettingConfocalScanOptionsResonant"))
        for name, node, kind, within, between, unsigned in (
            ("ChangeFormat", "ChangeFormat", bool, None, None, False),
            ("Format", "WantedPixelSizeX", int, (16, 512), None, True),
            ("ChangeSpeed", "ChangeSpeed", bool, None, None, False),
            ("Speed", "WantedSpeed", int, None, ("SpeedMin", "SpeedMax"), True),
            ("ChangeZoom", "ChangeFieldOfView", bool, None, None, False),
            ("Zoom", "SuggestedZoomReduction", float, None, ("ZoomMin", "ZoomMax"), False),
            ("Bidirectional", "UseBiDir", bool, None, None, False),
            ("NoAverage", "NoAccuAndOrAverage", bool, None, None, False),
        )
        if not (suffix == "Resonant" and name in ("ChangeSpeed", "Speed"))
    },
    # A light source's own output power in a setting (the Laser Overview's slider), percent; LCS sets it on every
    # line of the source when one is written (simulator, 2026-11-02). Which setting and source is the element's At.
    # A tunable laser's Excitation Control in the Laser Overview: 0 Maximum Power, 1 Constant Power (LCS then picks the
    # power curve and the range itself; simulator, 2026-11-03). Which light source is the element's At.
    "CurrentExcitationControlMode": TreeWrite(("Confocal", "LightSourceList", "LightSource", "AOTF", "CurrentExcitationControlMode"),
                                              choices=frozenset({0, 1}), numbered=("LightSource",)),
    "LaserTargetOutputPower": TreeWrite(
        ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialSettingParameter", "LightSourceList_SequentialInfo",
         "LightSource_SequentialInfo", "UnifiedLaser_SequentialInfo", "UnifiedLaserLine_SequentialInfo", "LaserTargetOutputPower"),
        float, within=(0, 100), numbered=("SequentialSettingParameter", "LightSource_SequentialInfo"),
    ),
    # The Reflection autofocus's coverslip offset, in metres; the window writes it here (simulator, 2026-11-02).
    # Kept within the autofocus's largest capture range, 5000 micrometres either way.
    # In an xz scan mode (xzy ...), the y-stack's scan line in metres, as the Y-Position box and the Move to buttons write
    # it, within LCS's range beside it; in the other modes the node is the focus, so the driver writes it only in xz modes
    # (simulator, 2026-11-03).
    "ZPosition": TreeWrite(("Acquire", "ZPosition"), float, between=("ZPositionMin", "ZPositionMax")),
    "ReflectionOffset": TreeWrite(("Acquire", "AutoFocusInfo", "ReflectionOffset"), float, within=(-0.005, 0.005)),
    # The Fluorifier disc: the notch filters chosen with the laser (Auto Select), and its left wheel turned as a
    # polarizer (in degrees, within LCS's range beside it) instead of holding a notch filter (simulator, 2026-11-03).
    # The multi-function port (MFP) chosen with the setting (Autoselect), as the beam path's box writes it (simulator, 2026-11-03).
    # The MFP's position, by its place in LCS's list, as the window's MFP list writes it with Autoselect off.
    "CurrentMultiFunctionPortPos": TreeWrite(("Confocal", "HWParameter", "CurrentMultiFunctionPortPos"), within=(0, 32)),
    "IsMultiFunctionPortAutoSelectionEnabled": TreeWrite(("Confocal", "HWParameter", "IsMultiFunctionPortAutoSelectionEnabled"), bool),
    "IsNotchFilterAutoSelectionEnabled": TreeWrite(("Confocal", "HWParameter", "IsNotchFilterAutoSelectionEnabled"), bool),
    "IsSpectrumTurnMode": TreeWrite(("Confocal", "HWParameter", "FilterWheelList", "PolarizationFW", "IsSpectrumTurnMode"), bool),
    "FilterSpectrumPos": TreeWrite(("Confocal", "HWParameter", "FilterWheelList", "PolarizationFW", "FilterSpectrumPos"), float,
                                   between=("FilterSpectrumPosMin", "FilterSpectrumPosMax")),
    # The Λ scan's "Lambda After Stage": the window writes it here (simulator, 2026-11-03).
    "IsExcitationLambdaAfterStageDim": TreeWrite(("Confocal", "LambdaDefinition", "IsExcitationLambdaAfterStageDim"), bool),
    "MotCorrRefPos": TreeWrite(
        ("Microscope", "ObjectiveList", "ObjectiveInfo", "MotCorrRefPos"), float,
        choices=frozenset({-1.0}), within=(0, 100), of_objective=True,
    ),
}

#: Text the driver writes into the state tree: 1 to 64 characters, letters, digits,
#: spaces and ``_ - . , ( ) / + #``; nothing that could be read as markup.
PLAIN_TEXT = re.compile(r"^[\w ._,()/+#:-]{1,64}$")

@dataclass(frozen=True)
class CheckedCommand:
    """A command LCS does not keep to its own range: its value (``attribute``, in the node's unit) must lie
    between the two nodes ``between`` names beside ``path``, read just before it is sent."""

    attribute: str
    path: tuple[str, ...]
    between: tuple[str, str]


#: The commands whose value the driver checks against LCS's own range before sending them.
#: ``SetPinhole`` with 2000 micrometres (LCS's range 100 to 1000) raised the motor's error and
#: still set the pinhole to 2000 (simulator, 2026-11-02).
CHECKED_COMMANDS = {
    "SetPinhole": CheckedCommand("PinholeDiameter", (*HW_PARAMETER, "Pinhole"), ("PinholeMin", "PinholeMax")),
}


def outside_lcs_range(xml: str) -> str | None:
    """LCS's refusal, as its answer, when *xml* is a checked command whose value lies outside LCS's own range; None otherwise."""
    from . import state_tree

    found = COMMAND.match(xml or "")
    checked = CHECKED_COMMANDS.get(found[1]) if found else None
    if checked is None:
        return None
    given = re.search(rf'\s{checked.attribute}="([^"]+)"', xml)
    if given is None:
        return f'<Result Error="1">{found[1]} without {checked.attribute}; not sent</Result>'
    value = float(given[1])
    nodes = state_tree.server_nodes()
    low, high = (state_tree.node_value(nodes, state_tree.node_at(nodes, (*checked.path[:-1], bound))) for bound in checked.between)
    room = 1e-9 * max(abs(low), abs(high))
    if not low - room <= value <= high + room:
        return f'<Result Error="1">{found[1]} {checked.attribute} {given[1]} is outside the range of LAS X, {low} to {high}; not sent</Result>'
    return None


#: A write into the state tree, as the writes build it: ``<StateTree Command="name" Value="n"/>``.
STATE_TREE = re.compile(r'^\s*<StateTree\s+Command="([^"]+)"\s+Value="([^"]+)"(?:\s+At="([0-9,]+)")?\s*/>\s*$')

#: An element's command, wherever it stands among the attributes (LAS X's window
#: writes ``FileName`` before ``Command`` in its setting commands).
COMMAND = re.compile(r'^\s*<\w+\b[^>]*?\sCommand="([^"]+)"')


class NotAllowed(RuntimeError):
    """An element whose command is not in :data:`ALLOWED_COMMANDS`; it was not sent."""


def tree_value(name: str, text: str):
    """The value *text* to write into the node *name*, when the list allows it; :class:`NotAllowed` otherwise.

    LCS's own range, for a value that has one, is checked at the write
    (:func:`write_into_state_tree`).
    """
    if name not in STATE_TREE_WRITES:
        raise NotAllowed(f"not written: {name} is not a value this driver writes into the state tree")
    entry = STATE_TREE_WRITES[name]
    if entry.kind is bool:
        if text not in ("0", "1"):
            raise NotAllowed(f"not written: {name} takes 0 or 1, not {text}")
        return text == "1"
    if entry.kind is str:
        if not PLAIN_TEXT.match(text):
            raise NotAllowed(f"not written: {name} takes plain text of 1 to 64 characters, not {text}")
        return text
    try:
        value = entry.kind(text)
    except ValueError:
        raise NotAllowed(f"not written: {name} takes a {entry.kind.__name__}, not {text}") from None
    if entry.kind is float and not math.isfinite(value):
        raise NotAllowed(f"not written: {name} takes a finite number, not {text}")
    chosen = value in entry.choices
    within = entry.within is not None and entry.within[0] <= value <= entry.within[1]
    if entry.choices and entry.within is not None and not (chosen or within):
        raise NotAllowed(f"not written: {name} takes {sorted(entry.choices)} or {entry.within[0]} to {entry.within[1]}, not {text}")
    if entry.choices and entry.within is None and not chosen:
        raise NotAllowed(f"not written: {name} takes {sorted(entry.choices)}, not {text}")
    if entry.within is not None and not entry.choices and not within:
        raise NotAllowed(f"not written: {name} takes {entry.within[0]} to {entry.within[1]}, not {text}")
    return value


def tree_places(name: str, at: str | None) -> dict:
    """Which of each repeated node the value *name* goes into, from the element's ``At``; :class:`NotAllowed` when it does not fit."""
    numbered = STATE_TREE_WRITES[name].numbered
    if not numbered and at is None:
        return {}
    given = [int(part) for part in at.split(",") if part] if at else []
    if len(given) != len(numbered):
        raise NotAllowed(f"not written: {name} takes a place for each of {list(numbered)} in At, not {at!r}")
    return dict(zip(numbered, given))


def outside_the_limits(client, elements) -> str | None:
    """Why this microscope's limits file does not allow a state-tree value among *elements*; None when it allows them all.

    Each value is shown to its own entry with the exact value it would be
    written as, before any element is sent, so a refusal leaves everything as
    it was. A value the driver's own list refuses is left to that refusal.
    """
    from ...dispatcher import limits as gate

    state = gate.state_of(client)
    for xml in elements:
        tree = STATE_TREE.match(xml or "")
        if tree is None:
            continue
        try:
            value = tree_value(tree[1], tree[2])
        except NotAllowed:
            continue
        if state is None or state.limits is None:
            return f"{tree[1]} not written: no limits are installed for this session"
        try:
            state.limits.check("state_tree", {"name": tree[1], "value": value})
        except gate.LimitViolation as why:
            return f"{tree[1]} {tree[2]} is out of range of this microscope's limits file ({why}); nothing was sent"
    return None


#: Where LCS gives LAS X's build (``"LAS X [ BETA ] 4.9.0.30051"`` on the simulator, 2026-11-03).
LASX_VERSION = ("Acquire", "LASAFversion")
#: For each LAS X build, the state-tree writes checked on it, each against its control in the window. On another
#: build, or for a write not listed for this one, nothing is written. A new build starts with none: each write is
#: checked there and added on its own, one at a time.
CHECKED_WRITES = {
    "4.9.0.30051": frozenset(STATE_TREE_WRITES),  # the simulator, every write (2026-11-01 to 2026-11-03)
    # The STELLARIS, each checked against the window (2026-11-04).
    "4.9.2.31016": frozenset({
        "ScanDirectionX", "PhaseX", "Resolution", "PinholeUnit", "SequentialMode",
        "EmissionWaveLengthForAiryAnddzCalculation", "StackCalculationMode",
        "AutofocusSpeed", "AutofocusScanDirectionX", "AutofocusZoom", "AFAnalyseType", "Precision", "UseFixSliceCount",
        "FixSliceCount", "FocusRange", "ZUseMode",
        # Fast Live's and Auto Sync's scan options, both scanners (not Auto Sync's own switch, IsAutoSyncActive).
        *(name for name in STATE_TREE_WRITES if name.startswith(("FastLive", "AutoSync"))),
        # The time series, the xt line scan, and when the autofocus runs in a series and at stage positions.
        "CalculationMode", "DelayTime", "Duration", "IsMinimized", "Lines", "LinesPerPage", "IsMaxLinesActive",
        "WorkflowTimelapse", "WorkflowTimelapseIterator", "WorkflowXY", "WorkflowXYIterator", "IsFirstPositionOnly",
        # The setting's name, the dye spectra shown, ROI scan and the bleach mode, the ROI scan's stack sections.
        "Name", "ShowExcitationSpectrum", "ShowEmissionSpectrum", "EnableRoiScan", "EnableBleachScan",
        "Is3DLimitedRoiScanEnabled", "First3DRoiSection", "Last3DRoiSection",
        # A detector's dye and TauContrast, a white-light line's AOBS Reflection, the laser's output power and Excitation
        # Control, the excitation scan's Lambda After Stage, the coverslip offset. Not IsDoubleModeEnabled: this AOBS has
        # no Enhanced Dynamics.
        "DyeName", "IsLifetimeContrastActive", "IsReflection", "LaserTargetOutputPower", "CurrentExcitationControlMode",
        "IsExcitationLambdaAfterStageDim", "ReflectionOffset",
        # The MFP's Autoselect (no MFP wheel here, so not its position), the notch filters' Auto Select, the polarizer,
        # a detector's operating mode, linear Z-compensation's reference and support point, the z-stack's Set Focus.
        "IsMultiFunctionPortAutoSelectionEnabled", "IsNotchFilterAutoSelectionEnabled", "IsSpectrumTurnMode",
        "FilterSpectrumPos", "OperatingModeIndex", "ReferencePosition", "ZPosition", "IsAFPlanePosition",
        # A detector's excitation line, and the Best Focus Setting's laser power.
        "TimegateReferenceLineIndex", "AutofocusLaserPower",
        # Auto Sync's own switch (off and on; the Best Focus Setting's light stayed), SmartCORR's range (written and read
        # back with the 10x; its run waits for an objective with a collar), and the bleach point's choice and power.
        "IsAutoSyncActive", "AutomationBegin", "AutomationEnd", "AutomationSteps", "CurrentBleachPoint", "BleachPointLaserPower",
        # The collar's reference, with the 40x/1.10 WATER: Set, GoTo and Reset, the collar moved and put back.
        "MotCorrRefPos",
        # Z Flow, on and off, against the window's switch; LCS's own neighbours followed and came back.
        "IsScanHeadControlledZStackActive",
    }),
}

#: LCS's two switches for whether LAS X takes input for its settings now: both on while it is idle; Live turned the
#: first off, and Start turns it off too (simulator, 2026-11-03). A state-tree write is made only with both on.
TAKES_INPUT = (("Acquire", "CanDoChangeOnIdleOrPrepare"), ("Acquire", "ATL", "UIInputForBlockAllowed"))


def write_into_state_tree(name: str, text: str, at: str | None = None) -> str:
    """Write one listed value into LCS's state tree; answer as LAS X answers a command.

    Nothing is written that was not checked on this LAS X build
    (:data:`CHECKED_WRITES`), as where a value sits in the tree can move from
    one build to the next. Nothing is written while LAS X itself takes no input for its settings
    (:data:`TAKES_INPUT`, as its window then greys its controls out). A
    value with a range of LCS's own is first checked against that range,
    read now; outside it, nothing is written. Either way the answer is a
    refusal (``Error="1"``) with the reason inside.
    """
    from . import state_tree

    nodes = state_tree.server_nodes()
    found = str(state_tree.node_value(nodes, state_tree.node_at(nodes, LASX_VERSION)) or "")
    build = re.search(r"\d+\.\d+\.\d+\.\d+", found)
    if not build or name not in CHECKED_WRITES.get(build.group(), ()):
        return (f'<Result Error="1">{name} was not checked on this LAS X build ({found or "none given"}): check it '
                f'against its control in the window, then add it to CHECKED_WRITES; not written</Result>')
    off = [path[-1] for path in TAKES_INPUT if not state_tree.node_value(nodes, state_tree.node_at(nodes, path))]
    if off:
        return (f'<Result Error="1">LAS X takes no input for its settings now ({", ".join(off)} off: '
                f'it is scanning or busy); {name} not written</Result>')
    entry = STATE_TREE_WRITES[name]
    value = tree_value(name, text)
    places = tree_places(name, at)
    if entry.listed is not None:
        nodes = state_tree.server_nodes()
        listed = state_tree.node_value(nodes, state_tree.node_at(nodes, (*entry.path[:-1], entry.listed)))
        if value not in tuple(listed or ()):
            shown = ", ".join(str(item) for item in listed or ())
            return f'<Result Error="1">{name} {text} is not one LAS X offers ({shown}); not written</Result>'
    if entry.between is not None:
        nodes = state_tree.server_nodes()
        low, high = (
            state_tree.node_value(nodes, state_tree.node_at(nodes, (*entry.path[:-1], bound)))
            for bound in entry.between
        )
        # A hair of room, so that a value given in other units (micrometres
        # for metres) and landing on the edge is not refused by rounding.
        room = 1e-9 * max(abs(low), abs(high))
        if not low - room <= value <= high + room:
            return f'<Result Error="1">{name} {text} is outside the range of LAS X, {low} to {high}; not written</Result>'
    state_tree.change_node(entry.path, value, of_objective=entry.of_objective, places=places, unsigned=entry.unsigned)
    return '<Result Error="0"/>'


def allowed(xml: str) -> str:
    """The command *xml* carries, when the driver may send it; :class:`NotAllowed` otherwise."""
    tree = STATE_TREE.match(xml or "")
    if tree is not None:
        tree_value(tree[1], tree[2])
        tree_places(tree[1], tree[3])
        return tree[1]
    found = COMMAND.match(xml or "")
    if found is None:
        raise NotAllowed(f"not sent: no command can be read in {xml[:80]!r}")
    if found[1] not in ALLOWED_COMMANDS:
        raise NotAllowed(f"not sent: {found[1]} is not a command this driver sends")
    return found[1]


def send(xml: str) -> str:
    """Send one element to the hardware server; return LAS X's answer. Never sent twice.

    A server that does not answer within the rule's windows (a dialog the
    element caused waits for the operator) ends the wait with
    ``HardwareServerUnavailable``. The element stays with LAS X, which may
    still apply it once the operator answers; the next element goes through
    a new sender. A command not in :data:`ALLOWED_COMMANDS` raises
    :class:`NotAllowed`, and nothing is sent.
    """
    from . import profile as waiting

    allowed(xml)

    with senders_lock:
        if senders["current"] is None:
            senders["current"] = Sender()
        sender = senders["current"]
    outcome, done = {}, threading.Event()
    sender.waiting.put((xml, outcome, done))
    deadline = time.monotonic() + waiting.WINDOWS * waiting.WINDOW_S
    while not done.wait(waiting.POLL_S):
        if time.monotonic() >= deadline:
            abandon(sender)
            raise hardware_server.HardwareServerUnavailable(
                f"the hardware server did not answer {xml[:120]} within "
                f"{waiting.WINDOWS} windows of {waiting.WINDOW_S}s"
            )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def abandon(sender):
    """Leave *sender* with the element LAS X holds; the next one gets a new sender."""
    with senders_lock:
        if senders["current"] is sender:
            senders["current"] = None


RESULT = re.compile(r"<Result\b([^>]*?)/?>")
ERROR = re.compile(r'\bError="([^"]*)"')
MESSAGE = re.compile(r"<Result\b[^>]*>(.*)</Result>", re.DOTALL)

#: LAS X's answer codes: 0 accepted, 1 refused, 2 accepted with the value
#: adjusted (pinhole 100 airy was set to 2.512 on the simulator). An adjusted
#: value counts as accepted and the difference is left to the read-back. The
#: ATL commands answer a bare ``<Result/>``, with no code: accepted.
ACCEPTED_CODES = ("0", "", "2")


def accepted(answer: str) -> bool:
    """Whether LAS X took the element."""
    result = RESULT.search(answer or "")
    if result is None:
        return False
    code = ERROR.search(result[1])
    return code is None or code[1] in ACCEPTED_CODES


def answer_text(answer: str) -> str:
    """What LAS X wrote inside its answer: the reason for a refusal or an adjustment."""
    match = MESSAGE.search(answer or "")
    return match[1].strip() if match else ""


# =============================================================================
# A dialog in the way, by LAS X's own log
# =============================================================================

#: How a dialog's entry starts in ``LAS X.log``: its time, then what the window showed.
SHOWN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) .*?'MessageBox (?!Result: )(.*)$")
ANSWERED = "'MessageBox Result: "
#: How much of the log's end is read for the last dialog.
TAIL_BYTES = 64 * 1024


def last_dialog_shown() -> tuple[float, str] | None:
    """The last dialog LAS X's window showed and has not had answered: when (``time.time()``) and its text.

    Reads the end of ``LAS X.log``. None when the log is not there, or the
    last dialog in it was answered.
    """
    with LASX_LOG.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("latin-1").splitlines()
    shown = None
    for line in lines:
        found = SHOWN.match(line)
        if found:
            shown = (found[1], found[2].strip(" '"))
        elif ANSWERED in line:
            shown = None
    if shown is None:
        return None
    at = datetime.strptime(shown[0], "%Y-%m-%d %H:%M:%S.%f").timestamp()
    return at, shown[1]
