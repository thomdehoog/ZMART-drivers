"""Small grey pictures for the wizard's page, written as PNG files without any drawing package.

The orientation step shows the operator the three snaps it measured from,
side by side, so they can see that the field had structure and that it
shifted between the snaps. A PNG file is simple enough to write by hand
(a short header, the rows of pixels compressed with ``zlib``, and a check
sum), so the wizard needs only ``numpy``, which the driver already uses.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

#: The widest a sheet may be, in pixels; wider snaps are shown smaller.
SHEET_WIDTH_PX = 1200

#: The white gap between two snaps on a sheet, in pixels.
GAP_PX = 8


def to_grey(plane):
    """A plane of any brightness as 0–255 grey, stretched so the faint and the bright parts both show.

    The darkest 1 % of the pixels become black and the brightest 1 % white,
    so one very bright speck does not leave the rest of the picture dark.
    """
    import numpy as np

    values = np.asarray(plane, dtype=float)
    low, high = np.percentile(values, [1, 99])
    if high <= low:
        return np.zeros(values.shape, dtype=np.uint8)
    return np.clip((values - low) / (high - low) * 255, 0, 255).round().astype(np.uint8)


def sheet(planes, *, width_px: int = SHEET_WIDTH_PX):
    """The planes side by side, each stretched on its own, with a white gap between them; one 2-D grey array.

    The planes are shown smaller, by keeping every second (or third, ...)
    pixel, when side by side they would be wider than *width_px*.
    """
    import numpy as np

    greys = [to_grey(p) for p in planes]
    total = sum(g.shape[1] for g in greys) + GAP_PX * (len(greys) - 1)
    step = max(1, -(-total // width_px))
    greys = [g[::step, ::step] for g in greys]
    height = max(g.shape[0] for g in greys)
    gap = np.full((height, GAP_PX), 255, dtype=np.uint8)
    parts = []
    for i, grey in enumerate(greys):
        padded = np.full((height, grey.shape[1]), 255, dtype=np.uint8)
        padded[: grey.shape[0]] = grey
        parts.append(padded)
        if i < len(greys) - 1:
            parts.append(gap)
    return np.hstack(parts)


def write_png(path: str | Path, grey) -> Path:
    """Write a 2-D array of 0–255 grey values as a PNG file; returns the path."""
    import numpy as np

    grey = np.ascontiguousarray(grey, dtype=np.uint8)
    height, width = grey.shape
    # Each row starts with a 0: "no filter", the simplest way PNG stores a row.
    rows = np.hstack([np.zeros((height, 1), dtype=np.uint8), grey]).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )
    return path
