"""How the camera is turned relative to the stage, and how a saved picture is turned back.

A camera is often mounted a quarter turn or a half turn away from the stage's
own x and y directions, and some settings mirror the picture. When that is
so, "move the stage to the right" shows up in the picture as a shift in some
other direction. This item records that fixed turn, measured once per
microscope (``measure_orientation.py``), and the driver turns every picture
it saves so that left-right and up-down in the file follow the stage's x and
y. The rest of ZMART can then treat picture and stage directions as the
same thing.

The turn is one of eight: a whole quarter turn (0, 90, 180 or 270 degrees),
with or without a mirror first. Each one only rearranges pixels; none blurs
or resamples the picture. (For the maintainer: these eight are the symmetry
group of a square, often called D4.)

Until the orientation is measured, the default that ships with the driver
means "no turn", and ``measured`` is False, so an unmeasured microscope is
never turned by guesswork; ``get_info`` says that it still needs measuring.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import store as _machine

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
VALID_ROTATIONS = (0, 90, 180, 270)

# For each turn, the 2x2 matrix that takes a displacement in the raw picture
# (columns to the right, rows down) to the displacement of the stage (x, y)
# it came from, acting on a column vector. A mirrored turn mirrors the raw
# picture left to right first, then turns it clockwise.
_STAGE_FROM_PICTURE: dict[tuple[int, bool], tuple[tuple[int, int], tuple[int, int]]] = {
    (0, False): ((1, 0), (0, 1)),
    (90, False): ((0, -1), (1, 0)),
    (180, False): ((-1, 0), (0, -1)),
    (270, False): ((0, 1), (-1, 0)),
    (0, True): ((-1, 0), (0, 1)),
    (90, True): ((0, -1), (-1, 0)),
    (180, True): ((1, 0), (0, -1)),
    (270, True): ((0, 1), (1, 0)),
}


@dataclass(frozen=True)
class Orientation:
    """The turn that takes a raw camera picture to the stage's axes.

    ``mirrored`` means: mirror the raw picture left to right first, then turn
    it clockwise by ``rotate_deg``. This fixed order gives each of the eight
    turns one name.
    """

    rotate_deg: int = 0
    mirrored: bool = False

    def __post_init__(self) -> None:
        if self.rotate_deg not in VALID_ROTATIONS:
            raise ValueError(
                f"rotate_deg must be a whole quarter turn, one of {VALID_ROTATIONS}, not {self.rotate_deg!r}. "
                "An angle in between means the camera is mounted crooked, which is fixed at the microscope."
            )
        if not isinstance(self.mirrored, bool):
            raise TypeError(f"mirrored must be True or False, not {self.mirrored!r}")

    @property
    def is_identity(self) -> bool:
        return self.rotate_deg == 0 and not self.mirrored

    @property
    def swaps_axes(self) -> bool:
        """A quarter turn swaps a picture's width and height."""
        return self.rotate_deg in (90, 270)

    @property
    def picture_to_stage(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """The 2x2 matrix from a raw-picture displacement (columns, rows) to the stage's (x, y)."""
        return _STAGE_FROM_PICTURE[(self.rotate_deg, self.mirrored)]

    def apply(self, picture):
        """The picture turned so that its columns run with stage x and its rows with stage y.

        *picture* is a two-dimensional array (rows, columns); more leading
        dimensions are left alone. Nothing is resampled.
        """
        import numpy as np

        turned = np.fliplr(picture) if self.mirrored else picture
        # numpy's rot90 turns counter-clockwise, so a clockwise turn is a negative count.
        return (
            np.rot90(turned, k=-(self.rotate_deg // 90), axes=(-2, -1))
            if self.rotate_deg
            else turned
        )

    def as_dict(self) -> dict[str, Any]:
        return {"rotation_deg": self.rotate_deg, "mirrored": self.mirrored}


def nearest_orientation(matrix) -> Orientation:
    """The one of the eight turns whose matrix is closest to a measured 2x2 matrix.

    The measured matrix takes a picture displacement to a stage displacement,
    in any units: only its directions matter, so it is compared after each
    column is scaled to unit length.
    """
    import numpy as np

    measured = np.asarray(matrix, dtype=float)
    norms = np.linalg.norm(measured, axis=0)
    if not np.all(norms > 0):
        raise ValueError(
            "the measured matrix has a zero column; the stage step produced no shift in the picture"
        )
    unit = measured / norms
    best, best_distance = None, None
    for (deg, mirrored), rows in _STAGE_FROM_PICTURE.items():
        distance = float(np.abs(unit - np.asarray(rows, dtype=float)).sum())
        if best_distance is None or distance < best_distance:
            best, best_distance = Orientation(deg, mirrored), distance
    return best


def default_record() -> dict[str, Any]:
    """The record that ships with the driver: no turn, not measured."""
    import json

    return json.loads(
        _machine.MACHINE.bundled_default_path("orientation").read_text(encoding="utf-8")
    )


def validate_record(record: Any, *, where: str = "orientation.json") -> dict[str, Any]:
    """Check an orientation record and hand it back; raise ``ValueError`` saying what is wrong."""
    if not isinstance(record, dict):
        raise ValueError(f"{where}: the orientation must be an object")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{where}: schema_version must be {SCHEMA_VERSION}, got {record.get('schema_version')!r}"
        )
    if not isinstance(record.get("measured"), bool):
        raise ValueError(f"{where}: measured must be true or false")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    Orientation(record.get("rotation_deg"), record.get("mirrored"))  # raises when wrong
    pixel = record.get("pixel_size_um")
    if pixel is not None and (
        isinstance(pixel, bool) or not isinstance(pixel, (int, float)) or pixel <= 0
    ):
        raise ValueError(f"{where}: pixel_size_um must be a positive number or null")
    return record


def load_rig_orientation(
    machine: _machine.MachineProfile | None = None,
) -> tuple[Orientation, dict]:
    """The orientation to apply to every saved picture, and a report of what was loaded.

    The newest saved record is used; without one, the bundled default (no
    turn). A record that cannot be read or does not check out stops the
    connection: turning pictures by a guess would mislead every later step.
    """
    machine = machine if machine is not None else _machine.MACHINE
    path = machine.latest_path("orientation")
    try:
        record = (
            machine.read_record("orientation") if path is not None else default_record()
        )
        record = validate_record(record, where=str(path or "the bundled default"))
    except (OSError, ValueError) as why:
        raise RuntimeError(
            f"the saved orientation at {path} could not be used ({why}); measure it again with "
            "driver_setup/orientation/notebooks/set_orientation.ipynb"
        ) from why
    orientation = Orientation(record["rotation_deg"], record["mirrored"])
    info = {
        "measured": record["measured"],
        "path": str(path) if path is not None else None,
        "rotation_deg": orientation.rotate_deg,
        "mirrored": orientation.mirrored,
        "pixel_size_um": record.get("pixel_size_um"),
    }
    if not record["measured"]:
        log.warning(
            "the camera-to-stage orientation of this microscope has not been measured; saved "
            "pictures are kept as the camera sees them. Run "
            "driver_setup/orientation/notebooks/set_orientation.ipynb once."
        )
    return orientation, info


def adopt_orientation(
    orientation: Orientation,
    *,
    pixel_size_um: float | None = None,
    measured: bool = True,
    machine: _machine.MachineProfile | None = None,
    moment: datetime | None = None,
    archive_paths=(),
) -> dict:
    """Save *orientation* as this microscope's, so every later connection applies it."""
    machine = machine if machine is not None else _machine.MACHINE
    record = validate_record(
        {
            "schema_version": SCHEMA_VERSION,
            "measured": measured,
            **orientation.as_dict(),
            "pixel_size_um": pixel_size_um,
        }
    )
    path = machine.write_record(
        "orientation", record, moment=moment, archive_paths=archive_paths
    )
    return {"record": str(path.parent), "orientation_path": str(path), **record}
