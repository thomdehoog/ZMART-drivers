"""Make the current stage position the zero point of the coordinates, and save it for every later connection.

This is the origin's setup step. It reads the stage and the objective, so it
uses the run-time readings; the saved file is what every later connection
loads (``saved_origin.py``). It moves nothing.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/origin/set_origin.py``.
"""

from __future__ import annotations

import time

from ...procedures import readings
from .. import store


def set_origin(handle) -> dict:
    """Make where the stage stands now the (0, 0, 0) of the coordinates, and save it.

    The objective in place is saved with it, so a later connection can say
    when positions are read under another objective (this driver has no
    objective calibration, so it cannot correct for the difference).

    Returns ``{"origin": {"x": 0, "y": 0, "z": 0}, "reference": <the stage
    numbers saved>, "origin_file": <its path>}``. Raises ``RuntimeError``
    when the stage cannot be read or the file cannot be saved; in the second
    case this connection already uses the new origin, but the next one would
    not, so fix the cause and run it again.
    """
    handle.require_open()
    xy = readings.xy(handle.client)
    z = readings.z(handle.client)
    nosepiece = readings.nosepiece(handle.client)
    position = nosepiece.get("position")
    handle.origin = {
        "x_um": xy["x_um"],
        "y_um": xy["y_um"],
        "z_um": z["z_um"],
        "objective": {
            "position": position,
            "name": nosepiece["objectives"].get(position),
        }
        if position
        else None,
    }
    try:
        path = store.machine().write(
            "origin",
            {"origin": handle.origin, "session": handle.hash6, "saved_at": time.time()},
        )
    except OSError as why:
        raise RuntimeError(
            f"could not save the origin ({why}). This connection already uses the new zero point, "
            "but the next connection will load the one saved before. Fix the cause and run set_origin() again."
        ) from why
    return {
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "reference": dict(handle.origin),
        "origin_file": str(path),
    }
