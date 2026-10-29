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

#: The numbers an origin carries: the stage's x and y and the focus, in ZEN's own coordinates.
ORIGIN_NUMBER_KEYS = ("x_um", "y_um", "z_um")


def load_saved_origin() -> dict | None:
    """The newest saved origin, or None when none was ever saved.

    Returns ``{"x_um", "y_um", "z_um", "objective"}``, as :func:`set_origin`
    saves it. Raises ``RuntimeError`` when the file exists but cannot be read,
    is not valid JSON, or lacks one of these values.
    """
    path = _machine.MACHINE.origin_path()
    fix = (
        "Fix or remove that file, or connect with load_origin=False and save the "
        "origin again with the driver's set_origin step."
    )
    try:
        payload = _machine.MACHINE.read_origin()
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"the saved origin at {path} could not be read ({why}). {fix}"
        ) from why
    if payload is None:
        log.warning(
            "no origin has been saved on this microscope yet, so positions are ZEN's own stage "
            "coordinates. Run the driver's set_origin step once to choose a zero point."
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
    # The objective lets the driver refuse a move under another objective
    # when no calibration can translate between the two, so a file that does
    # not say which objective it was saved under is refused.
    if "objective" not in stored:
        raise RuntimeError(
            f"the saved origin at {path} does not record which objective it was saved under. {fix}"
        )
    objective = stored["objective"]
    if objective is not None and not isinstance(objective, dict):
        raise RuntimeError(
            f"the saved origin at {path} records its objective in an unexpected form ({objective!r}). {fix}"
        )
    origin["objective"] = dict(objective) if objective is not None else None
    log.info("loaded the saved origin from %s", path)
    return origin
