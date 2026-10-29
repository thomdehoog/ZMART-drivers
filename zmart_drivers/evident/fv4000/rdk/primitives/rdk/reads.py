"""The readings of FLUOVIEW: every question the driver can ask, one function each, and the table of them.

A reading asks FLUOVIEW one thing and hands back a plain Python value in the
driver's units (micrometres, True or False, a list of names). Most are a
``Parameter.getParameter`` call with a ``settingId`` from the manual's section
3.2.7; a few are calls of their own (the version, the scan progress, the files
being acquired, the live-view canvases). None of them retries, waits or checks
limits: that is the dispatcher's business, which reaches them through the
table ``READS`` at the end of this file and ``profile.py``.

A reading *about* one item (one detector channel or one laser) is given that item's id as ``about``; the others ignore it. A
reading whose answer FLUOVIEW refuses raises :class:`~.parsing.RdkError`, and
the dispatcher carries that error as "no answer from this source".

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .parsing import nm_to_um, require_ok

#: The kind of imaging FLUOVIEW's protocol calls take for a normal picture
#: (XY, XYZ, XYT and so on): the imaging set up in FLUOVIEW, run once.
MANUAL_MAIN = "EXECUTION_TYPE_MANUAL_MAIN"

#: FLUOVIEW's four states (the manual's section 3.5).
STATES = ("IDLING", "SCANNING", "RESTING", "WAITING")


def get_parameter(client, setting_id: str, **arguments) -> dict:
    """Ask ``Parameter.getParameter`` for *setting_id*, and answer the reply without its ``result``."""
    reply = require_ok(
        f"Parameter.getParameter({setting_id})",
        client.call("Parameter.getParameter", {"settingId": setting_id, **arguments}),
    )
    return {key: value for key, value in reply.items() if key != "result"}


# =============================================================================
# The software and the scan
# =============================================================================


def get_version(client, about=None) -> str:
    """FLUOVIEW's version, such as ``"2.1.1.70"``; also the simplest way to see that it answers."""
    return str(
        require_ok("Maintenance.getVersion", client.call("Maintenance.getVersion"))[
            "version"
        ]
    )


def get_scan_state(client, about=None) -> str:
    """What FLUOVIEW is doing now: ``IDLING``, ``SCANNING``, ``RESTING`` (between time points) or ``WAITING`` (paused)."""
    return str(get_protocol_progress(client, about)["state"])


def get_protocol_progress(client, about=None) -> dict:
    """The whole progress record of a scan: its state, and how many of its z slices, time points and wavelengths are done.

    *about* is the kind of imaging asked about (``MANUAL_MAIN`` when None).
    The ``series`` part holds ``totalZ``, ``completeZ``, ``totalT``,
    ``completeT``, ``totalL`` and ``completeL``.
    """
    reply = require_ok(
        "Protocol.getProtocolProgress",
        client.call(
            "Protocol.getProtocolProgress", {"executionType": about or MANUAL_MAIN}
        ),
    )
    return {key: value for key, value in reply.items() if key != "result"}


def get_acquiring_files(client, about=None) -> list[str]:
    """The paths of the pictures being acquired right now (empty when none is)."""
    reply = require_ok(
        "Protocol.getAcquiringFiles", client.call("Protocol.getAcquiringFiles")
    )
    return [
        str(entry.get("name"))
        for entry in reply.get("targetName") or []
        if entry.get("name")
    ]


# =============================================================================
# The stage and the focus
# =============================================================================


def get_xy(client, about=None) -> dict:
    """Where the XY stage is, in micrometres: ``{"x_um", "y_um"}``."""
    answer = get_parameter(client, "XY_STAGE_POSITION_SETTING")
    return {"x_um": nm_to_um(answer["x"]), "y_um": nm_to_um(answer["y"])}


def get_z(client, about=None) -> float:
    """Where the Z stage (the focus motor) is, in micrometres, in FLUOVIEW's absolute coordinates."""
    return nm_to_um(get_parameter(client, "Z_STAGE_POSITION_SETTING")["stagePosition"])


def get_z_motor_relative(client, about=None) -> float:
    """The Z motor's position in micrometres, counted from FLUOVIEW's own Z origin (``get_z_origin``)."""
    return nm_to_um(get_parameter(client, "LSM_Z_MOTOR_POSITION_SETTING")["zPosition"])


def get_z_piezo(client, about=None) -> float:
    """The piezo's position in micrometres, counted from FLUOVIEW's own Z origin; 0 without a piezo in use."""
    return nm_to_um(get_parameter(client, "LSM_Z_PIEZO_POSITION_SETTING")["zPosition"])


def get_z_origin(client, about=None) -> float:
    """FLUOVIEW's own Z origin, in micrometres: the zero its relative Z values and z-stack positions count from."""
    return nm_to_um(
        get_parameter(client, "LSM_Z_COORDINATE_BASE_SETTING")["originPosition"]
    )


# =============================================================================
# The objectives
# =============================================================================


def get_objectives(client, about=None) -> list[dict]:
    """The objectives on the nosepiece: ``[{"slot": <revolver index from 0>, "name": <lens name>}, ...]``."""
    answer = get_parameter(client, "LSM_MICROSCOPE_OBJECTIVE_LENS_LIST_SETTING")
    return [
        {"slot": int(entry["revolverIndex"]), "name": str(entry.get("lensName", ""))}
        for entry in answer.get("revolvers") or []
    ]


def _holding(answer: dict, key: str) -> dict | None:
    """The dictionary in *answer* that holds *key*: the answer itself, or a struct (or the first of a list of them) inside it."""
    if key in answer:
        return answer
    for value in answer.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = value[0]
        if isinstance(value, dict) and key in value:
            return value
    return None


def get_objective(client, about=None) -> dict:
    """The objective in use: ``{"slot": <revolver index from 0>, "name": <lens name>}``.

    The manual shows the answer as a struct without naming it, so the slot is
    looked for both in the reply itself and in a struct inside it.
    """
    answer = get_parameter(client, "LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING")
    found = _holding(answer, "revolverIndex")
    if found is None:
        raise KeyError("revolverIndex")
    return {
        "slot": int(found["revolverIndex"]),
        "name": str(found.get("lensName", "")),
    }


# =============================================================================
# The detector channels
# =============================================================================


def get_channels(client, about=None) -> list[str]:
    """The ids of the detector channels, as FLUOVIEW names them."""
    return [
        str(c)
        for c in get_parameter(client, "LSM_CHANNEL_LIST_SETTING").get("channelList")
        or []
    ]


def get_channel_enabled(client, about) -> bool:
    """Whether the channel *about* is switched on."""
    return bool(
        get_parameter(client, "LSM_CHANNEL_ENABLE_SETTING", channelId=about)["enable"]
    )


def get_channel_hv(client, about) -> int:
    """The detector voltage (HV) of the channel *about*; only a PMT channel has one."""
    return int(get_parameter(client, "LSM_CHANNEL_HV_SETTING", channelId=about)["hv"])


def get_channel_gain(client, about) -> float:
    """The detector gain of the channel *about*; only a PMT channel has one."""
    return float(
        get_parameter(client, "LSM_CHANNEL_GAIN_SETTING", channelId=about)["gain"]
    )


def get_channel_offset(client, about) -> int:
    """The detector offset of the channel *about*; only a PMT channel has one."""
    return int(
        get_parameter(client, "LSM_CHANNEL_OFFSET_SETTING", channelId=about)["offset"]
    )


def get_channel_digital_gain(client, about) -> float:
    """The digital gain of the channel *about*, where the channel shows one."""
    return float(
        get_parameter(client, "LSM_CHANNEL_DIGITALGAIN_SETTING", channelId=about)[
            "gain"
        ]
    )


def get_channel_laser(client, about) -> list[str]:
    """The ids of the lasers assigned to the channel *about*."""
    answer = get_parameter(client, "LSM_CHANNEL_LASER_SETTING", channelId=about)
    # The manual names the set's list "laser" but leaves the get's list unnamed, so
    # the lasers are taken from whichever list in the reply holds laserId entries.
    entries = answer.get("laser")
    if not isinstance(entries, list):
        entries = next(
            (
                value
                for value in answer.values()
                if isinstance(value, list)
                and any(isinstance(e, dict) and "laserId" in e for e in value)
            ),
            [],
        )
    return [
        str(entry.get("laserId"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("laserId")
    ]


# =============================================================================
# The lasers
# =============================================================================


def get_lasers(client, about=None) -> list[dict]:
    """The lasers: ``[{"id", "wavelength_nm", "type" ("SPE" single-photon or "MPE" multi-photon), "phase"}, ...]``."""
    answer = get_parameter(client, "LSM_LASER_LIST_SETTING")
    return [
        {
            "id": str(entry.get("laserId")),
            "wavelength_nm": int(entry.get("laserWaveLength", 0) or 0),
            "type": str(entry.get("laserType", "")),
            "phase": str(entry.get("phaseId", "")),
        }
        for entry in answer.get("laserList") or []
    ]


def get_laser_enabled(client, about) -> bool:
    """Whether the laser *about* is switched on."""
    return bool(
        get_parameter(client, "LSM_LASER_ENABLE_SETTING", laserId=about)["enable"]
    )


def get_laser_intensity(client, about) -> float:
    """The power of the laser *about*, in percent, as FLUOVIEW shows it."""
    return float(
        get_parameter(client, "LSM_LASER_INTENSITY_SETTING", laserId=about)[
            "laserIntensity"
        ]
    )


# =============================================================================
# The z-stack, the time lapse and the scanner
# =============================================================================


def get_z_stack_enabled(client, about=None) -> bool:
    return bool(get_parameter(client, "LSM_Z_COORDINATE_ENABLE_SETTING")["enable"])


def get_z_stack_slices(client, about=None) -> int:
    return int(get_parameter(client, "LSM_Z_COORDINATE_SLICE_NUM_SETTING")["sliceNum"])


def get_z_stack_step(client, about=None) -> float:
    """The step between two slices, in micrometres."""
    return nm_to_um(
        get_parameter(client, "LSM_Z_COORDINATE_STEP_SIZE_SETTING")["stepSize"]
    )


def get_z_stack_start(client, about=None) -> float:
    """Where the stack starts, in micrometres from FLUOVIEW's Z origin."""
    return nm_to_um(
        get_parameter(client, "LSM_Z_COORDINATE_START_SETTING")["startPosition"]
    )


def get_z_stack_end(client, about=None) -> float:
    """Where the stack ends, in micrometres from FLUOVIEW's Z origin."""
    return nm_to_um(
        get_parameter(client, "LSM_Z_COORDINATE_END_SETTING")["endPosition"]
    )


def get_z_stack_range(client, about=None) -> float:
    """How tall the stack is, in micrometres."""
    return nm_to_um(get_parameter(client, "LSM_Z_COORDINATE_RANGE_SETTING")["range"])


def get_z_type(client, about=None) -> str:
    """How the stack is defined: ``START_END``, ``RANGE`` (both with the motor) or ``PIEZO``."""
    return str(get_parameter(client, "LSM_Z_COORDINATE_TYPE_SETTING")["zType"])


def get_time_lapse_enabled(client, about=None) -> bool:
    return bool(get_parameter(client, "LSM_TIME_LAPS_ENABLE_SETTING")["enable"])


def get_time_lapse_repeat(client, about=None) -> int:
    return int(
        get_parameter(client, "LSM_TIME_LAPS_REPEAT_TIMES_SETTING")["repeatCount"]
    )


def get_time_lapse_interval(client, about=None) -> float:
    """The time between two time points, in seconds (FLUOVIEW keeps a number and a power of ten)."""
    answer = get_parameter(client, "LSM_TIME_LAPS_INTERVAL_SETTING")
    return float(answer["interval"]) * 10.0 ** (-int(answer["unitOfInterval"]))


def get_scanner_type(client, about=None) -> str:
    """``GALVANO`` or ``RESONANT``."""
    return str(get_parameter(client, "LSM_SCANNER_TYPE_SETTING")["scannerType"])


def get_scanner_mode(client, about=None) -> str:
    """``ONEWAY`` or ``ROUNDTRIP`` (scanning in both directions)."""
    return str(get_parameter(client, "LSM_SCANNER_MODE_SETTING")["scannerMode"])


def get_canvases(client, about=None) -> list[str]:
    """The ids of the live-view canvases (one per shown channel, plus the merge in tile view)."""
    return [
        str(c)
        for c in require_ok(
            "Canvas.getCanvasIdList", client.call("Canvas.getCanvasIdList")
        ).get("idList")
        or []
    ]


# =============================================================================
# What counts as an answer, and the table of readings
# =============================================================================


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def xy_present(value) -> bool:
    return (
        isinstance(value, dict)
        and _finite(value.get("x_um"))
        and _finite(value.get("y_um"))
    )


def state_known(value) -> bool:
    return value in STATES


def is_number(value) -> bool:
    return not isinstance(value, bool) and _finite(value)


def is_text(value) -> bool:
    return isinstance(value, str) and bool(value)


def is_bool(value) -> bool:
    return isinstance(value, bool)


def is_list(value) -> bool:
    return isinstance(value, list)


def is_dict(value) -> bool:
    return isinstance(value, dict) and bool(value)


def slot_named(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("slot"), int)


@dataclass(frozen=True)
class Read:
    """One reading: how to fetch it, and what counts as an answer."""

    fetch: Callable
    counts: Callable


#: Every reading by its name: the table ``profile.py`` hands the dispatcher.
READS = {
    "version": Read(get_version, is_text),
    "scan_state": Read(get_scan_state, state_known),
    "protocol_progress": Read(get_protocol_progress, is_dict),
    "acquiring_files": Read(get_acquiring_files, is_list),
    "xy": Read(get_xy, xy_present),
    "z": Read(get_z, is_number),
    "z_motor_relative": Read(get_z_motor_relative, is_number),
    "z_piezo": Read(get_z_piezo, is_number),
    "z_origin": Read(get_z_origin, is_number),
    "objectives": Read(get_objectives, is_list),
    "objective": Read(get_objective, slot_named),
    "channels": Read(get_channels, is_list),
    "channel_enabled": Read(get_channel_enabled, is_bool),
    "channel_hv": Read(get_channel_hv, is_number),
    "channel_gain": Read(get_channel_gain, is_number),
    "channel_offset": Read(get_channel_offset, is_number),
    "channel_digital_gain": Read(get_channel_digital_gain, is_number),
    "channel_laser": Read(get_channel_laser, is_list),
    "lasers": Read(get_lasers, is_list),
    "laser_enabled": Read(get_laser_enabled, is_bool),
    "laser_intensity": Read(get_laser_intensity, is_number),
    "z_stack_enabled": Read(get_z_stack_enabled, is_bool),
    "z_stack_slices": Read(get_z_stack_slices, is_number),
    "z_stack_step_um": Read(get_z_stack_step, is_number),
    "z_stack_start_um": Read(get_z_stack_start, is_number),
    "z_stack_end_um": Read(get_z_stack_end, is_number),
    "z_stack_range_um": Read(get_z_stack_range, is_number),
    "z_type": Read(get_z_type, is_text),
    "time_lapse_enabled": Read(get_time_lapse_enabled, is_bool),
    "time_lapse_repeat": Read(get_time_lapse_repeat, is_number),
    "time_lapse_interval_s": Read(get_time_lapse_interval, is_number),
    "scanner_type": Read(get_scanner_type, is_text),
    "scanner_mode": Read(get_scanner_mode, is_text),
    "canvases": Read(get_canvases, is_list),
}
