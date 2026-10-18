"""What the instrument is, what can be changed, and the procedures it offers.

The commands here answer questions about the whole microscope rather than
about the stage or one picture: its state (the selected job, the objectives,
the pixel size, the limits that govern the session, and whether each setup
step has been done), the description of the microscope in plain words, the
named procedures it can run (backlash take-up, parking the z-galvo,
autofocus), and a short report of what the connection stands on.

The *changeable* part of the state is deliberately just the selected job:
the job is LAS X's unit of configuration, so selecting it again restores the
whole setup.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from ..configuration import session_state as _session_state
from ..configuration import store as _machine
from ..dispatcher import gate as _gate
from ..vendor_interface.parsing import parse_tile_geometry as _parse_tile_geometry
from ..vendor_interface.primitive_handling import state as _readers
from ..vendor_interface.primitive_handling.profiles import IMAGE_SAVE
from . import acquire as _capture
from . import backlash as _motion
from . import changes as _commands
from . import session as _info
from .frame import canvas, delta_or_warn, get_xyz, hardware_snapshot
from .session import ZmartHandle, job_catalog, note_unconfirmed, require_open, try_

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
            "machine's measured envelope with configuration/limits/notebooks/set_limits.ipynb first"
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
                f"{job!r} is an autofocus job — run it via the 'autofocus' "
                "procedure (run_procedure), not as state"
            )
        if job not in names:
            raise ValueError(
                f"job {job!r} no longer exists on this instrument (available: {names})"
            )
        result = _commands.select_job(handle.client, job)
        if not result.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {result}")
        if not result.get("confirmed"):
            note_unconfirmed(handle, f"set_state: select_job('{job}')", result)
        applied["job"] = job
    # What was applied, and what was sent but could not be confirmed by a
    # readback (the selection went ahead; the workflow is told, not stopped).
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


def get_procedures(handle: ZmartHandle) -> dict:
    """The named procedures this instrument offers (discover-then-apply)."""
    require_open(handle)
    _, autofocus = job_catalog(handle)
    return {
        "backlash_takeup": {
            "description": "pin the XY leadscrew slack at the current position (+X +Y approach)"
        },
        "zero_z_galvo": {
            "description": "park the z-galvo at 0 without changing the focus "
            "(its offset is transferred onto z-wide), freeing the galvo's "
            "full travel for a following z-stack"
        },
        "autofocus": {
            "description": "run a LAS X autofocus job (capture only, nothing saved); "
            "restores the previously selected job and returns the focus readback",
            "args": ["job"],
            "jobs": [j["Name"] for j in autofocus if j.get("Name")],
        },
    }


def run_procedure(handle: ZmartHandle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; report what ran."""
    require_open(handle)
    name = procedure.get("name")
    if name == "backlash_takeup":
        _motion.correct_backlash(handle.client)
        return {"ran": dict(procedure)}
    if name == "zero_z_galvo":
        return {"ran": dict(procedure), **_zero_z_galvo(handle)}
    if name == "autofocus":
        return _run_autofocus(handle, procedure)
    raise ValueError(f"unknown procedure {name!r}")


def _zero_z_galvo(handle: ZmartHandle) -> dict:
    """Park the z-galvo at 0 while keeping the focus.

    A pure re-split of the focus sum between the two drives: z-wide
    absorbs the galvo's current offset FIRST — so a refused z-wide leg
    (limits) aborts with nothing moved — and only then does the galvo
    drive to 0. The frame z is unchanged by construction, and the galvo's
    full travel becomes available for whatever comes next. Both legs go
    through the checked ``move_z`` door; a refused or failed leg raises, a
    leg that could not be confirmed is recorded and the procedure carries on.
    """
    snap = hardware_snapshot(handle)
    offset = snap["z_galvo_um"]
    if offset == 0.0:
        return {"transferred_um": 0.0}
    for z_mode, target in (("zwide", snap["z_wide_um"] + offset), ("galvo", 0.0)):
        result = _commands.move_z(
            handle.client, snap["job"], target, unit="um", z_mode=z_mode
        )
        if not result.get("success"):
            raise RuntimeError(f"zero_z_galvo: move_z ({z_mode}) failed: {result}")
        if not result.get("confirmed"):
            note_unconfirmed(
                handle, f"zero_z_galvo: move_z ({z_mode}) to {target}", result
            )
    return {"transferred_um": offset}


def _run_autofocus(handle: ZmartHandle, procedure: dict) -> dict:
    """Select the autofocus job, run it capture-only, restore the selection.

    ``job`` names the autofocus job; it may be omitted when the instrument
    has exactly one. Nothing is saved — the result is the focus readback
    right after the run (before the selection is restored), in both
    hardware and frame terms. Like every capture it never touches the
    scanning template: positions are never made in the Navigator Expert.
    """
    _, autofocus = job_catalog(handle)
    names = [j["Name"] for j in autofocus if j.get("Name")]
    if not names:
        raise RuntimeError("no autofocus job exists on this instrument")
    job = procedure.get("job")
    if job is None:
        if len(names) > 1:
            raise ValueError(
                f"multiple autofocus jobs; pass 'job' (available: {names})"
            )
        job = names[0]
    elif job not in names:
        raise ValueError(f"{job!r} is not an autofocus job (available: {names})")

    original = _info.selected_job_name(handle)
    if job != original:
        selected = _commands.select_job(handle.client, job)
        if not selected.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {selected}")
        if not selected.get("confirmed"):
            note_unconfirmed(handle, f"autofocus: select_job('{job}')", selected)
    try:
        acq = _capture.acquire(handle.client, job)
        # Read the focus result BEFORE restoring the selection: restoring
        # could reposition (jobs own objective state).
        snap = hardware_snapshot(handle)
    finally:
        if original is None:
            log.warning(
                "the job selected before autofocus was unknown, so none is restored"
            )
        elif job != original:
            restored = _commands.select_job(handle.client, original)
            if not restored.get("success"):
                log.warning(
                    "could not restore job %r after autofocus: %s", original, restored
                )
            elif not restored.get("confirmed"):
                note_unconfirmed(
                    handle, f"autofocus: restore select_job('{original}')", restored
                )
    focus = snap["z_wide_um"] + snap["z_galvo_um"]
    dt = delta_or_warn(handle, snap)
    return {
        "ran": "autofocus",
        "job": job,
        "focus_um": focus,
        "frame_z_um": focus - handle.origin["z_focus_um"] - dt[2],
        "duration_s": acq.finished_at - acq.started_at,
    }


# =============================================================================
# Live setup information and registration
# =============================================================================


def get_info(handle: ZmartHandle) -> dict:
    """Read the session's setup and resolved output root live.

    The LAS X scanning template is never read: positions are never made in
    the Navigator Expert, so there are no tile or focus positions to report,
    and answering never saves the experiment.
    """
    require_open(handle)
    selected = _info.selected_job_name(handle)
    root = _where_it_saves(handle)
    return {
        "setup": _setup(handle),
        "description": _described(handle),
        # The driver writes every file itself, from LAS X's cache, so it
        # saves in whatever output_folder acquire is given.
        "chooses_its_own_folder": False,
        "selected_job": selected,
        "client": handle.connection.get("client"),
        "session_hash6": handle.hash6,
        "canvas": canvas(handle),
        "connection_status": _connection_status(handle, root),
    }


# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from this.
# It says what the other answers cannot. The parts in braces are filled in by
# _described() from what this session has loaded and what LAS X reports, so
# the description never promises more than the driver allows.
DESCRIPTION = """A Leica STELLARIS 5 confocal microscope, driven through LAS X and its Navigator Expert (CAM) interface.

Stage: x and y move the motorised stage; z is the focus. The focus has two drives: "z-wide", the focus drive for long moves, and "z-galvo", a fast drive for small steps. The z position reported is their sum, so it reads the same whichever drive made the move. All positions are in micrometres from the origin saved in this microscope's setup (plain stage coordinates until one is saved). When the objective changes, the driver applies the objective calibration, so the origin stays on the same spot of the sample. {galvo}

Settings (the changeable part of the state): job is the name of a LAS X job. A job holds the whole imaging setup: objective, lasers, detectors, zoom and z-stack. Choosing a job applies all of it at once; set_state refuses a job that does not exist. {jobs}

Objectives on the turret, as slot: LAS X objective number: {objectives}.

Acquiring has one trigger, full: it runs the selected LAS X job and saves what LAS X captured in the output folder acquire is given, or else under this microscope's data location, each file named by the acquisition type and the position label it was given, then the time point, the channel and the plane."""


def _described(handle: ZmartHandle) -> str:
    """The description, filled in from the loaded limits and what LAS X reports now."""
    state = _gate.state_for(handle.client)
    galvo_range = None
    if state is not None and state.stage_cfg is not None:
        galvo_range = state.stage_cfg["stage_um"].get("z_galvo")
    galvo = (
        f"This microscope's limits let the z-galvo travel from {galvo_range[0]:g} to "
        f"{galvo_range[1]:g} um around its own zero."
        if galvo_range
        else "No limits are loaded in this session, so every move is refused."
    )
    normal, autofocus = job_catalog(handle)
    names = [j["Name"] for j in normal if j.get("Name")]
    af_names = [j["Name"] for j in autofocus if j.get("Name")]
    jobs = f"The jobs on this microscope now: {', '.join(names) or 'none'}."
    if af_names:
        jobs += (
            f" The autofocus jobs ({', '.join(af_names)}) are not settings; run them "
            "with the autofocus procedure."
        )
    hardware = _readers.get_hardware_info(handle.client) or {}
    turret = (hardware.get("Microscope") or {}).get("objectives") or []
    objectives = (
        ", ".join(f"{o.get('slotIndex')}: {o.get('objectiveNumber')}" for o in turret)
        or "not reported by LAS X"
    )
    return DESCRIPTION.format(galvo=galvo, jobs=jobs, objectives=objectives)


def _setup(handle: ZmartHandle) -> dict:
    """Each thing set up once on this microscope, whether it is done, and in words what it holds or what to do."""
    limits = _gate.describe(handle.client) or {}
    loaded = _session_state.get(handle.client)
    orientation = dict((loaded.orientation_info if loaded is not None else None) or {})
    calibration = dict((loaded.calibration_info if loaded is not None else None) or {})
    published = limits.get("source") == "machine" and not limits.get("is_fallback")
    origin_saved = try_(_machine.MACHINE.read_origin) is not None
    measured_slots = calibration.get("measured_slots") or []
    return {
        "limits": {
            "done": published,
            "says": f"published: {limits.get('path')}"
            if published
            else (
                "not published: the bundled defaults govern every move; publish this "
                "microscope's limits with configuration/limits/notebooks/set_limits.ipynb"
            ),
        },
        "orientation": {
            "done": bool(orientation.get("measured")),
            "says": "measured: saved pictures are turned to the stage's axes"
            if orientation.get("measured")
            else (
                "not measured: saved pictures are kept as the camera sees them; run "
                "configuration/image_stage_registration/notebooks/set_orientation.ipynb"
            ),
        },
        "calibration": {
            "done": bool(calibration.get("loaded")) and bool(measured_slots),
            "says": f"{calibration.get('name') or 'loaded'}: measured for objective slots {sorted(measured_slots)}"
            if calibration.get("loaded") and measured_slots
            else (
                "no measured objective calibration: a change of objective cannot keep the "
                "sample in place; run configuration/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
            ),
        },
        "origin": {
            "done": origin_saved,
            "says": "saved: positions count from it"
            if origin_saved
            else (
                "not saved: positions are plain stage coordinates; run the driver's set_origin step"
            ),
        },
        "data_location": {
            "done": handle.data_location is not None,
            "says": (
                f"{handle.data_location}, as {IMAGE_SAVE.format}"
                if handle.data_location is not None
                else _info.SET_UP
            ),
        },
    }


def _where_it_saves(handle: ZmartHandle) -> Path | None:
    """The folder this session saves under, or None when there is none yet."""
    try:
        return _info.output_root(handle)
    except RuntimeError:
        return None


def _calibration_status(calibration: dict | None) -> str:
    """The Connect card's one line about the objective calibration.

    The session always holds a calibration record, even when loading failed, so
    the line is decided by what the record says was loaded. A calibration that
    could not be used means objective changes are refused, and the card says so.
    """
    if calibration is None:
        return "failed — none found"

    if not calibration.get("enabled", True):
        return "switched off"

    if calibration.get("loaded"):
        return calibration.get("name") or "found"

    return "failed — no usable calibration, so objective changes are refused"


def _connection_status(handle: ZmartHandle, root: Path | None) -> dict:
    """What a session stands on, from what the driver already holds; a
    failure names itself."""
    limits = _gate.describe(handle.client)
    loaded = _session_state.get(handle.client)
    calibration = loaded.calibration_info if loaded is not None else None

    def stage() -> str:
        at = get_xyz(handle)
        return (
            f"x {at['x']['position']:.0f} · y {at['y']['position']:.0f} · "
            f"z {at['z']['position']:.1f} um"
        )

    return {
        "api": "answering"
        if try_(lambda: _readers.ping(handle.client))
        else "failed — no answer",
        "limits": (
            f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
            if limits
            else "failed — none loaded, every move refused"
        ),
        "calibration": _calibration_status(calibration),
        "stage": try_(stage) or "failed — no reading",
        "output root": str(root) if root is not None else f"not set up: {_info.SET_UP}",
    }
