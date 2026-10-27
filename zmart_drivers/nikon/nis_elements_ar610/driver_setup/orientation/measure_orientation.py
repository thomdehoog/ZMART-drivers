"""Measure which way the camera is turned relative to the stage, with two small stage steps.

The idea is simple: take a picture, move the stage a little along x, take
another, and see which way the picture shifted. Then the same along y. The
two picture shifts, put side by side, are a 2x2 matrix that takes a stage
step to a picture shift; the nearest of the eight whole turns is this
microscope's orientation. The size of the shifts, against the size of the
step, also gives the pixel size on the sample.

The shift between two pictures is found by *phase correlation*: a standard
way of comparing two pictures that finds by how many pixels one is moved
against the other, even when the sample is faint, as long as it has some
texture. It needs a sample with features in view; a blank field gives no
answer, and the measurement says so.

Every move goes through the driver's checked moves, so the limits hold, and
the stage is put back where it was.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ...procedures import absolute_coordinate_system as _frame
from ...procedures import acquire as _acquire
from ...procedures import changes as _changes
from . import Orientation, adopt_orientation, nearest_orientation

log = logging.getLogger(__name__)

#: How far the stage steps for the measurement, when the caller does not say.
#: Big enough for a clear shift at any magnification, small enough to stay
#: within one field of view of a 10x objective.
STEP_UM = 50.0
#: A correlation peak below this, against the mean, means the two pictures
#: share too little texture to trust the shift.
MIN_PEAK_RATIO = 3.0


def picture_shift(before, after) -> tuple[float, float, float]:
    """By how many pixels *after* is shifted against *before*: ``(columns, rows, peak ratio)``.

    Positive columns mean the content moved to the right, positive rows mean
    it moved down. The peak ratio says how clear the answer is (the height of
    the correlation peak against the average); below ``MIN_PEAK_RATIO`` the
    shift is not to be trusted.
    """
    import numpy as np

    a = np.asarray(before, dtype=float)
    b = np.asarray(after, dtype=float)
    if a.shape != b.shape or a.ndim != 2:
        raise ValueError(
            f"the two pictures must be two-dimensional and the same size, got {a.shape} and {b.shape}"
        )
    a = a - a.mean()
    b = b - b.mean()
    spectrum = np.fft.fft2(b) * np.conj(np.fft.fft2(a))
    magnitude = np.abs(spectrum)
    magnitude[magnitude == 0] = 1.0
    correlation = np.real(np.fft.ifft2(spectrum / magnitude))
    peak = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    rows, columns = int(peak[0]), int(peak[1])
    # A shift past half the picture wraps around to a negative one.
    if rows > a.shape[0] // 2:
        rows -= a.shape[0]
    if columns > a.shape[1] // 2:
        columns -= a.shape[1]
    mean = float(np.abs(correlation).mean())
    ratio = float(correlation[peak] / mean) if mean > 0 else 0.0
    return float(columns), float(rows), ratio


def measure(handle, *, step_um: float = STEP_UM) -> dict:
    """Step the stage along x and y, compare the pictures, and answer the measured orientation.

    Answers ``{"orientation": Orientation, "matrix": the measured 2x2 matrix
    (stage um per picture pixel), "pixel_size_um": the pixel size on the
    sample, "shifts": the raw shifts, "peak_ratios": how clear each was}``.
    Raises ``RuntimeError`` when a picture is blank or a move fails. The
    stage is moved back to where it started.
    """
    import numpy as np

    handle.require_open()
    start = _frame.hardware_snapshot(handle)
    x0, y0, z0 = start["x_um"], start["y_um"], start["z_um"]
    reference = _acquire.snap_picture(handle)
    shifts, ratios = {}, {}
    try:
        for axis, target in (
            ("x", (x0 + step_um, y0, z0)),
            ("y", (x0, y0 + step_um, z0)),
        ):
            _checked_move(handle, *target)
            moved = _acquire.snap_picture(handle)
            columns, rows, ratio = picture_shift(reference, moved)
            shifts[axis], ratios[axis] = (columns, rows), ratio
            log.info(
                "orientation: a %g um step along %s shifted the picture by (%g, %g) px (peak ratio %.1f)",
                step_um,
                axis,
                columns,
                rows,
                ratio,
            )
            if ratio < MIN_PEAK_RATIO:
                raise RuntimeError(
                    f"the pictures before and after the {axis} step share too little texture "
                    f"(peak ratio {ratio:.1f}); put a sample with features in view and measure again"
                )
    finally:
        _checked_move(handle, x0, y0, z0)
    # The field of view moves with the stage, and the content of the picture
    # moves the other way: a feature that was in the middle ends up further
    # left after a step to the right. So the field's shift, which is what
    # the stage step made, is minus the content's shift. The matrix below
    # takes a stage step (x, y) to the field's shift in the picture
    # (columns, rows).
    picture_from_stage = -np.array(
        [[shifts["x"][0], shifts["y"][0]], [shifts["x"][1], shifts["y"][1]]],
        dtype=float,
    )
    if abs(np.linalg.det(picture_from_stage)) < 1e-9:
        raise RuntimeError(
            "the x and y steps shifted the picture the same way; the measurement cannot be used"
        )
    stage_from_picture = np.linalg.inv(picture_from_stage) * step_um
    orientation = nearest_orientation(stage_from_picture)
    pixels_per_step = [float(np.hypot(*shifts[axis])) for axis in ("x", "y")]
    pixel_size_um = step_um / float(np.mean(pixels_per_step))
    return {
        "orientation": orientation,
        "matrix": stage_from_picture.tolist(),
        "pixel_size_um": pixel_size_um,
        "shifts": shifts,
        "peak_ratios": ratios,
        "step_um": step_um,
    }


def measure_and_adopt(handle, *, step_um: float = STEP_UM, notebook_paths=()) -> dict:
    """Measure the orientation and save it as this microscope's; answers both reports."""
    measured = measure(handle, step_um=step_um)
    saved = adopt_orientation(
        measured["orientation"],
        pixel_size_um=measured["pixel_size_um"],
        archive_paths=notebook_paths,
    )
    handle.orientation = measured["orientation"]
    return {**measured, "saved": saved}


def _checked_move(handle, x_um: float, y_um: float, z_um: float) -> None:
    """A raw stage move through the dispatcher; a refused or unconfirmed move stops the measurement."""
    result = _changes.move_xyz(handle.client, x_um, y_um, z_um)
    if not result.get("success") or not result.get("confirmed"):
        raise RuntimeError(
            f"the stage did not arrive at ({x_um:g}, {y_um:g}, {z_um:g}) um: {result.get('message')}"
        )


__all__ = ["Orientation", "measure", "measure_and_adopt", "picture_shift"]
