"""A description of the microscope, what is set up, and a short report of what the connection stands on.

``get_info`` answers in plain words: what the microscope is, what each
setting means, what is set up once on this microscope, and how the
connection stands. A person, a notebook or the ZMART AI agent builds its
picture of the instrument from it, so it says what the other answers cannot.

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

DESCRIPTION = """A ZEISS light microscope, driven through ZEN and its ZEN API (gRPC, through the ZEN API Gateway).

Stage: x and y move the motorised stage; z is the focus drive. All positions are in micrometres from the origin saved in this microscope's setup (ZEN's own coordinates until one is saved); +z is the direction ZEN's focus drive counts up. When the objective changes, the driver applies the objective calibration, so the origin stays on the same spot of the sample. {limits}

Settings (the changeable part of the state): experiment is the name of a ZEN experiment, a saved .czexp. It holds the whole imaging setup: channels, exposure, light sources, z-stack, time series, tiles. Loading one applies all of it; set_state refuses an experiment ZEN does not have. objective is the objective changer's position, counted from 1, or an objective's name. {experiments} {loaded}

Objectives on the changer, as position: name: {objectives}.

Acquiring has two triggers: full runs the whole loaded experiment as ZEN would (its z-stack, time series and tiles); snap takes one picture with its active channels. ZEN writes one CZI file per capture, which the driver moves into the output folder acquire is given, or else under this microscope's data location, named by the acquisition type and the position label, with a .zmart.json record beside it. The CZI is kept as ZEN wrote it, not turned; the record says how its columns and rows map onto the stage."""


def get_info(handle) -> dict:
    """The description, the setup, where the session saves, and how the connection stands."""
    handle.require_open()
    root = _where_it_saves(handle)
    return {
        "description": _described(handle),
        "setup": _setup(handle),
        # The driver moves ZEN's file into the output folder acquire is given.
        "chooses_its_own_folder": False,
        "connection_status": _connection_status(handle, root),
    }


def _described(handle) -> str:
    """The description, filled in from the loaded limits and what ZEN reports now."""
    state = _gate.state_for(handle.client)
    if state is not None and state.stage_cfg is not None:
        stage = state.stage_cfg["stage_um"]
        limits = (
            f"This microscope's limits let the stage travel x {stage['x'][0]:g} to {stage['x'][1]:g} um, "
            f"y {stage['y'][0]:g} to {stage['y'][1]:g} um and the focus {stage['z'][0]:g} to {stage['z'][1]:g} um, "
            "in ZEN's own coordinates."
        )
    else:
        limits = "No limits are loaded in this session, so every move is refused."
    names = _readers.get_experiments(handle.client)
    experiments = (
        f"The experiments ZEN can load now: {', '.join(names) if names else 'none'}."
    )
    loaded = (
        f"Loaded now: {handle.experiment['name']}."
        if handle.experiment
        else "No experiment is loaded yet, so acquire refuses until one is."
    )
    objectives = _readers.get_objectives(handle.client) or []
    listed = (
        ", ".join(f"{o['position']}: {o['name']}" for o in objectives)
        or "not reported by ZEN"
    )
    return DESCRIPTION.format(
        limits=limits, experiments=experiments, loaded=loaded, objectives=listed
    )


def _setup(handle) -> dict:
    """Each thing set up once on this microscope: whether it is done, and in words what it holds or what to do."""
    limits = _gate.describe(handle.client) or {}
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    published = limits.get("source") == "machine" and not limits.get("is_fallback")
    origin_saved = try_(_machine.MACHINE.read_origin) is not None
    measured = calibration.get("measured_positions") or []
    return {
        "limits": {
            "done": published,
            "says": f"published: {limits.get('path')}"
            if published
            else "not published: the bundled placeholder limits govern every move; measure this microscope's limits with driver_setup/limits/notebooks/set_limits.ipynb",
        },
        "orientation": {
            "done": bool(orientation.get("measured")) and handle.frame is not None,
            "says": f"measured: the picture is turned {orientation.get('rotate_deg')} degrees"
            + (" with a mirror" if orientation.get("mirrored") else "")
            + f", one pixel {handle.frame['pixel_size_um'][0]:.4f} um"
            if orientation.get("measured") and handle.frame
            else "not measured: the turn of the picture and the pixel size are unknown; run driver_setup/orientation/notebooks/set_orientation.ipynb",
        },
        "calibration": {
            "done": bool(calibration.get("loaded")) and len(measured) >= 2,
            "says": f"measured for objective positions {measured}"
            if calibration.get("loaded") and len(measured) >= 2
            else "no measured objective pair: a change of objective cannot keep the sample in place; run driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb",
        },
        "origin": {
            "done": origin_saved,
            "says": "saved: positions count from it"
            if origin_saved
            else "not saved: positions are ZEN's own coordinates; run the driver's set_origin step",
        },
        "data_location": {
            "done": handle.data_location is not None,
            "says": f"{handle.data_location}, as CZI with a .zmart.json record"
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
    """What the session stands on, from what the driver already holds; a failure names itself."""
    limits = _gate.describe(handle.client)
    calibration = handle.calibration_info or {}
    runtime = handle.client.runtime or {}

    def stage() -> str:
        at = get_xyz(handle)
        return f"x {at['x']['position']:.0f} · y {at['y']['position']:.0f} · z {at['z']['position']:.1f} um"

    if not calibration.get("enabled", True):
        calibration_says = "switched off"
    elif calibration.get("loaded"):
        calibration_says = (
            f"positions {calibration.get('measured_positions')}"
            if calibration.get("measured_positions")
            else "loaded, no pair measured yet"
        )
    else:
        calibration_says = (
            "failed — no usable calibration, so changes across objectives are refused"
        )
    return {
        "api": f"answering, zen_api {runtime.get('zen_api_version')} at {runtime.get('host')}:{runtime.get('port')}"
        if try_(lambda: _readers.ping(handle.client))
        else "failed — no answer",
        "gateway check": "the gateway's certificate is checked"
        if runtime.get("certificate")
        else "pending — the connection is encrypted, but the gateway's certificate is not checked",
        "limits": f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
        if limits
        else "failed — none loaded, every move refused",
        "calibration": calibration_says,
        "experiment": handle.experiment["name"]
        if handle.experiment
        else "pending — none loaded",
        "stage": try_(stage) or "failed — no reading",
        "zen images": handle.zen_images
        or handle.image_output_path
        or "failed — ZEN did not say where it writes",
        "output root": str(root) if root is not None else f"not set up: {SET_UP}",
    }
