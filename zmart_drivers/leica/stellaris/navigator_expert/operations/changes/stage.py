"""Moving the stage: ``move_xy`` and ``move_z``, each with its profile and its confirmation.

Positions are absolute, in micrometres by default. The limits gate is shown
the target in micrometres, then the stage envelope and its backstop are
checked, all before anything is sent. A move is confirmed by reading the
position back until it is within the profile's tolerance of the target.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import time
from functools import partial

from ...primitives.cam_api import waiting
from ...dispatcher.dispatch import make_log_entry
from ...primitives.cam_api.request import request_of
from ...procedures import readings as readers
from ...primitives.cam_api.profile import CommandProfile
from ...driver_setup.limits.limit_rules import check_xy, check_z
from ...primitives.cam_api.prechecks import check_idle
from . import confirming, run_change

log = logging.getLogger(__name__)

#: In micrometres, per unit a position can be given in.
UM_PER_UNIT = {"um": 1, "mm": 1000, "m": 1e6}

#: LAS X's names for the units, and their numbers when the enum cannot be read.
UNIT_ENUM = {"um": "eMicrons", "mm": "eMillimeter", "m": "eMeter"}
UNIT_NUMBER = {"um": 4, "mm": 3, "m": 1}

#: The z drives, by the name a move takes, as LAS X and the readers name them.
Z_DRIVE_ENUM = {"galvo": "eUseGalvo", "zwide": "eUseZWide"}
Z_DRIVE_KEY = {"galvo": "z-galvo", "zwide": "z-wide"}

MOVE_XY = CommandProfile(
    pre_check_fn=check_idle,
    # Sent without waiting for a receipt, so there is no reply to check;
    # the readback is the proof. An unconfirmed absolute move is sent again
    # (it is the same move) after waiting for idle.
    error_check_fn=None,
    confirm_tolerance=20.0,
    send_without_receipt=True,
)

MOVE_Z = CommandProfile(
    pre_check_fn=check_idle,
    confirm_tolerance=1.0,
)


def unit_enum(model_value, unit):
    """LAS X's enum member for ``unit``, or its number when the enum cannot be read."""
    try:
        return getattr(type(model_value), UNIT_ENUM[unit])
    except Exception:
        log.warning("Units enum resolution failed, using int fallback: %d", UNIT_NUMBER[unit])
        return UNIT_NUMBER[unit]


def move_xy(client, x, y, unit="um", *, max_retries=None, tolerance=None):
    """Move the XY stage to an absolute position.

    Args:
        client: LAS X API client.
        x, y: Target coordinates in ``unit``: 'um', 'mm' or 'm'.
        tolerance: How close the readback must come, in micrometres.

    Returns:
        The change's result; ``position`` is the *target* (in metres and
        micrometres), not a readback: ``confirmed`` says whether the stage
        was seen there.
    """
    if unit not in UM_PER_UNIT:
        return run_change.refused(f"unknown unit {unit!r} (expected 'um', 'mm', or 'm')", position=None)
    try:
        x_um, y_um = x * UM_PER_UNIT[unit], y * UM_PER_UNIT[unit]
        gated_values = {"x_um": x_um, "y_um": y_um}
        refusal = run_change.limits_refusal(client, "move_xy", gated_values, position=None)
        if refusal:
            return refusal
        check_xy(x_um, y_um)
    except (RuntimeError, TypeError) as e:
        return run_change.refused(str(e), position=None)

    api_obj = request_of(client, "PyApiMoveHardwareXY")
    unit_val = unit_enum(api_obj.Model.Units, unit)
    try:
        mode_val = type(api_obj.Model.MoveXyMode).eMoveXY
    except Exception:
        mode_val = 2  # eMoveXY=2 (NOT 0 which is eDontMove!)
        log.warning("MoveXyMode enum resolution failed, using int fallback: %d", mode_val)

    def setup(m):
        m.RelativePosition = False
        m.XPosition = x
        m.YPosition = y
        m.MoveXyMode = mode_val
        m.Units = unit_val

    result = run_change.dispatch_change(
        client,
        api_obj,
        f"MoveXY -> ({x}, {y}) {unit}",
        MOVE_XY,
        command="move_xy",
        values=gated_values,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_move_xy,
            target_x_um=x_um,
            target_y_um=y_um,
            tolerance=MOVE_XY.confirm_tolerance if tolerance is None else tolerance,
        ),
        max_retries=max_retries,
    )
    result["position"] = {"x": x_um * 1e-6, "y": y_um * 1e-6, "x_um": x_um, "y_um": y_um}
    return result


def move_z(client, job_name, z, unit="um", z_mode="galvo", *, max_retries=None, tolerance=None):
    """Move a z drive, ``galvo`` or ``zwide``, to an absolute position.

    Args:
        client: LAS X API client.
        job_name: The job whose z drive is moved.
        z: Target position in ``unit`` (it can be negative).
        unit: 'um', 'mm' or 'm'.
        z_mode: The drive: 'galvo' or 'zwide'.
        tolerance: How close the readback must come, in micrometres.
    """
    if z_mode not in Z_DRIVE_ENUM:
        return run_change.refused(f"Unknown z_mode '{z_mode}'. Use: {list(Z_DRIVE_ENUM)}")
    if unit not in UM_PER_UNIT:
        return run_change.refused(f"unknown unit {unit!r}. Use: {list(UM_PER_UNIT)}")
    try:
        z_um = z * UM_PER_UNIT[unit]
        gated_values = {f"z_{'galvo' if z_mode == 'galvo' else 'wide'}_um": z_um}
        refusal = run_change.limits_refusal(client, "move_z", gated_values)
        if refusal:
            return refusal
        check_z(z_um, z_mode)
    except (RuntimeError, ValueError, TypeError) as e:
        return run_change.refused(str(e))

    api_obj = request_of(client, "PyApiMoveZByJobName")
    try:
        z_use_val = getattr(type(api_obj.Model.ZUseMode), Z_DRIVE_ENUM[z_mode])
    except Exception:
        z_use_val = Z_DRIVE_ENUM[z_mode]
        log.warning("ZUseMode enum resolution failed for '%s'", z_use_val)
    unit_val = unit_enum(api_obj.Model.Units, unit)

    def setup(m):
        m.JobName = job_name
        m.RelativePosition = False
        m.ZPosition = z
        m.ZUseMode = z_use_val
        m.Units = unit_val

    return run_change.dispatch_change(
        client,
        api_obj,
        f"Z -> {z} {unit} ({z_mode})",
        MOVE_Z,
        command="move_z",
        values=gated_values,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_move_z,
            job_name=job_name,
            z_mode=z_mode,
            target_um=z_um,
            tolerance=MOVE_Z.confirm_tolerance if tolerance is None else tolerance,
            observed_after=time.time(),
        ),
        max_retries=max_retries,
    )


def confirm_move_xy(client, *, target_x_um, target_y_um, tolerance=20.0, poll_window=None):
    """Read the stage position until it is within ``tolerance`` of the target, for one window.

    Asks the direct reader, or the API (``readers.on_demand_mode``). Answers
    ``{"success": bool, "logs": [...], "last_position": ...}``.
    """
    poll_window = waiting.WINDOW_S if poll_window is None else poll_window
    observed_after = time.time()
    started = time.monotonic()
    deadline = started + poll_window
    last_position = None

    while time.monotonic() < deadline:
        pos = confirming.reading_value_after(
            readers.get_xy(
                client,
                mode=readers.on_demand_mode("xy"),
                fallback=readers.on_demand_fallback(),
                diagnostics=True,
                deadline=deadline,
            ),
            observed_after,
        )
        if pos is not None:
            last_position = pos
            dx = abs(pos["x_um"] - target_x_um)
            dy = abs(pos["y_um"] - target_y_um)
            log.debug(
                "MoveXY confirm: target=(%.1f, %.1f) actual=(%.1f, %.1f) delta=(%.2f, %.2f) um",
                target_x_um,
                target_y_um,
                pos["x_um"],
                pos["y_um"],
                dx,
                dy,
            )
            if dx < tolerance and dy < tolerance:
                return {"success": True, "logs": [], "last_position": last_position}
        time.sleep(waiting.POLL_S)

    msg = (
        f"MoveXY timeout after {time.monotonic() - started:.1f}s — "
        f"target=({target_x_um:.1f}, {target_y_um:.1f})"
    )
    log.warning(msg)
    return {"success": False, "logs": [make_log_entry("warning", msg)], "last_position": last_position}


def confirm_move_z(client, *, job_name, z_mode, target_um, tolerance=1.0, poll_window=None, observed_after=None):
    """Read the z drive until it is within ``tolerance`` of the target, for one window.

    The direct reader first, where the drive truly is; else the job's
    settings, whose reader knows they do not refresh for the drive carrying
    the job's z-stack and reads that one elsewhere. ``observed_after`` rejects
    readings from before the move was sent.
    """
    poll_window = waiting.WINDOW_S if poll_window is None else poll_window
    key = Z_DRIVE_KEY[z_mode]
    started = time.monotonic()
    deadline = started + poll_window

    while time.monotonic() < deadline:
        actual = readers.z_um_direct(key)
        if actual is None:
            settings = confirming.readback(client, job_name, deadline=deadline, observed_after=observed_after)
            try:
                actual = None if settings is None else readers.z_um_from(settings, key, client=client, job_name=job_name)
            except (KeyError, TypeError, RuntimeError, ValueError):
                actual = None
        if actual is not None:
            log.debug(
                "MoveZ confirm: target=%.2f actual=%.2f delta=%.3f um", target_um, actual, abs(actual - target_um)
            )
            if abs(actual - target_um) < tolerance:
                return {"success": True, "logs": []}
        time.sleep(waiting.POLL_S)

    msg = f"MoveZ timeout after {time.monotonic() - started:.1f}s — target={target_um:.1f} um ({z_mode})"
    log.warning(msg)
    return {"success": False, "logs": [make_log_entry("warning", msg)]}
