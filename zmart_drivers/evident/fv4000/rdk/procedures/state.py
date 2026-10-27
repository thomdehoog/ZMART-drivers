"""What the microscope is doing now and what can be changed: its state.

The *changeable* part is what ``set_state`` applies and reads back: the
objective, the scanner, the z-stack, the time lapse, each detector channel
and each laser. On the FV4000 there is no named "job" to select; the whole
imaging setup is recalled with ``load_settings_from`` (``Parameter.loadParameter``),
which copies the conditions a saved picture was taken with. The *observed*
part is a read-only report: FLUOVIEW's version, the objectives on the
nosepiece, the limits in force, what is set up, and the changes that were
never confirmed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ..dispatcher import limits as _gate
from ..driver_setup.frame import frame_as_saved
from . import changes
from . import keep_in_view as _compensated
from . import readings as _readers

log = logging.getLogger(__name__)

#: The changeable settings and the reading each is read back from, in the order ``set_state`` applies them.
SIMPLE_SETTINGS = (
    ("scanner_type", "scanner_type", "scanner_type", "value"),
    ("scanner_mode", "scanner_mode", "scanner_mode", "value"),
    ("z_stack_enabled", "z_stack_enabled", "z_stack_enable", "enable"),
    ("z_stack_slices", "z_stack_slices", "z_stack_slices", "value"),
    ("z_stack_step_um", "z_stack_step_um", "z_stack_step", "value_um"),
    ("z_stack_start_um", "z_stack_start_um", "z_stack_start", "value_um"),
    ("z_stack_end_um", "z_stack_end_um", "z_stack_end", "value_um"),
    ("time_lapse_enabled", "time_lapse_enabled", "time_lapse_enable", "enable"),
    ("time_lapse_repeat", "time_lapse_repeat", "time_lapse_repeat", "value"),
    (
        "time_lapse_interval_s",
        "time_lapse_interval_s",
        "time_lapse_interval",
        "value_s",
    ),
)
CHANNEL_FIELDS = (
    ("enabled", "channel_enabled", "channel_enable", "enable"),
    ("hv", "channel_hv", "channel_hv", "value"),
    ("gain", "channel_gain", "channel_gain", "value"),
    ("offset", "channel_offset", "channel_offset", "value"),
    ("digital_gain", "channel_digital_gain", "channel_digital_gain", "value"),
)
LASER_FIELDS = (
    ("enabled", "laser_enabled", "laser_enable", "enable"),
    ("intensity", "laser_intensity", "laser_intensity", "value"),
)

CHANGEABLE = frozenset(
    {
        "objective",
        "channels",
        "lasers",
        "load_settings_from",
        *(key for key, *_ in SIMPLE_SETTINGS),
    }
)


def get_state(handle) -> dict:
    """The state: ``changeable`` (what ``set_state`` applies) and ``observed`` (a report). Every value is a fresh read.

    A value FLUOVIEW does not have (the HV of a detector that is not a PMT)
    is None, not a stop.
    """
    handle.require_open()
    client = handle.client
    objective = _readers.get_objective(client) or {}
    changeable: dict[str, Any] = {"objective": objective.get("slot")}
    for key, reading, _setting, _arg in SIMPLE_SETTINGS:
        changeable[key] = _readers.read_now(reading, client)
    channels = _readers.get_channels(client) or []
    changeable["channels"] = {
        channel: {
            **{
                field: _readers.read_now(reading, client, about=channel)
                for field, reading, _s, _a in CHANNEL_FIELDS
            },
            "lasers": _readers.read_now("channel_laser", client, about=channel),
        }
        for channel in channels
    }
    lasers = _readers.get_lasers(client) or []
    changeable["lasers"] = {
        laser["id"]: {
            field: _readers.read_now(reading, client, about=laser["id"])
            for field, reading, _s, _a in LASER_FIELDS
        }
        for laser in lasers
    }
    changeable["load_settings_from"] = handle.settings_loaded_from
    limits = _gate.describe(client)
    # The controller's own two keys: what one picture covers, as it is saved. FLUOVIEW's
    # settings do not report them, so they come from the newest picture taken under this
    # objective (driver_setup/frame.py) and are None until one has been taken.
    image_size_px, pixel_size_um = frame_as_saved(
        handle.frames.get(objective.get("slot")), handle.orientation
    )
    return {
        "changeable": changeable,
        "observed": {
            "vendor": "evident",
            "microscope": handle.connection.get("microscope"),
            "fluoview_version": handle.fluoview_version,
            "scan_state": _readers.read_now("scan_state", client),
            "objective": dict(objective),
            "objectives": _readers.get_objectives(client) or [],
            "laser_lines": lasers,
            "z_origin_um": _readers.read_now("z_origin", client),
            "z_type": _readers.read_now("z_type", client),
            "z_stack_range_um": _readers.read_now("z_stack_range_um", client),
            "z_piezo_um": _readers.read_now("z_piezo", client),
            "image_size_px": image_size_px,
            "pixel_size_um": pixel_size_um,
            "frame": dict(handle.frames.get(objective.get("slot")) or {}) or None,
            "limits": limits,
            "setup": setup_readiness(handle, objective, limits),
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def setup_readiness(handle, active_objective: dict | None, limits: dict | None) -> dict:
    """Whether what is set up once (limits, orientation, calibration, origin, the frame) is in place for this objective, and what is missing."""
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    slot = (active_objective or {}).get("slot")
    origin_slot = (handle.origin.get("objective") or {}).get("slot")
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            "this microscope's measured limits are not active; publish them with driver_setup/limits/notebooks/set_limits.ipynb"
        )
    if not orientation.get("measured"):
        issues.append(
            "the image-to-stage orientation is not measured; run driver_setup/orientation/notebooks/set_orientation.ipynb"
        )
    if not calibration.get("loaded"):
        issues.append("no objective calibration is loaded")
    elif slot is None:
        issues.append("the objective in use could not be read")
    elif slot not in calibration.get("measured_slots", []):
        issues.append(
            f"objective slot {slot} is not in the calibration; run driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
        )
    if (
        calibration.get("loaded")
        and origin_slot is not None
        and origin_slot not in calibration.get("measured_slots", [])
    ):
        issues.append(
            f"the origin's objective slot {origin_slot} is not in the calibration"
        )
    if slot is not None and slot not in handle.frames:
        issues.append(
            f"no picture has been taken under objective slot {slot} yet, so its image size and pixel size are not known; take one picture and save it"
        )
    return {
        "ready": not issues,
        "issues": issues,
        "active_objective_slot": slot,
        "origin_objective_slot": origin_slot,
        "orientation": orientation,
        "calibration": calibration,
    }


def set_state(handle, state: dict) -> dict:
    """Apply the changeable part of *state*, in a fixed order, and report what was applied.

    The objective comes first (it moves the stage to keep the sample in view),
    then the scanner, the z-stack, the time lapse, each channel and each
    laser; ``load_settings_from`` (a picture's path) comes before all of
    them, since it resets the whole setup. A setting the microscope does not
    have raises ``ValueError``; a change FLUOVIEW refused raises
    ``RuntimeError``; one that was sent but not confirmed is recorded and the
    work goes on.
    """
    handle.require_open()
    changeable = dict(state.get("changeable") or {})
    unknown = sorted(set(changeable) - CHANGEABLE)
    if unknown:
        raise ValueError(
            f"no changeable setting {unknown}; the changeable settings are {sorted(CHANGEABLE)}"
        )
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)
    client = handle.client

    def apply(what, result, key=None, value=None):
        if not result.get("success"):
            raise RuntimeError(f"{what} failed: {result.get('message')}")
        if not result.get("confirmed", True):
            handle.note_unconfirmed(what, result)
        if key is not None:
            applied[key] = value

    if changeable.get("load_settings_from") is not None:
        path = str(changeable["load_settings_from"])
        apply(
            f"set_state: load settings from {path}",
            changes.load_parameter(client, path),
            "load_settings_from",
            path,
        )
        handle.settings_loaded_from = path
    if changeable.get("objective") is not None:
        slot = int(changeable["objective"])
        current = _readers.get_objective(client) or {}
        if current.get("slot") != slot:
            objectives = _readers.get_objectives(client)
            result = _compensated.set_objective(
                client, objectives, slot=slot, translations=handle.translations
            )
            apply(f"set_state: objective slot {slot}", result, "objective", slot)
        else:
            applied["objective"] = slot
    for key, _reading, setting, argument in SIMPLE_SETTINGS:
        if changeable.get(key) is not None:
            apply(
                f"set_state: {key} = {changeable[key]!r}",
                changes.change(client, setting, **{argument: changeable[key]}),
                key,
                changeable[key],
            )
    for channel, fields in (changeable.get("channels") or {}).items():
        done = {}
        for field, _reading, setting, argument in CHANNEL_FIELDS:
            if (fields or {}).get(field) is not None:
                apply(
                    f"set_state: channel {channel} {field}",
                    changes.change(
                        client,
                        setting,
                        channel_id=str(channel),
                        **{argument: fields[field]},
                    ),
                )
                done[field] = fields[field]
        if (fields or {}).get("lasers"):
            for laser in fields["lasers"]:
                apply(
                    f"set_state: channel {channel} laser {laser}",
                    changes.change(
                        client,
                        "channel_laser",
                        channel_id=str(channel),
                        laser_id=str(laser),
                    ),
                )
            done["lasers"] = list(fields["lasers"])
        if done:
            applied.setdefault("channels", {})[channel] = done
    for laser, fields in (changeable.get("lasers") or {}).items():
        done = {}
        for field, _reading, setting, argument in LASER_FIELDS:
            if (fields or {}).get(field) is not None:
                apply(
                    f"set_state: laser {laser} {field}",
                    changes.change(
                        client,
                        setting,
                        laser_id=str(laser),
                        **{argument: fields[field]},
                    ),
                )
                done[field] = fields[field]
        if done:
            applied.setdefault("lasers", {})[laser] = done
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}
