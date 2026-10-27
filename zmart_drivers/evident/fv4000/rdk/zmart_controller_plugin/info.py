"""A description of the microscope and a short report of what the connection stands on.

``get_info`` answers in plain words: what the microscope is, what is set up,
where files are saved, and how the connection stands.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path

from ..dispatcher import limits as _gate
from ..driver_setup import store as _machine
from ..methods import SET_UP
from ..procedures import readings as _readers
from ..procedures.absolute_coordinate_system import get_xyz
from ..procedures.acquire import try_


def get_info(handle) -> dict:
    """The description, what is set up, and the connection's standing, read live."""
    handle.require_open()
    root = _where_it_saves(handle)
    return {
        "setup": _setup(handle),
        "description": _described(handle),
        "chooses_its_own_folder": False,
        "session_hash6": handle.hash6,
        "connection_status": _connection_status(handle, root),
    }


# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from this.
DESCRIPTION = """An Evident FLUOVIEW FV4000 confocal microscope, driven through FLUOVIEW's Remote Development Kit (an XML-RPC interface).

Stage: x and y move the motorised stage; z is the focus motor (FLUOVIEW's "Z stage"). A piezo, when the microscope has one, is read and reported beside z but not moved by the driver. All positions are in micrometres from the origin saved in this microscope's setup (FLUOVIEW's own stage coordinates until one is saved). When the objective changes, the driver applies the objective calibration, so the origin stays on the same spot of the sample. {travel}

Settings (the changeable part of the state): objective (the nosepiece slot, counted from 0), scanner_type (GALVANO or RESONANT), scanner_mode (ONEWAY or ROUNDTRIP), the z-stack (z_stack_enabled, z_stack_slices, z_stack_step_um, z_stack_start_um and z_stack_end_um, the last two counted from FLUOVIEW's own Z origin), the time lapse (time_lapse_enabled, time_lapse_repeat, time_lapse_interval_s), channels (per detector channel: enabled, hv, gain, offset, digital_gain and its lasers) and lasers (per laser line: enabled and intensity in percent). load_settings_from takes the path of a picture FLUOVIEW saved and recalls the whole setup it was taken with; that is the FV4000's way of choosing a stored imaging setup. {objectives} {lasers}

Acquiring has one trigger, full: it runs the imaging set up in FLUOVIEW (one picture, a z-stack or a time series), reads the picture back from FLUOVIEW, and saves it as one OME-Zarr store in the output folder acquire is given, or else under this microscope's data location, named by the acquisition type and the position label. A multi-area time lapse set up in FLUOVIEW is not captured through the driver yet."""


def _described(handle) -> str:
    state = _gate.state_for(handle.client)
    travel = "No limits are loaded in this session, so every move is refused."
    if state is not None and state.stage_cfg is not None:
        stage = state.stage_cfg["stage_um"]
        travel = (
            f"This session's limits let the stage travel x {stage['x'][0]:g} to {stage['x'][1]:g}, "
            f"y {stage['y'][0]:g} to {stage['y'][1]:g} and z {stage['z'][0]:g} to {stage['z'][1]:g} um, in FLUOVIEW's coordinates."
        )
    objectives = _readers.get_objectives(handle.client) or []
    said = (
        ", ".join(f"{o.get('slot')}: {o.get('name') or 'unnamed'}" for o in objectives)
        or "not reported by FLUOVIEW"
    )
    lasers = _readers.get_lasers(handle.client) or []
    lines = (
        ", ".join(
            f"{laser.get('id')} ({laser.get('wavelength_nm')} nm)" for laser in lasers
        )
        or "not reported by FLUOVIEW"
    )
    return DESCRIPTION.format(
        travel=travel,
        objectives=f"Objectives on the nosepiece, as slot: name: {said}.",
        lasers=f"Laser lines: {lines}.",
    )


def _setup(handle) -> dict:
    """Each thing set up once on this microscope, whether it is done, and in words what it holds or what to do."""
    limits = _gate.describe(handle.client) or {}
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    published = limits.get("source") == "machine" and not limits.get("is_fallback")
    origin_saved = try_(_machine.MACHINE.read_origin) is not None
    measured = calibration.get("measured_slots") or []
    return {
        "limits": {
            "done": published,
            "says": f"published: {limits.get('path')}"
            if published
            else "not published: the bundled placeholder limits govern every move; measure this microscope's limits with driver_setup/limits/notebooks/set_limits.ipynb",
        },
        "orientation": {
            "done": bool(orientation.get("measured")),
            "says": "measured: saved pictures are turned to the stage's axes"
            if orientation.get("measured")
            else "not measured: saved pictures are kept as FLUOVIEW scanned them; run driver_setup/orientation/notebooks/set_orientation.ipynb",
        },
        "calibration": {
            "done": bool(calibration.get("loaded")) and bool(measured),
            "says": f"measured for objective slots {sorted(measured)}"
            if calibration.get("loaded") and measured
            else "no measured objective calibration: a change of objective cannot keep the sample in place; run driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb",
        },
        "origin": {
            "done": origin_saved,
            "says": "saved: positions count from it"
            if origin_saved
            else "not saved: positions are FLUOVIEW's own stage coordinates; run the driver's set_origin step",
        },
        "data_location": {
            "done": handle.data_location is not None,
            "says": f"{handle.data_location}, as OME-Zarr"
            if handle.data_location is not None
            else SET_UP,
        },
        "frame": {
            "done": bool(handle.frames),
            "says": f"learned for objective slots {sorted(handle.frames)}: the image size and pixel size of the newest picture under each"
            if handle.frames
            else "not learned: FLUOVIEW does not report the image size or pixel size of its settings, so take one picture under each objective and save it; the driver remembers them from then on",
        },
    }


def _where_it_saves(handle) -> Path | None:
    try:
        return handle.output_root()
    except RuntimeError:
        return None


def _connection_status(handle, root: Path | None) -> dict:
    """What a session stands on, each check in words; a failure names itself."""
    limits = _gate.describe(handle.client)
    calibration = handle.calibration_info or {}

    def stage() -> str:
        at = get_xyz(handle)
        return f"x {at['x']['position']:.0f} · y {at['y']['position']:.0f} · z {at['z']['position']:.1f} um"

    if not calibration.get("enabled", True):
        calibration_said = "switched off"
    elif calibration.get("loaded"):
        calibration_said = f"loaded for slots {calibration.get('measured_slots')}"
    else:
        calibration_said = (
            "failed — no usable calibration, so objective changes are refused"
        )
    return {
        "fluoview": f"answering, version {handle.fluoview_version}"
        if try_(lambda: _readers.ping(handle.client))
        else "failed — no answer",
        "limits": f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
        if limits
        else "failed — none loaded, every move refused",
        "calibration": calibration_said,
        "stage": try_(stage) or "failed — no reading",
        "output root": str(root) if root is not None else f"not set up: {SET_UP}",
    }
