"""Where the stage is, in the frame the controller uses, and how to move it there.

The driver speaks absolute stage micrometres; the controller speaks
micrometres from a zero point (the *origin*) saved once for this microscope.
This file holds that arithmetic: reading the stage and giving its position in
the controller's frame, moving to a position given in that frame, saving a new
origin, and the allowed range (the *canvas*) in the same frame.

The controller's single ``z`` axis is the *focus*: the sum of the two
physical focus drives, z-wide and z-galvo, relative to the origin's focus sum,
so it reads the same whichever drive made a move. When the objective is
changed, the calibration's per-objective offset keeps the same point of the
sample under the lens: x and y apply to the motorised stage, and the z offset
is made by z-wide. A move across objectives is refused when no calibration
can say how far the objectives differ; a read warns and reports the position
without the correction, because a read can never put the stage anywhere wrong.

The z model assumes that the two drives add up with the same sign. The
arithmetic and the sign are checked on a real microscope by
``testing/hardware/validate_zmart_adapter.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from . import coordinates
from ...dispatcher import gate as _gate
from ...vendor_specific.primitive_handling import state as _readers
from ...vendor_specific.primitive_handling.derived import z_um_from_settings as _z_um_from_settings
from ...vendor_specific.primitive_handling import changes as _commands
from .. import session as _info
from ..session import ZmartHandle, job_catalog, note_unconfirmed, require_open  # noqa: F401

log = logging.getLogger(__name__)


# The motors the driver moves each axis with, under the names the controller
# uses. These are the names ``get_actuators`` lists and ``with_actuators``
# accepts. The focus is always moved with z-wide: the objective offsets are
# measured on z-wide, so moving the focus with the fast z-galvo as well would
# let the two drives share the focus differently from call to call.
_ACTUATORS = {"x": ("motoric",), "y": ("motoric",), "z": ("z-wide",)}

# Where each motor's own reading sits in a hardware snapshot (see
# ``_hardware_snapshot``). ``get_xyz`` reports these readings under
# ``actuators``. They include the z-galvo, which is read but never moved here.
_MOTOR_READINGS = {
    "x": {"motoric": "x_um"},
    "y": {"motoric": "y_um"},
    "z": {"z-wide": "z_wide_um", "z-galvo": "z_galvo_um"},
}

# Fixed defaults for axes omitted from ``with_actuators`` — never sticky: a
# previous call's choice is not state.
_DEFAULT_ACTUATORS = {"x": "motoric", "y": "motoric", "z": "z-wide"}

# Every number in a get_xyz or set_xyz answer is in micrometres, and each
# axis says so under this key. The same spelling the controller uses.
UNIT = "micrometer"


def objective_delta_um(handle: ZmartHandle, current_objective: dict | None) -> tuple:
    """ΔT for this session's origin and calibration; see :func:`coordinates.objective_delta_um`."""
    return coordinates.objective_delta_um(
        handle.origin, handle.translations, current_objective
    )


def hardware_snapshot(handle: ZmartHandle) -> dict:
    """One consistent read of everything the frame math needs.

    Stage XY, both z drives, and the current objective use the configured
    reader policy; raises when any of them is unreadable.
    """
    xy = _readers.get_xy(handle.client)
    if not xy:
        raise RuntimeError("could not read stage XY position")
    job = _info.selected_job_name(handle)
    if job is None:
        raise RuntimeError(
            "could not determine the selected LAS X job, which this command needs "
            "to read the focus drives; nothing was moved"
        )
    settings = _readers.get_job_settings(handle.client, job)
    if not settings:
        raise RuntimeError(f"could not read job settings for '{job}'")
    return {
        "job": job,
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_wide_um": _z_um_from_settings(
            settings, "z-wide", client=handle.client, job_name=job
        ),
        "z_galvo_um": _z_um_from_settings(
            settings, "z-galvo", client=handle.client, job_name=job
        ),
        "objective": settings.get("objective"),
    }


def delta_or_warn(handle: ZmartHandle, snapshot: dict) -> tuple:
    """ΔT for a READ: uncompensated-but-loud when translations are missing.

    Reads must stay available (a read cannot land the stage anywhere wrong),
    so a missing translation degrades to ΔT = 0 with a warning instead of
    raising — unlike moves, which refuse.
    """
    try:
        return objective_delta_um(handle, snapshot.get("objective"))
    except RuntimeError as exc:
        log.warning("%s; frame values are UNCOMPENSATED for the objective change", exc)
        return (0.0, 0.0, 0.0)


def get_actuators(handle: ZmartHandle) -> dict:
    """The motors that can move each axis, and the one used when none is chosen.

    ``{"x": {"options": ["motoric"], "default": "motoric"}, ...,
    "z": {"options": ["z-wide"], "default": "z-wide"}}``. The options are
    exactly the names ``with_actuators`` accepts. The focus is always moved
    with z-wide, the drive the objective offsets are measured on; the z-galvo
    is still read and reported by :func:`get_xyz`, but never used to move.
    """
    require_open(handle)
    return {
        axis: {"options": list(opts), "default": _DEFAULT_ACTUATORS[axis]}
        for axis, opts in _ACTUATORS.items()
    }


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    """Merge a per-call actuator selection over the fixed defaults."""
    chosen = dict(_DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if actuator not in _ACTUATORS.get(axis, ()):
            raise ValueError(f"unknown actuator {actuator!r} for axis {axis!r}")
        chosen[axis] = actuator
    return chosen


def get_xyz(handle: ZmartHandle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``actuators`` and ``canvas``.

    The answer has the keys ``x``, ``y`` and ``z``. Each axis carries the
    same four entries, and every number in them is in micrometres:

    - ``position``: where the axis is, measured from the saved origin. For
      ``z`` this is the *focus*: the sum of the two z drives (z-wide plus
      z-galvo) minus the origin's focus sum, so it reads the same whichever
      drive made the move. When the objective in place is not the one the
      origin was saved under, the calibrated objective translation ΔT is
      subtracted as well, so the position names the same point of the
      sample under either objective. (In short: position = fresh reading −
      origin − ΔT.) When no translation is available, the position is
      reported uncompensated and a warning is logged, because a read can
      never land the stage anywhere wrong.
    - ``unit``: always ``"micrometer"``.
    - ``actuators``: every motor of the axis with its own reading, exactly
      as LAS X reports it: ``{"motoric": ...}`` for x and y, ``{"z-wide":
      ..., "z-galvo": ...}`` for z. These are the stage's own numbers, with
      nothing subtracted, so they show how the two z drives share the focus.
    - ``canvas``: ``[min, max]``, everywhere a picture can show along the
      axis, in the same frame as ``position`` (see :func:`_canvas`). When
      no limits govern the session, so that every move is refused, the
      canvas is the current position alone.

    ``with_actuators`` only checks that the motors it names exist; the
    answer always reports all of them. The driver adds one extra on top,
    ``objective_translation_um``: the ΔT that was subtracted, as
    ``[x, y, z]``, so an operator can see when a compensation is in play.
    """
    require_open(handle)
    chosen = _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    dt = delta_or_warn(handle, snap)
    frame = coordinates.frame_position(snap, handle.origin, dt)
    allowed = canvas(handle) or {}
    result = {
        axis: {
            "position": frame[axis],
            "unit": UNIT,
            "actuator": chosen[axis],
            "actuators": {
                motor: snap[reading] for motor, reading in _MOTOR_READINGS[axis].items()
            },
            "canvas": allowed.get(f"{axis}_um") or [frame[axis], frame[axis]],
        }
        for axis in ("x", "y", "z")
    }
    result["objective_translation_um"] = list(dt)
    handle.driven_to = {
        **frame,
        "z_wide_um": snap["z_wide_um"],
        "z_galvo_um": snap["z_galvo_um"],
    }
    return result


#: The z-galvo counts as parked when it reads within this much of 0. The
#: readback is never exact, and moving the galvo for noise would only add moves.
Z_GALVO_PARKED_TOLERANCE_UM = 0.5


def zero_z_galvo(handle: ZmartHandle) -> dict:
    """Park the z-galvo at 0 while keeping the focus.

    The driver moves the focus with z-wide alone, so the fast z-galvo is kept
    at 0 and out of the way. If it is not there (for example after someone
    used it in LAS X), z-wide first takes over the galvo's offset, so a refused
    z-wide move (limits) aborts with nothing moved, and only then does the
    galvo go to 0. The focus does not change. Both moves go through the checked
    ``move_z``; a refused or failed move raises, and a move that could not be
    confirmed is recorded and the procedure carries on.

    Returns how much was transferred to z-wide, in µm.
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


def set_xyz(
    handle: ZmartHandle,
    x: float,
    y: float,
    z: float,
    *,
    with_actuators: dict | None = None,
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, then answer like :func:`get_xyz`.

    Once every leg of the move is confirmed, the position is read back from
    the microscope and returned in exactly the shape :func:`get_xyz` gives,
    so the answer shows where the stage really is, not the numbers that
    were asked for. A move that is refused or cannot be confirmed raises
    instead, because carrying on at an unknown position is never safe.

    Destination = origin + F + ΔT, commanded absolutely. The focus is always
    moved with z-wide alone: its one target holds both the requested frame Z
    and the objective offset ΔT.z, and the z-galvo is parked at 0 first if it is not already there. Using
    one drive only means the focus cannot slowly shift between the two drives
    from one call to the next.

    ΔT compensates an objective change relative to the origin's objective
    (from the calibration's translation totals); a cross-objective move with
    no translations available REFUSES. The frame code does no limit checking of
    its own: every leg is checked inside the command that fires it, in the
    actions. A refused leg raises with the reason; legs that
    already ran stay where they arrived (each was checked too). A leg that
    was sent and accepted but could not be confirmed does not raise: it is
    recorded under the handle's ``unconfirmed`` list, and the answer is read
    back from the microscope anyway, so the workflow sees where the stage
    really is and decides for itself.
    """
    require_open(handle)
    _resolve_actuators(with_actuators)
    snap = hardware_snapshot(handle)
    if abs(snap["z_galvo_um"]) > Z_GALVO_PARKED_TOLERANCE_UM:
        zero_z_galvo(handle)
        snap = hardware_snapshot(handle)
    dt = objective_delta_um(handle, snap.get("objective"))  # raises if unavailable
    abs_x, abs_y, target_focus = coordinates.stage_target(x, y, z, handle.origin, dt)
    # The focus is moved with z-wide alone: it takes up the whole difference
    # to the target focus, with the z-galvo parked at 0 (see above).
    z_target = target_focus - snap["z_galvo_um"]
    z_targets = [("zwide", z_target)]

    # Every leg below is limit-checked inside the command it fires — the
    # frame code carries no checks of its own. A refused leg raises here with
    # an actionable message; legs that already ran stay where they arrived.
    arrived = _commands.move_xy(handle.client, abs_x, abs_y, unit="um")
    if not arrived.get("success"):
        raise RuntimeError(f"move_xy failed: {arrived}")
    if not arrived.get("confirmed"):
        note_unconfirmed(handle, f"set_xyz: xy to ({x}, {y})", arrived)

    for z_mode, target in z_targets:
        z_result = _commands.move_z(
            handle.client,
            snap["job"],
            target,
            unit="um",
            z_mode=z_mode,
        )
        if not z_result.get("success"):
            raise RuntimeError(f"move_z ({z_mode}) failed: {z_result}")
        if not z_result.get("confirmed"):
            note_unconfirmed(handle, f"set_xyz: z ({z_mode}) to {target}", z_result)

    # Read back from the microscope, so the answer is where the stage is now.
    return get_xyz(handle, with_actuators=with_actuators)


def canvas(handle: ZmartHandle) -> dict | None:
    """Everywhere a picture can show, per axis, in the frame. None when no limits govern the session.

    On z the frame follows the focus, the sum of the two z drives, so the
    canvas is the z-wide travel widened by the z-galvo travel: a z-stack on
    the galvo reaches no further than the galvo can go. On x and y the
    canvas is the stage travel itself. A picture taken at the edge of the
    travel does show half a field beyond it, but the field depends on the
    objective and the zoom, and LAS X only reports it for the objective in
    place now, so the driver cannot know the widest field of every allowed
    objective ahead of time and does not widen x and y.
    """
    state = _gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    return coordinates.canvas_in_frame(state.stage_cfg["stage_um"], handle.origin)
