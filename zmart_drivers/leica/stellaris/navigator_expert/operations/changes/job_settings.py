"""The settings of a LAS X job that the driver can change, one entry for each.

Changing a setting always goes the same way: show the new value to the limits
gate, fill in a LAS X request, send it, and read the job's settings back until
the new value shows. What differs from one setting to the next is a handful of
facts, and this file keeps them in one place, as a table:

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

``change`` changes any of them, and ``confirm`` reads one back.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from ...primitives.cam_api import waiting
from ...dispatcher.dispatch import make_log_entry
from ...primitives.cam_api.request import request_of
from ...primitives.cam_api.parsing import parse_format
from ...primitives.cam_api.profile import CommandProfile
from ...primitives.cam_api.error_classification import _check_api_error, classify_error, sends_again
from . import confirming, run_change

log = logging.getLogger(__name__)

#: Every setting is sent and confirmed the same way: four windows, sent again
#: between them, and unconfirmed is reported, never a failure.
PROFILE = CommandProfile()

#: What reading a value out of the job's settings may raise while LAS X has
#: not written it yet; the confirmation then looks again.
NOT_THERE_YET = (KeyError, TypeError, IndexError, StopIteration)


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
    error_check: Callable[[dict], Callable] | None = None

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

RESONANT_NO_CHANGE = "desired state does not differ from the current state"


def resonant_error_check(client, *, arguments):
    """Accept LAS X's resonant "no change" answer, but only when the readback agrees."""
    logs = []
    err = _check_api_error(client)
    if err is None:
        return {"success": True, "error": None, "transient": None, "logs": logs}

    error_msg = err.get("error", "")
    details = err.get("details", {})
    if RESONANT_NO_CHANGE in error_msg.lower():
        confirmed = confirm(client, "scan_resonant", arguments)
        logs.extend(confirmed.get("logs", []))
        if confirmed.get("success"):
            msg = f"Resonant -> {arguments['enable']} already matched; accepting LAS X no-change response"
            logs.append(make_log_entry("info", msg))
            return {"success": True, "error": None, "transient": None, "logs": logs}

    kind = classify_error(error_msg)
    transient = sends_again(kind)
    log_msg = f"API error: {error_msg}"
    if details:
        log_msg += f" | details: {details}"
    logs.append(make_log_entry("warning" if transient else "error", log_msg))
    return {"success": False, "error": error_msg, "kind": kind, "transient": transient, "logs": logs}


SCAN_RESONANT = Setting(
    name="scan_resonant",
    takes=("enable",),
    request="PyApiSetScannerToResonantByJobName",
    write=fields(JobName="job_name", EnableResonant="enable"),
    value="enable",
    read=at("scanSpeed", "isResonant"),
    describe="Resonant -> {enable}",
    error_check=lambda a: partial(resonant_error_check, arguments=a),
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


# --- changing a setting, and reading it back ---------------------------------------------


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


def change(client, name: str, job_name: str, *, max_retries=None, tolerance=None, **given) -> dict:
    """Change the setting ``name`` of the job, and read it back.

    The keywords are what the setting takes (``SETTINGS[name].takes``), such as
    ``value=2.0`` for the zoom. The limits gate is shown the new value first,
    and nothing is sent if it refuses. ``tolerance`` overrides how close the
    readback must come.
    """
    setting, a = arguments_of(name, job_name, given)
    gated_values = setting.gate(a) if setting.gate else {"job_name": job_name, "value": a[setting.value]}
    refusal = run_change.limits_refusal(client, setting.command, gated_values)
    if refusal:
        return refusal
    problem = setting.validate(a) if setting.validate else None
    if problem is not None:
        return run_change.refused(problem)

    return run_change.dispatch_change(
        client,
        request_of(client, setting.request),
        setting.describe.format(**a),
        PROFILE,
        command=setting.command,
        values=gated_values,
        setup_fn=lambda model: setting.write(model, a),
        confirm_fn=partial(confirmed, setting=setting, a=a, tolerance=tolerance),
        error_check_fn=setting.error_check(a) if setting.error_check else None,
        max_retries=max_retries,
    )


def confirm(client, name: str, job_name_or_arguments, *, tolerance=None, poll_window=None, **given) -> dict:
    """Read the job's settings back until the setting ``name`` shows its new value.

    Give the job and the setting's arguments as for ``change``, or the full
    arguments as one dict. One window long (``poll_window``, ``WINDOW_S`` by
    the rule). Answers ``{"success": bool, "logs": [...]}``.
    """
    if isinstance(job_name_or_arguments, dict):
        setting, a = SETTINGS[name], job_name_or_arguments
    else:
        setting, a = arguments_of(name, job_name_or_arguments, given)
    return confirmed(client, setting=setting, a=a, tolerance=tolerance, poll_window=poll_window)


def confirmed(client, *, setting: Setting, a: dict, tolerance=None, poll_window=None) -> dict:
    """One confirmation window for ``setting``, with arguments ``a``."""
    tolerance = setting.tolerance if tolerance is None else tolerance
    target = a[setting.value]
    poll_window = waiting.WINDOW_S if poll_window is None else poll_window
    started = time.monotonic()
    deadline = started + poll_window
    last = None

    while time.monotonic() < deadline:
        settings = confirming.readback(client, a["job_name"], deadline=deadline)
        if settings is not None:
            try:
                last = setting.read(settings, a)
                if setting.matches is not None:
                    shows = setting.matches(settings, a, tolerance)
                elif tolerance is None:
                    shows = last == target
                else:
                    shows = abs(last - target) < tolerance
                if shows:
                    return {"success": True, "logs": []}
                log.debug("%s confirm: target=%s actual=%s", setting.name, target, last)
            except NOT_THERE_YET:
                pass
        time.sleep(waiting.POLL_S)

    msg = (
        f"{setting.describe.format(**a)} not seen after {time.monotonic() - started:.1f}s "
        f"(last read {last!r})"
    )
    log.warning(msg)
    return {"success": False, "logs": [make_log_entry("warning", msg)]}
