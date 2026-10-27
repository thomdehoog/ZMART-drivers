"""Measure the safe stage travel: record the corners the operator drives the stage to.

The ``set_limits`` notebook connects, and the operator drives the stage with
the joystick (or in NIS) to each corner of the travel that is safe for the
sample holder in use, and runs one cell per corner. Each cell reads where the
stage stands. The x and y limits are then the rectangle the corners span.
The same is done for z: the lowest and the highest focus position that are
safe, with the objective clear of the sample.

Nothing here moves the stage; it only reads it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``CornerRecorder``.
"""

from __future__ import annotations

from typing import Any

from ...procedures import readings


class CornerRecorder:
    """The corners and the z ends the operator records, and the limits they span."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.corners: dict[int, dict] = {}
        self.z_ends: dict[str, float] = {}

    def record(self, corner: int) -> dict:
        """Read where the stage stands, as corner *corner* (1 to 4); what was read."""
        if corner not in (1, 2, 3, 4):
            raise ValueError("a corner is numbered 1, 2, 3 or 4")
        xy = readings.xy(self.client)
        self.corners[corner] = xy
        print(f"corner {corner}: x {xy['x_um']:.1f} um, y {xy['y_um']:.1f} um")
        return xy

    def record_z(self, end: str) -> float:
        """Read where the focus stands, as the ``"low"`` or ``"high"`` end of the safe z travel."""
        if end not in ("low", "high"):
            raise ValueError('a z end is "low" or "high"')
        z = readings.z(self.client)["z_um"]
        self.z_ends[end] = z
        print(f"z {end}: {z:.1f} um")
        return z

    def limits(self) -> dict:
        """The stage ranges the recorded corners and z ends span, as ``limits.json`` writes them."""
        missing = [n for n in (1, 2, 3, 4) if n not in self.corners] + [
            e for e in ("low", "high") if e not in self.z_ends
        ]
        if missing:
            raise RuntimeError(f"record these first: {missing}")
        xs = [c["x_um"] for c in self.corners.values()]
        ys = [c["y_um"] for c in self.corners.values()]
        zs = sorted(self.z_ends.values())
        return {
            "x_um": {"range": [min(xs), max(xs)]},
            "y_um": {"range": [min(ys), max(ys)]},
            "z_um": {"range": [zs[0], zs[1]]},
        }
