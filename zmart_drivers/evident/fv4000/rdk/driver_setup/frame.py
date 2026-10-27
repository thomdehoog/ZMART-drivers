"""What one picture covers under each objective, learned from the pictures themselves.

The ZMART Controller plans with two numbers before it captures anything: the
image size in pixels and the pixel size in micrometres of the current
settings. FLUOVIEW's remote interface has no setting that reports either; they
are only written into a picture (``IDA.getProperty`` answers ``PixelLength``
and ``AxisMaxSize`` for a saved image). So the driver learns them: each time a
capture is read back and saved, its size and pixel size are remembered for the
objective it was taken with, in this microscope's own settings
(``frame/<datetime>/frame.json``), and loaded at every connect.

Until a picture has been taken under an objective, its frame is simply not
known, and the state says so (``image_size_px`` and ``pixel_size_um`` are
None). Nothing is guessed. Taking one picture and saving it is the whole setup
step. A later picture with other scan settings (another zoom or pixel count)
replaces the record, so the frame is always that of the newest picture.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .store import MACHINE, MachineProfile

log = logging.getLogger(__name__)

#: What a frame record holds for one objective slot.
FRAME_KEYS = (
    "objective",
    "image_size_px",
    "pixel_size_um",
    "learned_at",
    "fluoview_file",
)


def load_frames(machine: MachineProfile = MACHINE) -> dict[int, dict[str, Any]]:
    """The frames learned on this microscope, one per objective slot; empty when no picture was taken yet.

    A record that cannot be read is skipped with a warning rather than
    stopping the connection: the frame is learned again at the next picture.
    """
    try:
        payload = machine.read_frame()
    except (OSError, ValueError) as error:
        log.warning(
            "the frame record could not be read (%s); frames are learned again at the next picture",
            error,
        )
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), dict):
        if payload:
            log.warning(
                "the frame record has no 'frames' table; frames are learned again at the next picture"
            )
        return {}
    frames: dict[int, dict[str, Any]] = {}
    for slot, frame in payload["frames"].items():
        try:
            frames[int(slot)] = _checked(frame)
        except (TypeError, ValueError) as error:
            log.warning("the frame of objective slot %s is skipped: %s", slot, error)
    return frames


def remember_frame(
    frames: dict[int, dict[str, Any]],
    slot: int,
    *,
    objective: str | None,
    image_size_px,
    pixel_size_um,
    fluoview_file: str | None = None,
    machine: MachineProfile = MACHINE,
) -> dict[str, Any]:
    """Record the frame of the picture just read back under objective *slot*, and save all frames whole.

    *image_size_px* is (width, height) in pixels and *pixel_size_um* is (x, y)
    in micrometres, both as FLUOVIEW scanned them (before any turn the
    orientation applies). Answers the record kept. A frame that cannot be
    saved (the settings folder is not writable) is kept for this session and
    logged, not raised: the capture itself was saved fine.
    """
    frame = _checked(
        {
            "objective": objective,
            "image_size_px": [int(image_size_px[0]), int(image_size_px[1])],
            "pixel_size_um": [float(pixel_size_um[0]), float(pixel_size_um[1])],
            "learned_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "fluoview_file": fluoview_file,
        }
    )
    frames[int(slot)] = frame
    try:
        machine.write_frame(
            {
                "frames": {
                    str(number): dict(kept) for number, kept in sorted(frames.items())
                }
            }
        )
    except (OSError, ValueError) as error:
        log.warning(
            "the frame of objective slot %s could not be saved (%s); it is kept for this session only",
            slot,
            error,
        )
    return frame


def frame_as_saved(
    frame: dict[str, Any] | None, orientation
) -> tuple[list[int] | None, list[float] | None]:
    """The image size and pixel size as a picture is saved: a quarter turn of the orientation swaps width and height."""
    if not frame:
        return None, None
    size_px, pixel_um = list(frame["image_size_px"]), list(frame["pixel_size_um"])
    if orientation is not None and getattr(orientation, "swaps_axes", False):
        size_px, pixel_um = size_px[::-1], pixel_um[::-1]
    return size_px, pixel_um


def _checked(frame: Any) -> dict[str, Any]:
    if not isinstance(frame, dict):
        raise TypeError("a frame record must be a dictionary")
    size = frame.get("image_size_px")
    pixel = frame.get("pixel_size_um")
    if not (
        isinstance(size, (list, tuple))
        and len(size) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in size)
    ):
        raise ValueError(
            f"image_size_px must be [width, height] in whole pixels above 0, not {size!r}"
        )
    if not (
        isinstance(pixel, (list, tuple))
        and len(pixel) == 2
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            for v in pixel
        )
    ):
        raise ValueError(
            f"pixel_size_um must be [x, y] in micrometres above 0, not {pixel!r}"
        )
    return {key: frame.get(key) for key in FRAME_KEYS}
