"""A description of the microscope, what is set up on it, and a short report of what the connection stands on.

``get_info`` answers in plain words. The description is for whoever drives
the microscope (a person, a notebook, or the ZMART AI agent, which builds its
picture of the instrument from it) and says what the other answers cannot.
``setup`` lists each thing set up once on this microscope, done or not.
``connection_status`` is the operator's Connect step, one line per check.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``zmart_controller_plugin/info.py``.
"""

from __future__ import annotations

from pathlib import Path

from ..dispatcher import limits as gate
from ..driver_setup import pixel_size
from ..methods import SET_UP
from ..procedures import readings
from ..procedures.absolute_coordinate_system import canvas, get_xyz


def get_info(handle) -> dict:
    """The description, the setup, where files are saved, and the connection's status, read now."""
    handle.require_open()
    root = where_it_saves(handle)
    return {
        "description": described(handle),
        "setup": setup(handle),
        # The driver converts every picture itself, so it saves in whatever
        # output_folder acquire is given, and nowhere else.
        "chooses_its_own_folder": False,
        "session_hash6": handle.hash6,
        "canvas": canvas(handle),
        "connection_status": connection_status(handle, root),
    }


def try_(read):
    """*read()*, or None when it raises: one missing answer does not hide the others."""
    try:
        return read()
    except Exception:  # noqa: BLE001 -- the report says "failed" instead
        return None


# The parts in braces are filled in by described() from what NIS reports now,
# so the description never promises more than this microscope has.
DESCRIPTION = """A Nikon microscope driven through NIS-Elements AR {version}, by its macro functions, which a small program inside NIS (the ZMART bridge) calls on the driver's behalf.

Stage: x and y move the motorised stage; z is the focus drive. Every position is in micrometres from the origin saved on this microscope (NIS's own stage coordinates until one is saved). {limits} Perfect Focus: {pfs} While Perfect Focus holds the focus, the driver refuses to move z, so as not to fight it.

Settings (the changeable part of the state): objective_position is the nosepiece position, counted from 1, and optical_configuration is the name of an optical configuration of NIS. An optical configuration holds the light path, filters, light source and camera settings, and may bring its own objective; set_state applies the objective first and the configuration second. Changing the objective does not move the stage to keep the sample in view: positions shift by the difference between the two objectives. The optical configurations in NIS now: {configurations}.

Objectives on the nosepiece, as position: name: {objectives}.

Acquiring has one trigger, snap: one picture with the settings as they are, taken into a new NIS document, saved by NIS as a TIFF, and then converted by the driver into one OME-Zarr store in the output folder acquire is given (or else this microscope's data location), named by the acquisition type and the position label. The pixel size is NIS's own calibration of the objective; a picture taken with an uncalibrated objective cannot be placed on the sample and is not saved."""


def described(handle) -> str:
    """The description, filled in from the loaded limits and what NIS reports now."""
    state = gate.state_for(handle.client)
    stage = (
        None
        if state is None or state.stage_cfg is None
        else state.stage_cfg["stage_um"]
    )
    if stage:
        limits = (
            "The limits in force let x, y and z travel within "
            + ", ".join(
                f"{axis[0]} {low:g} to {high:g} um"
                for axis, (low, high) in stage.items()
                if axis.endswith("_um")
            )
            + ", in NIS's stage coordinates."
        )
    else:
        limits = "No limits are loaded in this session, so every move is refused."
    focus = try_(lambda: readings.pfs(handle.client))
    pfs = (
        "not present."
        if not focus or not focus.get("present")
        else f"present, {'on' if focus.get('on') else 'off'} now."
    )
    turret = try_(lambda: readings.nosepiece(handle.client)) or {}
    objectives = (
        ", ".join(
            f"{p}: {name}"
            for p, name in sorted((turret.get("objectives") or {}).items())
        )
        or "not reported by NIS"
    )
    configurations = (
        ", ".join(try_(lambda: readings.optical_configurations(handle.client)) or [])
        or "none"
    )
    return DESCRIPTION.format(
        version=handle.nis_version or "7",
        limits=limits,
        pfs=pfs,
        configurations=configurations,
        objectives=objectives,
    )


def setup(handle) -> dict:
    """Each thing set up once on this microscope: whether it is done, and in words what it holds or what to do."""
    limits = gate.describe(handle.client) or {}
    published = limits.get("source") == "machine" and not limits.get("is_fallback")
    orientation = dict(handle.orientation_info or {})
    origin_saved = handle.origin.get("objective") is not None
    seen = pixel_size.entries()
    uncalibrated = sorted(
        {
            entry.get("objective") or key
            for key, entry in seen.items()
            if not entry.get("calibrated")
        }
    )
    return {
        "limits": {
            "done": published,
            "says": f"published: {limits.get('path')}"
            if published
            else "not published: the stage keeps to NIS's own travel and z to near where it stood at connect; "
            "publish this microscope's limits with driver_setup/limits/notebooks/set_limits.ipynb",
        },
        "orientation": {
            "done": bool(orientation.get("measured")),
            "says": "stated: saved pictures are turned to the stage's axes"
            if orientation.get("measured")
            else "not stated: saved pictures are kept as the camera gives them; "
            "run driver_setup/orientation/notebooks/set_orientation.ipynb",
        },
        "origin": {
            "done": origin_saved,
            "says": "saved: positions count from it"
            if origin_saved
            else "not saved: positions are NIS's own stage coordinates; run the driver's set_origin step",
        },
        "data_location": {
            "done": handle.data_location is not None,
            "says": f"{handle.data_location}, as OME-Zarr"
            if handle.data_location is not None
            else SET_UP,
        },
        "pixel calibration in NIS": {
            "done": bool(seen) and not uncalibrated,
            "says": (
                "not yet known: take one picture with each objective you will use; the driver then records "
                "whether NIS has a pixel size for it"
                if not seen
                else f"missing for {', '.join(uncalibrated)}: calibrate these objectives in NIS (Calibration), "
                "otherwise their pictures cannot be placed on the sample"
                if uncalibrated
                else f"calibrated for every objective pictured so far ({len(seen)} objective and configuration pairs)"
            ),
        },
    }


def where_it_saves(handle) -> Path | None:
    """The folder this session saves in, or None when there is none yet."""
    try:
        return handle.output_root()
    except RuntimeError:
        return None


def connection_status(handle, root: Path | None) -> dict:
    """What the session stands on, one line per check; a failure begins with "failed"."""
    limits = gate.describe(handle.client)

    def stage() -> str:
        at = get_xyz(handle)
        return f"x {at['x']['position']:.0f} · y {at['y']['position']:.0f} · z {at['z']['position']:.1f} um"

    def bridge() -> str:
        answer = handle.client.ping()
        return f"answering (bridge {answer.get('bridge')}, NIS {handle.nis_version})"

    return {
        "bridge": try_(bridge)
        or "failed — no answer; is start_bridge.mac running in NIS-Elements?",
        "limits": (
            f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
            if limits
            else "failed — none loaded, every move refused"
        ),
        "stage": try_(stage) or "failed — no reading",
        "output root": str(root) if root is not None else f"not set up: {SET_UP}",
    }
