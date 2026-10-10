"""XY limits measured at four corners of the stage, for the limits notebook.

The operator drives the stage to each safe corner of the travel, with the
joystick or in LAS X, and records it in the notebook. At each corner the
stage position is read from the API: the limits are saved for good, so they
are never taken from a log entry that may be stale. The four recorded
corners give the inclusive XY rectangle, checked against the hard stage
backstop.

Positions are never made in the Navigator Expert, for the setup neither: the
LAS X scanning template is not read. No controller API is involved.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ..dispatcher import read as _readers
from ..dispatcher.checks import STAGE_BACKSTOP_UM

CORNERS = (1, 2, 3, 4)


class CornerRecorder:
    """The four corners of the stage's safe travel, recorded where the stage stands.

    ``record(n)`` reads the stage position now and keeps it as corner ``n``;
    recording a corner again replaces it, so a notebook cell can simply be
    run again after moving the stage. ``limits()`` needs all four.
    """

    def __init__(self, client: Any):
        self._client = client
        self._corners: dict[int, dict[str, float]] = {}

    def record(self, number: int) -> dict[str, float]:
        """Read where the stage stands and keep it as corner *number*; returns it."""
        if isinstance(number, bool) or number not in CORNERS:
            raise ValueError(f"corner number must be 1, 2, 3 or 4, got {number!r}")
        position = _readers.get_xy(self._client, mode="api")
        try:
            x_um = float(position["x_um"])
            y_um = float(position["y_um"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            ) from exc
        if not (math.isfinite(x_um) and math.isfinite(y_um)):
            raise RuntimeError(
                f"could not read the stage position for corner {number}: {position!r}"
            )
        corner = {"x_um": x_um, "y_um": y_um}
        self._corners[number] = corner
        return corner

    def limits(self) -> dict[str, Any]:
        """The four corners and the XY limits they span."""
        missing = [number for number in CORNERS if number not in self._corners]
        if missing:
            raise RuntimeError(
                f"corner {missing[0]} is not recorded; move the stage there and record it"
            )
        points = [dict(self._corners[number]) for number in CORNERS]
        return {"points_um": points, "limits": xy_limits_from_points(points)}


def xy_limits_from_points(
    points: Sequence[Mapping[str, float]],
    *,
    stage_envelope: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, dict[str, list[float]]]:
    """Compute inclusive XY ranges and verify them against the hard backstop."""
    if len(points) != 4:
        raise ValueError(f"exactly four points are required, got {len(points)}")
    xs = [float(point["x_um"]) for point in points]
    ys = [float(point["y_um"]) for point in points]
    if not all(math.isfinite(value) for value in (*xs, *ys)):
        raise ValueError("point coordinates must be finite")

    x_range = [min(xs), max(xs)]
    y_range = [min(ys), max(ys)]
    if x_range[0] >= x_range[1] or y_range[0] >= y_range[1]:
        raise ValueError(
            f"four points must span a non-zero rectangle, got X={x_range}, Y={y_range}"
        )

    envelope = stage_envelope or STAGE_BACKSTOP_UM
    for axis, bounds in (("x", x_range), ("y", y_range)):
        envelope_min, envelope_max = map(float, envelope[axis])
        if bounds[0] < envelope_min or bounds[1] > envelope_max:
            raise RuntimeError(
                f"adaptive {axis.upper()} range {bounds} lies outside the maximum "
                f"stage envelope [{envelope_min}, {envelope_max}]"
            )
    return {
        "x_um": {"range": x_range},
        "y_um": {"range": y_range},
    }
