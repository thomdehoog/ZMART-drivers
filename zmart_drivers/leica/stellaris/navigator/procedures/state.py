"""What the instrument is doing now and what can be changed: its state.

The *changeable* part is what LAS X's plain Navigator lets the driver set:
the five settings of the current job (zoom, scan speed, scan field rotation,
line average, pinhole in Airy units) and the objective, by its slot. There
is no job to select: jobs belong to Navigator Expert. The *observed* part is
a read-only report: the instrument, the objectives, the pixel size, the
limits that govern the session, whether each setup step has been done, and
the changes that were never confirmed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..dispatcher import limits as _gate
from ..primitives.direct.writes import SETTINGS
from . import changes
from . import keep_in_view as _compensated
from . import readings as _readers

log = logging.getLogger(__name__)

#: What ``set_state`` can change: the five settings, and the objective by its slot.
CHANGEABLE = (*SETTINGS, "objective")


def setup_readiness(handle, active_objective: dict | None, limits: dict | None) -> dict:
    """The driver's verdict on the limits, the calibration and the orientation: ready, and the issues if not.

    Workflows read only ``ready`` and ``issues``. A slot counts as calibrated
    only when its entry records the session that measured it: the bundled
    placeholder has no such record, and reporting it as "ready" would let a
    never-calibrated microscope compensate objective changes with numbers
    nobody measured on it.
    """
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    slot = (active_objective or {}).get("slotIndex")
    slot = None if slot is None else int(slot)
    origin_slot = (handle.origin.get("objective") or {}).get("slotIndex")
    origin_slot = None if origin_slot is None else int(origin_slot)
    known_slots = calibration.get("slots", [])
    measured_slots = calibration.get("measured_slots", [])
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            f"machine-specific limits are not active (got {limits}); publish this "
            "machine's measured envelope with driver_setup/limits/notebooks/set_limits.ipynb first"
        )
    if not orientation.get("measured"):
        issues.append("camera-to-stage orientation is not a measured machine value; measure and adopt one")
    if not calibration.get("loaded"):
        issues.append("objective calibration is not loaded")
    elif slot is None:
        issues.append("active objective slot is unreadable")
    elif slot not in known_slots:
        issues.append(f"active objective slot {slot} is absent from the loaded objective calibration")
    elif slot not in measured_slots:
        issues.append(
            f"active objective slot {slot} carries only shipped placeholder values, not a "
            "calibration measured on this microscope"
        )
    if calibration.get("loaded"):
        if origin_slot is None:
            issues.append(
                "the saved frame origin does not record an objective; capture the "
                "origin again with the driver's set_origin step under the current objective"
            )
        elif origin_slot not in known_slots:
            issues.append(f"run-origin objective slot {origin_slot} is absent from the loaded objective calibration")
        elif origin_slot not in measured_slots:
            issues.append(
                f"run-origin objective slot {origin_slot} carries only shipped placeholder "
                "values, not a calibration measured on this microscope"
            )
    return {
        "ready": not issues,
        "issues": issues,
        "active_objective_slot": slot,
        "origin_objective_slot": origin_slot,
        "orientation": orientation,
        "calibration": calibration,
    }


def changeable_now(handle) -> dict:
    """The changeable part as LAS X has it now: each setting's value, and the objective's slot."""
    client = handle.client
    reads = {
        "zoom": lambda: _readers.get_zoom(client)["current"],
        "scan_speed": lambda: _readers.get_scan_speed(client)["value"],
        "scan_field_rotation": lambda: _readers.get_scan_field_rotation(client)["value"],
        "line_average": lambda: _readers.get_averaging(client)["lineAverage"],
        "pinhole_airy": lambda: _readers.get_pinhole(client)["airy"]["value"],
        "objective": lambda: _readers.get_objective(client)["slotIndex"],
    }
    found = {}
    for name in CHANGEABLE:
        try:
            found[name] = reads[name]()
        except (KeyError, TypeError):
            # A reading that fails is unknown, not a stop.
            log.warning("could not read %s", name)
            found[name] = None
    return found


def get_state(handle) -> dict:
    """The instrument state: the changeable part first, then the observed report.

    ``changeable`` is the promise: exactly what :func:`set_state` reapplies.
    ``observed`` is a read-only report of identity and condition, every
    LAS X-derived value a fresh read.
    """
    handle.require_open()
    hw = _readers.get_hardware_info(handle.client) or {}
    microscope = hw.get("Microscope") or {}
    active_objective = _readers.get_objective(handle.client) or {}
    pixel_size = frame_size = image_size_px = pixel_size_um = None
    try:
        px = _readers.get_pixel_size(handle.client)
        fov = _readers.get_fov(handle.client)
        columns, rows = (int(v) for v in _readers.get_format(handle.client).split("x"))
        pixel_x, pixel_y = px[0] * 1e6, px[1] * 1e6
        if not all(math.isfinite(v) and v > 0 for v in (pixel_x, pixel_y)):
            raise ValueError("pixel size must be finite and positive")
        pixel_size = {"x": pixel_x, "y": pixel_y, "unit": "um"}
        frame_size = {"x": fov[0] * 1e6, "y": fov[1] * 1e6, "unit": "um"}
        image_size_px, pixel_size_um = _as_saved((columns, rows), (pixel_x, pixel_y), _saved_orientation(handle))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    limits = _gate.describe(handle.client)
    return {
        "changeable": changeable_now(handle),
        "observed": {
            "vendor": "leica",
            "microscope": handle.connection.get("microscope"),
            "navigator": "plain",
            "serial_number": hw.get("SerialNumber"),
            "system_type": hw.get("SystemType"),
            "stand": microscope.get("name"),
            "objectives": [[o.get("slotIndex"), o.get("objectiveNumber")] for o in (microscope.get("objectives") or [])],
            "active_objective": dict(active_objective),
            "pixel_size": pixel_size,
            "frame_size": frame_size,
            # The controller's own keys: the image size and pixel size of these
            # settings, as a picture is saved.
            "image_size_px": image_size_px,
            "pixel_size_um": pixel_size_um,
            # Which limits file governs this session (evidence, not an
            # instruction). None when the limits handshake failed.
            "limits": limits,
            "setup": setup_readiness(handle, active_objective, limits),
            # Changes this session sent that were accepted but never
            # confirmed by a read-back, newest last.
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def _as_saved(size_px, pixel_size_um, orientation) -> tuple[list, list]:
    """The image size and pixel size as a picture is saved: a quarter turn swaps width and height."""
    if orientation.swaps_axes:
        size_px, pixel_size_um = size_px[::-1], pixel_size_um[::-1]
    return list(size_px), list(pixel_size_um)


def _saved_orientation(handle):
    """The turn this connection applies to the pixels it saves (none when it has none)."""
    from ..driver_setup.orientation import Orientation

    return handle.orientation if handle.orientation is not None else Orientation()


def set_state(handle, state: dict) -> dict:
    """Apply the changeable part (``state["changeable"]``); report what was applied and what stayed unconfirmed.

    Each setting goes through its named change, and the objective through
    the change that keeps the sample in view. A setting LAS X refused, or a
    name this microscope does not have, raises; a change that was sent but
    never seen is recorded, and the rest go on.
    """
    handle.require_open()
    asked = dict((state or {}).get("changeable") or {})
    unknown = sorted(set(asked) - set(CHANGEABLE))
    if unknown:
        raise ValueError(f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}")
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)
    for name, value in asked.items():
        if value is None:
            continue
        if name == "objective":
            result = _compensated.set_objective(
                handle.client, _readers.get_objectives(handle.client), slot_index=int(value),
                translations=handle.translations,
            )
        else:
            result = changes.change(handle.client, name, value)
        if not result.get("success"):
            raise RuntimeError(f"set_state({name!r}) failed: {result.get('message')}")
        if not result.get("confirmed"):
            handle.note_unconfirmed(f"set_state: {name} -> {value}", result)
        applied[name] = value
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}
