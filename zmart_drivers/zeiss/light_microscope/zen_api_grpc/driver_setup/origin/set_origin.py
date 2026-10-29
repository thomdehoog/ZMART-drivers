"""Make the current stage position the zero point of the frame, and save it.

This is the setup step of the origin. It reads the stage through the
run-time readings; the saved file is what every later connection loads
(``saved_origin.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import time

from ...procedures.absolute_coordinate_system import hardware_snapshot
from .. import store as _machine


def set_origin(handle) -> dict:
    """Make the current stage position the (0, 0, 0) of the frame, and save it for every later connection.

    It reads the stage's x and y, the focus and the objective in place, and
    records them. The objective is saved with it so that a later objective
    change can be corrected with the calibration, or refused when no
    calibration covers the pair. It moves nothing, so the limits are not asked.

    Returns ``{"origin": {"x": 0, "y": 0, "z": 0}, "reference": <the ZEN
    values saved>, "origin_file": <the saved file>}``. Raises ``RuntimeError``
    when the position cannot be read or the file cannot be saved; in the
    second case this session already uses the new origin, but the next
    connection would not, so fix the cause and run it again.
    """
    handle.require_open()
    snap = hardware_snapshot(handle)
    objective = snap["objective"]
    handle.origin = {
        "x_um": snap["x_um"],
        "y_um": snap["y_um"],
        "z_um": snap["z_um"],
        "objective": {
            "position": objective.get("position"),
            "name": objective.get("name"),
        },
    }
    payload = {
        "origin": handle.origin,
        "session_hash6": handle.hash6,
        "captured_at": time.time(),
    }
    try:
        path = _machine.MACHINE.write_origin(payload)
    except OSError as why:
        raise RuntimeError(
            f"could not save the origin: {why}. This session already uses the new zero point, but the "
            "next connection will load the previous one. Fix the cause and run set_origin again."
        ) from why
    return {
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "reference": dict(handle.origin),
        "origin_file": str(path),
    }
