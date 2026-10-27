"""Where the stage is, in the frame the controller uses, and how to move it there.

FLUOVIEW reports positions in its own stage coordinates. The controller
works in a frame whose zero point, the *origin*, was saved once for this
microscope (``driver_setup/origin``): a position in the frame is the stage
position minus the origin. When the objective in use differs from the one
the origin was saved under, the objective calibration says how far the two
objectives are offset (called ΔT here), and that offset is taken into
account as well, so the same number names the same point of the sample
under either objective.

The controller's ``z`` is the focus motor, FLUOVIEW's "Z stage". The piezo,
when the microscope has one, is read and reported beside it under
``actuator_readings`` but never used to move the focus here, and not added
to ``z``: FLUOVIEW reports the piezo relative to its own Z origin, and how
the two drives add up has not been checked on an FV4000 yet (see the README).
A move across objectives is refused when no calibration can say how far the
objectives differ; a read warns and reports the position without the
correction, because a read can never put the stage anywhere wrong.

Everything in the arithmetic part is plain numbers and dictionaries, so it
can be checked on paper. The session handling (what to read, what to move)
is below it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import limits as _gate
from . import changes
from . import readings as _readers

log = logging.getLogger(__name__)

#: The motors the driver moves each axis with, under the names the controller
#: uses: these are what ``get_actuators`` lists and ``with_actuators`` accepts.
_ACTUATORS = {"x": ("stage",), "y": ("stage",), "z": ("motor",)}
_DEFAULT_ACTUATORS = {"x": "stage", "y": "stage", "z": "motor"}

#: Where each motor's own reading sits in a hardware snapshot. The piezo is
#: read and reported, never moved here.
_MOTOR_READINGS = {
    "x": {"stage": "x_um"},
    "y": {"stage": "y_um"},
    "z": {"motor": "z_um", "piezo": "z_piezo_um"},
}

#: Every number in a get_xyz or set_xyz answer is in micrometres, and each axis says so.
UNIT = "micrometer"


# =============================================================================
# The arithmetic
# =============================================================================


def frame_position(snapshot: dict, origin: dict, delta: tuple) -> dict:
    """The stage position in the controller's frame: reading − origin − ΔT, per axis."""
    return {
        "x": snapshot["x_um"] - origin["x_um"] - delta[0],
        "y": snapshot["y_um"] - origin["y_um"] - delta[1],
        "z": snapshot["z_um"] - origin["z_um"] - delta[2],
    }


def stage_target(x: float, y: float, z: float, origin: dict, delta: tuple) -> tuple:
    """The stage coordinates (x, y, z) that belong to a frame position: origin + position + ΔT."""
    return (
        origin["x_um"] + x + delta[0],
        origin["y_um"] + y + delta[1],
        origin["z_um"] + z + delta[2],
    )


def canvas_in_frame(stage: dict, origin: dict) -> dict:
    """Everywhere a picture can show, per axis, in the frame: the measured travel moved by the origin."""
    return {
        f"{axis}_um": [
            stage[axis][0] - origin[f"{axis}_um"],
            stage[axis][1] - origin[f"{axis}_um"],
        ]
        for axis in ("x", "y", "z")
    }


def delta_between_objectives_um(
    origin: dict, translations: dict | None, current_objective: dict | None
) -> tuple:
    """ΔT = T[current objective] − T[origin's objective], in µm (x, y, z).

    Zero when the origin has no objective or the objective is unchanged.
    Raises ``RuntimeError`` when the objective HAS changed but the
    translations cannot say by how much; the caller decides whether that
    refuses (a move) or warns (a read).
    """
    origin_slot = (origin.get("objective") or {}).get("slot")
    current_slot = (current_objective or {}).get("slot")
    if origin_slot is None or current_slot == origin_slot:
        return (0.0, 0.0, 0.0)
    if current_slot is None:
        raise RuntimeError(
            f"the objective changed since the origin was captured (slot {origin_slot} -> unknown), so the frame "
            "cannot be mapped; capture the origin again under the current objective"
        )
    from ..driver_setup.optical_calibration import model as _calibration

    try:
        return _calibration.translation_delta_um(
            translations, int(origin_slot), int(current_slot)
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"the objective changed since the origin was captured but {exc}, or capture the origin again under the current objective"
        ) from exc


# =============================================================================
# The session
# =============================================================================


def hardware_snapshot(handle) -> dict:
    """One read of everything the frame arithmetic needs: XY, the focus motor, the piezo and the objective in use.

    Raises ``RuntimeError`` when the stage or the focus cannot be read. A
    piezo or an objective FLUOVIEW does not report is None, not a stop.
    """
    xy = _readers.get_xy(handle.client)
    if not xy:
        raise RuntimeError("could not read the XY stage position from FLUOVIEW")
    z_um = _readers.get_z(handle.client)
    if z_um is None:
        raise RuntimeError("could not read the focus position from FLUOVIEW")
    piezo = _readers.read_now("z_piezo", handle.client)
    objective = _readers.get_objective(handle.client)
    return {
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_um": float(z_um),
        "z_piezo_um": float(piezo) if piezo is not None else 0.0,
        "objective": dict(objective) if objective else None,
    }


def objective_delta_um(handle, current_objective: dict | None) -> tuple:
    """ΔT for this session's origin and calibration; see :func:`delta_between_objectives_um`."""
    return delta_between_objectives_um(
        handle.origin, handle.translations, current_objective
    )


def delta_or_warn(handle, snapshot: dict) -> tuple:
    """ΔT for a read: uncompensated, and said loudly, when the calibration cannot give it."""
    try:
        return objective_delta_um(handle, snapshot.get("objective"))
    except RuntimeError as exc:
        log.warning(
            "%s; the frame values are UNCOMPENSATED for the objective change", exc
        )
        return (0.0, 0.0, 0.0)


def get_actuators(handle) -> dict:
    """The motors that can move each axis, and the one used when none is chosen.

    ``{"x": {"options": ["stage"], "default": "stage"}, "y": ..., "z": {"options": ["motor"], "default": "motor"}}``.
    The piezo is reported by :func:`get_xyz` but never used to move.
    """
    handle.require_open()
    return {
        axis: {"options": list(options), "default": _DEFAULT_ACTUATORS[axis]}
        for axis, options in _ACTUATORS.items()
    }


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    chosen = dict(_DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if actuator not in _ACTUATORS.get(axis, ()):
            raise ValueError(
                f"unknown actuator {actuator!r} for axis {axis!r}; the actuators are {_ACTUATORS}"
            )
        chosen[axis] = actuator
    return chosen


def canvas(handle) -> dict | None:
    """Everywhere a picture can show, per axis, in the frame; None when no limits govern the session."""
    state = _gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    return canvas_in_frame(state.stage_cfg["stage_um"], handle.origin)


def get_xyz(handle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``used_actuator``, ``actuator_readings`` and ``canvas``.

    Every number is in micrometres. ``position`` is measured from the saved
    origin, with the objective offset ΔT subtracted when the objective in
    use is not the origin's (position = reading − origin − ΔT); without a
    usable calibration it is reported uncompensated, with a warning.
    ``actuator_readings`` are FLUOVIEW's own numbers with nothing subtracted:
    ``{"stage": ...}`` for x and y, ``{"motor": ..., "piezo": ...}`` for z.
    ``canvas`` is ``[min, max]`` along the axis, in the same frame; the
    current position alone when no limits govern the session. The extra
    ``objective_translation_um`` says which ΔT was subtracted.
    """
    handle.require_open()
    chosen = _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    dt = delta_or_warn(handle, snap)
    frame = frame_position(snap, handle.origin, dt)
    allowed = canvas(handle) or {}
    result = {
        axis: {
            "position": frame[axis],
            "unit": UNIT,
            "used_actuator": chosen[axis],
            "actuator_readings": {
                motor: snap[reading] for motor, reading in _MOTOR_READINGS[axis].items()
            },
            "canvas": allowed.get(f"{axis}_um") or [frame[axis], frame[axis]],
        }
        for axis in ("x", "y", "z")
    }
    result["objective_translation_um"] = list(dt)
    handle.driven_to = {
        **frame,
        "z_motor_um": snap["z_um"],
        "z_piezo_um": snap["z_piezo_um"],
        "objective": snap["objective"],
    }
    return result


def set_xyz(
    handle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, then answer like :func:`get_xyz`.

    The target stage coordinates are origin + position + ΔT. Each leg is
    checked by the limits inside the change that sends it; a refused or
    failed leg raises, and the legs that already ran stay where they
    arrived. A leg that was accepted but could not be confirmed is recorded
    (``handle.unconfirmed``) and the answer is still read back from FLUOVIEW,
    so the workflow sees where the stage really is. A move across objectives
    without a calibration that covers them is refused before anything moves.
    """
    handle.require_open()
    _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    dt = objective_delta_um(
        handle, snap.get("objective")
    )  # raises when it cannot be known
    target_x, target_y, target_z = stage_target(x, y, z, handle.origin, dt)

    arrived = changes.move_xy(
        handle.client, target_x, target_y, escape=handle.escape_objective
    )
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
