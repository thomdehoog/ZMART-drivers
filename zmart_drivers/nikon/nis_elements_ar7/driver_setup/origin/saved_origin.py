"""Read the saved origin, the stage position that counts as (0, 0, 0), and refuse one that is damaged.

A damaged origin file stops the connection, saying how to fix it: guessing
a zero origin would quietly shift every position of the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/origin/saved_origin.py``.
"""

from __future__ import annotations

import logging
import math

from .. import store

log = logging.getLogger(__name__)

#: The numbers an origin holds, in micrometres of NIS's own stage coordinates.
NUMBERS = ("x_um", "y_um", "z_um")

#: The origin used when none is saved: plain stage coordinates.
NO_ORIGIN = {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0, "objective": None}


def load_saved_origin(machine: store.MachineProfile | None = None) -> dict | None:
    """The newest saved origin, or None when none was ever saved.

    Returns ``{"x_um", "y_um", "z_um", "objective"}``, where ``objective`` is
    ``{"position", "name"}`` of the objective it was saved under, or None.
    Raises ``RuntimeError`` when the file exists but cannot be used.
    """
    machine = machine or store.machine()
    path = machine.saved_path("origin")
    fix = "Fix or remove that file, or connect with load_origin=False and save the origin again with set_origin()."
    try:
        payload = machine.read("origin")
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"the saved origin at {path} could not be read ({why}). {fix}"
        ) from why
    if payload is None:
        log.warning(
            "no origin is saved on this microscope yet, so positions are plain stage coordinates; run set_origin() once"
        )
        return None
    stored = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        raise RuntimeError(f"the saved origin at {path} has no 'origin' section. {fix}")  # noqa: TRY004 -- a damaged setting stops the connection
    origin = {}
    for key in NUMBERS:
        value = stored.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(
                f"the saved origin at {path} has no usable {key!r} (found {value!r}). {fix}"
            )
        origin[key] = float(value)
    objective = stored.get("objective")
    if objective is not None and not isinstance(objective, dict):
        raise RuntimeError(
            f"the saved origin at {path} records its objective as {objective!r}, not as a position and a name. {fix}"
        )
    origin["objective"] = dict(objective) if objective else None
    log.info("loaded the saved origin from %s", path)
    return origin
