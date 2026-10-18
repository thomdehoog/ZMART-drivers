"""Read the saved frame origin, and refuse one that is damaged.

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

# The values a frame origin must carry. They match what set_origin captures,
# so an origin loaded at connect behaves exactly like a freshly captured one.
_ORIGIN_NUMBER_KEYS = ("x_um", "y_um", "z_wide_um", "z_galvo_um", "z_focus_um")


def load_saved_origin() -> dict | None:
    """Read the newest saved frame origin, or return None when none exists.

    The origin lives in this microscope's ``origin/<datetime>/origin.json``
    (see ``config/machine.py``); the newest folder wins, just as for the
    limits, orientation and calibration.

    Returns:
        The origin with the same fields :func:`set_origin` captures
        (``x_um``, ``y_um``, ``z_wide_um``, ``z_galvo_um``, ``z_focus_um``
        and ``objective``), or None when no origin has ever been saved. In
        that case a warning explains that the frame is the plain stage
        coordinates.

    Raises:
        RuntimeError: when the file exists but cannot be read, is not valid
            JSON, or is missing one of the values above. Guessing a zero
            origin here would quietly shift every position in the
            experiment, so the connection stops and says how to fix it.
    """
    path = _machine.MACHINE.origin_path()
    fix = (
        "Fix or remove that file, or connect with load_origin=False and capture "
        "the origin again with the driver's set_origin step."
    )
    try:
        payload = _machine.MACHINE.read_origin()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"the saved frame origin at {path} could not be read ({exc}). {fix}"
        ) from exc
    if payload is None:
        log.warning(
            "no frame origin has been saved on this microscope yet, so positions "
            "are plain stage coordinates. Run the driver's set_origin step once "
            "to choose a zero point; it will then be loaded at every connect."
        )
        return None
    stored = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        raise RuntimeError(
            f"the saved frame origin at {path} has no 'origin' section. {fix}"
        )
    origin: dict[str, Any] = {}
    for key in _ORIGIN_NUMBER_KEYS:
        value = stored.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(
                f"the saved frame origin at {path} has no usable value for {key!r} "
                f"(found {value!r}). {fix}"
            )
        origin[key] = float(value)
    # The objective is what lets the driver refuse a move under a different
    # objective when no calibration can translate between the two, so a
    # file that does not say which objective it was captured under is
    # refused rather than treated as "no objective recorded".
    if "objective" not in stored:
        raise RuntimeError(
            f"the saved frame origin at {path} does not record which objective it "
            f"was captured under. {fix}"
        )
    objective = stored["objective"]
    if objective is not None and not isinstance(objective, dict):
        raise RuntimeError(
            f"the saved frame origin at {path} records its objective in an unexpected "
            f"form ({objective!r}). {fix}"
        )
    origin["objective"] = dict(objective) if objective is not None else None
    log.info("loaded the saved frame origin from %s", path)
    return origin
