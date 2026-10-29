"""How the camera's picture is turned against the stage, and how large a pixel is.

A camera is often mounted a quarter or half turn away from the stage's own
x and y, and some light paths mirror the picture. When that happens, moving
the stage "right" shows up in the picture as a shift in some other
direction. The orientation records that fixed relation, measured once per
microscope by ``measure_orientation.py`` and its notebook, as one of eight
lossless turns: 0, 90, 180 or 270 degrees, with or without a mirror.

When a capture is saved as an OME-Zarr store, its pixels are turned with
this orientation, so the stored picture's columns run along the stage's x
and its rows along the stage's y, as every ZMART store's do. The turn only
rearranges pixels. The store also says which turn was applied (the image
frame of the ZMART metadata convention), and ZEN's own CZI, kept beside the
store, stays as the camera saw it.

The same measurement gives the **frame**: the image size in pixels and the
pixel size in micrometres, under the objective it was measured with. The ZEN
API does not report these before a capture, and the controller asks for
them in ``get_state``, so the driver reports the measured frame, scaled by
magnification for another objective, until a capture of the session gives
the exact numbers.

Until it is measured, the shipped ``defaults/orientation.json`` means "no
turn, frame unknown"; its ``measured`` false keeps it from being mistaken
for a measured identity.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
The eight turns and their matrices follow the Leica driver's orientation item.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
VALID_ROTATIONS = (0, 90, 180, 270)

#: How the stage moves for a step along the picture's columns and rows, for
#: each turn and mirror: the 2x2 matrix whose first column is the stage
#: direction of +1 column and whose second is the stage direction of +1 row.
STAGE_FROM_ORIENTATION: dict[
    tuple[int, bool], tuple[tuple[int, int], tuple[int, int]]
] = {
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
    """One of the eight lossless turns of the picture against the stage."""

    rotate_deg: int = 0
    mirrored: bool = False

    def __post_init__(self) -> None:
        if self.rotate_deg not in VALID_ROTATIONS:
            raise ValueError(
                f"rotate_deg must be a whole quarter turn, one of {VALID_ROTATIONS}, not {self.rotate_deg!r}; "
                "an angle in between means the camera is mounted crooked, which is a fix at the microscope"
            )
        if not isinstance(self.mirrored, bool):
            raise TypeError(f"mirrored must be True or False, not {self.mirrored!r}")

    @property
    def is_identity(self) -> bool:
        return self.rotate_deg == 0 and not self.mirrored

    @property
    def swaps_axes(self) -> bool:
        """Whether the picture's width runs along the stage's y (a quarter turn)."""
        return self.rotate_deg in (90, 270)

    @property
    def image_to_stage(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return STAGE_FROM_ORIENTATION[(self.rotate_deg, self.mirrored)]


def orientation_from_image_to_stage(matrix) -> Orientation:
    """The turn whose matrix is *matrix* (a measured 2x2, rounded); ``ValueError`` when it is none of the eight."""
    rounded = tuple(tuple(int(round(v)) for v in row) for row in matrix)
    for (degrees, mirrored), expected in STAGE_FROM_ORIENTATION.items():
        if rounded == expected:
            return Orientation(rotate_deg=degrees, mirrored=mirrored)
    raise ValueError(
        f"the image-to-stage matrix {matrix} is not one of the eight quarter turns and mirrors"
    )


def validate_frame(frame: Any) -> dict | None:
    """A measured frame checked: ``{"image_size_px": [w, h], "pixel_size_um": [x, y], "magnification", "objective"}``."""
    if frame is None:
        return None
    if not isinstance(frame, dict):
        raise ValueError(f"the frame must be an object, got {frame!r}")
    size, pixel = frame.get("image_size_px"), frame.get("pixel_size_um")
    if not (
        isinstance(size, list)
        and len(size) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in size)
    ):
        raise ValueError(
            f"the frame's image_size_px must be [width, height] in whole pixels, got {size!r}"
        )
    if not (
        isinstance(pixel, list)
        and len(pixel) == 2
        and all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and v > 0
            for v in pixel
        )
    ):
        raise ValueError(
            f"the frame's pixel_size_um must be [x, y] in micrometres above 0, got {pixel!r}"
        )
    magnification = frame.get("magnification")
    if magnification is not None and (
        not isinstance(magnification, (int, float)) or magnification <= 0
    ):
        raise ValueError(
            f"the frame's magnification must be a number above 0, got {magnification!r}"
        )
    return {
        "image_size_px": list(size),
        "pixel_size_um": [float(v) for v in pixel],
        "magnification": magnification,
        "objective": frame.get("objective"),
        "from": "the orientation setup",
    }


def orientation_config(
    orientation: Orientation, frame: dict | None, *, measured: bool = True
) -> dict:
    """The orientation document, as ``orientation.json`` holds it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "measured": bool(measured),
        "rotation_deg": int(orientation.rotate_deg),
        "reflection": orientation.mirrored,
        "frame": None
        if frame is None
        else {k: v for k, v in frame.items() if k != "from"},
    }


def orientation_from_config(data: Any) -> tuple[Orientation, dict | None]:
    """The turn and the frame of an orientation document; ``ValueError`` or ``TypeError`` says what is wrong."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"the orientation must be an object with schema_version {SCHEMA_VERSION}, got {data!r}"
        )
    missing = sorted({"measured", "rotation_deg", "reflection", "frame"} - set(data))
    if missing:
        raise ValueError(f"the orientation is missing {missing}")
    if not isinstance(data["measured"], bool):
        raise TypeError("the orientation's 'measured' must be True or False")
    if not isinstance(data["rotation_deg"], int) or isinstance(
        data["rotation_deg"], bool
    ):
        raise TypeError("the orientation's 'rotation_deg' must be a whole number")
    orientation = Orientation(
        rotate_deg=data["rotation_deg"], mirrored=data["reflection"]
    )
    return orientation, validate_frame(data["frame"])


def load_rig_orientation() -> tuple[Orientation, dict, dict | None]:
    """The measured orientation, what was loaded, and the frame; from one read of one file.

    Returns ``(orientation, info, frame)``. A file that cannot be used gives
    no turn and no frame, with a warning; it never stops the connection.
    """
    from ..store import MACHINE

    path = None
    try:
        path = MACHINE.orientation_path().absolute()
        data = json.loads(path.read_text(encoding="utf-8"))
        orientation, frame = orientation_from_config(data)
    except Exception as why:  # noqa: BLE001 -- a file problem degrades, never crashes connect
        log.warning(
            "the orientation could not be loaded (%s); captures are recorded without a measured turn. "
            "Measure it with driver_setup/orientation/notebooks/set_orientation.ipynb",
            why,
        )
        return (
            Orientation(),
            {
                "loaded": False,
                "measured": False,
                "path": None if path is None else str(path),
                "error": str(why),
            },
            None,
        )
    info = {
        "loaded": True,
        "measured": data.get("measured") is True,
        "path": str(path),
        "rotate_deg": orientation.rotate_deg,
        "mirrored": orientation.mirrored,
        "image_to_stage": [list(row) for row in orientation.image_to_stage],
    }
    return orientation, info, frame
