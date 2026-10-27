"""Record and apply how the camera's picture is turned relative to the stage.

A camera or scanner is often mounted a quarter- or half-turn away from the
stage's own X and Y directions. When that happens, telling the stage to "move
right" shows up in the picture as a shift in some other direction, and the
software would end up chasing features the wrong way. This module records that
fixed turn, stated once per microscope in the ``set_orientation``
notebook, and corrects each saved image so that its left-right and up-down line
up with the stage's X and Y. After that, the rest of the software can treat image
and stage directions as the same thing, with no orientation maths anywhere.

The correction is one of the eight lossless turns and mirrors (the "D4" group): a whole quarter-turn
(0, 90, 180, or 270 degrees), optionally combined with a mirror. Some microscope
acquisition settings mirror the camera image deliberately, so a measured mirror
is recorded and corrected rather than treated as a broken rig. These transforms
only rearrange pixels; they never resample or blur the image.

This is a separate thing from pixel-size calibration and from the instrument limits,
and it lives entirely inside the driver. Workflows never deal with it: the images
they receive are already lined up with the stage.

The value belongs to one microscope, so it is saved with its other settings,
in a dated folder of ``orientation/`` in the computer's configuration folder
(``driver_setup/store.py``), by the ``set_orientation`` notebook, where the
operator states it after looking at two pictures. Until then, the shipped
``defaults/orientation.json`` means "no turn" and says it was not stated, so
a microscope is never turned by guesswork. ``measured`` tells the stated
"no turn" from that placeholder.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Copied from the Leica driver's ``driver_setup/orientation``; the loader is this driver's.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
_VALID_ROTATIONS = (0, 90, 180, 270)

# How a clockwise turn of ``rotate_deg`` moves an image displacement (column,
# row): each entry is the 2x2 matrix that sends (+1 column, 0 rows) and
# (0 columns, +1 row) to their new places. A measured image->stage matrix, if
# the rig is turned by a whole quarter, is exactly one of these.
_STAGE_FROM_ROTATION: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {
    0: ((1, 0), (0, 1)),
    90: ((0, -1), (1, 0)),
    180: ((-1, 0), (0, -1)),
    270: ((0, 1), (-1, 0)),
}

# A mirrored orientation is represented canonically as a left-right mirror of
# the raw image followed by the clockwise turn. Together with the four plain
# rotations this covers all eight D4 mappings without an ambiguous mirror axis.
_STAGE_FROM_ORIENTATION: dict[
    tuple[int, bool], tuple[tuple[int, int], tuple[int, int]]
] = {
    **{(deg, False): matrix for deg, matrix in _STAGE_FROM_ROTATION.items()},
    (0, True): ((-1, 0), (0, 1)),
    (90, True): ((0, -1), (-1, 0)),
    (180, True): ((1, 0), (0, -1)),
    (270, True): ((0, 1), (1, 0)),
}


@dataclass(frozen=True)
class Orientation:
    """Lossless correction from the raw image axes to the stage axes.

    ``mirrored=True`` means mirror the raw image left-to-right first, then apply
    the clockwise ``rotate_deg`` quarter-turn. This fixed order gives every D4
    mapping one unique representation. ``axis_mapping`` and ``axis_signs`` are
    derived from the same authoritative matrix used to transform pixels.
    """

    rotate_deg: int = 0
    mirrored: bool = False

    def __post_init__(self) -> None:
        if self.rotate_deg not in _VALID_ROTATIONS:
            raise ValueError(
                f"rotate_deg must be a whole quarter-turn -- one of "
                f"{_VALID_ROTATIONS}. An in-between angle means the camera is "
                f"physically misaligned, which is a rig problem to fix, not "
                f"something to rotate around. Got {self.rotate_deg!r}."
            )
        if not isinstance(self.mirrored, bool):
            raise TypeError(f"mirrored must be bool, got {self.mirrored!r}")

    @property
    def is_identity(self) -> bool:
        return self.rotate_deg == 0 and not self.mirrored

    @property
    def swaps_axes(self) -> bool:
        return self.rotate_deg in (90, 270)

    @property
    def image_to_stage(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return _STAGE_FROM_ORIENTATION[(self.rotate_deg, self.mirrored)]

    @property
    def axis_mapping(self) -> dict[str, str]:
        """Signed raw-image axis used for each positive stage axis."""
        rows = self.image_to_stage
        return {
            "stage_x_from_image": _signed_axis(rows[0]),
            "stage_y_from_image": _signed_axis(rows[1]),
        }

    @property
    def axis_signs(self) -> dict[str, int]:
        """Polarity of the image axis selected for each stage axis."""
        return {
            "stage_x": _axis_sign(self.image_to_stage[0]),
            "stage_y": _axis_sign(self.image_to_stage[1]),
        }

    @property
    def reflection_axis(self) -> str | None:
        """Net reflection axis after applying the canonical correction."""
        if not self.mirrored:
            return None
        return {
            0: "vertical",
            90: "anti_diagonal",
            180: "horizontal",
            270: "main_diagonal",
        }[self.rotate_deg]


def _axis_sign(row: tuple[int, int]) -> int:
    return next(value for value in row if value)


def _signed_axis(row: tuple[int, int]) -> str:
    index = next(index for index, value in enumerate(row) if value)
    sign = "+" if row[index] > 0 else "-"
    return f"{sign}{('X', 'Y')[index]}"


def orientation_config(
    orientation: Orientation, *, measured: bool = True
) -> dict[str, Any]:
    """Build the minimal authoritative orientation document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "measured": bool(measured),
        "rotation_deg": int(orientation.rotate_deg),
        "reflection": orientation.mirrored,
        "sign_convention": orientation.axis_mapping,
    }


def orientation_from_config(data: dict[str, Any]) -> Orientation:
    """Validate and decode either a current or legacy orientation document.

    Migration policy: files written by older drivers (schema 1 or 2) keep
    loading as the measured state they are — upgrading the driver never asks a
    working rig to re-measure. An old file is rewritten in the current minimal
    schema only when the operator next runs the ``set_orientation`` notebook.
    """
    schema_version = int(data.get("schema_version", 1))
    if schema_version >= SCHEMA_VERSION:
        required = {"measured", "rotation_deg", "reflection", "sign_convention"}
        missing = sorted(required.difference(data))
        if missing:
            raise ValueError(
                f"orientation schema {schema_version} missing fields: {missing}"
            )
        if not isinstance(data["measured"], bool):
            raise TypeError("orientation field 'measured' must be boolean")
        if not isinstance(data["rotation_deg"], int) or isinstance(
            data["rotation_deg"], bool
        ):
            raise TypeError("orientation field 'rotation_deg' must be an integer")
        if not isinstance(data["reflection"], bool):
            raise TypeError("orientation field 'reflection' must be boolean")
        orientation = Orientation(
            rotate_deg=data["rotation_deg"],
            mirrored=data["reflection"],
        )
        if data["sign_convention"] != orientation.axis_mapping:
            raise ValueError(
                "orientation field 'sign_convention' contradicts rotation/reflection: "
                f"got {data['sign_convention']!r}, expected {orientation.axis_mapping!r}"
            )
        return orientation

    if schema_version == 2:
        required = {
            "rotate_deg",
            "mirrored",
            "axis_signs",
            "axis_mapping",
            "image_to_stage",
        }
        missing = sorted(required.difference(data))
        if missing:
            raise ValueError(
                f"orientation schema {schema_version} missing fields: {missing}"
            )
        orientation = orientation_from_image_to_stage(data["image_to_stage"])
        expected = {
            "rotate_deg": int(orientation.rotate_deg),
            "mirrored": orientation.mirrored,
            "axis_signs": orientation.axis_signs,
            "axis_mapping": orientation.axis_mapping,
            "image_to_stage": [list(row) for row in orientation.image_to_stage],
            "reflection_axis": orientation.reflection_axis,
        }
        for key in required:
            if data[key] != expected[key]:
                raise ValueError(
                    f"orientation field {key!r} contradicts image_to_stage: "
                    f"got {data[key]!r}, expected {expected[key]!r}"
                )
        if (
            "reflection_axis" in data
            and data["reflection_axis"] != expected["reflection_axis"]
        ):
            raise ValueError(
                "orientation field 'reflection_axis' contradicts image_to_stage: "
                f"got {data['reflection_axis']!r}, "
                f"expected {expected['reflection_axis']!r}"
            )
        return orientation

    return Orientation(
        rotate_deg=int(data.get("rotate_deg", 0)),
        mirrored=data.get("mirrored") is True,
    )


def load_orientation(path: Any) -> Orientation:
    """Read an :class:`Orientation` from an ``orientation.json`` file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return orientation_from_config(data)


def orientation_from_image_to_stage(matrix) -> Orientation:
    """The :class:`Orientation` whose image-to-stage matrix is *matrix* (a 2x2 of 0, 1 and -1)."""
    rows = tuple(tuple(int(v) for v in row) for row in matrix)
    for (deg, mirrored), expected in _STAGE_FROM_ORIENTATION.items():
        if expected == rows:
            return Orientation(rotate_deg=deg, mirrored=mirrored)
    raise ValueError(
        f"image-to-stage matrix {matrix} is not a quarter turn or a mirror"
    )


def reorient_array(array, orientation: Orientation):
    """Apply *orientation* to the last two axes (y, x) of an image, without losing any detail.

    A mirrored correction flips left to right first; the clockwise quarter
    turn follows. Both only rearrange pixels. Returns a new array.
    """
    import numpy as np

    if orientation.mirrored:
        array = np.flip(array, axis=-1)
    k = orientation.rotate_deg // 90
    if k:
        array = np.rot90(array, k=-k, axes=(-2, -1))  # a negative k turns clockwise
    return np.ascontiguousarray(array)


def save_orientation(orientation: Orientation, *, machine=None) -> Path:
    """Save *orientation* as this microscope's, stated by the operator; the saved file's path."""
    from .. import store

    path = (machine or store.machine()).write(
        "orientation", orientation_config(orientation, measured=True)
    )
    print(
        f"saved: turned {orientation.rotate_deg} degrees clockwise, {'mirrored' if orientation.mirrored else 'not mirrored'}: {path}"
    )
    return path


def load_rig_orientation(machine=None) -> tuple[Orientation, dict]:
    """This microscope's orientation, and what was loaded, from one read of one file.

    The newest saved ``orientation.json``, else the driver's default, which
    means "no turn" and says it was not stated. A file that cannot be used is
    reported, and the session keeps the pictures as the camera gives them,
    with a warning: a wrong turn would be worse than none.
    """
    from .. import store

    machine = machine or store.machine()
    identity = Orientation()
    path = machine.saved_path("orientation") or machine.bundled_default("orientation")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        orientation = orientation_from_config(data)
    except Exception as exc:  # noqa: BLE001 -- a damaged file must not stop the connection
        _log.warning(
            "the orientation could not be loaded (%s); saved pictures are kept as the camera gives them this "
            "session. State it again with driver_setup/orientation/notebooks/set_orientation.ipynb.",
            exc,
        )
        return identity, {
            "loaded": False,
            "measured": False,
            "path": str(path),
            "rotate_deg": 0,
            "mirrored": False,
            "error": str(exc),
        }
    return orientation, {
        "loaded": True,
        "measured": data.get("measured") is True,
        "path": str(path),
        "rotate_deg": int(orientation.rotate_deg),
        "mirrored": orientation.mirrored,
        "image_to_stage": [list(row) for row in orientation.image_to_stage],
    }
