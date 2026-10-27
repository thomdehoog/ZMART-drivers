"""Read the saved origin, and refuse one that is damaged.

A damaged origin file stops the connection with how to fix it. Guessing a
zero origin would quietly shift every position in the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .. import store as _machine

log = logging.getLogger(__name__)

#: The numbers an origin must carry. They match what set_origin records, so an
#: origin loaded at connect behaves exactly like a freshly captured one.
ORIGIN_NUMBER_KEYS = ("x_um", "y_um", "z_um")


def empty_origin() -> dict[str, Any]:
    """The origin of a session without a saved one: plain stage coordinates, under no particular objective."""
    return {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0, "objective": None}


def load_saved_origin(machine: _machine.MachineProfile | None = None) -> dict | None:
    """The newest saved origin, or None when none exists (with a warning that positions are stage coordinates).

    Raises ``RuntimeError`` when the file exists but cannot be read, is not
    valid JSON, or is missing one of its values; the error says how to fix
    it. ``objective`` is the nosepiece slot and name the origin was captured
    under, or None, and it is what lets a later objective change be
    compensated with the calibration, or refused when none covers it.
    """
    machine = machine if machine is not None else _machine.MACHINE
    path = machine.latest_path("origin")
    fix = (
        "Fix or remove that file, or connect with load_origin=False and capture the origin "
        "again with the driver's set_origin()."
    )
    try:
        payload = machine.read_record("origin")
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"the saved origin at {path} could not be read ({why}). {fix}"
        ) from why
    if payload is None:
        log.warning(
            "no origin has been saved on this microscope yet, so positions are plain stage "
            "coordinates. Run the driver's set_origin() once to choose a zero point; it is "
            "then loaded at every connect."
        )
        return None
    stored = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        raise RuntimeError(f"the saved origin at {path} has no 'origin' section. {fix}")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    origin: dict[str, Any] = {}
    for key in ORIGIN_NUMBER_KEYS:
        value = stored.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(
                f"the saved origin at {path} has no usable value for {key!r} (found {value!r}). {fix}"
            )
        origin[key] = float(value)
    if "objective" not in stored:
        raise RuntimeError(
            f"the saved origin at {path} does not record which objective it was captured under. {fix}"
        )
    objective = stored["objective"]
    if objective is not None and not (
        isinstance(objective, dict) and isinstance(objective.get("slot"), int)
    ):
        raise RuntimeError(
            f"the saved origin at {path} records its objective in an unexpected form ({objective!r}). {fix}"
        )
    origin["objective"] = dict(objective) if objective is not None else None
    log.info("loaded the saved origin from %s", path)
    return origin
