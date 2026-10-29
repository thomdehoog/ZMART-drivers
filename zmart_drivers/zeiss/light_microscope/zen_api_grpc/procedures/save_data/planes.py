"""The planes of a capture: which channel, depth and time point each image is, and where it was taken.

The controller asks every driver to describe a capture the same way: one
entry per image plane with its channel ``c``, its depth ``z`` and its time
point ``t``, and the stage position it was taken at, in the sample's frame.
``z = 0`` is the lowest slice and each depth above it is higher, whichever
way the microscope moved.

Where the numbers come from, in this order: the CZI file ZEN wrote, read
with ZEISS's ``pylibCZIrw`` when it is installed (its sizes, channel names
and pixel size are exact); else what ZEN said about the experiment (its
z-stack, its tracks, the status after the run). The slice heights come from
the experiment's z-stack: ZEN's focus positions, put into the frame like
every other position.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def read_czi_shape(path: Path | str) -> dict | None:
    """What the CZI says about itself: sizes, channel names and the pixel size; None without ``pylibCZIrw``.

    ``{"size_c", "size_z", "size_t", "width_px", "height_px", "channels",
    "pixel_size_um": [x, y] or None, "z_step_um"}``. A file that cannot be
    read answers None too, with a warning.
    """
    try:
        from pylibCZIrw import czi as pyczi
    except ImportError:
        return None
    try:
        with pyczi.open_czi(str(path)) as document:
            box = document.total_bounding_box
            metadata = document.metadata
    except Exception as why:  # noqa: BLE001 -- a file ZEN wrote in a way this reader does not know
        log.warning(
            "the CZI %s could not be read (%s); its planes are described from ZEN's answers",
            path,
            why,
        )
        return None
    sizes = {axis: span[1] - span[0] for axis, span in box.items()}
    image = (metadata.get("ImageDocument") or {}).get("Metadata") or {}
    channels = _channel_names(image)
    scaling = _scaling_um(image)
    return {
        "size_c": max(1, sizes.get("C", 1)),
        "size_z": max(1, sizes.get("Z", 1)),
        "size_t": max(1, sizes.get("T", 1)),
        "width_px": sizes.get("X"),
        "height_px": sizes.get("Y"),
        "channels": channels,
        "pixel_size_um": [scaling["X"], scaling["Y"]]
        if "X" in scaling and "Y" in scaling
        else None,
        "z_step_um": scaling.get("Z"),
    }


def _channel_names(image: dict) -> list[str]:
    try:
        found = image["Information"]["Image"]["Dimensions"]["Channels"]["Channel"]
    except (KeyError, TypeError):
        return []
    if isinstance(found, dict):
        found = [found]
    return [
        str(one.get("@Name") or one.get("@Id") or index)
        for index, one in enumerate(found)
    ]


def _scaling_um(image: dict) -> dict:
    try:
        distances = image["Scaling"]["Items"]["Distance"]
    except (KeyError, TypeError):
        return {}
    if isinstance(distances, dict):
        distances = [distances]
    found = {}
    for one in distances:
        try:
            value = float(one["Value"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            found[str(one.get("@Id"))] = value
    return found


def slice_heights_um(
    stack: dict | None, slices: int, z_at_um: float, to_frame
) -> list[float]:
    """The height of each slice in the frame, in the CZI's own order.

    *stack* is the experiment's z-stack reading (ZEN focus positions), *slices*
    how many planes there are, *z_at_um* the frame z of the capture, and
    *to_frame* turns a ZEN focus position into the frame. Without a stack,
    every slice sits at the capture's z.
    """
    if not stack or not stack.get("is_zstack") or slices <= 1:
        return [z_at_um] * slices
    first, interval = stack["first_um"], stack["interval_um"]
    if (
        stack.get("last_um") is not None
        and first is not None
        and stack["last_um"] < first
        and interval > 0
    ):
        interval = -interval
    return [to_frame(first + index * interval) for index in range(slices)]


def describe_planes(
    *,
    path: Path | str,
    channels: list[str],
    heights_um: list[float],
    time_points: int,
    x_um: float,
    y_um: float,
) -> list[dict]:
    """One entry per plane, with ``z`` counted from the lowest slice upwards.

    *heights_um* are the slice heights in the CZI's order; ``czi_z`` keeps
    that order, so the plane can be found in the file, while ``z`` is the
    depth index the controller asks for.
    """
    ascending = sorted(range(len(heights_um)), key=lambda index: heights_um[index])
    depth_of = {czi_index: depth for depth, czi_index in enumerate(ascending)}
    planes = []
    for t in range(time_points):
        for c, channel in enumerate(channels):
            for czi_z, height in enumerate(heights_um):
                planes.append(
                    {
                        "path": str(path),
                        "c": c,
                        "z": depth_of[czi_z],
                        "t": t,
                        "x_um": float(x_um),
                        "y_um": float(y_um),
                        "z_um": float(height),
                        "channel": channel,
                        "czi_z": czi_z,
                    }
                )
    return planes
