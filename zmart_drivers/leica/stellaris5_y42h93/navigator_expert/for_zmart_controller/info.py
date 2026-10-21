"""A description of the microscope and a short report of what the connection stands on.

``get_info`` answers in plain words: what the microscope is, what is set up, where files
are saved, and how the connection stands.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path
from ..session import loaded_config as _session_state
from ..driver_setup import store as _machine
from ..dispatcher import gate as _gate
from ..vendor_specific.primitive_handling.readings import state as _readers
from ..session import handle as _info
from ..procedures.absolute_coordinate_system.frame import canvas, get_xyz
from ..session.handle import ZmartHandle, job_catalog, require_open, try_


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
            f" The autofocus jobs ({', '.join(af_names)}) are Navigator Expert's own; "
            "the driver does not select them."
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
                "microscope's limits with driver_setup/limits/notebooks/set_limits.ipynb"
            ),
        },
        "orientation": {
            "done": bool(orientation.get("measured")),
            "says": "measured: saved pictures are turned to the stage's axes"
            if orientation.get("measured")
            else (
                "not measured: saved pictures are kept as the camera sees them; run "
                "driver_setup/orientation/notebooks/set_orientation.ipynb"
            ),
        },
        "calibration": {
            "done": bool(calibration.get("loaded")) and bool(measured_slots),
            "says": f"{calibration.get('name') or 'loaded'}: measured for objective slots {sorted(measured_slots)}"
            if calibration.get("loaded") and measured_slots
            else (
                "no measured objective calibration: a change of objective cannot keep the "
                "sample in place; run driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
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
                f"{handle.data_location}, as OME-Zarr"
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
