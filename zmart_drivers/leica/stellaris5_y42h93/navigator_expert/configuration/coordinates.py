"""The arithmetic between the stage's own coordinates and the controller's frame.

The microscope reports positions in its own stage coordinates. The controller
works in a frame whose zero point, the *origin*, was saved once for this
microscope: a position in the frame is the stage position minus the origin.
When the objective in place differs from the one the origin was saved under,
the objective calibration says how far the two objectives are offset
(called ΔT here), and that offset is taken into account as well, so that the
same number names the same point of the sample under either objective.

Everything here is plain arithmetic on numbers and dictionaries. Nothing
talks to the microscope, so each function can be checked on paper. The
session handling (what to read, what to move) is in ``procedures/frame.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from . import store as _machine

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


def objective_delta_um(
    origin: dict, translations: dict | None, current_objective: dict | None
) -> tuple:
    """ΔT = T[current objective] − T[origin's objective], in µm (x, y, z-focus).

    Zero when the origin has no objective anchor or the objective is
    unchanged. Raises RuntimeError when the objective HAS changed but the
    translations are unavailable — the caller decides whether that refuses
    (moves) or warns (reads).
    """
    origin_slot = (origin.get("objective") or {}).get("slotIndex")
    current_slot = (current_objective or {}).get("slotIndex")
    if origin_slot is None or current_slot == origin_slot:
        return (0.0, 0.0, 0.0)
    if current_slot is None:
        raise RuntimeError(
            f"objective changed since the origin was captured (slot {origin_slot} -> "
            f"unidentified) so the frame cannot be mapped; capture the origin "
            f"again under the current objective"
        )
    # The delta math and the missing-pair policy live in ONE place —
    # calibration/core/model.py — shared with the driver's swap-time
    # compensation, so the two layers can never drift apart. Imported
    # lazily, matching the connection layer's calibration imports.
    from .optical_calibration import model as _cal_model

    try:
        return _cal_model.translation_delta_um(translations, origin_slot, current_slot)
    except RuntimeError as exc:
        raise RuntimeError(
            f"objective changed since the origin was captured but {exc} — or "
            f"capture the origin again under the current objective"
        ) from exc


def frame_position(snapshot: dict, origin: dict, delta: tuple) -> dict:
    """The stage position in the controller's frame: reading − origin − ΔT, per axis.

    ``z`` is the focus: the sum of the two z drives, compared with the
    origin's focus sum.
    """
    z_focus = snapshot["z_wide_um"] + snapshot["z_galvo_um"]
    return {
        "x": snapshot["x_um"] - origin["x_um"] - delta[0],
        "y": snapshot["y_um"] - origin["y_um"] - delta[1],
        "z": z_focus - origin["z_focus_um"] - delta[2],
    }


def stage_target(x: float, y: float, z: float, origin: dict, delta: tuple) -> tuple:
    """The stage coordinates (x, y, focus) that belong to a frame position: origin + position + ΔT."""
    return (
        origin["x_um"] + x + delta[0],
        origin["y_um"] + y + delta[1],
        origin["z_focus_um"] + z + delta[2],
    )


def canvas_in_frame(stage: dict, origin: dict) -> dict:
    """Everywhere a picture can show, per axis, in the frame.

    *stage* is the stage travel from the limits (``x``, ``y``, ``z_wide`` and
    optionally ``z_galvo``). On z the frame follows the focus, so the canvas is
    the z-wide travel widened by the z-galvo travel.
    """
    galvo = stage.get("z_galvo") or [0.0, 0.0]
    return {
        "x_um": [stage["x"][0] - origin["x_um"], stage["x"][1] - origin["x_um"]],
        "y_um": [stage["y"][0] - origin["y_um"], stage["y"][1] - origin["y_um"]],
        "z_um": [
            stage["z_wide"][0] + galvo[0] - origin["z_focus_um"],
            stage["z_wide"][1] + galvo[1] - origin["z_focus_um"],
        ],
    }
