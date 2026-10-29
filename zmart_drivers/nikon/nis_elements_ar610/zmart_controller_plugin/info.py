"""A description of the microscope and a short report of what the connection stands on.

``get_info`` answers in plain words: what the microscope is, what is set up,
where files are saved, and how the connection stands.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path

from ..dispatcher import limits as _gate
from ..methods import SET_UP
from ..procedures import readings as _readers
from ..procedures.absolute_coordinate_system import get_xyz
from ..procedures.acquire import try_

# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from
# this. The parts in braces are filled in from what NIS-Elements reports,
# so the description never promises more than the driver allows.
DESCRIPTION = """A Nikon Ti2 widefield microscope, driven through NIS-Elements AR 6.10 and the nis-bridge.

Stage: x and y move the motorised stage; z is the focus drive. All positions are in micrometres from the origin saved in this microscope's setup (plain NIS-Elements stage coordinates until one is saved). When the objective changes, the driver applies the objective calibration, so the origin stays on the same spot of the sample. {limits}

Settings (the changeable part of the state): objective is a nosepiece slot, counted from 1; channel is the name of a NIS-Elements optical configuration, which holds one channel's filter cube, light source and exposure (NIS cannot report which one is selected, so the driver reports the one it selected last); exposure_ms is the camera exposure in milliseconds, which the camera may round; pfs switches the Perfect Focus System on or off, True or False, and pfs_offset sets the plane it keeps, {{"offset": NIS units, "dish": "glass" or "plastic"}}. The devices can be set too, each only where it differs from now, and all optional: light_path {{"condenser", "apertures": {{name: size}}, "zoom", "analyzer"}}, port {{"port", "disk_shutter", "disk_speed_rpm"}}, filters {{changer: position}}, camera {{"format", "roi", "averaging", "integration"}}, lights {{name: {{"on", "voltage", "iris"}}}}, lasers {{laser: {{"wavelength in nm": {{"on", "power"}}}}}} and shutters {{name: True for open}}. A setting asked for a device overrides what the channel set. {objectives} {channels}

Acquiring has one trigger, snap: one picture with the settings as they are, saved as an OME-Zarr store in the output folder acquire is given, or else under this microscope's data location, named by the acquisition type and the position label. A z-stack or a time series is a series of moves and snaps by the workflow.

Procedures: autofocus runs NIS's image-based focus sweep; lock_focus switches the Perfect Focus System; save_data writes the captures taken without saving; set_origin makes the current position the zero point; safe_state switches off every light source; escape lowers the objective and refocus brings it back; live starts or stops the live view; auto_exposure lets NIS choose the exposure; move_piezo_z and center_piezo_z move the piezo focus drive; edit_optical_configuration, copy_optical_configuration and delete_optical_configuration change the saved channels."""


def get_info(handle) -> dict:
    """What the controller's ``get_info`` answers, read live."""
    handle.require_open()
    root = _where_it_saves(handle)
    return {
        "setup": _setup(handle),
        "description": _described(handle),
        # The driver writes every store itself, so it saves in whatever
        # output_folder acquire is given.
        "chooses_its_own_folder": False,
        "session_hash6": handle.hash6,
        "connection_status": _connection_status(handle, root),
    }


def _described(handle) -> str:
    """The description, filled in from the loaded limits and what NIS-Elements reports now."""
    limits = _gate.describe(handle.client)
    if limits:
        envelope = limits["envelope_um"]
        limits_text = "The stage may travel x {} to {}, y {} to {} and z {} to {} um, in NIS-Elements' own coordinates.".format(
            *(f"{bound:g}" for axis in ("x", "y", "z") for bound in envelope[axis])
        )
    else:
        limits_text = "No limits are loaded in this session, so every move is refused."
    objectives = _readers.get_objectives(handle.client) or {"objectives": {}}
    listed = (
        ", ".join(
            f"{slot}: {name}" for slot, name in sorted(objectives["objectives"].items())
        )
        or "none reported"
    )
    channels = (
        ", ".join(_readers.get_optical_configurations(handle.client) or [])
        or "none yet; make one in NIS-Elements"
    )
    return DESCRIPTION.format(
        limits=limits_text,
        objectives=f"Objectives on the nosepiece, as slot: name: {listed}.",
        channels=f"The optical configurations on this microscope now: {channels}.",
    )


def _setup(handle) -> dict:
    """Each thing set up once on this microscope, whether it is done, and in words what it holds or what to do."""
    limits = _gate.describe(handle.client) or {}
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    published = limits.get("source") == "machine" and not limits.get("is_fallback")
    measured_slots = calibration.get("measured_slots") or []
    return {
        "limits": {
            "done": published,
            "says": f"published: {limits.get('path')}"
            if published
            else (
                "not published: the bundled defaults govern every move; publish this microscope's "
                "limits with driver_setup/limits/notebooks/set_limits.ipynb"
            ),
        },
        "orientation": {
            "done": bool(orientation.get("measured")),
            "says": f"measured: a {orientation.get('rotation_deg')} degree turn"
            f"{', mirrored' if orientation.get('mirrored') else ''}; saved pictures follow the stage's axes"
            if orientation.get("measured")
            else (
                "not measured: saved pictures are kept as the camera sees them; run "
                "driver_setup/orientation/notebooks/set_orientation.ipynb"
            ),
        },
        "calibration": {
            "done": bool(measured_slots),
            "says": f"measured for nosepiece slots {measured_slots}"
            if measured_slots
            else (
                "no objective pair calibrated: a change of objective cannot keep the sample in view; run "
                "driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
            ),
        },
        "origin": {
            "done": handle.origin.get("objective") is not None,
            "says": "saved: positions count from it"
            if handle.origin.get("objective") is not None
            else "not saved: positions are plain stage coordinates; run the driver's set_origin()",
        },
        "data_location": {
            "done": handle.data_location is not None,
            "says": f"{handle.data_location}, as OME-Zarr"
            if handle.data_location is not None
            else SET_UP,
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

    answered = try_(lambda: _readers.ping(handle.client))
    return {
        "bridge": f"answering: bridge {answered.get('bridge')}, NIS-Elements {answered.get('nis')}"
        if answered
        else "failed — no answer; is start_bridge.mac running in NIS-Elements?",
        "limits": (
            f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
            if limits
            else "failed — none loaded, every move refused"
        ),
        "calibration": (
            f"measured for slots {calibration.get('measured_slots')}"
            if calibration.get("measured_slots")
            else "switched off"
            if not calibration.get("enabled", True)
            else "none measured, so objective changes are refused"
        ),
        "stage": try_(stage) or "failed — no reading",
        "output root": str(root) if root is not None else f"not set up: {SET_UP}",
    }
