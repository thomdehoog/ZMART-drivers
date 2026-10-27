"""What the microscope is doing now, and what can be changed: its state.

The *changeable* part is what ``set_state`` applies: the objective (its
position on the nosepiece, counted from 1) and the optical configuration (a
named setup in NIS-Elements that carries the light path, the filters, the
camera's exposure and, often, the objective). Applying both again restores
the imaging setup. The *observed* part can only be read: the NIS version,
the stage's travel, the objectives and optical configurations there are,
Perfect Focus, the limits in force, which objective the origin was saved
under, how far this microscope is set up, the changes that were sent but
never confirmed, and the size of a picture and of its pixels.

The size of a picture (``image_size_px``) and of its pixels
(``pixel_size_um``) are the two keys the ZMART Controller asks every driver
for. NIS tells them only for an image that is open, so they are answered,
in this order, from:

1. what this microscope recorded at its last capture with the same objective
   and optical configuration (``driver_setup/pixel_size``);
2. the image open in NIS now, which may have been taken with other settings;
3. nothing: both are None, and ``image_size_source`` says to capture one picture.

``image_size_source`` always says which it was. ``get_state`` never fails
for want of an image.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/state.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..dispatcher import limits as gate
from ..driver_setup import pixel_size
from . import changes, readings

log = logging.getLogger(__name__)

#: What ``set_state`` can change, in the order it applies them.
CHANGEABLE = ("objective_position", "optical_configuration")


def _try(what: str, read):
    """*read()*, or None with a warning when NIS cannot give it: one missing reading does not hide the rest."""
    try:
        return read()
    except RuntimeError as why:
        log.warning("get_state: %s could not be read: %s", what, why)
        return None


def sizes(handle, objective: str | None, configuration: str | None) -> dict:
    """``image_size_px``, ``pixel_size_um`` and ``image_size_source``, as the picture is saved (turned when it is)."""
    entry = pixel_size.lookup(objective, configuration)
    if entry is not None:
        size, px = entry["image_size_px"], entry["pixel_size_um"]
        source = f"the last capture with {objective} and {configuration}, {entry.get('seen')}"
        if not px:
            source += "; that objective is not calibrated in NIS, so the pixel size is unknown"
    else:
        info = _try("the open image", lambda: readings.image_info(handle.client)) or {}
        calibration = (
            _try("the calibration", lambda: readings.calibration(handle.client)) or {}
        )
        if info.get("open"):
            size, px = [info["width"], info["height"]], calibration.get("pixel_size_um")
            source = "the image open in NIS now, which may have been taken with other settings"
            if not px:
                source += f"; its pixel size is unknown ({calibration.get('why') or 'no calibration'})"
        else:
            return {
                "image_size_px": None,
                "pixel_size_um": None,
                "image_size_source": "unknown: capture one picture",
            }
    pixels = [px, px] if px else None
    if handle.orientation is not None and handle.orientation.swaps_axes:
        size = size[::-1]
    return {
        "image_size_px": list(size),
        "pixel_size_um": pixels,
        "image_size_source": source,
    }


def setup_readiness(handle, limits: dict | None) -> dict:
    """Whether this microscope is set up for an experiment, and what is missing, in words."""
    issues = []
    if not limits or limits.get("is_fallback"):
        issues.append(
            "the limits are not published: the stage keeps to NIS's travel and z to near where it stood at connect"
        )
    if not (handle.orientation_info or {}).get("measured"):
        issues.append(
            "the orientation is not stated: pictures are saved as the camera gives them"
        )
    if handle.origin.get("objective") is None:
        issues.append("no origin is saved: positions are NIS's own stage coordinates")
    if handle.data_location is None and not handle.connection.get("output_root"):
        issues.append("no data location is set: acquire needs an output_folder")
    return {"ready": not issues, "issues": issues}


def get_state(handle) -> dict:
    """``{"changeable", "observed"}``: the objective and optical configuration, then what can only be read."""
    handle.require_open()
    client = handle.client
    turret = _try("the nosepiece", lambda: readings.nosepiece(client)) or {
        "present": False,
        "position": None,
        "objectives": {},
    }
    position = turret.get("position")
    objective = turret["objectives"].get(position) if position else None
    known = (
        _try(
            "the optical configurations",
            lambda: readings.optical_configurations(client),
        )
        or []
    )
    configuration = _try(
        "the selected optical configuration",
        lambda: readings.selected_optical_configuration(client),
    )
    limits = gate.describe(client)
    origin_objective = handle.origin.get("objective")
    observed: dict[str, Any] = {
        "vendor": "nikon",
        "microscope": handle.connection.get("microscope"),
        "nis_version": handle.nis_version,
        "nis_travel": _try("NIS's travel", lambda: readings.stage_limits(client)),
        "objectives": {str(k): v for k, v in turret["objectives"].items()},
        "objective": {"position": position, "name": objective},
        "optical_configurations": known,
        "pfs": _try("Perfect Focus", lambda: readings.pfs(client)),
        "limits": limits,
        "origin_objective": origin_objective,
        "objective_differs_from_origin": bool(origin_objective)
        and origin_objective.get("position") != position,
        **sizes(handle, objective, configuration),
        "orientation": dict(handle.orientation_info or {}),
        "setup": setup_readiness(handle, limits),
        "unconfirmed": list(handle.unconfirmed),
    }
    return {
        "changeable": {
            "objective_position": position,
            "optical_configuration": configuration,
        },
        "observed": observed,
    }


def set_state(handle, changeable: dict) -> dict:
    """Apply the objective, then the optical configuration (it may bring an objective of its own); what was applied.

    Each is read back. ``ValueError`` for a setting this microscope does not
    have, or a value NIS does not offer; ``RuntimeError`` when NIS refused or
    the limits did. A change that was sent but never seen is recorded, and
    reported with the answer.
    """
    handle.require_open()
    unknown = sorted(set(changeable) - set(CHANGEABLE))
    if unknown:
        raise ValueError(
            f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}"
        )
    noted_before = len(handle.unconfirmed)
    applied: dict[str, Any] = {}
    steps = (
        ("objective_position", changes.set_objective),
        ("optical_configuration", changes.select_optical_configuration),
    )
    for name, change in steps:
        value = changeable.get(name)
        if value is None:
            continue
        result = change(handle.client, value)
        if not result.get("success"):
            raise RuntimeError(
                f"{name} {value!r} was not applied: {result.get('message')}"
            )
        if not result.get("confirmed"):
            handle.note_unconfirmed(f"set_state: {name} {value!r}", result)
        applied[name] = value
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}
