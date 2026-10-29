"""Measuring the limits: the four corners of the stage's safe travel, and the two ends of the focus.

The ZEN API does not report how far a stage can travel, so the operator
shows the driver: drive the stage to each safe corner with the joystick or
in ZEN, and record it; the four corners span the x and y ranges. The focus
is recorded the same way at its lowest and highest safe position. Each
record reads the position from ZEN, so the limits are never guessed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ...procedures import readings as _readers
from .limit_rules import STAGE_BACKSTOP_UM

CORNERS = (1, 2, 3, 4)


class CornerRecorder:
    """The four corners of the stage's safe travel, and the two ends of the focus, recorded where they stand.

    ``record(n)`` reads the stage position now and keeps it as corner ``n``;
    recording a corner again replaces it, so a notebook cell can simply be
    run again after moving the stage. ``record_focus("low")`` and
    ``record_focus("high")`` do the same for the focus. ``limits()`` needs
    all four corners; the focus ends are optional.
    """

    def __init__(self, client: Any):
        self._client = client
        self._corners: dict[int, dict[str, float]] = {}
        self._focus: dict[str, float] = {}

    def record(self, number: int) -> dict[str, float]:
        """Read where the stage stands and keep it as corner *number*; returns it."""
        if isinstance(number, bool) or number not in CORNERS:
            raise ValueError(f"the corner number must be 1, 2, 3 or 4, not {number!r}")
        position = _readers.get_xy(self._client)
        try:
            corner = {"x_um": float(position["x_um"]), "y_um": float(position["y_um"])}
        except (KeyError, TypeError, ValueError) as why:
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            ) from why
        if not all(math.isfinite(v) for v in corner.values()):
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            )
        self._corners[number] = corner
        return corner

    def record_focus(self, end: str) -> float:
        """Read where the focus stands and keep it as the ``"low"`` or ``"high"`` end of its travel; returns it."""
        if end not in ("low", "high"):
            raise ValueError(f"the focus end must be 'low' or 'high', not {end!r}")
        position = _readers.get_z(self._client)
        try:
            z_um = float(position["z_um"])
        except (KeyError, TypeError, ValueError) as why:
            raise RuntimeError(
                f"could not read the focus position for the {end} end: {position!r}"
            ) from why
        self._focus[end] = z_um
        return z_um

    def limits(self) -> dict[str, Any]:
        """The recorded corners and ends, and the ranges they span (``{"x_um": {"range": ..}, ...}``)."""
        missing = [number for number in CORNERS if number not in self._corners]
        if missing:
            raise RuntimeError(
                f"corner {missing[0]} is not recorded; move the stage there and record it"
            )
        points = [dict(self._corners[number]) for number in CORNERS]
        found = xy_limits_from_points(points)
        if len(self._focus) == 2:
            found["z_um"] = {
                "range": focus_limits_from_ends(self._focus["low"], self._focus["high"])
            }
        return {"points_um": points, "focus_um": dict(self._focus), "limits": found}


def xy_limits_from_points(
    points: Sequence[Mapping[str, float]],
    *,
    stage_envelope: Mapping[str, Sequence[float]] | None = None,
) -> dict:
    """The x and y ranges spanned by four points, checked against the backstop."""
    if len(points) != 4:
        raise ValueError(f"exactly four points are required, got {len(points)}")
    xs = [float(point["x_um"]) for point in points]
    ys = [float(point["y_um"]) for point in points]
    if not all(math.isfinite(value) for value in (*xs, *ys)):
        raise ValueError("the corners' coordinates must be finite")
    x_range, y_range = [min(xs), max(xs)], [min(ys), max(ys)]
    if x_range[0] >= x_range[1] or y_range[0] >= y_range[1]:
        raise ValueError(
            f"the four points must span a rectangle, got X={x_range}, Y={y_range}"
        )
    envelope = stage_envelope or STAGE_BACKSTOP_UM
    for axis, bounds in (("x", x_range), ("y", y_range)):
        low, high = map(float, envelope[axis])
        if bounds[0] < low or bounds[1] > high:
            raise RuntimeError(
                f"the measured {axis.upper()} range {bounds} lies outside the backstop [{low}, {high}]"
            )
    return {"x_um": {"range": x_range}, "y_um": {"range": y_range}}


def focus_limits_from_ends(low_um: float, high_um: float) -> list[float]:
    """The focus range between two recorded ends, in order, checked against the backstop."""
    bounds = [min(low_um, high_um), max(low_um, high_um)]
    if bounds[0] >= bounds[1]:
        raise ValueError(f"the two focus ends must differ, got {bounds}")
    low, high = STAGE_BACKSTOP_UM["z"]
    if bounds[0] < low or bounds[1] > high:
        raise RuntimeError(
            f"the measured focus range {bounds} lies outside the backstop [{low}, {high}]"
        )
    return bounds
