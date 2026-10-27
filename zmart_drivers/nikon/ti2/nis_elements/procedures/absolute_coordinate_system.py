"""The absolute coordinate system: where the stage is in the frame the workflow uses, and how to move it there.

NIS-Elements reports positions in its own stage coordinates. The workflow
works in a frame whose zero point, the *origin*, was saved once for this
microscope: a position in the frame is the stage position minus the origin.
When the objective in place differs from the one the origin was saved under,
the objective calibration says how far the two objectives are offset (called
ΔT here, "the translation delta"), and that offset is taken into account as
well, so that the same numbers name the same point of the sample under
either objective. In short: position = reading − origin − ΔT, and a move
goes to origin + position + ΔT.

The Ti2 has one focus drive, so ``z`` is simply that drive. The Perfect
Focus System holds the focus by moving the same drive, so a reading always
says where the drive is now.

Everything here is plain arithmetic on numbers and dictionaries, apart from
the three functions that read and move the stage through the named readings
and changes. The arithmetic can be checked on paper, and is, in the tests.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import limits as _gate
from ..driver_setup.optical_calibration import model as _calibration
from . import changes
from . import readings as _readers

log = logging.getLogger(__name__)

#: The motors that move each axis, under the names the controller uses: x and
#: y move with the stage, z with the focus drive. These are the names
#: ``get_actuators`` lists and ``with_actuators`` accepts.
ACTUATORS = {"x": ("stage",), "y": ("stage",), "z": ("focus-drive",)}
DEFAULT_ACTUATORS = {"x": "stage", "y": "stage", "z": "focus-drive"}

#: Where each motor's own reading sits in a hardware snapshot.
MOTOR_READINGS = {
    "x": {"stage": "x_um"},
    "y": {"stage": "y_um"},
    "z": {"focus-drive": "z_um"},
}

#: Every number in a get_xyz or set_xyz answer is in micrometres, and each
#: axis says so under this key. The same spelling the controller uses.
UNIT = "micrometer"


# =============================================================================
# The arithmetic
# =============================================================================


def frame_position(snapshot: dict, origin: dict, delta: tuple) -> dict:
    """The stage position in the frame: reading − origin − ΔT, per axis."""
    return {
        "x": snapshot["x_um"] - origin["x_um"] - delta[0],
        "y": snapshot["y_um"] - origin["y_um"] - delta[1],
        "z": snapshot["z_um"] - origin["z_um"] - delta[2],
    }


def stage_target(x: float, y: float, z: float, origin: dict, delta: tuple) -> tuple:
    """The stage coordinates that belong to a frame position: origin + position + ΔT."""
    return (
        origin["x_um"] + x + delta[0],
        origin["y_um"] + y + delta[1],
        origin["z_um"] + z + delta[2],
    )


def canvas_in_frame(stage_um: dict, origin: dict) -> dict:
    """Everywhere the stage may go, per axis, in the frame: the envelope shifted by the origin."""
    return {
        f"{axis}_um": [
            stage_um[axis][0] - origin[f"{axis}_um"],
            stage_um[axis][1] - origin[f"{axis}_um"],
        ]
        for axis in ("x", "y", "z")
    }


def delta_between_objectives_um(
    origin: dict, translations: dict | None, current_objective: dict | None
) -> tuple:
    """ΔT = T[current objective] − T[origin's objective], in micrometres (x, y, z).

    Zero when the origin records no objective, or the objective is the same,
    or the session declined the calibration (*translations* None). Raises
    ``RuntimeError`` when the objective has changed and the calibration does
    not cover the pair; the caller decides whether that refuses (a move) or
    warns (a reading).
    """
    origin_slot = (origin.get("objective") or {}).get("slot")
    current_slot = (current_objective or {}).get("slot")
    if origin_slot is None or current_slot == origin_slot or translations is None:
        return (0.0, 0.0, 0.0)
    if current_slot is None:
        raise RuntimeError(
            f"the objective changed since the origin was saved (slot {origin_slot} -> unknown), so the "
            "frame cannot be mapped; save the origin again under the objective in use"
        )
    try:
        return _calibration.translation_delta_um(
            translations, int(origin_slot), int(current_slot)
        )
    except RuntimeError as why:
        raise RuntimeError(
            f"the objective changed since the origin was saved, but {why}; or save the origin again under the objective in use"
        ) from why


# =============================================================================
# Reading and moving
# =============================================================================


def hardware_snapshot(handle) -> dict:
    """One read of everything the frame arithmetic needs: the stage, the focus drive and the objective in use.

    Raises ``RuntimeError`` when the stage cannot be read. ``objective`` is
    ``{"slot", "name"}``, or None when NIS reports no nosepiece.
    """
    position = _readers.get_xyz(handle.client)
    if not position:
        raise RuntimeError(
            "the stage position could not be read; check that NIS-Elements and the bridge answer"
        )
    objectives = _readers.get_objectives(handle.client) or {}
    slot = objectives.get("current")
    objective = (
        None
        if slot is None
        else {
            "slot": int(slot),
            "name": objectives.get("objectives", {}).get(int(slot), ""),
        }
    )
    return {
        "x_um": float(position["x_um"]),
        "y_um": float(position["y_um"]),
        "z_um": float(position["z_um"]),
        "objective": objective,
    }


def objective_delta_um(handle, current_objective: dict | None) -> tuple:
    """ΔT for this session's origin and calibration; see :func:`delta_between_objectives_um`."""
    return delta_between_objectives_um(
        handle.origin, handle.translations, current_objective
    )


def delta_or_warn(handle, snapshot: dict) -> tuple:
    """ΔT for a reading: zero with a warning when the pair is not covered, since a reading moves nothing."""
    try:
        return objective_delta_um(handle, snapshot.get("objective"))
    except RuntimeError as why:
        log.warning("%s; the position is reported without the objective offset", why)
        return (0.0, 0.0, 0.0)


def get_actuators(handle) -> dict:
    """The motors that move each axis, and the one used when none is chosen."""
    handle.require_open()
    return {
        axis: {"options": list(opts), "default": DEFAULT_ACTUATORS[axis]}
        for axis, opts in ACTUATORS.items()
    }


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    chosen = dict(DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if actuator not in ACTUATORS.get(axis, ()):
            raise ValueError(
                f"unknown actuator {actuator!r} for axis {axis!r}; the actuators are {ACTUATORS}"
            )
        chosen[axis] = actuator
    return chosen


def canvas(handle) -> dict | None:
    """Everywhere the stage may go, per axis, in the frame; None when no limits govern the session."""
    state = _gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    return canvas_in_frame(state.stage_cfg["stage_um"], handle.origin)


def get_xyz(handle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``used_actuator``, ``actuator_readings`` and ``canvas``.

    Every number is in micrometres. ``position`` is measured from the saved
    origin, with the objective offset taken into account (position = reading
    − origin − ΔT). ``actuator_readings`` are NIS's own numbers, with nothing
    subtracted. ``canvas`` is ``[min, max]``, as far as the stage may go in
    the frame; when no limits govern the session it is the position alone.
    The answer also carries ``objective_translation_um``, the ΔT that was
    subtracted, so an operator can see when a compensation is in play.
    """
    handle.require_open()
    chosen = _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    delta = delta_or_warn(handle, snap)
    frame = frame_position(snap, handle.origin, delta)
    allowed = canvas(handle) or {}
    result = {
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
    result["objective_translation_um"] = list(delta)
    handle.driven_to = {**frame, "objective": snap["objective"]}
    return result


def set_xyz(
    handle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, then answer like :func:`get_xyz`.

    The target is origin + position + ΔT, sent as one absolute move of all
    three axes. The limits are asked inside the change, before anything is
    sent; a refused or failed move raises ``RuntimeError``, because carrying
    on at an unknown position is never safe. A move that was sent but never
    seen to arrive is recorded under the session's ``unconfirmed`` and the
    answer is read back anyway, so the workflow sees where the stage really
    is. A move under an objective the calibration does not cover is refused.
    """
    handle.require_open()
    _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    delta = objective_delta_um(
        handle, snap.get("objective")
    )  # raises when the pair is not covered
    target = stage_target(x, y, z, handle.origin, delta)
    result = changes.move_xyz(handle.client, *target)
    if not result.get("success"):
        raise RuntimeError(
            f"the move to ({x}, {y}, {z}) failed: {result.get('message')}"
        )
    if not result.get("confirmed"):
        handle.note_unconfirmed(f"set_xyz: to ({x}, {y}, {z})", result)
    return get_xyz(handle, with_actuators=with_actuators)
