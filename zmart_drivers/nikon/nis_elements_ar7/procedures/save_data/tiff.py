"""Read the TIFF that NIS saved for one capture: its channels, its pixels, and NIS's own description.

NIS writes the picture with ``ImageSaveAs`` as a TIFF into the staging
folder (``procedures/acquire.py``). How a camera's picture comes out in a
TIFF depends on the camera, so the channels are read by named rules, from
the layout tifffile finds (its ``axes``):

- ``YX``: one channel, a monochrome camera, named after the optical configuration;
- ``YXS`` with three samples: a colour camera, split into red, green and blue channels;
- ``CYX`` (or ``QYX``/``IYX``, how tifffile names pages it cannot place): one channel per page.

Anything else is refused, saying what NIS wrote, rather than guessed at.

The pixels must be the camera's raw values, not the picture as NIS displays
it: a file with 8-bit pixels from a camera NIS reported as deeper than 8
bits, or with values above what the camera's bits can hold, is refused.
(Whether ``ImageSaveAs(path, 18, 0)`` writes raw values on NIS 7 is to be
checked on the microscope; the plan's section 11 says so.)

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: The colours of a colour camera's channels, as OME's signed 32-bit RGBA.
RGB_COLOURS = ((0xFF0000FF - (1 << 32)), 0x00FF00FF, 0x0000FFFF)
PAGE_AXES = ("C", "Q", "I")


@dataclass(frozen=True)
class StagedPicture:
    """One staged picture read back: its channels (c, y, x), their names and colours, and NIS's description."""

    channels: np.ndarray
    names: tuple[str, ...]
    colours: tuple[int | None, ...]
    description: str | None
    axes: str


def read_staged(path: str | Path, *, bits: int, name: str | None) -> StagedPicture:
    """Read the staged TIFF at *path*; *bits* is the depth NIS reported, *name* the optical configuration's.

    Raises ``RuntimeError`` naming what is wrong when the file is not one of
    the layouts above, or does not hold the camera's raw values.
    """
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        series = tif.series[0]
        axes = series.axes
        pixels = series.asarray()
        description = tif.pages[0].description or None

    if axes == "YX":
        channels = pixels[np.newaxis]
        names = (name or "channel 0",)
        colours = (None,)
    elif axes == "YXS" and pixels.shape[-1] == 3:
        channels = np.moveaxis(pixels, -1, 0)
        names = ("red", "green", "blue")
        colours = RGB_COLOURS
    elif len(axes) == 3 and axes[0] in PAGE_AXES and axes[1:] == "YX":
        channels = pixels
        names = tuple(
            f"{name or 'channel'} {index}" for index in range(pixels.shape[0])
        )
        colours = (None,) * pixels.shape[0]
    else:
        raise RuntimeError(
            f"NIS saved {path} with the layout {axes!r} and shape {pixels.shape}; this driver reads one plane "
            "(YX), a colour picture (YXS) or one page per channel, and nothing else"
        )

    if channels.dtype.kind not in "ui":
        raise RuntimeError(
            f"NIS saved {path} with {channels.dtype} pixels; this driver reads whole numbers only"
        )
    deepest = (1 << bits) - 1 if bits else None
    if bits and bits > 8 and channels.dtype.itemsize == 1 and axes != "YXS":
        raise RuntimeError(
            f"NIS saved {path} with 8-bit pixels, but the camera gives {bits} bits: it saved the picture as it is "
            "displayed, not the camera's values; nothing was converted"
        )
    if (
        deepest is not None
        and channels.size
        and int(channels.max()) > deepest
        and axes != "YXS"
    ):
        raise RuntimeError(
            f"{path} holds values above {deepest}, more than the camera's {bits} bits can give"
        )
    return StagedPicture(
        channels=np.ascontiguousarray(channels),
        names=names,
        colours=colours,
        description=description,
        axes=axes,
    )
