"""Measure which way the camera's picture is turned relative to the stage, from three pictures.

A camera is often mounted a quarter turn, or a mirror image, away from the
stage's own x and y. The driver turns every saved picture so that its left to
right is the stage's +x and its top to bottom the stage's +y. This file finds
that turn by looking, instead of asking the operator to state it:

1. take a picture where the stage stands;
2. move the stage a little in +x, and take a second picture;
3. move it the same distance in +y (from where it started), and take a third;
4. move back to where it started.

A sample that stays put on a moving stage moves the opposite way in the
picture: when the picture is the right way round, a +x move shifts its content
to the left, and a +y move shifts it up. There are eight ways a picture can be
turned or mirrored. For each, the three pictures are turned that way, and the
shift between them is measured (by comparing the pictures' patterns, a method
called phase correlation). The way that makes +x go left and +y go up, and is
clearly better than any other, is the orientation.

Every move goes through the driver, so the limits are asked first, and the
pictures are taken as ``acquire`` takes them, into a new NIS document that is
closed again. Choose a field with clear structure, in focus: an empty field or
a blurred one cannot show which way it moved, and the measurement says so
instead of guessing.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
After the Leica driver's three-picture measurement in
``driver_setup/orientation/measure_orientation.py``.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import Orientation, reorient_array

#: The eight ways a picture can be turned (clockwise) and mirrored.
CANDIDATES = tuple(
    Orientation(rotate_deg=d, mirrored=m)
    for m in (False, True)
    for d in (0, 90, 180, 270)
)

#: How far the stage moves when the field of view is not known yet, in micrometres.
DEFAULT_STEP_UM = 20.0
#: The move as a share of the smaller side of the field of view, when it is known.
STEP_SHARE_OF_FIELD = 0.2
#: The fewest pixels a move must shift the picture by to be told from noise.
SMALLEST_SHIFT_PX = 2.0
#: How much better the chosen orientation must score than the next one.
CLEAR_MARGIN = 1.5


@dataclass
class Measurement:
    """What one measurement found: the shifts under each orientation, the one chosen, and why."""

    step_um: float
    pictures: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    shifts: dict[str, dict] = field(default_factory=dict)
    chosen: Orientation | None = None
    accepted: bool = False
    why: str | None = None

    @property
    def label(self) -> str | None:
        return label(self.chosen) if self.chosen is not None else None


def label(orientation: Orientation) -> str:
    """The orientation in words: "turned 90 degrees clockwise, mirrored"."""
    turn = (
        f"turned {orientation.rotate_deg} degrees clockwise"
        if orientation.rotate_deg
        else "not turned"
    )
    return f"{turn}, {'mirrored' if orientation.mirrored else 'not mirrored'}"


def shift_between(first, second) -> tuple[float, float, float]:
    """How far the content of *second* lies from *first*, in pixels: ``(rows, columns, sharpness)``.

    Positive rows mean the content moved down, positive columns that it moved
    right. ``sharpness`` says how clearly the two pictures match: the height of
    the best match over the average, about 1 for pictures that do not match.
    """
    import numpy as np

    a = np.asarray(first, dtype=float)
    b = np.asarray(second, dtype=float)
    # Fade both pictures out towards their edges (a Hann window). Without it,
    # the pictures' own borders, which line up at no shift at all, outweigh
    # the sample, and every move looks like no move.
    fade = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    a = (a - a.mean()) * fade
    b = (b - b.mean()) * fade
    product = np.fft.fft2(b) * np.conj(np.fft.fft2(a))
    product /= np.maximum(np.abs(product), 1e-12)
    match = np.abs(np.fft.ifft2(product))
    row, column = np.unravel_index(int(np.argmax(match)), match.shape)
    rows, columns = match.shape
    dy = row - rows if row > rows // 2 else row
    dx = column - columns if column > columns // 2 else column
    sharpness = float(match.max() / max(match.mean(), 1e-12))
    return float(dy), float(dx), sharpness


def score(shift_x: tuple, shift_y: tuple) -> float:
    """How well a +x shift to the left and a +y shift upwards fit: higher is better, and below 0 is wrong."""
    dy_x, dx_x, _ = shift_x
    dy_y, dx_y, _ = shift_y
    return (-dx_x - abs(dy_x)) + (-dy_y - abs(dx_y))


def choose(first, after_x, after_y, step_um: float) -> Measurement:
    """The orientation the three pictures show, or why none could be chosen."""
    found = Measurement(step_um=step_um)
    for candidate in CANDIDATES:
        a, bx, by = (reorient_array(p, candidate) for p in (first, after_x, after_y))
        shift_x, shift_y = shift_between(a, bx), shift_between(a, by)
        name = label(candidate)
        found.shifts[name] = {
            "after +x (rows, columns)": shift_x[:2],
            "after +y (rows, columns)": shift_y[:2],
        }
        found.scores[name] = score(shift_x, shift_y)
    ranked = sorted(CANDIDATES, key=lambda c: found.scores[label(c)], reverse=True)
    best, runner_up = ranked[0], ranked[1]
    top, second = found.scores[label(best)], found.scores[label(runner_up)]
    sharp = min(shift_between(first, after_x)[2], shift_between(first, after_y)[2])
    found.chosen = best
    if sharp < 3.0:
        found.why = "the pictures do not match well enough to see a shift (an empty, blurred or changing field?)"
    elif top < 2 * SMALLEST_SHIFT_PX:
        found.why = "the picture hardly moved with the stage; choose a larger move, or a field with clearer structure"
    elif second > 0 and top < CLEAR_MARGIN * second:
        found.why = (
            f"two orientations fit almost as well ({label(best)}, {label(runner_up)})"
        )
    else:
        found.accepted = True
    return found


def step_for(scope) -> float:
    """How far to move: a fifth of the field of view when the picture size is known, else :data:`DEFAULT_STEP_UM`."""
    try:
        observed = scope.state()["observed"]
        width, height = observed["image_size_px"]
        pixel = observed["pixel_size_um"][0]
        return round(STEP_SHARE_OF_FIELD * min(width, height) * pixel, 1)
    except (KeyError, TypeError, ValueError, RuntimeError):
        return DEFAULT_STEP_UM


def picture(scope, folder: Path, name: str):
    """One picture, taken as ``acquire`` takes it, read back from NIS's TIFF as one plane."""
    import tifffile

    from ...procedures import changes

    path = folder / f"{name}.tif"
    changes.take_picture(scope.client, str(path))
    pixels = tifffile.imread(str(path))
    if pixels.ndim == 3:
        pixels = pixels.mean(axis=-1) if pixels.shape[-1] == 3 else pixels[0]
    return pixels, str(path)


def measure(
    scope, *, step_um: float | None = None, folder: Path | None = None
) -> Measurement:
    """Take the three pictures with the connected ``Microscope`` *scope*, move back, and choose the orientation.

    Raises ``RuntimeError`` when a move or a picture fails; the stage is then
    sent back to where it started, as far as it can be.
    """
    step = float(step_um) if step_um else step_for(scope)
    folder = Path(folder or tempfile.mkdtemp(prefix="zmart_orientation_"))
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    start = scope.position()
    x0, y0, z0 = (start[axis]["position"] for axis in ("x", "y", "z"))
    try:
        first, path_a = picture(scope, folder, f"orientation_{stamp}_here")
        scope.move_to(x0 + step, y0, z0)
        after_x, path_x = picture(scope, folder, f"orientation_{stamp}_plus_x")
        scope.move_to(x0, y0 + step, z0)
        after_y, path_y = picture(scope, folder, f"orientation_{stamp}_plus_y")
    finally:
        scope.move_to(x0, y0, z0)
    found = choose(first, after_x, after_y, step)
    found.pictures = [path_a, path_x, path_y]
    return found
