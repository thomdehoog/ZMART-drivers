"""Write one capture as one OME-Zarr 0.5 image, with the community's own tools.

The format choice ``"ome-zarr"`` of ``save`` lands here: every plane of the
capture goes into one image store, ``<folder>_<hash>_<label>.ome.zarr``,
instead of one OME-TIFF a plane, so the capture exists once. The store is
made by OME's own models (``ome-zarr-models``: OME-Zarr 0.5, checked as it
is built) and written by zarr-python (Zarr v3). It holds:

- the full-resolution pixels, axes t, c, z, y, x, z ascending;
- the voxel size, and where the image lies on the sample: the centre of its
  first voxel, half a pixel in from the frame's corner, at its lowest plane
  (OME-NGFF RFC-5: the voxel centre is the origin of the coordinates), in
  the frame the workflow drives in;
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

import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from ...driver_setup.orientation import Orientation, reorient_array
from . import ome_canonical as materialize
from zmart_drivers import metadata_convention as convention
from . import ome_canonical as canonical
from .product import AcquisitionMetadata, ExportedAcquisition, PlaneIndex

__all__ = [
    "Capture",
    "gather",
    "max_projection",
    "read_plane",
    "write_position_store",
    "write_store",
]

log = logging.getLogger(__name__)

#: The edge of a chunk in y and x: what the run stores the viewer reads use.
PIECE = 128

#: How long the finished store may wait to be renamed into place while
#: Windows refuses ("Access is denied"): another program, such as the search
#: indexer, has one of its fresh files open for a moment. On the STELLARIS
#: (15 October 2026) this happened to two captures in ten and both folders
#: could be renamed a minute later, so the store waits rather than fails.
RENAME_WAIT_S = 10.0

AXES = "tczyx"

#: Plane z number -> where on the sample that plane was taken (``x_um``,
#: ``y_um``, ``z_um``), in the frame the workflow drives in.
Placement = Callable[[int], dict]


@dataclass(frozen=True)
class Capture:
    """One capture gathered for writing: its pixels and what is said about them.

    ``volume`` is t, c, z, y, x with z ascending and the orientation applied;
    ``metadata`` describes exactly that volume (its planes renumbered from
    the lowest up); ``translation`` is where the centre of the volume's
    first voxel lies on the canvas, in t, c, z, y, x.
    """

    volume: np.ndarray
    metadata: AcquisitionMetadata
    translation: list[float]
    #: Where each plane of the vendor's capture sits in ``volume``, as (t, c, z).
    #: The volume holds its planes lowest first, which can be the other way
    #: round from the vendor's numbering, so a plane is found through this.
    store_position: Mapping[PlaneIndex, tuple[int, int, int]] = field(
        default_factory=dict
    )


def write_position_store(
    exported: ExportedAcquisition,
    store: Path,
    *,
    orientation: Orientation,
    where: Placement | None = None,
    notes: convention.Notes | None = None,
) -> Path:
    """Write *exported* as the OME-Zarr image *store*, and return it.

    *where* places the image on the canvas (plane z number -> its canvas x,
    y, z), and is required. A store that already exists is refused: a
    capture is never written over another.
    """
    if where is None:
        raise ValueError(
            "an OME-Zarr store is placed by its canvas position, and none was given "
            "(save's placement); without it the image would lie at an invented place"
        )
    return write_store(
        gather(exported, orientation=orientation, where=where), store, notes=notes
    )


def gather(
    exported: ExportedAcquisition, *, orientation: Orientation, where: Placement | None
) -> Capture:
    """Read every plane of *exported* into one volume, lowest plane first, and place it.

    Without *where* the planes keep the vendor's order and the volume is not
    placed (its translation is zero): enough for a projection saved as
    OME-TIFF, never for a store.
    """
    metadata = exported.metadata
    planes = {
        index: source
        for position in exported.positions
        for index, source in position.planes.items()
    }
    times = sorted({index.t for index in planes})
    channels = sorted({index.c for index in planes})
    numbers = sorted({index.z for index in planes})

    heights = {z: where(z).get("z_um") if where is not None else None for z in numbers}
    # Lowest plane first: a voxel size is positive, so a stack swept
    # downwards is turned round on the way in.
    ascending = sorted(
        numbers,
        key=lambda z: (heights[z] is None, heights[z] if heights[z] is not None else z),
    )

    first = materialize._read_source_plane(
        planes[PlaneIndex(t=times[0], z=numbers[0], c=channels[0])]
    )
    first = reorient_array(first, orientation) if not orientation.is_identity else first
    volume = np.empty(
        (len(times), len(channels), len(numbers), *first.shape), dtype=first.dtype
    )
    for ti, t in enumerate(times):
        for ci, c in enumerate(channels):
            for zi, z in enumerate(ascending):
                plane = materialize._read_source_plane(
                    planes[PlaneIndex(t=t, z=z, c=c)]
                )
                volume[ti, ci, zi] = (
                    reorient_array(plane, orientation)
                    if not orientation.is_identity
                    else plane
                )

    pixel_y, pixel_x = metadata.physical_size_y_um, metadata.physical_size_x_um
    if orientation.swaps_axes:
        pixel_y, pixel_x = pixel_x, pixel_y
    if where is not None and (not pixel_x or not pixel_y):
        raise RuntimeError(
            "the capture does not say its pixel size, so the image cannot be placed"
        )
    ny, nx = volume.shape[-2:]
    centre = where(ascending[0]) if where is not None else {}
    lowest = heights[ascending[0]]
    # The planes are placed by the middle of the frame; the store by the
    # centre of its first voxel, half a pixel in from the frame's corner.
    translation = [
        0.0,
        0.0,
        lowest if lowest is not None else 0.0,
        centre["y_um"] - (ny - 1) * pixel_y / 2
        if centre.get("y_um") is not None
        else 0.0,
        centre["x_um"] - (nx - 1) * pixel_x / 2
        if centre.get("x_um") is not None
        else 0.0,
    ]
    described = replace(
        metadata,
        size_t=len(times),
        size_c=len(channels),
        size_z=len(numbers),
        size_y=ny,
        size_x=nx,
        physical_size_x_um=pixel_x,
        physical_size_y_um=pixel_y,
        channels=tuple(
            replace(metadata.channel(c), index=ci) for ci, c in enumerate(channels)
        ),
        planes={
            PlaneIndex(t=ti, z=zi, c=ci): metadata.planes[PlaneIndex(t=t, z=z, c=c)]
            for ti, t in enumerate(times)
            for ci, c in enumerate(channels)
            for zi, z in enumerate(ascending)
            if PlaneIndex(t=t, z=z, c=c) in metadata.planes
        },
    )
    store_position = {
        PlaneIndex(t=t, z=z, c=c): (ti, ci, zi)
        for ti, t in enumerate(times)
        for ci, c in enumerate(channels)
        for zi, z in enumerate(ascending)
    }
    return Capture(
        volume=volume,
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
    """The brightest of every pixel over z: one plane, lying where the stack starts.

    What it was made from is said beside it: the method, how many planes,
    and the lowest and highest of them on the canvas. A plane of a
    projection has no single drive position, so the actuators are left out.
    """
    metadata = capture.metadata
    heights = sorted(
        plane.position_z_um
        for plane in metadata.planes.values()
        if plane.position_z_um is not None
    )
    flat = {
        PlaneIndex(t=index.t, z=0, c=index.c): replace(plane, actuators=())
        for index, plane in metadata.planes.items()
        if index.z == 0
    }
    facts = [("projection", "max over z"), ("projected planes", str(metadata.size_z))]
    if heights:
        facts += [
            ("lowest plane z (µm)", canonical._num(heights[0])),
            ("highest plane z (µm)", canonical._num(heights[-1])),
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
    ny, nx = volume.shape[-2:]
    names = [
        channel.name or f"channel {channel.index}" for channel in metadata.channels
    ]

    writing = store.with_name(f".{store.name}.{uuid.uuid4().hex[:8]}.writing")
    store.parent.mkdir(parents=True, exist_ok=True)
    spec = ArraySpec.from_array(
        volume,
        chunk_grid={
            "name": "regular",
            "configuration": {"chunk_shape": [1, 1, 1, min(PIECE, ny), min(PIECE, nx)]},
        },
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
        scales=[
            [
                1.0,
                1.0,
                metadata.physical_size_z_um or 1.0,
                metadata.physical_size_y_um,
                metadata.physical_size_x_um,
            ]
        ],
        translations=[capture.translation],
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
    ome = _without_empty(dict(group.attrs["ome"]))
    ome["multiscales"][0]["metadata"] = {"description": _levels_described(metadata)}
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
        canonical.image_xml(metadata, image_name=store.name, notes=notes)
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


def _levels_described(metadata) -> str:
    """What the store's levels are, as OME-Zarr's multiscale ``metadata`` says it."""
    said = "one level, at full resolution: no downsampled levels"
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
