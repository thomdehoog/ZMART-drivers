"""Write one capture as one OME-TIFF file, placed by the same rules as an OME-Zarr store.

The format choice ``"ome-tiff"`` lands here. Every plane of the capture goes
into one file, ``<folder>_<hash>_<label>.ome.tif``, which opens with a drag
and drop in Fiji, napari, QuPath or Imaris. It holds the same as the
OME-Zarr store would (see :mod:`.ome_zarr`):

- the full-resolution pixels, every channel and plane, z ascending, stored
  with z changing fastest, then the channel, then time
  (``DimensionOrder="XYZCT"``);
- the same coarser copies, each halving y and x by the mean of 2x2 voxels,
  kept inside the file as sub-images of each plane, where Fiji, QuPath and
  napari look for them;
- the whole capture's OME-XML (layers 2 to 4 of ZMART's metadata
  convention), as the file's own description;
- and, in ZMART's note (``ome_canonical.POSITION_NOTE``), the OME-Zarr
  description the store would carry in its ``zarr.json`` (layer 1): every
  level's scale, the centre of its first voxel as its translation, the
  channels' display, and the convention's version.

Where the image lies is read from that note only. The OME-XML's own
``PositionX`` and ``PositionY`` stay as the microscope reported them, but
programs disagree on which point of a picture they name, so ZMART never
places a picture by them.

The file is written under a temporary name beside its place and renamed
into it whole, so a reader watching the folder sees either nothing or the
finished file.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np

from zmart_drivers import metadata_convention as convention

from . import ome_canonical as canonical
from . import ome_zarr
from .ome_zarr import Capture

__all__ = ["read_plane", "write_tiff"]

#: How hard each tile is compressed (zlib). Level 6 is the usual default:
#: lossless, and the file is written once.
DEFLATE_LEVEL = 6


def write_tiff(
    capture: Capture, path: Path, *, notes: convention.Notes | None = None
) -> Path:
    """Write one gathered capture as the OME-TIFF file *path*, whole, and return it.

    A file that already exists is refused: a capture is never written over
    another. Raises ``RuntimeError`` when the pixels read back from the
    file are not the ones written; nothing is then left behind.
    """
    import tifffile
    import zarr

    if path.exists():
        raise RuntimeError(
            f"{path} already exists; a capture is never written over another"
        )
    volume, metadata = capture.volume, capture.metadata
    levels = ome_zarr.mean_pyramid(volume)
    # The description is made exactly as for the OME-Zarr store, by the same
    # model, only kept in memory: so both formats place the image alike.
    model = ome_zarr.image_of(capture, path.name, levels)
    in_memory = model.to_zarr(zarr.storage.MemoryStore(), path="")
    ome = ome_zarr.described(dict(in_memory.attrs["ome"]), capture, len(levels))
    note = {
        "multiscales": json.dumps(ome["multiscales"], sort_keys=True),
        "omero": json.dumps(ome["omero"], sort_keys=True),
        "zmart": json.dumps(convention.zarr_attributes(), sort_keys=True),
    }
    description = canonical.image_xml(
        metadata, image_name=path.name, notes=notes, position_note=note
    )

    # Every plane as a page, t outermost and z innermost, as XYZCT says.
    pages = [level.reshape(-1, *level.shape[-2:]) for level in levels]
    options = {
        "tile": (ome_zarr.PIECE, ome_zarr.PIECE),
        "photometric": "minisblack",
        "compression": "zlib",
        "compressionargs": {"level": DEFLATE_LEVEL},
        "metadata": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path.with_name(f".{uuid.uuid4().hex[:8]}.writing")
    try:
        # A classic TIFF cannot be larger than 4 GB; the larger kind can.
        with tifffile.TiffWriter(writing, bigtiff=volume.nbytes >= 2**31) as tif:
            for index, plane in enumerate(pages[0]):
                tif.write(
                    plane,
                    subifds=len(levels) - 1,
                    description=description if index == 0 else None,
                    **options,
                )
                for smaller in pages[1:]:
                    tif.write(smaller[index], subfiletype=1, **options)
        with tifffile.TiffFile(writing) as tif:
            read_back = tif.series[0].levels[0].asarray()
        if not np.array_equal(read_back.reshape(volume.shape), volume):
            raise RuntimeError(
                f"{path.name}: the pixels read back from disk are not those written; not saved"
            )
        ome_zarr._rename_when_free(writing, path)
    finally:
        writing.unlink(missing_ok=True)
    return path


def read_plane(path: Path, position: tuple[int, int, int], shape: tuple[int, ...]) -> np.ndarray:
    """One 2-D plane of the saved file *path*, at (t, c, z) in the order the file holds them.

    *shape* is the capture's full (t, c, z, y, x): with it, the plane's page
    is found without reading any other.
    """
    import tifffile

    t, c, z = position
    _, channels, depth = shape[:3]
    with tifffile.TiffFile(path) as tif:
        return tif.pages[(t * channels + c) * depth + z].asarray()
