"""Make the current stage position the zero point of the frame, and save it.

This is the setup step of the origin. It reads the stage through the
run-time procedures; the saved file is what every later connection loads
(``saved_origin.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import time

from ...procedures.absolute_coordinate_system import hardware_snapshot
from .. import store as _machine


def set_origin(handle) -> dict:
    """Make the current stage position the (0, 0, 0) of the frame and save it for later connections.

    Drive the stage to the spot that should count as zero, in NIS-Elements
    or with the joystick, then call this once::

        import zmart_drivers.nikon.ti2.nis_elements as nis_elements
        nis_elements.connect().set_origin()

    It reads the stage, the focus drive and the objective in use, and records
    them as the zero point. The objective is saved with it so that a later
    objective change can be compensated with the calibration, or refused
    when no calibration covers the two objectives. The session that called
    this uses the new origin straight away.

    Answers ``{"origin": {"x": 0, "y": 0, "z": 0}, "reference": the stage
    values recorded, "origin_file": the saved file}``. Raises
    ``RuntimeError`` when the position cannot be read or the file cannot be
    saved; in the second case this session already uses the new origin, but
    the next connect would not, so fix the cause and run it again.
    """
    handle.require_open()
    snap = hardware_snapshot(handle)
    handle.origin = {
        "x_um": snap["x_um"],
        "y_um": snap["y_um"],
        "z_um": snap["z_um"],
        "objective": snap["objective"],
    }
    payload = {
        "origin": handle.origin,
        "session_hash6": handle.hash6,
        "captured_at": time.time(),
    }
    try:
        path = _machine.MACHINE.write_record("origin", payload)
    except OSError as why:
        raise RuntimeError(
            f"could not save the origin: {why}. This connection already uses the new zero point, "
            "but the next connect will load the previously saved origin. Fix the cause and run "
            "set_origin again."
        ) from why
    return {
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "reference": dict(handle.origin),
        "origin_file": str(path),
    }
