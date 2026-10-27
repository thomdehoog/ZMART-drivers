"""Measuring the safe travel: record the stage at the corners the operator drives it to.

The limits NIS-Elements knows are the stage's whole travel. The safe travel
for a sample holder is usually smaller, and only the operator can say where
it ends: they drive the stage to each corner with the joystick, with the
objective clear of everything, and record it. The rectangle those corners
span becomes the x and y limits; the z range is typed in the notebook, since
a focus drive's safe range depends on the sample and the objective.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ...procedures import readings


class CornerRecorder:
    """Records where the stage stands at up to four corners, and spans the x and y limits."""

    def __init__(self, client) -> None:
        self.client = client
        self.corners: dict[int, dict] = {}

    def record(self, number: int) -> dict:
        """Read the stage now and keep it as corner *number* (1 to 4); recording again replaces it."""
        if number not in (1, 2, 3, 4):
            raise ValueError("a corner is numbered 1, 2, 3 or 4")
        position = readings.get_xyz(self.client)
        if position is None:
            raise RuntimeError(
                "the stage position could not be read; check that the bridge answers"
            )
        self.corners[number] = {
            "x_um": position["x_um"],
            "y_um": position["y_um"],
            "z_um": position["z_um"],
        }
        print(
            f"corner {number}: x {position['x_um']:.1f} um, y {position['y_um']:.1f} um"
        )
        return dict(self.corners[number])

    def limits(self) -> dict:
        """The x and y ranges spanned by the recorded corners, in the limits file's shape."""
        if len(self.corners) < 2:
            raise RuntimeError("record at least two corners first")
        xs = [corner["x_um"] for corner in self.corners.values()]
        ys = [corner["y_um"] for corner in self.corners.values()]
        return {
            "corners": {
                number: dict(corner) for number, corner in sorted(self.corners.items())
            },
            "limits": {
                "x_um": {"range": [min(xs), max(xs)]},
                "y_um": {"range": [min(ys), max(ys)]},
            },
        }
