"""What the instrument is doing now and what can be changed: its state.

The state holds the selected job, the objectives, the pixel size, the limits that govern
the session, and whether each setup step has been done. The *changeable* part is
deliberately just the selected job: the job is LAS X's unit of configuration, so
selecting it again restores the whole setup.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from typing import Any
from ..session import loaded_config as _session_state
from ..dispatcher import gate as _gate
from ..vendor_specific.primitive_handling.readings.parsing import (
    parse_tile_geometry as _parse_tile_geometry,
)
from ..vendor_specific.primitive_handling.readings import state as _readers
from ..procedures.objective_change_with_compensation import compensated_changes as _compensated
from ..session import handle as _info
from ..session.handle import ZmartHandle, job_catalog, note_unconfirmed, require_open

log = logging.getLogger(__name__)


def setup_readiness(
    handle: ZmartHandle,
    active_objective: dict | None,
    limits: dict | None,
) -> dict:
    """Driver-owned verdict for limits, calibration, and orientation readiness.

    Workflows consume only ``ready`` and ``issues``. The Leica driver owns the
    meaning of the evidence and all machine-configuration application.
    """
    loaded = _session_state.get(handle.client)
    orientation = dict((loaded.orientation_info if loaded else None) or {})
    calibration = dict((loaded.calibration_info if loaded else None) or {})
    slot = (active_objective or {}).get("slotIndex")
    slot = None if slot is None else int(slot)
    origin_slot = (handle.origin.get("objective") or {}).get("slotIndex")
    origin_slot = None if origin_slot is None else int(origin_slot)
    # A slot only counts as calibrated when its entry records the session that
    # measured it. A missing calibration file is seeded from the repository's
    # bundled placeholder, which has no such provenance — reporting those
    # values as "ready" would let a never-calibrated microscope compensate
    # objective changes with numbers nobody measured on it.
    known_slots = calibration.get("slots", [])
    measured_slots = calibration.get("measured_slots", [])
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            f"machine-specific limits are not active (got {limits}); publish this "
            "machine's measured envelope with driver_setup/limits/notebooks/set_limits.ipynb first"
        )
    if not orientation.get("measured"):
        issues.append(
            "camera-to-stage orientation is not a measured machine value; "
            "run and adopt set_orientation"
        )
    if not calibration.get("loaded"):
        issues.append("objective calibration is not loaded")
    elif slot is None:
        issues.append("active objective slot is unreadable")
    elif slot not in known_slots:
        issues.append(
            f"active objective slot {slot} is absent from the loaded objective calibration"
        )
    elif slot not in measured_slots:
        issues.append(
            f"active objective slot {slot} carries only shipped placeholder values, not a "
            "calibration measured on this microscope; run and adopt the "
            "calibrate_objective_pair notebook"
        )
    if calibration.get("loaded"):
        if origin_slot is None:
            issues.append(
                "the saved frame origin does not record an objective; capture the "
                "origin again with the driver's set_origin step under the current objective"
            )
        elif origin_slot not in known_slots:
            issues.append(
                f"run-origin objective slot {origin_slot} is absent from the loaded "
                "objective calibration"
            )
        elif origin_slot not in measured_slots:
            issues.append(
                f"run-origin objective slot {origin_slot} carries only shipped placeholder "
                "values, not a calibration measured on this microscope; run and adopt the "
                "calibrate_objective_pair notebook"
            )
    return {
        "ready": not issues,
        "issues": issues,
        "active_objective_slot": slot,
        "origin_objective_slot": origin_slot,
        "orientation": orientation,
        "calibration": calibration,
    }


def get_state(handle: ZmartHandle) -> dict:
    """The instrument state: the changeable part first, then the observed report.

    ``changeable`` is the promise — exactly what :func:`set_state` reapplies.
    It is deliberately just the selected job: the job *is* the unit of
    configuration in LAS X, so reapplying the selection round-trips the whole
    setup. ``observed`` is a read-only report of identity and condition — the
    connection identity, the hardware-reported serial number and system type
    (simulator vs. real), the stand, the turret configuration (slot →
    objectiveNumber), the full selected-job record, its current XY pixel size,
    the job catalog, and the provenance of the function limits governing this
    session. All LAS X-derived values are fresh reads.
    """
    require_open(handle)
    hw = _readers.get_hardware_info(handle.client) or {}
    microscope = hw.get("Microscope") or {}
    selected = _readers.get_selected_job(handle.client) or {}
    if not selected.get("Name"):
        # A reading that fails is unknown, not a stop: the job is reported
        # as None, and set_state with a job name will select it afresh.
        log.warning("could not determine the selected LAS X job")
        selected = {}
    settings = (
        _readers.get_job_settings(handle.client, selected["Name"])
        if selected.get("Name")
        else {}
    ) or {}
    try:
        geometry = _parse_tile_geometry(settings)
        pixel_x = float(geometry["pixel_w_um"])
        pixel_y = float(geometry["pixel_h_um"])
        if not all(math.isfinite(value) and value > 0 for value in (pixel_x, pixel_y)):
            raise ValueError("pixel size must be finite and positive")
        pixel_size = {
            "x": pixel_x,
            "y": pixel_y,
            "unit": "um",
        }
        # The field of view, from the job's imageSize: parsed here anyway to
        # get the pixel size, and the only measured answer to how much sample
        # one frame covers.
        frame_size = {
            "x": float(geometry["tile_w_um"]),
            "y": float(geometry["tile_h_um"]),
            "unit": "um",
        }
    except (KeyError, TypeError, ValueError):
        pixel_size = None
        frame_size = None
    active_objective = settings.get("objective")
    normal, autofocus = job_catalog(handle)
    limits = _gate.describe(handle.client)
    return {
        "changeable": {"job": selected.get("Name")},
        "observed": {
            "vendor": "leica",
            "microscope": handle.connection.get("microscope"),
            "serial_number": hw.get("SerialNumber"),
            "system_type": hw.get("SystemType"),
            "stand": microscope.get("name"),
            "objectives": [
                [o.get("slotIndex"), o.get("objectiveNumber")]
                for o in (microscope.get("objectives") or [])
            ],
            "job": dict(selected),
            "active_objective": dict(active_objective or {}),
            "pixel_size": pixel_size,
            "frame_size": frame_size,
            "jobs": normal,
            "autofocus_jobs": autofocus,
            # Which function-limits file governs this session (evidence, not
            # an instruction): path, source tag, is_fallback — reported by
            # the commands-layer gate. None when the limits handshake failed
            # (every mutating command underneath is then refusing).
            "limits": limits,
            "setup": setup_readiness(handle, active_objective, limits),
            # Changes this session sent that were accepted but never
            # confirmed by a readback, newest last. The driver carried on;
            # a workflow that cares can look here.
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def set_state(handle: ZmartHandle, state: dict) -> dict:
    """Apply the changeable part; report what stuck.

    ``observed`` is a report, never an instruction — it is not read here
    (operator decision, 2026-07-02; an identity gate returns only if the
    changeable part ever grows beyond the low-risk job selection). The job
    must still exist on this instrument before it is reapplied: the catalog
    changes legitimately, so only the REFERENT is guarded — with an error
    that lists what is available.
    """
    require_open(handle)
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)
    job = (state.get("changeable") or {}).get("job")
    # When the selected job cannot be read, the one asked for is selected
    # anyway: selecting is harmless to repeat.
    if job and job != _info.selected_job_name(handle):
        normal, autofocus = job_catalog(handle)
        names = [j.get("Name") for j in normal if j.get("Name")]
        if job in (j.get("Name") for j in autofocus):
            raise ValueError(
                f"{job!r} is an autofocus job of Navigator Expert; "
                "the driver does not select autofocus jobs"
            )
        if job not in names:
            raise ValueError(
                f"job {job!r} no longer exists on this instrument (available: {names})"
            )
        result = _compensated.select_job(handle.client, job)
        if not result.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {result}")
        if not result.get("confirmed"):
            note_unconfirmed(handle, f"set_state: select_job('{job}')", result)
        applied["job"] = job
    # What was applied, and what was sent but could not be confirmed by a
    # readback (the selection went ahead; the workflow is told, not stopped).
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}
