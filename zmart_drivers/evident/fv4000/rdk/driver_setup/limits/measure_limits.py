"""Measuring the travel: the stage corners and the focus ends, recorded where the stage stands.

The operator drives the stage to each safe corner of its travel, and the
focus to its safe low and high end, with the joystick or in FLUOVIEW, and
records each in the notebook. Each record reads the position from FLUOVIEW
itself. The four corners give the XY rectangle and the two ends the Z range;
both are checked against the physical backstop before they are published.

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
ENDS = ("low", "high")


class TravelRecorder:
    """The corners of the stage's safe travel and the ends of the focus, recorded where they stand.

    ``record_corner(n)`` reads the XY position now and keeps it as corner
    ``n``; ``record_z("low")`` and ``record_z("high")`` read the focus. A
    record made again replaces the earlier one, so a notebook cell can simply
    be run again after moving. ``limits()`` needs all four corners and both ends.
    """

    def __init__(self, client: Any):
        self._client = client
        self._corners: dict[int, dict[str, float]] = {}
        self._ends: dict[str, float] = {}

    def record_corner(self, number: int) -> dict[str, float]:
        """Read where the stage stands and keep it as corner *number*; answers it."""
        if isinstance(number, bool) or number not in CORNERS:
            raise ValueError(f"corner number must be 1, 2, 3 or 4, got {number!r}")
        position = _readers.get_xy(self._client)
        try:
            corner = {"x_um": float(position["x_um"]), "y_um": float(position["y_um"])}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            ) from exc
        if not all(math.isfinite(v) for v in corner.values()):
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            )
        self._corners[number] = corner
        return corner

    def record_z(self, end: str) -> float:
        """Read where the focus motor stands and keep it as the *end* (``"low"`` or ``"high"``) of its travel."""
        if end not in ENDS:
            raise ValueError(f"end must be 'low' or 'high', got {end!r}")
        z_um = _readers.get_z(self._client)
        if z_um is None or not math.isfinite(float(z_um)):
            raise RuntimeError(f"could not read the focus position for the {end} end")
        self._ends[end] = float(z_um)
        return self._ends[end]

    def limits(self) -> dict[str, Any]:
        """The recorded points and the ``x_um``, ``y_um`` and ``z_um`` ranges they span."""
        missing = [number for number in CORNERS if number not in self._corners]
        if missing:
            raise RuntimeError(
                f"corner {missing[0]} is not recorded; move the stage there and record it"
            )
        missing_ends = [end for end in ENDS if end not in self._ends]
        if missing_ends:
            raise RuntimeError(
                f"the {missing_ends[0]} end of the focus is not recorded; move the focus there and record it"
            )
        points = [dict(self._corners[number]) for number in CORNERS]
        limits = xy_limits_from_points(points)
        limits["z_um"] = z_limits_from_ends(self._ends["low"], self._ends["high"])
        return {"points_um": points, "z_ends_um": dict(self._ends), "limits": limits}


def xy_limits_from_points(
    points: Sequence[Mapping[str, float]],
    *,
    stage_envelope: Mapping[str, Sequence[float]] | None = None,
) -> dict:
    """The XY rectangle four recorded corners span, checked against the physical backstop."""
    if len(points) != 4:
        raise ValueError(f"exactly four points are required, got {len(points)}")
    xs = [float(point["x_um"]) for point in points]
    ys = [float(point["y_um"]) for point in points]
    if not all(math.isfinite(value) for value in (*xs, *ys)):
        raise ValueError("point coordinates must be finite")
    x_range, y_range = [min(xs), max(xs)], [min(ys), max(ys)]
    if x_range[0] >= x_range[1] or y_range[0] >= y_range[1]:
        raise ValueError(
            f"four points must span a rectangle, got X={x_range}, Y={y_range}"
        )
    envelope = stage_envelope or STAGE_BACKSTOP_UM
    for axis, bounds in (("x", x_range), ("y", y_range)):
        lo, hi = map(float, envelope[axis])
        if bounds[0] < lo or bounds[1] > hi:
            raise RuntimeError(
                f"the measured {axis.upper()} range {bounds} lies outside the physical backstop [{lo}, {hi}]"
            )
    return {"x_um": {"range": x_range}, "y_um": {"range": y_range}}


def z_limits_from_ends(
    low_um: float,
    high_um: float,
    *,
    stage_envelope: Mapping[str, Sequence[float]] | None = None,
) -> dict:
    """The focus range two recorded ends span (in either order), checked against the physical backstop."""
    lo, hi = sorted((float(low_um), float(high_um)))
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
        raise ValueError(f"the two focus ends must differ, got {low_um} and {high_um}")
    envelope_lo, envelope_hi = map(float, (stage_envelope or STAGE_BACKSTOP_UM)["z"])
    if lo < envelope_lo or hi > envelope_hi:
        raise RuntimeError(
            f"the measured Z range [{lo}, {hi}] lies outside the physical backstop [{envelope_lo}, {envelope_hi}]"
        )
    return {"range": [lo, hi]}
