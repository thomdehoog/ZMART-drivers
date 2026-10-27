"""Where the stage is in the coordinates the experiment uses, and how to move it there.

NIS-Elements reports the stage in its own coordinates, in micrometres. The
experiment works in micrometres from a zero point, the *origin*, saved once
for this microscope (``driver_setup/origin``): a position is NIS's reading
minus the origin. Each axis has one motor: the motorised stage for x and y,
and the focus drive for z. Their own readings are reported as they are,
under ``actuator_readings``, with nothing subtracted.

This driver has no objective calibration. When the objective in place is not
the one the origin was saved under, positions are not corrected for the shift
between the two objectives; ``get_state`` says so, and so does ``get_info``.

The *canvas* is everywhere a picture can show on an axis: the travel the
limits allow, in the same coordinates, widened in x and y by half a field of
view when the pixel size of the objective in place is known.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/absolute_coordinate_system.py``.
"""

from __future__ import annotations

import logging

from ..dispatcher import limits as gate
from ..driver_setup import pixel_size
from . import changes, readings

log = logging.getLogger(__name__)

#: The motors of each axis, under the names the ZMART Controller uses.
ACTUATORS = {"x": ("motoric",), "y": ("motoric",), "z": ("motoric",)}
DEFAULT_ACTUATORS = {"x": "motoric", "y": "motoric", "z": "motoric"}

#: Every number of a position is in micrometres, and each axis says so.
UNIT = "micrometer"

#: NIS's reading of each axis, in a hardware snapshot.
READINGS = {"x": "x_um", "y": "y_um", "z": "z_um"}


def get_actuators(handle) -> dict:
    """The motors of each axis, and the one used when none is chosen."""
    handle.require_open()
    return {
        axis: {"options": list(options), "default": DEFAULT_ACTUATORS[axis]}
        for axis, options in ACTUATORS.items()
    }


def resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    """The motor of each axis for one call: the defaults, with the ones asked for; ``ValueError`` for an unknown one."""
    chosen = dict(DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if axis not in ACTUATORS:
            raise ValueError(f"unknown axis {axis!r}; the axes are x, y and z")
        if actuator not in ACTUATORS[axis]:
            raise ValueError(
                f"unknown motor {actuator!r} for axis {axis!r}; its motors are {list(ACTUATORS[axis])}"
            )
        chosen[axis] = actuator
    return chosen


def hardware_snapshot(handle) -> dict:
    """NIS's own reading of the stage and the focus: ``{"x_um", "y_um", "z_um"}``; ``RuntimeError`` when unreadable."""
    xy = readings.xy(handle.client)
    z = readings.z(handle.client)
    return {"x_um": xy["x_um"], "y_um": xy["y_um"], "z_um": z["z_um"]}


def frame_position(snapshot: dict, origin: dict) -> dict:
    """The position in the experiment's coordinates: NIS's reading minus the origin, per axis."""
    return {axis: snapshot[key] - origin[key] for axis, key in READINGS.items()}


def stage_target(x: float, y: float, z: float, origin: dict) -> dict:
    """NIS's own coordinates of a position in the experiment's: the origin plus the position."""
    return {
        "x_um": origin["x_um"] + x,
        "y_um": origin["y_um"] + y,
        "z_um": origin["z_um"] + z,
    }


def half_field_um(handle) -> tuple[float, float]:
    """Half the field of view in x and y for the objective and optical configuration in place; (0, 0) when unknown."""
    try:
        objective = readings.objective(handle.client)["name"]
        configuration = readings.selected_optical_configuration(handle.client)
    except RuntimeError:
        return 0.0, 0.0
    entry = pixel_size.lookup(objective, configuration)
    if not entry or not entry.get("pixel_size_um"):
        return 0.0, 0.0
    width, height = entry["image_size_px"]
    size = entry["pixel_size_um"]
    return width * size / 2, height * size / 2


def canvas(handle) -> dict | None:
    """Everywhere a picture can show, per axis, in the experiment's coordinates; None when no limits are in force."""
    state = gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    stage = state.stage_cfg["stage_um"]
    half_x, half_y = half_field_um(handle)
    origin = handle.origin
    return {
        "x": [
            stage["x_um"][0] - origin["x_um"] - half_x,
            stage["x_um"][1] - origin["x_um"] + half_x,
        ],
        "y": [
            stage["y_um"][0] - origin["y_um"] - half_y,
            stage["y_um"][1] - origin["y_um"] + half_y,
        ],
        "z": [stage["z_um"][0] - origin["z_um"], stage["z_um"][1] - origin["z_um"]],
    }


def get_xyz(handle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``used_actuator``, ``actuator_readings`` and ``canvas``.

    ``position`` is micrometres from the origin. ``actuator_readings`` holds
    the axis's motor with NIS's own reading. ``canvas`` is ``[min, max]``, in
    the same coordinates as ``position``; the position alone when no limits
    are in force. Raises ``RuntimeError`` when NIS cannot read the stage.
    """
    handle.require_open()
    chosen = resolve_actuators(with_actuators)
    snapshot = hardware_snapshot(handle)
    frame = frame_position(snapshot, handle.origin)
    allowed = canvas(handle) or {}
    return {
        axis: {
            "position": frame[axis],
            "unit": UNIT,
            "used_actuator": chosen[axis],
            "actuator_readings": {"motoric": snapshot[READINGS[axis]]},
            "canvas": allowed.get(axis) or [frame[axis], frame[axis]],
        }
        for axis in ("x", "y", "z")
    }


def set_xyz(
    handle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, and answer like :func:`get_xyz`, read back after the move.

    Both targets are put to the limits before anything moves, so a move
    whose focus is out of bounds does not first carry the stage away. Then
    the stage moves, then the focus, each through the dispatcher, which asks
    the limits once more before sending. A move that is refused or fails
    raises ``RuntimeError``, and a move already made stays where it arrived.
    A move that was sent but never seen to arrive is recorded in the session
    (``get_state`` lists it), and the position is read back all the same.
    """
    handle.require_open()
    resolve_actuators(with_actuators)
    for name, value in (("x", x), ("y", y), ("z", z)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number of micrometres, not {value!r}")  # noqa: TRY004 -- the driver answers a wrong request with ValueError
    target = stage_target(float(x), float(y), float(z), handle.origin)
    for command, values in (
        ("move_xy", {"x_um": target["x_um"], "y_um": target["y_um"]}),
        ("move_z", {"z_um": target["z_um"]}),
    ):
        refusal = gate.check_refusal(handle.client, command, values)
        if refusal:
            raise RuntimeError(f"nothing moved: {refusal}")

    moved = changes.move_xy(handle.client, target["x_um"], target["y_um"])
    if not moved.get("success"):
        raise RuntimeError(f"the stage did not move: {moved.get('message')}")
    if not moved.get("confirmed"):
        handle.note_unconfirmed(f"set_xyz: x, y to ({x}, {y})", moved)

    focused = changes.move_z(handle.client, target["z_um"])
    if not focused.get("success"):
        raise RuntimeError(f"the focus did not move: {focused.get('message')}")
    if not focused.get("confirmed"):
        handle.note_unconfirmed(f"set_xyz: z to {z}", focused)

    return get_xyz(handle, with_actuators=with_actuators)
