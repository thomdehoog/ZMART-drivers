"""Write one capture as one OME-Zarr 0.5 image store, with the community's own tools.

The store holds:

- the pixels, axes t, c, z, y, x, with the orientation already applied;
- the pixel size, and where the picture lies on the sample: the centre of its
  first pixel, half a pixel in from the frame's corner (OME-NGFF RFC-5: the
  pixel centre is the origin of the coordinates), in the coordinates the
  workflow drives in; a single plane's z scale is 1, a placeholder, and the
  store says so;
- each channel's name and colour, and a display window from its pixels,
  ending at what the camera's bits can hold (OME-Zarr's ``omero`` block);
- the capture's OME-XML at ``OME/METADATA.ome.xml`` (``ome_xml.py``).

Below the full resolution, coarser levels for looking at the picture zoomed
out: each halves y and x by the mean of 2x2 pixels, the ZMART Viewer's own
rule, until a level fits one chunk. The store says so (``type: "mean"``).

The store is written under a temporary name beside its place and renamed
into it whole, so a reader watching the folder sees nothing, or the
finished store; and a store that exists already is never written over.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Copied from the Leica driver's ``procedures/save_data/ome_zarr.py``: the
writing, the pyramid and the display window, unchanged.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from zmart_drivers import metadata_convention as convention

from . import ome_xml
from .product import AcquisitionMetadata

log = logging.getLogger(__name__)

#: The edge of a chunk in y and x: what the run stores the viewer reads use.
PIECE = 128

#: How long the finished store may wait to be renamed into place while Windows
#: refuses ("Access is denied") because another program, such as the search
#: indexer, holds one of its fresh files open for a moment (seen with the Leica
#: driver on the STELLARIS PC, 15 October 2026).
RENAME_WAIT_S = 10.0

AXES = "tczyx"


@dataclass(frozen=True)
class Capture:
    """One capture ready for writing: its pixels (t, c, z, y, x), what is said about them, and where it lies.

    ``translation`` is where the centre of the first pixel lies, in t, c, z, y, x.
    """

    volume: np.ndarray
    metadata: AcquisitionMetadata
    translation: list[float]


def translation(
    centre_x_um: float,
    centre_y_um: float,
    z_um: float,
    *,
    nx: int,
    ny: int,
    pixel_x_um: float,
    pixel_y_um: float,
) -> list[float]:
    """Where the first pixel's centre lies, for a picture whose field is centred at (x, y): half a pixel in from the corner."""
    return [
        0.0,
        0.0,
        z_um,
        centre_y_um - (ny - 1) * pixel_y_um / 2,
        centre_x_um - (nx - 1) * pixel_x_um / 2,
    ]


def write_store(
    capture: Capture, store: Path, *, notes: convention.Notes | None = None
) -> Path:
    """Write one gathered capture as the OME-Zarr image *store*, whole, and return it.

    The ZMART metadata convention: the image and the convention's version in
    its ``zarr.json``, and the OME data model with the convention's
    annotations (*notes*) in ``OME/METADATA.ome.xml``.
    """
    import zarr
    from ome_zarr_models.v05.axes import Axis
    from ome_zarr_models.v05.image import Image
    from pydantic_zarr.v3 import ArraySpec

    if store.exists():
        raise RuntimeError(
            f"{store} already exists; a capture is never written over another"
        )
    volume, metadata = capture.volume, capture.metadata
    names = [
        channel.name or f"channel {channel.index}" for channel in metadata.channels
    ]

    # Short, and hidden by its dot: the store's chunks lie several folders
    # deeper, and Windows refuses a path longer than 260 characters where long
    # paths are not enabled; repeating the store's name here cost 40 of them.
    writing = store.with_name(f".{uuid.uuid4().hex[:8]}.writing")
    store.parent.mkdir(parents=True, exist_ok=True)
    levels = mean_pyramid(volume)
    specs = [
        ArraySpec.from_array(
            level,
            chunk_grid={
                "name": "regular",
                "configuration": {
                    "chunk_shape": [
                        1,
                        1,
                        1,
                        min(PIECE, level.shape[-2]),
                        min(PIECE, level.shape[-1]),
                    ]
                },
            },
            dimension_names=list(AXES),
            # Said outright: newer zarr refuses a multi-byte array whose byte
            # order is left to a default.
            codecs=[{"name": "bytes", "configuration": {"endian": "little"}}],
        )
        for level in levels
    ]
    # Each coarser voxel is the mean of a 2x2 block, so its centre lies half
    # a finer voxel in from the block's first: x and y step on by that.
    scales, translations = [], []
    step_y, step_x = metadata.physical_size_y_um, metadata.physical_size_x_um
    t, c, z, y, x = capture.translation
    for _ in levels:
        scales.append([1.0, 1.0, metadata.physical_size_z_um or 1.0, step_y, step_x])
        translations.append([t, c, z, y, x])
        y, x = y + step_y / 2, x + step_x / 2
        step_y, step_x = step_y * 2, step_x * 2
    image = Image.new(
        array_specs=specs,
        paths=[str(index) for index in range(len(levels))],
        axes=[
            Axis(name="t", type="time", unit="second"),
            Axis(name="c", type="channel"),
            Axis(name="z", type="space", unit="micrometer"),
            Axis(name="y", type="space", unit="micrometer"),
            Axis(name="x", type="space", unit="micrometer"),
        ],
        scales=scales,
        translations=translations,
        name=store.name,
    )
    group = image.to_zarr(zarr.storage.LocalStore(str(writing)), path="")
    group["0"][...] = volume
    if not np.array_equal(group["0"][...], volume):
        _remove_when_free(writing)
        raise RuntimeError(
            f"{store.name}: the pixels read back from disk are not those written; not saved"
        )
    deepest = (1 << (metadata.significant_bits or volume.dtype.itemsize * 8)) - 1
    # The models write an absent value as null (a channel axis's unit, a
    # multiscale's metadata); a browser viewer refuses "unit": null, so what
    # is absent is left out, as the specification has it.
    for index, level in enumerate(levels[1:], start=1):
        group[str(index)][...] = level
    ome = _without_empty(dict(group.attrs["ome"]))
    # The viewer bakes only stores that say their pyramid is a mean one.
    ome["multiscales"][0]["type"] = "mean"
    ome["multiscales"][0]["metadata"] = {
        "description": _levels_described(metadata, len(levels))
    }
    ome["omero"] = {
        "channels": [
            {
                "label": name,
                "color": _hex(channel.color) if channel.color is not None else "FFFFFF",
                "window": _window(volume[:, index], deepest),
                "active": True,
                "coefficient": 1,
                "family": "linear",
                "inverted": False,
            }
            for index, (name, channel) in enumerate(
                zip(names, metadata.channels, strict=True)
            )
        ],
        "rdefs": {"defaultT": 0, "defaultZ": metadata.size_z // 2, "model": "color"},
    }
    group.attrs["ome"] = ome
    group.attrs["zmart"] = convention.zarr_attributes()
    (writing / "OME").mkdir()
    (writing / "OME" / "METADATA.ome.xml").write_bytes(
        ome_xml.image_xml(metadata, image_name=store.name, notes=notes)
    )
    _rename_when_free(writing, store)
    return store


def _rename_when_free(
    writing: Path, store: Path, *, wait_s: float = RENAME_WAIT_S
) -> None:
    """Rename the finished *writing* folder to *store*, waiting out a passing lock on it.

    Windows refuses to rename a folder while any program holds one of its
    files open, with "Access is denied". That hold is usually brief, so the
    rename is tried again every tenth of a second for up to *wait_s*, and
    how long it waited is logged. After that the error is raised as it is.
    """
    deadline = time.perf_counter() + wait_s
    started = time.perf_counter()
    while True:
        try:
            os.replace(writing, store)
        except PermissionError:
            if time.perf_counter() >= deadline:
                raise
            time.sleep(0.1)
            continue
        waited = time.perf_counter() - started
        if waited >= 0.1:
            log.warning(
                "%s: renamed into place after %.1f s; something held a file in it open",
                store.name,
                waited,
            )
        return


def _remove_when_free(writing: Path, *, wait_s: float = RENAME_WAIT_S) -> None:
    """Remove the half-written *writing* folder, waiting out a passing lock on it.

    Windows refuses to delete a file that another program holds open for a
    moment, as it refuses a rename, so the removal is tried again every tenth
    of a second for up to *wait_s*. A folder that still cannot be removed is
    logged and left: the capture is refused either way, and its name shows it
    was never finished. Seen on the LAS X simulator PC, 2026-10-21, where the
    half-written folder stayed behind in two runs of six.
    """
    deadline = time.perf_counter() + wait_s
    while writing.exists():
        try:
            shutil.rmtree(writing)
        except OSError as why:
            if time.perf_counter() >= deadline:
                log.warning("%s could not be removed: %s", writing, why)
                return
            time.sleep(0.1)


def mean_pyramid(volume: np.ndarray) -> list[np.ndarray]:
    """*volume* (t, c, z, y, x) and its coarser levels, each halving y and x by a 2x2 mean.

    Down until a level fits one chunk (:data:`PIECE`), and always at least
    one coarser level. z, c and t are never reduced.
    """
    levels = [volume]
    while len(levels) < 2 or max(levels[-1].shape[-2:]) > PIECE:
        if min(levels[-1].shape[-2:]) <= 1:
            break
        levels.append(halve_xy(levels[-1]))
    return levels


def halve_xy(values: np.ndarray) -> np.ndarray:
    """One level coarser: the mean of each 2x2 block in y and x.

    The ZMART Viewer's own reducer, so the viewer can use these levels as
    its own: an odd last row or column is repeated, the mean is taken in
    float64, and integers are rounded half to even into their own type.
    """
    h, w = values.shape[-2:]
    even = np.pad(
        values, [(0, 0)] * (values.ndim - 2) + [(0, h % 2), (0, w % 2)], mode="edge"
    )
    blocks = even.reshape(*values.shape[:-2], (h + 1) // 2, 2, (w + 1) // 2, 2)
    mean = blocks.mean(axis=(-3, -1), dtype="float64")
    return (np.rint(mean) if values.dtype.kind in "iu" else mean).astype(values.dtype)


def _levels_described(metadata, count: int) -> str:
    """What the store's levels are, as OME-Zarr's multiscale ``metadata`` says it."""
    said = (
        f"{count} levels: full resolution, then each halving y and x by the mean of "
        "2x2 voxels (an odd edge repeated, integers rounded half to even); "
        "z, c and t are not reduced"
    )
    if metadata.size_z == 1 and not metadata.physical_size_z_um:
        said += "; a single plane, so its z scale of 1 is a placeholder, not a measured size"
    return said


def _without_empty(value):
    """*value* with every null entry left out of its dictionaries, at any depth."""
    if isinstance(value, Mapping):
        return {
            key: _without_empty(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_without_empty(item) for item in value]
    return value


def _window(pixels: np.ndarray, deepest: int) -> dict:
    """Where a channel's display starts and ends: its 0.1st to 99.9th percentile."""
    start, end = (float(v) for v in np.percentile(pixels, (0.1, 99.9)))
    return {"min": 0.0, "max": float(deepest), "start": start, "end": end}


def _hex(rgba: int) -> str:
    """An OME colour (signed 32-bit RGBA) as the six hex digits OME-Zarr's omero block takes."""
    return f"{(rgba & 0xFFFFFFFF) >> 8:06X}"


def read_plane(store: Path, position: tuple[int, int, int]) -> np.ndarray:
    """One 2-D plane of the saved *store*, at (t, c, z) in the order the store holds them."""
    import zarr

    t, c, z = position
    group = zarr.open_group(zarr.storage.LocalStore(str(store)), mode="r")
    return np.asarray(group["0"][t, c, z])
