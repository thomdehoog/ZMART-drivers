"""The settings of a LAS X job that the driver can change, one entry for each.

Changing a setting always goes the same way: show the new value to the limits
gate, fill in a LAS X request, send it, and read the state back to see that it
took. What differs from one setting to the next is only a handful of facts, and
this file keeps those facts in one place, as a table:

- ``command``: the name the limits gate knows the setting by;
- ``request``: the LAS X request object that carries it out;
- ``profile``: how often to retry it and how long to wait for it;
- ``gate``: the values the limits gate is shown;
- ``describe``: the label used in the log;
- ``write``: how the request is filled in, field by field and in order;
- ``confirm``: how the result is checked by reading the state back.

``procedures/changes.py`` holds the one runner that uses every entry, and the
named functions (``set_zoom`` and the others) that operators call.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
        University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from ...dispatcher.envelope import _make_log_entry
from ...dispatcher import error_rules
from ..error_classification import _check_api_error, classify_error
from ..parsing import parse_format
from . import confirmations as _check
from . import profiles as _profiles
from .profiles import CommandProfile


@dataclass(frozen=True)
class Setting:
    """Everything that is particular to changing one LAS X setting."""

    command: str
    request: str
    profile: CommandProfile
    gate: Callable[[dict], dict]
    describe: str
    write: Callable[[Any, dict], None]
    confirm: Callable[[dict, float | None], Callable]
    # Optional steps, used by the few settings that need them.
    normalize: Callable[[dict], dict] | None = None
    validate: Callable[[dict], str | None] | None = None
    error_check: Callable[[dict], Callable] | None = None


# --- small builders, so that each entry below reads as a list of facts ----------------


def _gate(value: str = "value") -> Callable[[dict], dict]:
    """Show the gate the job and the one argument that carries the new value."""
    return lambda a: {"job_name": a["job_name"], "value": a[value]}


def _fields(**model_fields) -> Callable[[Any, dict], None]:
    """Write these request fields in this order.

    A text names the argument to copy; a function computes the value from the
    arguments.
    """

    def write(model, a):
        for attr, source in model_fields.items():
            setattr(model, attr, source(a) if callable(source) else a[source])

    return write


def _confirm(function, profile: CommandProfile, tolerance: bool = False, **arguments):
    """Build the readback check for a call: each keyword names the argument it takes.

    With ``tolerance=True`` the check also gets the tolerance, from the caller
    if one was given and from the profile otherwise.
    """

    def build(a, tolerance_given):
        keywords = {key: a[name] for key, name in arguments.items()}
        if tolerance:
            keywords["tolerance"] = (
                tolerance_given
                if tolerance_given is not None
                else profile.confirm_tolerance
            )
        return partial(function, **keywords)

    return build


def _per_setting(command, request, profile, check, model_field, *, tolerance=False):
    """A setting that is one number on one entry of the job's setting list."""
    return Setting(
        command=command,
        request=request,
        profile=profile,
        gate=_gate(),
        describe="Setting[{setting_index}]." + model_field + " -> {value}",
        write=_fields(
            JobName="job_name", SettingIndex="setting_index", **{model_field: "value"}
        ),
        confirm=_confirm(
            check,
            profile,
            tolerance,
            job_name="job_name",
            si="setting_index",
            target="value",
        ),
    )


# --- settings that are one number or word on the job ----------------------------------

ZOOM = Setting(
    command="set_zoom",
    request="PyApiSetZoomByJobName",
    profile=_profiles.ZOOM,
    gate=_gate(),
    describe="Zoom -> {value}",
    write=_fields(JobName="job_name", ZoomValue="value"),
    confirm=_confirm(
        _check._confirm_zoom, _profiles.ZOOM, True, job_name="job_name", target="value"
    ),
)

SCAN_SPEED = Setting(
    command="set_scan_speed",
    request="PyApiSetScanSpeedByJobName",
    profile=_profiles.SCAN_SPEED,
    gate=_gate(),
    describe="ScanSpeed -> {value}",
    write=_fields(JobName="job_name", ScanSpeed="value"),
    confirm=_confirm(
        _check._confirm_scan_speed,
        _profiles.SCAN_SPEED,
        job_name="job_name",
        target="value",
    ),
)

SCAN_MODE = Setting(
    command="set_scan_mode",
    request="PyApiSetScanModeByJobName",
    profile=_profiles.SCAN_MODE,
    gate=_gate("mode"),
    describe="ScanMode -> {mode}",
    write=_fields(JobName="job_name", ScanModeValue="mode"),
    confirm=_confirm(
        _check._confirm_scan_mode,
        _profiles.SCAN_MODE,
        job_name="job_name",
        target="mode",
    ),
)

SCAN_FIELD_ROTATION = Setting(
    command="set_scan_field_rotation",
    request="PyApiSetScanFieldRotationByJobName",
    profile=_profiles.SCAN_FIELD_ROTATION,
    gate=_gate("angle"),
    describe="Rotation -> {angle}",
    write=_fields(JobName="job_name", Rotation="angle"),
    confirm=_confirm(
        _check._confirm_scan_field_rotation,
        _profiles.SCAN_FIELD_ROTATION,
        True,
        job_name="job_name",
        target="angle",
    ),
)

# --- resonant scanning: LAS X answers "no change" when it is already in that state -----

_SCAN_RESONANT_NO_CHANGE = "desired state does not differ from the current state"


def _scan_resonant_error_check(client, *, job_name, target, timeout=None):
    """Accept LAS X's resonant no-change echo only when readback agrees."""
    logs = []
    err = _check_api_error(client)

    if err is None:
        return {"success": True, "error": None, "transient": None, "logs": logs}

    error_msg = err.get("error", "")
    details = err.get("details", {})
    if _SCAN_RESONANT_NO_CHANGE in error_msg.lower():
        confirmed = _check._confirm_scan_resonant(
            client, job_name, target, poll_window=timeout
        )
        logs.extend(confirmed.get("logs", []))
        if confirmed.get("success"):
            msg = f"Resonant -> {target} already matched; accepting LAS X no-change response"
            logs.append(_make_log_entry("info", msg))
            return {"success": True, "error": None, "transient": None, "logs": logs}

    kind = classify_error(error_msg)
    transient = error_rules.sends_again(kind)
    log_msg = f"API error: {error_msg}"
    if details:
        log_msg += f" | details: {details}"
    logs.append(_make_log_entry("warning" if transient else "error", log_msg))
    return {
        "success": False,
        "error": error_msg,
        "kind": kind,
        "transient": transient,
        "logs": logs,
    }


SCAN_RESONANT = Setting(
    command="set_scan_resonant",
    request="PyApiSetScannerToResonantByJobName",
    profile=_profiles.SCAN_RESONANT,
    gate=_gate("enable"),
    describe="Resonant -> {enable}",
    write=_fields(JobName="job_name", EnableResonant="enable"),
    confirm=_confirm(
        _check._confirm_scan_resonant,
        _profiles.SCAN_RESONANT,
        job_name="job_name",
        target="enable",
    ),
    error_check=lambda a: partial(
        _scan_resonant_error_check,
        job_name=a["job_name"],
        target=a["enable"],
        timeout=_profiles.SCAN_RESONANT.confirm_poll_s,
    ),
)

# --- sequential mode: a word from a short list, written as a .NET enum ----------------

_SEQUENTIAL_MODES = {
    "Line": "eSequentialLine",
    "Frame": "eSequentialFrame",
    "Stack": "eSequentialStack",
}


def _check_sequential_mode(a):
    """The reason this mode cannot be sent, or None when it can."""
    mode = a["mode"]
    if not isinstance(mode, str) or not mode.strip():
        return (
            f"SequentialMode -> {repr(mode)} | Invalid: mode must be a "
            f"non-empty string. Valid: ['Line', 'Frame', 'Stack']"
        )
    if _SEQUENTIAL_MODES.get(mode) is None:
        return (
            f"Could not map {repr(mode)} to SequentialMode enum. "
            f"Valid: {list(_SEQUENTIAL_MODES.keys())}"
        )
    return None


def _write_sequential_mode(m, a):
    enum_val = _SEQUENTIAL_MODES[a["mode"]]
    m.JobName = a["job_name"]
    try:
        current = m.SequentialMode
        m.SequentialMode = getattr(type(current), enum_val, enum_val)
    except (AttributeError, TypeError):
        m.SequentialMode = enum_val


SEQUENTIAL_MODE = Setting(
    command="set_sequential_mode",
    request="PyApiSetSequentialModeByJobName",
    profile=_profiles.SEQUENTIAL_MODE,
    gate=_gate("mode"),
    describe="SequentialMode -> {mode}",
    write=_write_sequential_mode,
    confirm=_confirm(
        _check._confirm_sequential_mode,
        _profiles.SEQUENTIAL_MODE,
        job_name="job_name",
        target="mode",
    ),
    validate=_check_sequential_mode,
)

# --- image format: "512 x 512" or a (width, height) pair -------------------------------


def _split_format(a):
    """Add the width and height to the arguments, whichever way the format was given.

    The limits gate is shown the format as the one text a limits file lists
    ("512 x 512"), so that "512x512" and (512, 512) are checked the same way.
    """
    format_str = a["format_str"]
    w, h = format_str if isinstance(format_str, tuple) else parse_format(format_str)
    return {**a, "w": w, "h": h}


IMAGE_FORMAT = Setting(
    command="set_image_format",
    request="PyApiSetImageSizeByJobName",
    profile=_profiles.IMAGE_FORMAT,
    gate=lambda a: {"job_name": a["job_name"], "value": f"{a['w']} x {a['h']}"},
    describe="Format -> {w} x {h}",
    write=_fields(
        JobName="job_name",
        ImageWidth="w",
        ImageHeight="h",
        IsAutoFocusDefinition=lambda a: False,
    ),
    confirm=_confirm(
        _check._confirm_image_format,
        _profiles.IMAGE_FORMAT,
        job_name="job_name",
        w="w",
        h="h",
    ),
    normalize=_split_format,
)

# --- z-stack: sizes are given in micrometers and sent in meters -------------------------


def _reset_flag(new_value, old_value):
    """LAS X's flag for one end of the stack: 1 = set it, 0 = reset it, 2 = leave it."""
    if new_value is not None:
        return 1
    return 0 if old_value is not None else 2


def _write_z_stack_definition(m, a):
    begin, end = a["begin_um"], a["end_um"]
    m.JobName = a["job_name"]
    m.SetBegin = _reset_flag(begin, a["old_begin_um"])
    # An explicit None check: 0.0 is a valid z position.
    m.BeginValue = (begin if begin is not None else 0) * 1e-6
    m.SetEnd = _reset_flag(end, a["old_end_um"])
    m.EndValue = (end if end is not None else 0) * 1e-6


Z_STACK_DEFINITION = Setting(
    command="set_z_stack_definition",
    request="PyApiSetZStackDefinitionByJobName",
    profile=_profiles.Z_STACK_DEFINITION,
    gate=lambda a: {
        "job_name": a["job_name"],
        "values": [v for v in (a["begin_um"], a["end_um"]) if v is not None],
    },
    describe="Z-stack def: begin={begin_um}, end={end_um}",
    write=_write_z_stack_definition,
    confirm=_confirm(
        _check._confirm_z_stack_definition,
        _profiles.Z_STACK_DEFINITION,
        True,
        job_name="job_name",
        begin_um="begin_um",
        end_um="end_um",
    ),
)

Z_STACK_STEP_SIZE = Setting(
    command="set_z_stack_step_size",
    request="PyApiCommandSetZStackStepSizeByJobName",
    profile=_profiles.Z_STACK_STEP_SIZE,
    gate=_gate("step_size_um"),
    describe="Z-stack step -> {step_size_um} um",
    write=_fields(JobName="job_name", StackStepSize=lambda a: a["step_size_um"] * 1e-6),
    confirm=_confirm(
        _check._confirm_z_stack_step_size,
        _profiles.Z_STACK_STEP_SIZE,
        True,
        job_name="job_name",
        target="step_size_um",
    ),
)

Z_STACK_SIZE = Setting(
    command="set_z_stack_size",
    request="PyApiSetZStackSizeByJobName",
    profile=_profiles.Z_STACK_SIZE,
    gate=_gate("size_um"),
    describe="Z-stack size -> {size_um} um",
    write=_fields(JobName="job_name", StackSize=lambda a: a["size_um"] * 1e-6),
    confirm=_confirm(
        _check._confirm_z_stack_size,
        _profiles.Z_STACK_SIZE,
        True,
        job_name="job_name",
        target_um="size_um",
    ),
)

# --- settings that belong to one entry of the job's setting list ------------------------

FRAME_ACCUMULATION = _per_setting(
    "set_frame_accumulation",
    "PyApiSetFrameAccumulationByJobName",
    _profiles.FRAME_ACCUMULATION,
    _check._confirm_frame_accumulation,
    "FrameAccumulation",
)

FRAME_AVERAGE = _per_setting(
    "set_frame_average",
    "PyApiSetFrameAverageByJobName",
    _profiles.FRAME_AVERAGE,
    _check._confirm_frame_average,
    "FrameAverage",
)

LINE_ACCUMULATION = _per_setting(
    "set_line_accumulation",
    "PyApiSetLineAccumulationByJobName",
    _profiles.LINE_ACCUMULATION,
    _check._confirm_line_accumulation,
    "LineAccumulation",
)

LINE_AVERAGE = _per_setting(
    "set_line_average",
    "PyApiSetLineAverageByJobName",
    _profiles.LINE_AVERAGE,
    _check._confirm_line_average,
    "LineAverage",
)

PINHOLE_AIRY = _per_setting(
    "set_pinhole_airy",
    "PyApiSetPinholeAUByJobName",
    _profiles.PINHOLE_AIRY,
    _check._confirm_pinhole_airy,
    "PinholeAiry",
    tolerance=True,
)

# --- detector, laser and filter wheel: settings tied to a beam route ---------------------

DETECTOR_GAIN = Setting(
    command="set_detector_gain",
    request="PyApiSetDetectorGainByJobName",
    profile=_profiles.DETECTOR_GAIN,
    gate=_gate(),
    describe="Setting[{setting_index}].Detector[{beam_route}].Gain -> {value}",
    write=_fields(
        JobName="job_name",
        SettingIndex="setting_index",
        BeamRoute="beam_route",
        GainValue="value",
    ),
    confirm=_confirm(
        _check._confirm_detector_gain,
        _profiles.DETECTOR_GAIN,
        True,
        job_name="job_name",
        si="setting_index",
        beam_route="beam_route",
        target="value",
    ),
)

LASER_INTENSITY = Setting(
    command="set_laser_intensity",
    request="PyApiSetLaserIntensityByJobName",
    profile=_profiles.LASER_INTENSITY,
    gate=_gate(),
    describe="Setting[{setting_index}].Laser[{beam_route}][{line_index}] -> {value}",
    write=_fields(
        JobName="job_name",
        SettingIndex="setting_index",
        BeamRoute="beam_route",
        LaserLineIndex="line_index",
        IntensityValue="value",
        IsRoiBackground=lambda a: False,
    ),
    confirm=_confirm(
        _check._confirm_laser_intensity,
        _profiles.LASER_INTENSITY,
        True,
        job_name="job_name",
        si="setting_index",
        beam_route="beam_route",
        line_index="line_index",
        target="value",
    ),
)

LASER_SHUTTER = Setting(
    command="set_laser_shutter",
    request="PyApiSetLaserShutterByJobName",
    profile=_profiles.LASER_SHUTTER,
    gate=_gate("activate"),
    describe="Setting[{setting_index}].Shutter[{beam_route}] -> {state}",
    write=_fields(
        JobName="job_name",
        SettingIndex="setting_index",
        BeamRoute="beam_route",
        Activate="activate",
    ),
    confirm=_confirm(
        _check._confirm_laser_shutter,
        _profiles.LASER_SHUTTER,
        job_name="job_name",
        si="setting_index",
        beam_route="beam_route",
        target="activate",
    ),
    normalize=lambda a: {**a, "state": "Open" if a["activate"] else "Closed"},
)


def _write_filter_wheel(position_field, position_argument):
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
    command="set_filter_wheel_slot",
    request="PyApiSetFilterWheelSlotByJobName",
    profile=_profiles.FILTER_WHEEL_SLOT,
    gate=_gate("slot_index"),
    describe="FilterWheel[{beam_route}] slot -> {slot_index}",
    write=_write_filter_wheel("SlotIndex", "slot_index"),
    confirm=_confirm(
        _check._confirm_filter_wheel_slot,
        _profiles.FILTER_WHEEL_SLOT,
        job_name="job_name",
        si="setting_index",
        beam_route="beam_route",
        fw_type="filter_wheel_type",
        target="slot_index",
    ),
)

FILTER_WHEEL_SPECTRUM = Setting(
    command="set_filter_wheel_spectrum",
    request="PyApiSetFilterWheelSpectrumPositionByJobName",
    profile=_profiles.FILTER_WHEEL_SPECTRUM,
    gate=_gate("position"),
    describe="FilterWheel[{beam_route}] spectrum -> {position}",
    write=_write_filter_wheel("FilterSpectrumPosition", "position"),
    confirm=_confirm(
        _check._confirm_filter_wheel_spectrum,
        _profiles.FILTER_WHEEL_SPECTRUM,
        True,
        job_name="job_name",
        si="setting_index",
        beam_route="beam_route",
        fw_type="filter_wheel_type",
        target="position",
    ),
)
