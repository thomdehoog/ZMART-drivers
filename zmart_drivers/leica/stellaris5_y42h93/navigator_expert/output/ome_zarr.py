"""Write one capture as one OME-Zarr 0.5 image, with the community's own tools.

The format choice ``"ome-zarr"`` of ``save`` lands here: every plane of the
capture goes into one image store, ``<folder>_<hash>_<label>.ome.zarr``,
instead of one OME-TIFF a plane, so the capture exists once. The store is
made by OME's own models (``ome-zarr-models``: OME-Zarr 0.5, checked as it
is built) and written by zarr-python (Zarr v3). It holds:

- the full-resolution pixels, axes t, c, z, y, x, z ascending;
- the voxel size, and where the image lies on the sample (the corner of the
  frame, its lowest plane), in the frame the workflow drives in;
- each channel's name and colour, as LAS X described them, and a display
  window from its pixels (OME-Zarr's ``omero`` block);
- the whole capture's OME-XML (OME 2016-06: instrument, channels, the time
  and drive positions of every plane) at ``OME/METADATA.ome.xml``.

Only the full-resolution level is written. Coarser copies for looking at
the image zoomed out are the viewer's to build, from this one level, the
way it builds the live picture of a whole run; the store says its pyramid
is a mean pyramid (``type: "mean"``), which the viewer asks of every store
it takes, and which a single level trivially is.

The store is written under a temporary name beside its place and renamed
into it whole, so a reader watching the folder sees either nothing or the
finished store.

ngio would write the same store, but it imports h5py, whose HDF5 library
clashes with the one OpenCV loads (the driver uses OpenCV to measure the
camera's orientation): with OpenCV in the process first, ngio does not
import at all.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..configuration.image_stage_registration import Orientation, reorient_array
from . import materialize
from . import ome_canonical as canonical
from .product import ExportedAcquisition, PlaneIndex

__all__ = ["write_position_store"]

#: The edge of a chunk in y and x: what the run stores the viewer reads use.
PIECE = 128

AXES = "tczyx"

#: Plane z number -> where on the sample that plane was taken (``x_um``,
#: ``y_um``, ``z_um``), in the frame the workflow drives in.
Placement = Callable[[int], dict]


def write_position_store(
    exported: ExportedAcquisition,
    store: Path,
    *,
    orientation: Orientation,
    where: Placement | None = None,
    state: dict | None = None,
) -> Path:
    """Write *exported* as the OME-Zarr image *store*, and return it.

    *where* places the image on the canvas (plane z number -> its canvas x,
    y, z), and is required. A store that already exists is refused: a
    capture is never written over another.
    """
    import zarr
    from ome_zarr_models.v05.axes import Axis
    from ome_zarr_models.v05.image import Image
    from pydantic_zarr.v3 import ArraySpec

    if store.exists():
        raise RuntimeError(f"{store} already exists; a capture is never written over another")
    if where is None:
        raise ValueError(
            "an OME-Zarr store is placed by its canvas position, and none was given "
            "(save's placement); without it the image would lie at an invented place"
        )
    metadata = exported.metadata
    planes = {index: source for position in exported.positions for index, source in position.planes.items()}
    times = sorted({index.t for index in planes})
    channels = sorted({index.c for index in planes})
    numbers = sorted({index.z for index in planes})

    heights = {z: where(z).get("z_um") for z in numbers}
    # Lowest plane first: a voxel size is positive, so a stack swept
    # downwards is turned round on the way in.
    ascending = sorted(numbers, key=lambda z: (heights[z] is None, heights[z] if heights[z] is not None else z))

    first = materialize._read_source_plane(planes[PlaneIndex(t=times[0], z=numbers[0], c=channels[0])])
    first = reorient_array(first, orientation) if not orientation.is_identity else first
    volume = np.empty((len(times), len(channels), len(numbers), *first.shape), dtype=first.dtype)
    for ti, t in enumerate(times):
        for ci, c in enumerate(channels):
            for zi, z in enumerate(ascending):
                plane = materialize._read_source_plane(planes[PlaneIndex(t=t, z=z, c=c)])
                volume[ti, ci, zi] = reorient_array(plane, orientation) if not orientation.is_identity else plane

    pixel_y, pixel_x = metadata.physical_size_y_um, metadata.physical_size_x_um
    if orientation.swaps_axes:
        pixel_y, pixel_x = pixel_x, pixel_y
    if not pixel_x or not pixel_y:
        raise RuntimeError("the capture does not say its pixel size, so the image cannot be placed")
    ny, nx = volume.shape[-2:]
    centre = where(ascending[0])
    lowest = heights[ascending[0]]
    translation = [
        0.0,
        0.0,
        lowest if lowest is not None else 0.0,
        centre["y_um"] - ny * pixel_y / 2 if centre.get("y_um") is not None else 0.0,
        centre["x_um"] - nx * pixel_x / 2 if centre.get("x_um") is not None else 0.0,
    ]

    described = [metadata.channel(c) for c in channels]
    names = [channel.name or f"channel {c}" for channel, c in zip(described, channels, strict=True)]

    writing = store.with_name(f".{store.name}.{uuid.uuid4().hex[:8]}.writing")
    store.parent.mkdir(parents=True, exist_ok=True)
    spec = ArraySpec.from_array(
        volume,
        chunk_grid={"name": "regular", "configuration": {"chunk_shape": [1, 1, 1, min(PIECE, ny), min(PIECE, nx)]}},
        dimension_names=list(AXES),
        # Said outright: newer zarr refuses a multi-byte array whose byte
        # order is left to a default.
        codecs=[{"name": "bytes", "configuration": {"endian": "little"}}],
    )
    image = Image.new(
        array_specs=[spec],
        paths=["0"],
        axes=[
            Axis(name="t", type="time", unit="second"),
            Axis(name="c", type="channel"),
            Axis(name="z", type="space", unit="micrometer"),
            Axis(name="y", type="space", unit="micrometer"),
            Axis(name="x", type="space", unit="micrometer"),
        ],
        scales=[[1.0, 1.0, metadata.physical_size_z_um or 1.0, pixel_y, pixel_x]],
        translations=[translation],
        name=store.name,
        multiscale_type="mean",
    )
    group = image.to_zarr(zarr.storage.LocalStore(str(writing)), path="")
    group["0"][...] = volume
    deepest = (1 << (metadata.significant_bits or volume.dtype.itemsize * 8)) - 1
    ome = dict(group.attrs["ome"])
    ome["omero"] = {
        "channels": [
            {
                "label": name,
                "color": _hex(channel.color) if channel.color is not None else "FFFFFF",
                "window": _window(volume[:, index], deepest),
                "active": True,
            }
            for index, (name, channel) in enumerate(zip(names, described, strict=True))
        ]
    }
    group.attrs["ome"] = ome

    in_store = replace(
        metadata,
        size_t=len(times),
        size_c=len(channels),
        size_z=len(numbers),
        size_y=ny,
        size_x=nx,
        physical_size_x_um=pixel_x,
        physical_size_y_um=pixel_y,
        planes={
            PlaneIndex(t=ti, z=zi, c=ci): metadata.planes[PlaneIndex(t=t, z=z, c=c)]
            for ti, t in enumerate(times)
            for ci, c in enumerate(channels)
            for zi, z in enumerate(ascending)
            if PlaneIndex(t=t, z=z, c=c) in metadata.planes
        },
    )
    (writing / "OME").mkdir()
    (writing / "OME" / "METADATA.ome.xml").write_bytes(canonical.image_xml(in_store, image_name=store.name, state=state))
    os.replace(writing, store)
    return store


def _window(pixels: np.ndarray, deepest: int) -> dict:
    """Where a channel's display starts and ends: its 0.1st to 99.9th percentile."""
    start, end = (float(v) for v in np.percentile(pixels, (0.1, 99.9)))
    return {"min": 0.0, "max": float(deepest), "start": start, "end": end}


def _hex(rgba: int) -> str:
    """An OME colour (signed 32-bit RGBA) as the six hex digits OME-Zarr's omero block takes."""
    return f"{(rgba & 0xFFFFFFFF) >> 8:06X}"
