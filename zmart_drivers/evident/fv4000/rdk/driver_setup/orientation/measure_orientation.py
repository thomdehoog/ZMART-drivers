"""Measuring how the picture is turned relative to the stage: three pictures and a known move.

The measurement is simple to picture. A picture is taken where the stage
stands. The stage is moved a small, known distance along its X axis and a
second picture is taken; then along Y for a third. Comparing each picture
with the first says in which direction, in the picture, a move along X and
along Y shows up. Those two directions are one of the eight quarter-turn and
mirror combinations (:class:`~.Orientation`), which is what is saved.

The shift between two pictures is found with phase correlation: a standard
way of comparing two images that are the same scene moved by some amount. It
needs only numpy, and a field with some structure in it (cells, beads, a
grid). A featureless field gives no clear shift, and the measurement says so
instead of guessing.

Everything here works on a connected session (the ``Session`` of
``methods.py``) through the run-time procedures: the stage is moved with the
same checked moves as any workflow's, and a picture is taken and read back
through the same ``acquire`` and ``save_data`` code. The stage is moved back to
where it started at the end, also when the measurement fails.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ...procedures import absolute_coordinate_system as frame
from ...procedures import acquire as acquire_step
from ...procedures.save_data import collect
from .. import store as _machine
from . import Orientation, orientation_config, orientation_from_image_to_stage

log = logging.getLogger(__name__)

#: How far the stage is moved for each test picture, in micrometres. A quarter
#: of the field is a clear shift that still leaves most of the scene in view.
DEFAULT_STEP_FRACTION = 0.25

#: The least sharpness a phase-correlation peak must have (its height over the
#: mean of the correlation map) for the shift to count as seen.
MIN_PEAK_RATIO = 6.0


@dataclass
class OrientationMeasurement:
    """What one measurement found: the pictures, the shifts and the mapping."""

    step_um: float
    pixel_size_um: tuple[float, float]
    home: dict
    shift_px_x: tuple[float, float] | None = None
    shift_px_y: tuple[float, float] | None = None
    peak_ratios: dict = field(default_factory=dict)
    image_to_stage: list | None = None
    orientation: Orientation | None = None
    problems: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.orientation is not None and not self.problems


def phase_shift(
    reference: np.ndarray, moved: np.ndarray
) -> tuple[tuple[float, float], float]:
    """How far *moved* is displaced from *reference*, as ``((columns, rows), peak ratio)``.

    A positive column shift means the scene moved to the right in the picture;
    a positive row shift, downwards. The peak ratio says how clear the answer
    is (see :data:`MIN_PEAK_RATIO`).
    """
    a = np.asarray(reference, dtype=np.float64)
    b = np.asarray(moved, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    window = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2(a * window), np.fft.fft2(b * window)
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-12
    correlation = np.real(np.fft.ifft2(cross))
    peak = np.unravel_index(np.argmax(correlation), correlation.shape)
    ratio = float(correlation[peak] / (np.abs(correlation).mean() + 1e-12))
    rows, cols = correlation.shape
    dy = peak[0] if peak[0] <= rows // 2 else peak[0] - rows
    dx = peak[1] if peak[1] <= cols // 2 else peak[1] - cols
    # The correlation above finds the shift that brings *moved* back onto
    # *reference*; the scene's own displacement is the opposite.
    return (float(-dx), float(-dy)), ratio


def mapping_from_shifts(
    shift_for_x: tuple[float, float], shift_for_y: tuple[float, float]
) -> list:
    """The 2x2 image-to-stage matrix from the picture shift of a +X move and of a +Y move.

    Each shift is rounded to the nearest axis direction: a move along one
    stage axis must show as a move along one picture axis, else the scanner
    is not mounted at a whole quarter turn.
    """

    def unit(shift):
        dx, dy = shift
        if abs(dx) >= abs(dy):
            return (1 if dx > 0 else -1, 0)
        return (0, 1 if dy > 0 else -1)

    ux, uy = unit(shift_for_x), unit(shift_for_y)
    if ux == uy or ux == (-uy[0], -uy[1]):
        raise ValueError(
            f"the X move ({shift_for_x}) and the Y move ({shift_for_y}) show along the same picture axis"
        )
    # Row k of image_to_stage says which signed picture axis gives stage axis k.
    return [list(ux), list(uy)]


def measure(handle, *, step_um: float | None = None) -> OrientationMeasurement:
    """Take the three pictures, move the stage back, and answer what was found.

    *step_um* is how far to move along each axis; by default a quarter of the
    picture's width. The stage is moved through the checked ``move_to``, so a
    step outside the limits is refused before anything moves.
    """
    handle.require_open()
    home = frame.get_xyz(handle)
    at = {axis: home[axis]["position"] for axis in ("x", "y", "z")}
    first = _picture(handle, "orientation_home")
    pixel = first.pixel_size_um
    if pixel is None:
        raise RuntimeError(
            "the picture does not say its pixel size, so the move cannot be chosen"
        )
    width_um = first.volume.shape[-1] * pixel[0]
    step = float(step_um) if step_um is not None else width_um * DEFAULT_STEP_FRACTION
    result = OrientationMeasurement(step_um=step, pixel_size_um=pixel, home=at)
    try:
        frame.set_xyz(handle, at["x"] + step, at["y"], at["z"])
        moved_x = _picture(handle, "orientation_plus_x")
        frame.set_xyz(handle, at["x"], at["y"] + step, at["z"])
        moved_y = _picture(handle, "orientation_plus_y")
    finally:
        frame.set_xyz(handle, at["x"], at["y"], at["z"])
    reference = first.volume[0, 0, 0]
    result.shift_px_x, ratio_x = phase_shift(reference, moved_x.volume[0, 0, 0])
    result.shift_px_y, ratio_y = phase_shift(reference, moved_y.volume[0, 0, 0])
    result.peak_ratios = {"x": ratio_x, "y": ratio_y}
    for axis, ratio in result.peak_ratios.items():
        if ratio < MIN_PEAK_RATIO:
            result.problems.append(
                f"the shift for the {axis.upper()} move is not clear (peak ratio {ratio:.1f} < {MIN_PEAK_RATIO}); "
                "choose a field with more structure, or a larger step"
            )
    expected_px = step / pixel[0]
    for axis, shift in (("x", result.shift_px_x), ("y", result.shift_px_y)):
        seen = max(abs(shift[0]), abs(shift[1]))
        if seen < expected_px * 0.5 or seen > expected_px * 1.5:
            result.problems.append(
                f"the {axis.upper()} move of {step:g} um should show as about {expected_px:.0f} pixels, "
                f"but {seen:.0f} were seen; check the pixel size and that the stage really moved"
            )
    try:
        result.image_to_stage = mapping_from_shifts(
            result.shift_px_x, result.shift_px_y
        )
        result.orientation = orientation_from_image_to_stage(result.image_to_stage)
    except ValueError as why:
        result.problems.append(str(why))
    return result


def _picture(handle, label: str):
    """One capture, read back from FLUOVIEW as pixels, without saving it anywhere."""
    taken = acquire_step.acquire(
        handle,
        position_label=label,
        acquisition_type="setup",
        output_folder=tempfile.gettempdir(),
    )
    pending = next(p for p in handle.unsaved if p.label == taken["position_label"])
    handle.unsaved.remove(pending)
    return collect.read_capture(handle.client, pending.acq.target)


def describe(result: OrientationMeasurement) -> str:
    """The measurement in words, for the notebook."""
    lines = [
        f"step: {result.step_um:g} um; pixel size: {result.pixel_size_um[0]:g} x {result.pixel_size_um[1]:g} um",
        f"+X move shows as {result.shift_px_x} pixels (columns, rows); +Y move as {result.shift_px_y}",
    ]
    if result.orientation is not None:
        o = result.orientation
        lines.append(
            f"orientation: {o.rotate_deg} degrees, {'mirrored' if o.mirrored else 'not mirrored'}; {o.axis_mapping}"
        )
    lines += [f"PROBLEM: {p}" for p in result.problems]
    return "\n".join(lines)


def adopt_orientation(
    result: OrientationMeasurement,
    notebook_path: str | Path | None = None,
    *,
    machine=None,
) -> dict:
    """Save a successful measurement as this microscope's orientation; answers the record's folder.

    A measurement with problems is refused: a guessed orientation would turn
    every saved picture the wrong way.
    """
    if not result.ok:
        raise RuntimeError(
            "the orientation was not measured cleanly; nothing was saved:\n"
            + "\n".join(result.problems)
        )
    machine = machine if machine is not None else _machine.MACHINE
    record = {
        "measured_at": datetime.now(UTC).isoformat(),
        "step_um": result.step_um,
        "pixel_size_um": list(result.pixel_size_um),
        "shift_px": {"x": list(result.shift_px_x), "y": list(result.shift_px_y)},
        "peak_ratios": result.peak_ratios,
        "image_to_stage": result.image_to_stage,
    }
    archive = []
    folder = Path(tempfile.mkdtemp(prefix="zmart_orientation_"))
    (folder / "measurement.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    archive.append(folder / "measurement.json")
    if notebook_path is not None:
        archive.append(Path(notebook_path))
    snapshot = machine.publish_snapshot(
        orientation=orientation_config(result.orientation, measured=True),
        archive_paths=archive,
    )
    return {
        "snapshot": str(snapshot),
        "orientation": orientation_config(result.orientation, measured=True),
    }
