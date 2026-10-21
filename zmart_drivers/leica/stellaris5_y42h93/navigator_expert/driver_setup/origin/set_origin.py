"""Make the current stage position the zero point of the frame, and save it.

This is the setup step of the origin. It reads the stage, so it uses the
run-time procedures that read it; the saved file is what every later
connection loads (``saved_origin.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import time

from .. import store as _machine
from ...procedures.absolute_coordinate_system.frame import hardware_snapshot
from ...session.handle import Session, require_open


def set_origin(handle: Session) -> dict:
    """Make the current stage position the (0, 0, 0) of the frame and save it.

    This is a setup step for the driver, not a controller command. The
    operator runs it once, working with the driver directly, for example::

        import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as navigator_expert
        navigator_expert.connect().set_origin()

    It reads stage XY, both z drives (z-wide and z-galvo), their sum (the
    focus) and the current objective, and records them as the zero point.
    The record is saved to this microscope's configuration as a new
    ``origin/<datetime>/origin.json``, and every later :func:`connect` loads
    the newest one, so all sessions share the same frame until the origin
    is captured again. The objective is saved with it so that a later
    objective change can be compensated with the calibration, or refused
    when no calibration can translate between the two objectives.

    The handle passed in starts using the new origin straight away.

    Returns:
        ``{"origin": {"x": 0, "y": 0, "z": 0}, "reference": <the captured
        stage values>, "origin_file": <path of the saved origin.json>}``.

    Raises:
        RuntimeError: when the position cannot be read, or when the file
            cannot be saved. In the second case this handle already uses the
            new origin, but the next connect would not, so fix the cause and
            run ``set_origin`` again.

    It is not checked against the limits, because it moves nothing: it only
    reads the current position and writes a small file. (``set_origin``
    stays in the ``limits.json`` ``functions`` list so machine files remain
    explicit about it.)
    """
    require_open(handle)
    snap = hardware_snapshot(handle)
    handle.origin = {
        "x_um": snap["x_um"],
        "y_um": snap["y_um"],
        "z_wide_um": snap["z_wide_um"],
        "z_galvo_um": snap["z_galvo_um"],
        "z_focus_um": snap["z_wide_um"] + snap["z_galvo_um"],
        "objective": snap["objective"],
    }
    payload = {
        "origin": handle.origin,
        "job": snap["job"],
        "session_hash6": handle.hash6,
        "captured_at": time.time(),
    }
    try:
        path = _machine.MACHINE.write_origin(payload)
    except OSError as exc:
        # This handle already uses the new origin, but the next connect will
        # load the previous saved one. Say so clearly, because otherwise the
        # two would disagree without anyone noticing.
        raise RuntimeError(
            f"could not save the origin: {exc}. This connection already uses the "
            f"new zero point, but the next connect will load the previously saved "
            f"origin. Fix the cause and run set_origin again."
        ) from exc
    return {
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "reference": dict(handle.origin),
        "origin_file": str(path),
    }
