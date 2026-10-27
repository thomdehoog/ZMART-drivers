"""Read the saved origin, and refuse one that is damaged.

A damaged origin file stops the connection with how to fix it. Guessing a zero
origin would quietly shift every position in the experiment.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .. import store as _machine

log = logging.getLogger(__name__)

#: The numbers an origin must carry: the stage, the focus motor and the piezo,
#: in micrometres, as FLUOVIEW reported them when the origin was captured.
ORIGIN_NUMBER_KEYS = ("x_um", "y_um", "z_um", "z_piezo_um")


def empty_origin() -> dict[str, Any]:
    """The origin a session starts with when none was saved: FLUOVIEW's own zero, under no particular objective."""
    return {**{key: 0.0 for key in ORIGIN_NUMBER_KEYS}, "objective": None}


def load_saved_origin() -> dict | None:
    """The newest saved origin, or None when none has been saved yet (said in a warning).

    Raises ``RuntimeError`` when the file exists but cannot be read, is not
    valid JSON, or lacks one of the values, with how to fix it.
    """
    path = _machine.MACHINE.origin_path()
    fix = (
        "Fix or remove that file, or connect with load_origin=False and capture the origin "
        "again with the driver's set_origin step."
    )
    try:
        payload = _machine.MACHINE.read_origin()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"the saved origin at {path} could not be read ({exc}). {fix}"
        ) from exc
    if payload is None:
        log.warning(
            "no origin has been saved on this microscope yet, so positions are FLUOVIEW's own "
            "stage coordinates. Run the driver's set_origin step once to choose a zero point."
        )
        return None
    stored = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        raise RuntimeError(f"the saved origin at {path} has no 'origin' section. {fix}")
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
    # Which objective the origin was captured under decides whether a later
    # objective change can be compensated, so a file that does not say is refused.
    if "objective" not in stored:
        raise RuntimeError(
            f"the saved origin at {path} does not record which objective it was captured under. {fix}"
        )
    objective = stored["objective"]
    if objective is not None and not isinstance(objective, dict):
        raise RuntimeError(
            f"the saved origin at {path} records its objective in an unexpected form ({objective!r}). {fix}"
        )
    origin["objective"] = dict(objective) if objective is not None else None
    log.info("loaded the saved origin from %s", path)
    return origin
