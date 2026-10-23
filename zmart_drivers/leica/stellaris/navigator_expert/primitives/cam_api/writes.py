"""The changes LAS X can be asked for through the CAM API, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits, the LAS X
request that carries it and how that request is filled in, the label in the
log, and what belongs to this change alone: what to wait for before it is sent
(:class:`Expect` the scanner idle), which reading shows that it took effect and
how close counts, whether it is sent without a receipt, which LAS X replies
mean "already so", and whether it may never be sent again. The dispatcher's
``run()`` does the rest. Nothing here calls LAS X or the dispatcher.

The builders: :func:`move_xy`, :func:`move_z`, :func:`set_objective`,
:func:`select_job`, :func:`acquire`, and :func:`change` for the settings of a
job, which are one table (``SETTINGS``):

- ``name``: what a person calls it (``"zoom"``); the limits gate knows it as
  ``set_<name>``;
- ``takes``: its arguments after the job, in order; ``optional`` ones may be
  left out and are then None;
- ``request``: the LAS X request that carries it out, and ``write``: how the
  request is filled in;
- ``value``: the argument that holds the new value; the limits gate is shown
  it, and the readback is compared with it;
- ``read``: where the value stands in the job's settings read back, and
  ``tolerance``: how close it must come (None: exactly);
- ``describe``: the label in the log.

The checks that say how close counts (:func:`within_xy`, :func:`z_within`,
:func:`slot_is`, :func:`job_named`, :func:`setting_shows`, :func:`is_idle`) each take one reading's
value and answer True or False, so each can be tested alone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..direct import profile as policy
from .derived import z_um_from_settings
from .parsing import make_changeable_copy, parse_format

log = logging.getLogger(__name__)


# =============================================================================
# A write, and what it waits for
# =============================================================================


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str  # "xy", "job_settings", "selected_job", "scan_status"
    accept: Callable[[object], bool]  # how close counts
    about: str | None = None  # the job, for a reading about one job
    mode: str | None = None  # read order; None = the reading's own
    fallback: str | None = None


@dataclass(frozen=True)
class Write:
    """One change: its request, its limits, and what belongs to it alone."""

    command: str  # its name in the limits (MUTATING_COMMANDS)
    values: dict  # what the limits gate is shown
    request: str  # e.g. "PyApiMoveHardwareXY"
    fill: Callable  # fill(model): writes the fields, reads LAS X's enums from the model
    description: str
    ready: Expect | None = None  # waited for before every send (scanner idle)
    confirm: Expect | None = None  # read back, one window at a time
    without_receipt: bool = False  # UpdateAsync, no reply checked
    accepted_replies: tuple = ()  # LAS X replies that mean "already so"
    never_resend: bool = False


def is_idle(status) -> bool:
    """The scanner status says idle."""
    return "Idle" in status


def idle() -> Expect:
    """The scanner idle, read from a source that answers on demand.

    The log writes the status only when it changes, so its "idle" can be from
    before; this reading decides whether a change may be sent, so it asks the
    direct reader, or the API.
    """
    return Expect(
        "scan_status",
        is_idle,
        mode=policy.on_demand_mode("scan_status"),
        fallback=policy.on_demand_fallback(),
    )


# =============================================================================
# The stage
# =============================================================================

#: In micrometres, per unit a position can be given in.
UM_PER_UNIT = {"um": 1, "mm": 1000, "m": 1e6}

#: LAS X's names for the units, and their numbers when the enum cannot be read.
UNIT_ENUM = {"um": "eMicrons", "mm": "eMillimeter", "m": "eMeter"}
UNIT_NUMBER = {"um": 4, "mm": 3, "m": 1}

#: The z drives, by the name a move takes, as LAS X and the readers name them.
Z_DRIVE_ENUM = {"galvo": "eUseGalvo", "zwide": "eUseZWide"}
Z_DRIVE_KEY = {"galvo": "z-galvo", "zwide": "z-wide"}

#: How close a move must come, in micrometres, unless the caller says.
XY_TOLERANCE_UM = 20.0
Z_TOLERANCE_UM = 1.0


def unit_enum(model_value, unit):
    """LAS X's enum member for ``unit``, or its number when the enum cannot be read."""
    try:
        return getattr(type(model_value), UNIT_ENUM[unit])
    except Exception:
        log.warning("Units enum resolution failed, using int fallback: %d", UNIT_NUMBER[unit])
        return UNIT_NUMBER[unit]


def within_xy(x_um, y_um, tolerance):
    """A stage position within ``tolerance`` micrometres of ``(x_um, y_um)`` on both axes."""

    def check(position) -> bool:
        try:
            return abs(position["x_um"] - x_um) < tolerance and abs(position["y_um"] - y_um) < tolerance
        except (KeyError, TypeError):
            return False

    return check


def z_within(drive, target_um, tolerance):
    """Job settings read while the z ``drive`` (``"z-galvo"``, ``"z-wide"``) is within ``tolerance`` of the target.

    Where the drive is comes from the direct reader first, where it truly is;
    else from the job's settings, which know they do not refresh for the drive
    carrying the job's z-stack and read that one from LAS X's hardware server
    (``z_um_from_settings``). When neither answers, the check is not met.
    """

    def check(settings) -> bool:
        try:
            z_um = policy.z_um_direct(drive)
            if z_um is None:
                z_um = z_um_from_settings(settings, drive, direct=policy.STATE_READERS.z_mode == "direct")
            return abs(z_um - target_um) < tolerance
        except (KeyError, TypeError, RuntimeError, ValueError):
            return False

    return check


def move_xy(x, y, unit, *, tolerance=None) -> Write:
    """Move the XY stage to an absolute position, in ``unit`` ('um', 'mm' or 'm').

    Sent without waiting for a receipt, so there is no reply to check; the
    readback is the proof. An unconfirmed absolute move is sent again (it is
    the same move) after waiting for idle. ``tolerance`` in micrometres,
    ``XY_TOLERANCE_UM`` when None.
    """
    x_um, y_um = x * UM_PER_UNIT[unit], y * UM_PER_UNIT[unit]
    tolerance = XY_TOLERANCE_UM if tolerance is None else tolerance

    def fill(m):
        try:
            mode_val = type(m.MoveXyMode).eMoveXY
        except Exception:
            mode_val = 2  # eMoveXY=2 (NOT 0 which is eDontMove!)
            log.warning("MoveXyMode enum resolution failed, using int fallback: %d", mode_val)
        m.Units = unit_enum(m.Units, unit)
        m.RelativePosition = False
        m.XPosition = x
        m.YPosition = y
        m.MoveXyMode = mode_val

    return Write(
        command="move_xy",
        values={"x_um": x_um, "y_um": y_um},
        request="PyApiMoveHardwareXY",
        fill=fill,
        description=f"MoveXY -> ({x}, {y}) {unit}",
        ready=idle(),
        confirm=Expect(
            "xy",
            within_xy(x_um, y_um, tolerance),
            mode=policy.on_demand_mode("xy"),
            fallback=policy.on_demand_fallback(),
        ),
        without_receipt=True,
    )


def move_z(job_name, z, unit, z_mode, *, tolerance=None) -> Write:
    """Move a z drive, ``galvo`` or ``zwide``, of ``job_name`` to an absolute position.

    ``tolerance`` in micrometres, ``Z_TOLERANCE_UM`` when None.
    """
    z_um = z * UM_PER_UNIT[unit]
    tolerance = Z_TOLERANCE_UM if tolerance is None else tolerance

    def fill(m):
        try:
            z_use_val = getattr(type(m.ZUseMode), Z_DRIVE_ENUM[z_mode])
        except Exception:
            z_use_val = Z_DRIVE_ENUM[z_mode]
            log.warning("ZUseMode enum resolution failed for '%s'", z_use_val)
        m.Units = unit_enum(m.Units, unit)
        m.JobName = job_name
        m.RelativePosition = False
        m.ZPosition = z
        m.ZUseMode = z_use_val

    return Write(
        command="move_z",
        values={f"z_{'galvo' if z_mode == 'galvo' else 'wide'}_um": z_um},
        request="PyApiMoveZByJobName",
        fill=fill,
        description=f"Z -> {z} {unit} ({z_mode})",
        ready=idle(),
        confirm=Expect(
            "job_settings",
            z_within(Z_DRIVE_KEY[z_mode], z_um, tolerance),
            about=job_name,
        ),
    )


# =============================================================================
# The objective
# =============================================================================


def slot_is(slot):
    """Job settings whose objective is the one in ``slot``."""

    def check(settings) -> bool:
        try:
            return settings["objective"]["slotIndex"] == slot
        except (KeyError, TypeError):
            return False

    return check


def set_objective(job_name, slot, name) -> Write:
    """Change the objective of ``job_name`` to the one in ``slot`` (called ``name``), without moving the stage.

    Four confirmation windows, sent again between them; a slow turret is
    absorbed by the wait for idle before each send.
    """

    def fill(m):
        m.JobName = job_name
        m.ObjectiveSlotIndex = slot

    return Write(
        command="set_objective",
        values={"job_name": job_name, "objective_slot": slot},
        request="PyApiSetObjectiveSlotByJobName",
        fill=fill,
        description=f"Objective -> {name} (slot {slot})",
        ready=idle(),
        confirm=Expect("job_settings", slot_is(slot), about=job_name),
    )


# =============================================================================
# The job, and taking a picture
# =============================================================================


def job_named(job_name):
    """A selected job called ``job_name``."""

    def check(selected) -> bool:
        try:
            return selected["Name"] == job_name
        except (KeyError, TypeError):
            return False

    return check


def select_job(job_name, *, mode=None, fallback=None) -> Write:
    """Select the job ``job_name``, without moving the stage.

    Confirmed by the ``selected_job`` reading in its read order: ``mode``
    first (None: the reading's own, the direct reader, LAS X's live
    selection); when it cannot answer, ``fallback`` (None: the profile's
    ``selected_job_confirm_fallback``, ``hybrid``: the CAM API's job list,
    then the log). Four windows, sent again between them, like every change.
    """

    def fill(m):
        m.JobName = job_name

    return Write(
        command="select_job",
        values={"job_name": job_name},
        request="PyApiSelectJobByName",
        fill=fill,
        description=f"SelectJob '{job_name}'",
        ready=idle(),
        confirm=Expect(
            "selected_job",
            job_named(job_name),
            mode=mode,
            fallback=policy.STATE_READERS.selected_job_confirm_fallback if fallback is None else fallback,
        ),
    )


def acquire(job_name) -> Write:
    """Run the job ``job_name`` once: one picture.

    Never sent again, after an error or otherwise: a second send is a second
    picture. Sent without a receipt, and with nothing read back: whether the
    scan ran is watched after the send (``procedures/changes.py``).
    """

    def fill(m):
        m.JobName = job_name

    return Write(
        command="acquire",
        values={"job_name": job_name},
        request="PyApiAcquireJob",
        fill=fill,
        description=f"Acquire '{job_name}'",
        ready=idle(),
        without_receipt=True,
        never_resend=True,
    )


# =============================================================================
# The settings of a job, one table
# =============================================================================

#: What reading a value out of the job's settings may raise while LAS X has
#: not written it yet (ValueError: settings without the keys every job's
#: settings carry); the check then answers no, and the reading looks again.
NOT_THERE_YET = (KeyError, TypeError, IndexError, StopIteration, ValueError)


@dataclass(frozen=True)
class Setting:
    """Everything that is particular to changing one setting of a job."""

    name: str
    takes: tuple[str, ...]
    request: str
    write: Callable[[Any, dict], None]
    value: str
    read: Callable[[dict, dict], Any]
    describe: str
    tolerance: float | None = None
    optional: tuple[str, ...] = ()
    # Optional steps, used by the few settings that need them.
    gate: Callable[[dict], dict] | None = None
    normalize: Callable[[dict], dict] | None = None
    validate: Callable[[dict], str | None] | None = None
    matches: Callable[[dict, dict, float | None], bool] | None = None
    accepted_replies: tuple[str, ...] = ()

    @property
    def command(self) -> str:
        """The setting's name in the limits: ``set_zoom`` for ``zoom``."""
        return f"set_{self.name}"


# --- small builders, so that each entry below reads as a list of facts ----------------


def fields(**model_fields) -> Callable[[Any, dict], None]:
    """Write these request fields in this order.

    A text names the argument to copy; a function computes the value from the
    arguments.
    """

    def write(model, a):
        for attr, source in model_fields.items():
            setattr(model, attr, source(a) if callable(source) else a[source])

    return write


def at(*path: str) -> Callable[[dict, dict], Any]:
    """Read the value at this path in the job's settings."""

    def read(settings, a):
        for key in path:
            settings = settings[key]
        return settings

    return read


def in_setting(*path: str) -> Callable[[dict, dict], Any]:
    """Read the value at this path in the job's setting ``setting_index``."""

    def read(settings, a):
        return at(*path)(settings["activeSettings"][a["setting_index"]], a)

    return read


def on_route(group: str, *path: str, line=False, wheel=False) -> Callable[[dict, dict], Any]:
    """Read the value at this path on the beam route's entry in the setting's ``group``.

    ``line`` also matches the laser line, ``wheel`` the filter wheel's type.
    """

    def read(settings, a):
        entry = next(
            e
            for e in settings["activeSettings"][a["setting_index"]][group]
            if e["_beamRoute"] == a["beam_route"]
            and (not line or e["_lineIndex"] == a["line_index"])
            and (not wheel or e.get("type") == a["filter_wheel_type"])
        )
        return at(*path)(entry, a)

    return read


def per_setting(name, request, model_field, read_key, *, tolerance=None):
    """A setting that is one number on one entry of the job's setting list."""
    return Setting(
        name=name,
        takes=("setting_index", "value"),
        request=request,
        write=fields(JobName="job_name", SettingIndex="setting_index", **{model_field: "value"}),
        value="value",
        read=in_setting(*read_key),
        describe="Setting[{setting_index}]." + model_field + " -> {value}",
        tolerance=tolerance,
    )


# --- settings that are one number or word on the job ----------------------------------

ZOOM = Setting(
    name="zoom",
    takes=("value",),
    request="PyApiSetZoomByJobName",
    write=fields(JobName="job_name", ZoomValue="value"),
    value="value",
    read=at("zoom", "current"),
    describe="Zoom -> {value}",
    tolerance=0.1,
)

SCAN_SPEED = Setting(
    name="scan_speed",
    takes=("value",),
    request="PyApiSetScanSpeedByJobName",
    write=fields(JobName="job_name", ScanSpeed="value"),
    value="value",
    read=at("scanSpeed", "value"),
    describe="ScanSpeed -> {value}",
)

SCAN_MODE = Setting(
    name="scan_mode",
    takes=("mode",),
    request="PyApiSetScanModeByJobName",
    write=fields(JobName="job_name", ScanModeValue="mode"),
    value="mode",
    read=at("scanMode"),
    describe="ScanMode -> {mode}",
)

SCAN_FIELD_ROTATION = Setting(
    name="scan_field_rotation",
    takes=("angle",),
    request="PyApiSetScanFieldRotationByJobName",
    write=fields(JobName="job_name", Rotation="angle"),
    value="angle",
    read=at("scanFieldRotation", "value"),
    describe="Rotation -> {angle}",
    tolerance=0.5,
)

# --- resonant scanning: LAS X answers "no change" when it is already in that state -----
# That answer is accepted as "already so"; the readback then confirms it.

RESONANT_NO_CHANGE = "desired state does not differ from the current state"

SCAN_RESONANT = Setting(
    name="scan_resonant",
    takes=("enable",),
    request="PyApiSetScannerToResonantByJobName",
    write=fields(JobName="job_name", EnableResonant="enable"),
    value="enable",
    read=at("scanSpeed", "isResonant"),
    describe="Resonant -> {enable}",
    accepted_replies=(RESONANT_NO_CHANGE,),
)

# --- sequential mode: a word from a short list, written as a .NET enum ----------------

SEQUENTIAL_MODES = {
    "Line": "eSequentialLine",
    "Frame": "eSequentialFrame",
    "Stack": "eSequentialStack",
}


def check_sequential_mode(a):
    """The reason this mode cannot be sent, or None when it can."""
    mode = a["mode"]
    if not isinstance(mode, str) or not mode.strip():
        return (
            f"SequentialMode -> {repr(mode)} | Invalid: mode must be a "
            f"non-empty string. Valid: ['Line', 'Frame', 'Stack']"
        )
    if SEQUENTIAL_MODES.get(mode) is None:
        return f"Could not map {repr(mode)} to SequentialMode enum. Valid: {list(SEQUENTIAL_MODES)}"
    return None


def write_sequential_mode(m, a):
    enum_val = SEQUENTIAL_MODES[a["mode"]]
    m.JobName = a["job_name"]
    try:
        current = m.SequentialMode
        m.SequentialMode = getattr(type(current), enum_val, enum_val)
    except (AttributeError, TypeError):
        m.SequentialMode = enum_val


SEQUENTIAL_MODE = Setting(
    name="sequential_mode",
    takes=("mode",),
    request="PyApiSetSequentialModeByJobName",
    write=write_sequential_mode,
    value="mode",
    read=at("sequentialMode"),
    describe="SequentialMode -> {mode}",
    validate=check_sequential_mode,
)

# --- image format: "512 x 512" or a (width, height) pair -------------------------------


def split_format(a):
    """Add the width, the height and the one text LAS X shows ("512 x 512").

    The limits gate is shown that text, the way a limits file lists it, so
    that "512x512" and (512, 512) are checked the same way.
    """
    format_str = a["format_str"]
    w, h = format_str if isinstance(format_str, tuple) else parse_format(format_str)
    return {**a, "w": w, "h": h, "format": f"{w} x {h}"}


IMAGE_FORMAT = Setting(
    name="image_format",
    takes=("format_str",),
    request="PyApiSetImageSizeByJobName",
    write=fields(JobName="job_name", ImageWidth="w", ImageHeight="h", IsAutoFocusDefinition=lambda a: False),
    value="format",
    read=at("format"),
    describe="Format -> {w} x {h}",
    normalize=split_format,
)

# --- z-stack: sizes are given in micrometers and sent in meters -------------------------


def reset_flag(new_value, old_value):
    """LAS X's flag for one end of the stack: 1 = set it, 0 = reset it, 2 = leave it."""
    if new_value is not None:
        return 1
    return 0 if old_value is not None else 2


def write_z_stack_definition(m, a):
    begin, end = a["begin_um"], a["end_um"]
    m.JobName = a["job_name"]
    m.SetBegin = reset_flag(begin, a["old_begin_um"])
    # An explicit None check: 0.0 is a valid z position.
    m.BeginValue = (begin if begin is not None else 0) * 1e-6
    m.SetEnd = reset_flag(end, a["old_end_um"])
    m.EndValue = (end if end is not None else 0) * 1e-6


def quantised_sizes(size, step):
    """The sizes LAS X may snap ``size`` to in "Z-Step Size" mode: the nearest multiples of the step."""
    if not step or step <= 0:
        return []
    direction = -1.0 if size < 0 else 1.0
    return [n * step * direction for n in sorted({max(1, math.floor(abs(size) / step)), max(1, math.ceil(abs(size) / step))})]


def stack_ends_match(stack_settings, a, tolerance):
    """The stack's begin and end match the ones asked for, or LAS X's step-snapped stack.

    Only the ends that were given are checked. In "Z-Step Size" mode LAS X
    snaps the stack's size to a multiple of its step and keeps the centre, so
    the snapped begin and end count too.
    """
    stack = stack_settings["stack"]
    begin, end = a["begin_um"], a["end_um"]
    candidates = [(begin if begin is not None else stack["begin"], end if end is not None else stack["end"])]
    if begin is not None and end is not None:
        centre = (begin + end) / 2.0
        candidates += [(centre - q / 2.0, centre + q / 2.0) for q in quantised_sizes(end - begin, stack.get("stepSize"))]
    return any(
        (begin is None or abs(stack["begin"] - b) < tolerance) and (end is None or abs(stack["end"] - e) < tolerance)
        for b, e in candidates
    )


def stack_size_matches(stack_settings, a, tolerance):
    """The stack's size matches, or matches LAS X's step-snapped size."""
    actual, target = stack_settings["stack"]["size"], a["size_um"]
    sizes = [target, *quantised_sizes(target, stack_settings["stack"].get("stepSize"))]
    return any(abs(actual - size) < tolerance for size in sizes)


Z_STACK_DEFINITION = Setting(
    name="z_stack_definition",
    takes=("begin_um", "end_um", "old_begin_um", "old_end_um"),
    optional=("begin_um", "end_um", "old_begin_um", "old_end_um"),
    request="PyApiSetZStackDefinitionByJobName",
    write=write_z_stack_definition,
    value="begin_um",
    read=at("stack"),
    describe="Z-stack def: begin={begin_um}, end={end_um}",
    tolerance=1.0,
    gate=lambda a: {"job_name": a["job_name"], "values": [v for v in (a["begin_um"], a["end_um"]) if v is not None]},
    matches=stack_ends_match,
)

Z_STACK_STEP_SIZE = Setting(
    name="z_stack_step_size",
    takes=("step_size_um",),
    request="PyApiCommandSetZStackStepSizeByJobName",
    write=fields(JobName="job_name", StackStepSize=lambda a: a["step_size_um"] * 1e-6),
    value="step_size_um",
    read=at("stack", "stepSize"),
    describe="Z-stack step -> {step_size_um} um",
    tolerance=0.5,
)

Z_STACK_SIZE = Setting(
    name="z_stack_size",
    takes=("size_um",),
    request="PyApiSetZStackSizeByJobName",
    write=fields(JobName="job_name", StackSize=lambda a: a["size_um"] * 1e-6),
    value="size_um",
    read=at("stack", "size"),
    describe="Z-stack size -> {size_um} um",
    tolerance=1.5,
    matches=stack_size_matches,
)

# --- settings that belong to one entry of the job's setting list ------------------------

FRAME_ACCUMULATION = per_setting("frame_accumulation", "PyApiSetFrameAccumulationByJobName", "FrameAccumulation", ("frameAccumulation",))
FRAME_AVERAGE = per_setting("frame_average", "PyApiSetFrameAverageByJobName", "FrameAverage", ("frameAverage",))
LINE_ACCUMULATION = per_setting("line_accumulation", "PyApiSetLineAccumulationByJobName", "LineAccumulation", ("lineAccumulation",))
LINE_AVERAGE = per_setting("line_average", "PyApiSetLineAverageByJobName", "LineAverage", ("lineAverage",))
PINHOLE_AIRY = per_setting("pinhole_airy", "PyApiSetPinholeAUByJobName", "PinholeAiry", ("pinholeAiry", "value"), tolerance=0.05)

# --- detector, laser and filter wheel: settings tied to a beam route ---------------------

DETECTOR_GAIN = Setting(
    name="detector_gain",
    takes=("setting_index", "beam_route", "value"),
    request="PyApiSetDetectorGainByJobName",
    write=fields(JobName="job_name", SettingIndex="setting_index", BeamRoute="beam_route", GainValue="value"),
    value="value",
    read=on_route("activeDetectors", "gain", "value"),
    describe="Setting[{setting_index}].Detector[{beam_route}].Gain -> {value}",
    tolerance=1.0,
)

LASER_INTENSITY = Setting(
    name="laser_intensity",
    takes=("setting_index", "beam_route", "line_index", "value"),
    request="PyApiSetLaserIntensityByJobName",
    write=fields(
        JobName="job_name",
        SettingIndex="setting_index",
        BeamRoute="beam_route",
        LaserLineIndex="line_index",
        IntensityValue="value",
        IsRoiBackground=lambda a: False,
    ),
    value="value",
    read=on_route("activeLaserLines", "intensity", "value", line=True),
    describe="Setting[{setting_index}].Laser[{beam_route}][{line_index}] -> {value}",
    tolerance=0.005,
)

LASER_SHUTTER = Setting(
    name="laser_shutter",
    takes=("setting_index", "beam_route", "activate"),
    request="PyApiSetLaserShutterByJobName",
    write=fields(JobName="job_name", SettingIndex="setting_index", BeamRoute="beam_route", Activate="activate"),
    value="activate",
    read=on_route("activeLaserLines", "shutterOpen"),
    describe="Setting[{setting_index}].Shutter[{beam_route}] -> {state}",
    normalize=lambda a: {**a, "state": "Open" if a["activate"] else "Closed"},
)


def write_filter_wheel(position_field, position_argument):
    """Fill in a filter-wheel request; the wheel type is cast to LAS X's own enum when it can be."""

    def write(m, a):
        m.JobName = a["job_name"]
        m.SettingIndex = a["setting_index"]
        m.BeamRoute = a["beam_route"]
        try:
            wheel = type(m.FilterWheelType)(a["filter_wheel_type"])
        except Exception:
            wheel = a["filter_wheel_type"]
        m.FilterWheelType = wheel
        setattr(m, position_field, a[position_argument])

    return write


FILTER_WHEEL_SLOT = Setting(
    name="filter_wheel_slot",
    takes=("setting_index", "beam_route", "filter_wheel_type", "slot_index"),
    request="PyApiSetFilterWheelSlotByJobName",
    write=write_filter_wheel("SlotIndex", "slot_index"),
    value="slot_index",
    read=on_route("filterWheels", "filterIndex", wheel=True),
    describe="FilterWheel[{beam_route}] slot -> {slot_index}",
)

FILTER_WHEEL_SPECTRUM = Setting(
    name="filter_wheel_spectrum",
    takes=("setting_index", "beam_route", "filter_wheel_type", "position"),
    request="PyApiSetFilterWheelSpectrumPositionByJobName",
    write=write_filter_wheel("FilterSpectrumPosition", "position"),
    value="position",
    read=on_route("filterWheels", "spectrumPosition", wheel=True),
    describe="FilterWheel[{beam_route}] spectrum -> {position}",
    tolerance=1.0,
)


#: Every setting by its name, such as ``"zoom"`` or ``"laser_intensity"``.
SETTINGS = {
    setting.name: setting
    for setting in (
        ZOOM,
        SCAN_SPEED,
        SCAN_MODE,
        SCAN_FIELD_ROTATION,
        SCAN_RESONANT,
        SEQUENTIAL_MODE,
        IMAGE_FORMAT,
        Z_STACK_DEFINITION,
        Z_STACK_STEP_SIZE,
        Z_STACK_SIZE,
        FRAME_ACCUMULATION,
        FRAME_AVERAGE,
        LINE_ACCUMULATION,
        LINE_AVERAGE,
        PINHOLE_AIRY,
        DETECTOR_GAIN,
        LASER_INTENSITY,
        LASER_SHUTTER,
        FILTER_WHEEL_SLOT,
        FILTER_WHEEL_SPECTRUM,
    )
}


# --- changing a setting -------------------------------------------------------------------


def arguments_of(name: str, job_name: str, given: dict) -> tuple[Setting, dict]:
    """The setting called ``name`` and its full arguments; ValueError for a wrong name or argument."""
    setting = SETTINGS.get(name)
    if setting is None:
        raise ValueError(f"no setting {name!r}; the settings are {sorted(SETTINGS)}")
    unknown = sorted(set(given) - set(setting.takes))
    missing = [arg for arg in setting.takes if arg not in given and arg not in setting.optional]
    if unknown or missing:
        raise ValueError(f"{name} takes {list(setting.takes)}; unknown {unknown}, missing {missing}")
    a = {"job_name": job_name, **{arg: None for arg in setting.optional}, **given}
    return setting, setting.normalize(a) if setting.normalize else a


def setting_shows(setting: Setting, a: dict, tolerance=None):
    """Job settings in which ``setting`` shows its new value from the arguments ``a``.

    ``tolerance`` overrides the setting's own (None: the setting's).
    """
    tolerance = setting.tolerance if tolerance is None else tolerance
    target = a[setting.value]

    def check(settings) -> bool:
        try:
            ch = make_changeable_copy(settings)
            if setting.matches is not None:
                return bool(setting.matches(ch, a, tolerance))
            actual = setting.read(ch, a)
            if tolerance is None:
                return actual == target
            return abs(actual - target) < tolerance
        except NOT_THERE_YET:
            return False

    return check


def change(name: str, job_name: str, *, tolerance=None, **given) -> Write | str:
    """The change of the setting ``name`` of the job; or the reason it cannot be sent.

    The keywords are what the setting takes (``SETTINGS[name].takes``), such as
    ``value=2.0`` for the zoom; a wrong name or argument raises ValueError.
    ``tolerance`` overrides how close the readback must come.
    """
    setting, a = arguments_of(name, job_name, given)
    problem = setting.validate(a) if setting.validate else None
    if problem is not None:
        return problem
    return Write(
        command=setting.command,
        values=setting.gate(a) if setting.gate else {"job_name": job_name, "value": a[setting.value]},
        request=setting.request,
        fill=lambda model: setting.write(model, a),
        description=setting.describe.format(**a),
        confirm=Expect("job_settings", setting_shows(setting, a, tolerance), about=job_name),
        accepted_replies=setting.accepted_replies,
    )
