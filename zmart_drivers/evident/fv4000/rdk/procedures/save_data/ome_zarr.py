"""Write one capture as one OME-Zarr 0.5 image, with the community's own tools.

Every plane of the capture goes into one image store,
``<stem>.ome.zarr``, so the capture exists once. The store is made by OME's
own models (``ome-zarr-models``: OME-Zarr 0.5, checked as it is built) and
written by zarr-python (Zarr v3). It holds:

- the full-resolution pixels, axes t, c, z, y, x, z ascending;
- the voxel size, and where the image lies on the sample: the centre of its
  first voxel, half a pixel in from the frame's corner, at its lowest plane,
  in the frame the workflow drives in;
- each channel's name and a display window from its pixels (OME-Zarr's
  ``omero`` block);
- the whole capture's OME-XML at ``OME/METADATA.ome.xml`` (``ome_xml.py``).

Below the full-resolution level, coarser levels for looking at the image
zoomed out: each halves y and x by the mean of 2x2 voxels, by the ZMART
Viewer's own rule, down until a level fits one chunk. The store says so
(``type: "mean"``).

The store is written under a temporary name beside its place and renamed
into it whole, so a reader watching the folder sees either nothing or the
finished store.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
After the Leica driver's OME-Zarr writer.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from zmart_drivers import metadata_convention as convention

from ...driver_setup.orientation import Orientation, reorient_array
from . import ome_xml
from .product import AcquisitionMetadata, PlaneIndex

log = logging.getLogger(__name__)

#: The edge of a chunk in y and x: what the ZMART Viewer reads.
PIECE = 128
#: How long a finished store may wait to be renamed into place while Windows
#: refuses because another program holds a fresh file open for a moment.
RENAME_WAIT_S = 10.0
AXES = "tczyx"

#: Plane z number -> where on the sample that plane was taken (``x_um``, ``y_um``, ``z_um``).
Placement = Callable[[int], dict]


@dataclass(frozen=True)
class Capture:
    """One capture gathered for writing: its pixels and what is said about them.

    ``volume`` is t, c, z, y, x with z ascending and the orientation applied;
    ``metadata`` describes exactly that volume; ``translation`` is where the
    centre of the volume's first voxel lies on the canvas, in t, c, z, y, x;
    ``store_position`` says where each plane of FLUOVIEW's capture sits in
    the volume, as (t, c, z), because the volume holds its planes lowest first.
    """

    volume: np.ndarray
    metadata: AcquisitionMetadata
    translation: list[float]
    store_position: Mapping[PlaneIndex, tuple[int, int, int]] = field(
        default_factory=dict
    )


def gather(
    volume: np.ndarray,
    metadata: AcquisitionMetadata,
    *,
    orientation: Orientation,
    where: Placement,
) -> Capture:
    """Turn the capture's volume (t, c, z, y, x, as FLUOVIEW numbered it) into one placed, oriented volume, lowest plane first."""
    size_t, size_c, size_z = volume.shape[:3]
    heights = {z: where(z).get("z_um") for z in range(size_z)}
    ascending = sorted(
        range(size_z),
        key=lambda z: (heights[z] is None, heights[z] if heights[z] is not None else z),
    )
    planes = []
    for t in range(size_t):
        for c in range(size_c):
            for z in ascending:
                plane = volume[t, c, z]
                planes.append(
                    reorient_array(plane, orientation)
                    if not orientation.is_identity
                    else plane
                )
    ny, nx = planes[0].shape
    oriented = np.asarray(planes).reshape(size_t, size_c, size_z, ny, nx)

    pixel_y, pixel_x = metadata.physical_size_y_um, metadata.physical_size_x_um
    if orientation.swaps_axes:
        pixel_y, pixel_x = pixel_x, pixel_y
    if not pixel_x or not pixel_y:
        raise RuntimeError(
            "the capture does not say its pixel size, so the image cannot be placed"
        )
    centre = where(ascending[0])
    lowest = heights[ascending[0]]
    translation = [
        0.0,
        0.0,
        lowest if lowest is not None else 0.0,
        centre["y_um"] - (ny - 1) * pixel_y / 2,
        centre["x_um"] - (nx - 1) * pixel_x / 2,
    ]
    described = replace(
        metadata,
        size_y=ny,
        size_x=nx,
        physical_size_x_um=pixel_x,
        physical_size_y_um=pixel_y,
        planes={
            PlaneIndex(t=t, z=zi, c=c): metadata.planes[PlaneIndex(t=t, z=z, c=c)]
            for t in range(size_t)
            for c in range(size_c)
            for zi, z in enumerate(ascending)
            if PlaneIndex(t=t, z=z, c=c) in metadata.planes
        },
    )
    store_position = {
        PlaneIndex(t=t, z=z, c=c): (t, c, zi)
        for t in range(size_t)
        for c in range(size_c)
        for zi, z in enumerate(ascending)
    }
    return Capture(
        volume=oriented,
        metadata=described,
        translation=translation,
        store_position=store_position,
    )


def read_plane(store: Path, position: tuple[int, int, int]) -> np.ndarray:
    """One 2-D plane of the saved *store*, at (t, c, z) in the order the store holds them."""
    import zarr

    t, c, z = position
    group = zarr.open_group(zarr.storage.LocalStore(str(store)), mode="r")
    return np.asarray(group["0"][t, c, z])


def max_projection(capture: Capture) -> Capture:
    """The brightest of every pixel over z: one plane, lying where the stack starts, saying what it was made from."""
    metadata = capture.metadata
    heights = sorted(
        p.position_z_um for p in metadata.planes.values() if p.position_z_um is not None
    )
    flat = {
        PlaneIndex(t=i.t, z=0, c=i.c): replace(p, actuators=())
        for i, p in metadata.planes.items()
        if i.z == 0
    }
    facts = [("projection", "max over z"), ("projected planes", str(metadata.size_z))]
    if heights:
        facts += [
            ("lowest plane z (um)", f"{heights[0]:.12g}"),
            ("highest plane z (um)", f"{heights[-1]:.12g}"),
        ]
    return Capture(
        volume=capture.volume.max(axis=2, keepdims=True),
        metadata=replace(
            metadata,
            size_z=1,
            physical_size_z_um=None,
            planes=flat,
            annotations=(*metadata.annotations, *facts),
        ),
        translation=list(capture.translation),
    )


def write_store(
    capture: Capture, store: Path, *, notes: convention.Notes | None = None
) -> Path:
    """Write one gathered capture as the OME-Zarr image *store*, whole, and answer it. A store that exists is refused."""
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
            codecs=[{"name": "bytes", "configuration": {"endian": "little"}}],
        )
        for level in levels
    ]
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
    for index, level in enumerate(levels[1:], start=1):
        group[str(index)][...] = level
    deepest = (1 << (metadata.significant_bits or volume.dtype.itemsize * 8)) - 1
    ome = _without_empty(dict(group.attrs["ome"]))
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
        if time.perf_counter() - started >= 0.1:
            log.warning(
                "%s: renamed into place after %.1f s; something held a file in it open",
                store.name,
                time.perf_counter() - started,
            )
        return


def _remove_when_free(writing: Path, *, wait_s: float = RENAME_WAIT_S) -> None:
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
    """*volume* and its coarser levels, each halving y and x by a 2x2 mean, down until a level fits one chunk."""
    levels = [volume]
    while len(levels) < 2 or max(levels[-1].shape[-2:]) > PIECE:
        if min(levels[-1].shape[-2:]) <= 1:
            break
        levels.append(halve_xy(levels[-1]))
    return levels


def halve_xy(values: np.ndarray) -> np.ndarray:
    """One level coarser: the mean of each 2x2 block in y and x (an odd edge repeated, integers rounded half to even)."""
    h, w = values.shape[-2:]
    even = np.pad(
        values, [(0, 0)] * (values.ndim - 2) + [(0, h % 2), (0, w % 2)], mode="edge"
    )
    blocks = even.reshape(*values.shape[:-2], (h + 1) // 2, 2, (w + 1) // 2, 2)
    mean = blocks.mean(axis=(-3, -1), dtype="float64")
    return (np.rint(mean) if values.dtype.kind in "iu" else mean).astype(values.dtype)


def _levels_described(metadata, count: int) -> str:
    said = (
        f"{count} levels: full resolution, then each halving y and x by the mean of 2x2 voxels "
        "(an odd edge repeated, integers rounded half to even); z, c and t are not reduced"
    )
    if metadata.size_z == 1 and not metadata.physical_size_z_um:
        said += "; a single plane, so its z scale of 1 is a placeholder, not a measured size"
    return said


def _without_empty(value):
    if isinstance(value, Mapping):
        return {
            key: _without_empty(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_without_empty(item) for item in value]
    return value


def _window(pixels: np.ndarray, deepest: int) -> dict:
    start, end = (float(v) for v in np.percentile(pixels, (0.1, 99.9)))
    return {"min": 0.0, "max": float(deepest), "start": start, "end": end}


def _hex(rgba: int) -> str:
    return f"{(rgba & 0xFFFFFFFF) >> 8:06X}"
