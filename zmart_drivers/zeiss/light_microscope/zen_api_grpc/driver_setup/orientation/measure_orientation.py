"""Measuring the orientation and the pixel size: three snaps, two small stage moves.

The driver takes a snap where the stage stands, moves the stage a little
along x and takes another, then back and a little along y and a third, and
finally returns. Between two snaps the sample appears shifted in the
picture; how far and in which direction it shifted, found by phase
correlation (a standard way to measure the shift between two pictures of
the same scene), gives both how the picture is turned against the stage and
how many micrometres one pixel is.

What the sample under the objective needs: some structure (cells, beads, a
grid), sharp enough to recognise after a move of ``MOVE_UM``. A blank field
gives no shift to measure, and the measurement says so.

The snaps are ordinary ZEN snaps with the loaded experiment; their CZI files
are read with ZEISS's ``pylibCZIrw`` and deleted afterwards, so they do not
clutter ZEN's image folder.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...procedures import changes
from ...procedures import readings as _readers
from ...procedures.absolute_coordinate_system import hardware_snapshot
from ...procedures.save_data.save import wait_for_file, where_zen_put_it
from . import Orientation, orientation_config, orientation_from_image_to_stage

#: How far the stage moves for each measurement, in micrometres. Large enough
#: to shift the picture by many pixels, small enough to stay within the field.
MOVE_UM = 15.0

#: How close the measured matrix must come to one of the eight turns.
MATRIX_TOLERANCE = 0.2

#: How much the pixel sizes measured along x and along y may differ, as a share.
PIXEL_SIZE_AGREEMENT = 0.05


def read_plane(path: Path):
    """The first plane of a CZI as a 2-D array of numbers (the first channel, slice and time point)."""
    import numpy as np
    from pylibCZIrw import czi as pyczi

    with pyczi.open_czi(str(path)) as document:
        plane = document.read(plane={"C": 0, "Z": 0, "T": 0})
    plane = np.asarray(plane, dtype=float)
    return plane[..., 0] if plane.ndim == 3 else plane


def phase_shift(before, after) -> tuple[float, float, float]:
    """How far the scene moved from *before* to *after*, in pixels (columns, rows), and how clear the peak was.

    The peak's height against the mean of the correlation says how sure the
    answer is: a blank or noisy field gives a low one.
    """
    import numpy as np

    a = before - before.mean()
    b = after - after.mean()
    cross = np.fft.fft2(b) * np.conj(np.fft.fft2(a))
    cross /= np.abs(cross) + 1e-12
    correlation = np.real(np.fft.ifft2(cross))
    row, col = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    height, width = correlation.shape
    if row > height // 2:
        row -= height
    if col > width // 2:
        col -= width
    clarity = float(correlation.max() / (np.abs(correlation).mean() + 1e-12))
    return float(col), float(row), clarity


def orientation_from_shifts(
    move_um: float, shift_x: tuple[float, float], shift_y: tuple[float, float]
) -> tuple[Orientation, list[float], list[list[float]]]:
    """The turn and the pixel size from the picture's shifts for a stage move along x and along y.

    A pixel at picture offset u sees the stage point S + p·M·u, where M is
    the image-to-stage matrix and p the pixel size. After the stage moves by
    Δ, a feature appears shifted by −M⁻¹·Δ/p pixels. So the two shifts give
    M⁻¹ = −(p/d)·[s_x s_y], and p = d / |s|.
    """
    import numpy as np

    sx, sy = np.asarray(shift_x, dtype=float), np.asarray(shift_y, dtype=float)
    length_x, length_y = float(np.hypot(*sx)), float(np.hypot(*sy))
    if length_x < 1 or length_y < 1:
        raise RuntimeError(
            "the picture did not shift when the stage moved: the field may be blank, or the stage did not "
            "move. Put a sample with structure under the objective and focus on it"
        )
    pixel_x, pixel_y = move_um / length_x, move_um / length_y
    if abs(pixel_x - pixel_y) > PIXEL_SIZE_AGREEMENT * max(pixel_x, pixel_y):
        raise RuntimeError(
            f"the pixel size measured along x ({pixel_x:.4f} um) and along y ({pixel_y:.4f} um) disagree; "
            "the field may have too little structure, or the stage moved further than asked. Measure again"
        )
    inverse = -np.column_stack([sx / length_x, sy / length_y])
    matrix = np.linalg.inv(inverse)
    if np.max(np.abs(matrix - np.round(matrix))) > MATRIX_TOLERANCE:
        raise RuntimeError(
            f"the measured image-to-stage matrix {matrix.round(3).tolist()} is not a quarter turn; measure again"
        )
    orientation = orientation_from_image_to_stage(matrix)
    return orientation, [pixel_x, pixel_y], matrix.round(4).tolist()


def measure_orientation(handle: Any, *, move_um: float = MOVE_UM) -> dict:
    """Measure the orientation and the frame where the stage stands, and come back to it.

    Needs a loaded experiment and ``pylibCZIrw``. Every move goes through the
    limits as usual. Returns ``{"orientation", "frame", "matrix", "clarity",
    "shifts_px", "document", "planes"}``, where ``document`` is ready for
    :func:`adopt_orientation` and ``planes`` holds the three snaps as arrays.
    """
    handle.require_open()
    experiment = handle.experiment
    if not experiment:
        raise RuntimeError(
            "load an experiment first (set_state or connect with experiment=...): the snaps use it"
        )
    zen_folder = handle.image_output_path or _readers.get_image_output_path(
        handle.client
    )
    start = hardware_snapshot(handle)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []

    def snap(tag: str):
        result = changes.acquire(
            handle.client,
            experiment["id"],
            experiment["name"],
            f"zmart_orientation_{stamp}_{tag}",
            snap=True,
        )
        if not result["success"]:
            raise RuntimeError(
                f"the snap for the orientation failed: {result['message']}"
            )
        path = where_zen_put_it(zen_folder, result["output_name"], handle.zen_images)
        if not wait_for_file(path):
            raise RuntimeError(
                f"the snap {path} did not appear; see zen_images in the driver's README"
            )
        written.append(path)
        return read_plane(path)

    def go(x_um: float, y_um: float):
        result = changes.move_xy(handle.client, x_um, y_um)
        if not result["success"]:
            raise RuntimeError(
                f"the stage could not move for the orientation: {result['message']}"
            )

    try:
        here = snap("start")
        go(start["x_um"] + move_um, start["y_um"])
        along_x = snap("x")
        go(start["x_um"], start["y_um"] + move_um)
        along_y = snap("y")
    finally:
        go(start["x_um"], start["y_um"])
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
    *shift_x, clarity_x = phase_shift(here, along_x)
    *shift_y, clarity_y = phase_shift(here, along_y)
    orientation, pixel_um, matrix = orientation_from_shifts(
        move_um, tuple(shift_x), tuple(shift_y)
    )
    height, width = here.shape
    objective = start["objective"]
    frame = {
        "image_size_px": [int(width), int(height)],
        "pixel_size_um": [round(v, 6) for v in pixel_um],
        "magnification": objective.get("magnification"),
        "objective": {
            "position": objective.get("position"),
            "name": objective.get("name"),
        },
    }
    return {
        "orientation": orientation,
        "frame": frame,
        "matrix": matrix,
        "clarity": [round(clarity_x, 1), round(clarity_y, 1)],
        "shifts_px": [shift_x, shift_y],
        "document": orientation_config(orientation, frame),
        # The three snaps themselves (start, after the move along x, after the
        # move along y), so a notebook or the setup wizard can show them.
        "planes": [here, along_x, along_y],
    }


def adopt_orientation(
    measured: dict, *, machine: Any = None, moment=None, notebook_paths=()
) -> dict:
    """Save a measured orientation as a new snapshot of this microscope's orientation; where it was saved."""
    if machine is None:
        from ..store import MACHINE

        machine = MACHINE
    document = measured["document"] if "document" in measured else measured
    from . import orientation_from_config

    orientation_from_config(document)
    snapshot = machine.publish_snapshot(
        moment or datetime.now(UTC),
        orientation=document,
        notebook_paths=tuple(Path(p) for p in notebook_paths),
    )
    return {
        "snapshot": str(snapshot),
        "orientation_path": str(Path(snapshot) / "orientation.json"),
    }


def describe(measured: dict) -> str:
    """The measurement in a few words, for the notebook."""
    orientation = measured["orientation"]
    frame = measured["frame"]
    turn = f"{orientation.rotate_deg} degrees" + (
        " with a mirror" if orientation.mirrored else ""
    )
    return (
        f"The picture is turned {turn} against the stage. One pixel is "
        f"{frame['pixel_size_um'][0]:.4f} x {frame['pixel_size_um'][1]:.4f} um, the picture "
        f"{frame['image_size_px'][0]} x {frame['image_size_px'][1]} pixels, under "
        f"{(frame['objective'] or {}).get('name') or 'the objective in place'}. "
        f"Peak clarity {measured['clarity']} (above about 5 is a clear match)."
    )
