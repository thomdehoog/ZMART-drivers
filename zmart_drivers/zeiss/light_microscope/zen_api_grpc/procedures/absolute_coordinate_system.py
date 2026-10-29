"""The absolute coordinate system: where the stage is in the sample's frame, and how to move it there.

ZEN reports positions in its own stage and focus coordinates. The controller
works in a frame whose zero point, the *origin*, was saved once for this
microscope (``driver_setup/origin``): a position in the frame is the stage
position minus the origin. When the objective in place differs from the one
the origin was saved under, the objective calibration
(``driver_setup/optical_calibration``) says how far the two objectives are
offset (called ΔT here: how far the centre of the view and the focus shift
when the lens is changed), and that offset is taken into account as well, so
that the same number names the same point of the sample under either
objective.

A ZEISS stand has one motorised XY stage and one focus drive, so the
controller's ``z`` is the focus drive itself. Everything here is plain
arithmetic on readings; the readings come through the dispatcher, and the
moves go through the named changes, which ask the limits first.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import limits as _gate
from . import changes
from . import readings as _readers

log = logging.getLogger(__name__)

#: The motor of each axis, under the names the controller uses: one each.
ACTUATORS = {"x": ("motoric",), "y": ("motoric",), "z": ("focus",)}
DEFAULT_ACTUATORS = {"x": "motoric", "y": "motoric", "z": "focus"}

#: Where each motor's own reading sits in a hardware snapshot.
MOTOR_READINGS = {
    "x": {"motoric": "x_um"},
    "y": {"motoric": "y_um"},
    "z": {"focus": "z_um"},
}

#: Every number in a get_xyz or set_xyz answer is in micrometres.
UNIT = "micrometer"

#: An origin that was never saved: the frame is ZEN's own coordinates.
NO_ORIGIN = {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0, "objective": None}


def hardware_snapshot(handle) -> dict:
    """One read of everything the frame arithmetic needs: stage XY, the focus and the objective in place.

    Raises ``RuntimeError`` when any of them cannot be read.
    """
    xy = _readers.get_xy(handle.client)
    if not xy:
        raise RuntimeError("could not read the stage position from ZEN")
    z = _readers.get_z(handle.client)
    if not z:
        raise RuntimeError("could not read the focus position from ZEN")
    objective = _readers.get_objective(handle.client)
    if not objective:
        raise RuntimeError("could not read which objective is in place from ZEN")
    return {
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_um": float(z["z_um"]),
        "objective": objective,
    }


def get_actuators(handle) -> dict:
    """The motors that can move each axis, and the one used when none is chosen: one each."""
    handle.require_open()
    return {
        axis: {"options": list(options), "default": DEFAULT_ACTUATORS[axis]}
        for axis, options in ACTUATORS.items()
    }


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    """The motor chosen per axis, over the defaults; an unknown motor raises ``ValueError``."""
    chosen = dict(DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if actuator not in ACTUATORS.get(axis, ()):
            raise ValueError(
                f"unknown actuator {actuator!r} for axis {axis!r}; the axes have {ACTUATORS}"
            )
        chosen[axis] = actuator
    return chosen


def delta_between_objectives_um(
    origin: dict, translations: dict | None, current_objective: dict | None
) -> tuple:
    """ΔT = T[objective in place] − T[the origin's objective], in micrometres (x, y, z).

    Zero when the origin names no objective or the objective is unchanged.
    Raises ``RuntimeError`` when the objective has changed but the
    calibration cannot say by how much; the caller decides whether that
    refuses (a move) or warns (a read).
    """
    origin_position = (origin.get("objective") or {}).get("position")
    current_position = (current_objective or {}).get("position")
    if origin_position is None or current_position == origin_position:
        return (0.0, 0.0, 0.0)
    if current_position is None:
        raise RuntimeError(
            f"the objective changed since the origin was saved (position {origin_position} -> unknown), "
            "so the frame cannot be mapped; save the origin again under the current objective"
        )
    from ..driver_setup.optical_calibration.model import translation_delta_um

    try:
        return translation_delta_um(
            translations, int(origin_position), int(current_position)
        )
    except RuntimeError as why:
        raise RuntimeError(
            f"the objective changed since the origin was saved, but {why}; calibrate the pair with "
            "driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb, or save the "
            "origin again under the current objective"
        ) from why


def objective_delta_um(handle, current_objective: dict | None) -> tuple:
    """ΔT for this session's origin and calibration; see :func:`delta_between_objectives_um`."""
    return delta_between_objectives_um(
        handle.origin, handle.translations, current_objective
    )


def delta_or_warn(handle, snapshot: dict) -> tuple:
    """ΔT for a read: zero with a warning when it cannot be known, because a read must stay available."""
    try:
        return objective_delta_um(handle, snapshot.get("objective"))
    except RuntimeError as why:
        log.warning("%s; the position is reported without the objective offset", why)
        return (0.0, 0.0, 0.0)


def frame_position(snapshot: dict, origin: dict, delta: tuple) -> dict:
    """The position in the frame: reading − origin − ΔT, per axis."""
    return {
        "x": snapshot["x_um"] - origin["x_um"] - delta[0],
        "y": snapshot["y_um"] - origin["y_um"] - delta[1],
        "z": snapshot["z_um"] - origin["z_um"] - delta[2],
    }


def stage_target(x: float, y: float, z: float, origin: dict, delta: tuple) -> tuple:
    """The ZEN coordinates that belong to a frame position: origin + position + ΔT."""
    return (
        origin["x_um"] + x + delta[0],
        origin["y_um"] + y + delta[1],
        origin["z_um"] + z + delta[2],
    )


def canvas_in_frame(stage: dict, origin: dict) -> dict:
    """Everywhere the stage may go, per axis, in the frame: the travel from the limits, minus the origin."""
    return {
        f"{axis}_um": [
            stage[axis][0] - origin[f"{axis}_um"],
            stage[axis][1] - origin[f"{axis}_um"],
        ]
        for axis in ("x", "y", "z")
    }


def canvas(handle) -> dict | None:
    """Everywhere the stage may go, per axis, in the frame; None when no limits govern the session.

    The limits give the travel. A picture taken at the edge shows half a
    field beyond it, but the field depends on the objective and the camera,
    which the ZEN API does not report, so the canvas is the travel itself.
    """
    state = _gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    return canvas_in_frame(state.stage_cfg["stage_um"], handle.origin)


def get_xyz(handle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``used_actuator``, ``actuator_readings`` and ``canvas``.

    ``position`` is micrometres from the origin, with the objective offset
    ΔT subtracted when the objective in place is not the origin's (position
    = reading − origin − ΔT). ``actuator_readings`` are ZEN's own numbers,
    with nothing subtracted. ``canvas`` is ``[min, max]`` in the frame, or
    the position alone when no limits are loaded. The extra
    ``objective_translation_um`` is the ΔT that was subtracted.
    """
    handle.require_open()
    chosen = _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    delta = delta_or_warn(handle, snap)
    frame = frame_position(snap, handle.origin, delta)
    allowed = canvas(handle) or {}
    answer = {
        axis: {
            "position": frame[axis],
            "unit": UNIT,
            "used_actuator": chosen[axis],
            "actuator_readings": {
                motor: snap[reading] for motor, reading in MOTOR_READINGS[axis].items()
            },
            "canvas": allowed.get(f"{axis}_um") or [frame[axis], frame[axis]],
        }
        for axis in ("x", "y", "z")
    }
    answer["objective_translation_um"] = list(delta)
    handle.driven_to = {**frame, "objective": snap["objective"].get("position")}
    return answer


def set_xyz(
    handle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, then answer like :func:`get_xyz`.

    The target is origin + position + ΔT, commanded absolutely: first XY,
    then the focus. Each move is checked against the limits inside the
    change that sends it; a refused or failed move raises ``RuntimeError``,
    and a move that was sent but never seen is recorded in the session and
    the work goes on. The answer is read back from ZEN, so it shows where the
    stage really is.
    """
    handle.require_open()
    _resolve_actuators(with_actuators)
    x, y, z = (
        changes.checked_number(v, label) for v, label in ((x, "x"), (y, "y"), (z, "z"))
    )
    snap = hardware_snapshot(handle)
    delta = objective_delta_um(
        handle, snap.get("objective")
    )  # raises when the pair is not calibrated
    target_x, target_y, target_z = stage_target(x, y, z, handle.origin, delta)

    arrived = changes.move_xy(handle.client, target_x, target_y)
    if not arrived.get("success"):
        raise RuntimeError(f"move_xy failed: {arrived.get('message')}")
    if not arrived.get("confirmed"):
        handle.note_unconfirmed(f"set_xyz: xy to ({x}, {y})", arrived)

    focused = changes.move_z(handle.client, target_z)
    if not focused.get("success"):
        raise RuntimeError(f"move_z failed: {focused.get('message')}")
    if not focused.get("confirmed"):
        handle.note_unconfirmed(f"set_xyz: z to {z}", focused)

    return get_xyz(handle, with_actuators=with_actuators)
