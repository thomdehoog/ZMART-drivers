"""What each CAM API command becomes at LAS X's hardware server.

Every entry was read from ``lcsCommand.log`` on the LAS X simulator
(2026-10-12): the driver's own command was run through the CAM API, and the
element the CAM API sent on is written here, attribute for attribute and in
the same order, without its ``Origin`` label. An entry reads the fields the
driver's command wrote into the CAM object's model; nothing else.

Job ids come from LAS X's state tree, not from the CAM API (the route's
``job``): every ``BlockId``, and for select_job the sequence's block id and
the job's element. select_job sends ``<ATL Command="ATL_Sequence"
BlockIDOfSequence=… SubCommand="SetCurrentSelectedElementID" ElementID=…
DoNotActivateBlockFromElement="0"/>``, taking both ids as LAS X's own log shows the
CAM API doing.

Acquire (2026-10-14) names the job's block id and the Data Container
experiment the image goes into, ``CurrentDCObjectID``. LAS X's window takes
the experiment from its project tree (``GUILogic.GetSystemTargetDCObjectID``
-> ``ExpTreeSubjectNode``), which is not in the hardware server's state tree;
the acquire hands in what opens one instead (``Write.experiment``, asked
through the route's ``images_experiment_id``): for LAS X the experiment of
the last image LAS X made (the state tree's
``Acquire/ScanUpdate/LastDCImageID``, its parent experiment from the Data
Container), which on the simulator was the id the CAM API sent
(``procedures/clear_cache/data_container.experiment_for_capture``).

A CAM command missing here has no direct translation; the route then reports that nothing was sent, and the CAM API carries it.
Enum values without a seen translation are refused the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class NoTranslation(ValueError):
    """The command, or one of its values, has no verified hardware-server form."""


#: The driver's own integer fallbacks for enums (``actions/set.py``), by field.
ENUM_INTEGERS = {
    "Units": {1: "eMeter", 3: "eMillimeter", 4: "eMicrons"},
    "MoveXyMode": {2: "eMoveXY"},
}
#: The enum fields a translation reads.
ENUM_FIELDS = ("Units", "MoveXyMode", "ZUseMode", "SequentialMode")
METRES_PER_UNIT = {"eMeter": 1.0, "eMillimeter": 1e-3, "eMicrons": 1e-6}
Z_USE_MODE = {"eUseGalvo": 1, "eUseZWide": 2}
SEQUENTIAL_MODE = {"eSequentialLine": 0, "eSequentialFrame": 1, "eSequentialStack": 2}


class Fields:
    """The CAM model's fields, with the enum types they had before the command wrote them.

    The driver writes an enum either as the enum's own value (whose name is
    its text), as a member of the model's enum type (named by looking it up on
    that type), or as one of its own integer fallbacks.
    """

    def __init__(self, model, enum_types):
        self.model = model
        self.enum_types = enum_types

    def __getattr__(self, field):
        return getattr(self.model, field)

    def enum(self, field: str) -> str:
        value = getattr(self.model, field)
        if isinstance(value, bool):
            raise NoTranslation(f"{field} = {value!r} is not an enum")
        if not isinstance(value, int):
            return str(value)
        kind = self.enum_types.get(field)
        names = [
            name
            for name in dir(kind or object)
            if name.startswith("e") and member_int(getattr(kind, name)) == value
        ]
        if len(names) == 1:
            return names[0]
        try:
            return ENUM_INTEGERS[field][value]
        except KeyError:
            raise NoTranslation(f"{field} = {value!r} has no known name") from None


def member_int(member):
    try:
        return int(member)
    except (TypeError, ValueError):
        return None


def pick(table: dict, m: Fields, field: str):
    name = m.enum(field)
    try:
        return table[name]
    except KeyError:
        raise NoTranslation(
            f"{field} = {name} has no hardware-server value seen yet"
        ) from None


def flag(value) -> int:
    return 1 if value else 0


def metres(m: Fields, field: str) -> float:
    return getattr(m, field) * pick(METRES_PER_UNIT, m, "Units")


def move_xy_mode(m: Fields) -> list:
    mode = m.enum("MoveXyMode")
    if mode != "eMoveXY":
        raise NoTranslation(f"MoveXyMode = {mode}: only eMoveXY was seen")
    return [("MoveStageX", 1), ("MoveStageY", 1)]


@dataclass(frozen=True)
class Translation:
    """One CAM command's hardware-server form.

    *attributes* takes the CAM model's :class:`Fields` and the route (for a job's
    ``BlockId``, element and sequence) and
    returns the attributes in order; *command_at* is where ``Command`` stands
    among them (first, unless the CAM API put it later); *beam_route* adds the
    model's ``BeamRoute`` (e.g. ``"40;2"``) as a child element.
    """

    element: str
    command: str
    attributes: Callable[[Any, Any], list]
    beam_route: bool = False
    command_at: int = 0


def per_setting(command: str, field: str, attribute: str) -> Translation:
    """A value of one sequential setting of a job: ``SetMode="2"`` and the setting's index."""
    return Translation(
        "Scanner",
        command,
        lambda m, route: [
            (attribute, getattr(m, field)),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
        ],
    )


TRANSLATIONS: dict[str, Translation] = {
    "PyApiSetZoomByJobName": Translation(
        "Scanner",
        "SetZoom",
        lambda m, route: [
            ("Zoom", m.ZoomValue),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetScanSpeedByJobName": Translation(
        "Scanner",
        "SetScanSpeed",
        lambda m, route: [
            ("Speed", m.ScanSpeed),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetScannerToResonantByJobName": Translation(
        "Scanner",
        "SetScannerToResonant",
        lambda m, route: [
            ("Enable", flag(m.EnableResonant)),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetSequentialModeByJobName": Translation(
        "Scanner",
        "SetConfocalSequentialMode",
        lambda m, route: [
            ("SequentialMode", pick(SEQUENTIAL_MODE, m, "SequentialMode")),
            ("IsApiCommand", 1),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetScanModeByJobName": Translation(
        "Scanner",
        "ScanMode",
        lambda m, route: [
            ("ScanMode", str(m.ScanModeValue)),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetScanFieldRotationByJobName": Translation(
        "Scanner",
        "SetConfocalScanFieldRotation",
        lambda m, route: [
            ("Rotation", m.Rotation),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetImageSizeByJobName": Translation(
        "Scanner",
        "SetFormat",
        lambda m, route: [
            ("FormatX", m.ImageWidth),
            ("FormatY", m.ImageHeight),
            ("BlockId", route.block_id(m.JobName)),
            ("IsAutoFocusDefinition", flag(m.IsAutoFocusDefinition)),
        ],
    ),
    "PyApiSetFrameAccumulationByJobName": per_setting(
        "SetFrameAccu", "FrameAccumulation", "FrameAccu"
    ),
    "PyApiSetFrameAverageByJobName": per_setting(
        "SetFrameAverage", "FrameAverage", "FrameAverage"
    ),
    "PyApiSetLineAccumulationByJobName": per_setting(
        "SetLineAccu", "LineAccumulation", "LineAccu"
    ),
    "PyApiSetLineAverageByJobName": per_setting(
        "SetLineAverage", "LineAverage", "LineAverage"
    ),
    "PyApiSetPinholeAUByJobName": per_setting(
        "SetPinholeAU", "PinholeAiry", "PinholeAU"
    ),
    "PyApiSetDetectorGainByJobName": Translation(
        "Scanner",
        "SetDetectorGain",
        lambda m, route: [
            ("DetectorGain", m.GainValue),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
        ],
        beam_route=True,
    ),
    "PyApiSetLaserIntensityByJobName": Translation(
        "Scanner",
        "SetLaserLineIntensity",
        lambda m, route: [
            ("LineIndex", m.LaserLineIndex),
            ("LineIntensity", m.IntensityValue),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
            ("IsRoiBackground", flag(m.IsRoiBackground)),
        ],
        beam_route=True,
    ),
    "PyApiSetLaserShutterByJobName": Translation(
        "Scanner",
        "ActivateShutter",
        lambda m, route: [
            ("Activate", flag(m.Activate)),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
        ],
        beam_route=True,
    ),
    "PyApiSetFilterWheelSlotByJobName": Translation(
        "Scanner",
        "FilterWheel_ApiSetSlot",
        lambda m, route: [
            ("FilterWheelType", int(m.FilterWheelType)),
            ("SlotIndex", m.SlotIndex),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
        ],
        beam_route=True,
    ),
    "PyApiSetFilterWheelSpectrumPositionByJobName": Translation(
        "Scanner",
        "FilterWheel_SetSpectrumPosition",
        lambda m, route: [
            ("FilterWheelType", int(m.FilterWheelType)),
            ("FilterSpectrumPosition", m.FilterSpectrumPosition),
            ("SetMode", 2),
            ("SingleSettingIndex", m.SettingIndex),
            ("BlockId", route.block_id(m.JobName)),
            ("IsApiCommand", 1),
        ],
        beam_route=True,
    ),
    "PyApiSelectJobByName": Translation(
        "ATL",
        "ATL_Sequence",
        lambda m, route: [
            ("BlockIDOfSequence", route.sequence_id()),
            ("SubCommand", "SetCurrentSelectedElementID"),
            ("ElementID", route.element_id(m.JobName)),
            ("DoNotActivateBlockFromElement", 0),
        ],
    ),
    "PyApiAcquireJob": Translation(
        "ATL",
        "ATL_Block_RunBlock",
        lambda m, route: [
            ("CurrentDCObjectID", route.images_experiment_id()),
            ("Attribute", route.block_id(m.JobName)),
        ],
        command_at=1,
    ),
    "PyApiCommandSetZStackStepSizeByJobName": Translation(
        "Scanner",
        "SetStackStepSize",
        lambda m, route: [
            ("StepSizeInMeter", m.StackStepSize),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetZStackSizeByJobName": Translation(
        "Scanner",
        "SetZStackSize",
        lambda m, route: [
            ("ZStackSize", m.StackSize),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetZStackDefinitionByJobName": Translation(
        "Scanner",
        "SetZStackDefinition",
        lambda m, route: [
            ("SetBegin", int(m.SetBegin)),
            ("SetEnd", int(m.SetEnd)),
            ("BeginValue", m.BeginValue),
            ("EndValue", m.EndValue),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiSetObjectiveSlotByJobName": Translation(
        "Microscope",
        "SetObjectiveSlotIndex",
        lambda m, route: [
            ("SlotIndex", m.ObjectiveSlotIndex),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
    "PyApiMoveHardwareXY": Translation(
        "Scanner",
        "SetStagePositionInHwOnly",
        lambda m, route: [
            ("StagePosX", metres(m, "XPosition")),
            ("StagePosY", metres(m, "YPosition")),
            ("RelativeCoordinates", flag(m.RelativePosition)),
            *move_xy_mode(m),
        ],
    ),
    "PyApiMoveZByJobName": Translation(
        "Scanner",
        "SetZPosition",
        lambda m, route: [
            ("PosZ", metres(m, "ZPosition")),
            ("RelativeCoordinates", flag(m.RelativePosition)),
            ("ZUseMode", pick(Z_USE_MODE, m, "ZUseMode")),
            ("BlockId", route.block_id(m.JobName)),
        ],
    ),
}
