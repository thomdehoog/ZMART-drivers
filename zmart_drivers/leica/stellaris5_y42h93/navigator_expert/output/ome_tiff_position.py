"""Write one capture as one OME-TIFF file, holding every channel and plane of it.

The format choice ``"ome-tiff"`` of ``save`` lands here: every plane of the
capture goes into one file, ``<folder>_<hash>_<label>.ome.tif``, a page per
plane, so the capture exists once and opens with a drag and drop in Fiji,
napari, QuPath or Imaris as one stack with the right pixel size and channel
names. It is gathered exactly as the OME-Zarr store is (``ome_zarr.gather``):
the full-resolution pixels, z ascending, the orientation applied, and placed
on the sample in the frame the workflow drives in.

Its OME-XML is the capture's own full description (instrument, channels, the
time and drive positions of every plane, and the machine's state), with two
differences from the description LAS X's planes carry:

- every plane's stage position is the **corner** of the frame (its first
  pixel), not its middle, the convention every ZMART position uses, so the
  file lies where an OME-Zarr store of the same capture would;
- ZMART's note, a map annotation with the namespace ``zmart/position``, says
  so (``position_origin`` = ``corner``), and keeps each channel's colour and
  the brightness window it first opens with (``omero``, as the OME-Zarr store
  says it), for which OME-XML has no place. The ZMART interface and viewer
  read that note to use the file as a position as it is. Other programs skip
  it.

Only the full-resolution level is written, as for the OME-Zarr store: the
viewer builds the zoomed-out copies itself. The pixels are packed with
deflate at its fastest level, which every OME reader decodes.

The file is written under a temporary name beside its place, read back, and
renamed into place whole, so a reader watching the folder sees either
nothing or the finished file.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..configuration.image_stage_registration import Orientation
from . import ome_canonical as canonical
from .ome_zarr import PIECE, Capture, Placement, _rename_when_free, gather, omero_of
from .product import ExportedAcquisition

__all__ = ["ZMART_NOTE", "write_position_tiff", "write_tiff"]

#: The namespace of ZMART's note, which the ZMART interface and viewer read.
ZMART_NOTE = "zmart/position"

#: How hard the pixels are packed: deflate at its fastest level, about a third
#: of the raw size for fluorescence pictures, and quick enough for a live run.
DEFLATE_LEVEL = 1


def write_position_tiff(
    exported: ExportedAcquisition,
    path: Path,
    *,
    orientation: Orientation,
    where: Placement | None = None,
    state: dict | None = None,
) -> Path:
    """Write *exported* as the OME-TIFF position *path*, and return it.

    *where* places the picture on the canvas (plane z number -> its canvas
    x, y, z), and is required. A file that already exists is refused: a
    capture is never written over another.
    """
    if where is None:
        raise ValueError(
            "an OME-TIFF position is placed by its canvas position, and none was given "
            "(save's placement); without it the picture would lie at an invented place"
        )
    return write_tiff(gather(exported, orientation=orientation, where=where), path, state=state)


def write_tiff(capture: Capture, path: Path, *, state: dict | None = None) -> Path:
    """Write one gathered capture as the OME-TIFF *path*, whole, and return it."""
    import tifffile

    if path.exists():
        raise RuntimeError(f"{path} already exists; a capture is never written over another")
    volume = capture.volume
    planes = volume.reshape(-1, *volume.shape[-2:])  # z fastest, then c, then t: XYZCT
    described = _described(capture, image_name=path.name, state=state)

    writing = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.writing")
    path.parent.mkdir(parents=True, exist_ok=True)
    options = {
        "tile": (PIECE, PIECE),
        "photometric": "minisblack",
        "compression": "zlib",
        "compressionargs": {"level": DEFLATE_LEVEL},
        "metadata": None,
    }
    try:
        # A classic TIFF cannot be larger than 4 GB; the larger kind can.
        with tifffile.TiffWriter(writing, bigtiff=volume.nbytes >= 2**31) as tif:
            for index, plane in enumerate(planes):
                tif.write(plane, description=described if index == 0 else None, **options)
        if not np.array_equal(tifffile.imread(writing).reshape(planes.shape), planes):
            raise RuntimeError(f"{path.name}: the pixels read back from disk are not those written; not saved")
        _rename_when_free(writing, path)
    finally:
        writing.unlink(missing_ok=True)
    return path


def _described(capture: Capture, *, image_name: str, state: dict | None) -> bytes:
    """The capture's OME-XML, placed at its corner, its planes in this file, with ZMART's note."""
    _, _, _, corner_y, corner_x = capture.translation
    metadata = capture.metadata
    metadata = replace(
        metadata,
        planes={
            index: replace(plane, position_x_um=corner_x, position_y_um=corner_y)
            for index, plane in metadata.planes.items()
        },
    )
    root = ET.fromstring(canonical.image_xml(metadata, image_name=image_name, state=state))
    ns = {"ome": canonical.OME_NS}
    image = root.find("ome:Image", ns)
    pixels = image.find("ome:Pixels", ns)
    # The description of a store says its pixels are elsewhere; here they are
    # the pages of this very file, in the order DimensionOrder gives.
    absent = pixels.find("ome:MetadataOnly", ns)
    count = int(np.prod(capture.volume.shape[:3]))
    tiff_data = ET.Element(canonical._tag("TiffData"), {"IFD": "0", "PlaneCount": str(count)})
    pixels.insert(list(pixels).index(absent), tiff_data)
    pixels.remove(absent)
    note_id = "Annotation:zmart-position"
    ET.SubElement(image, canonical._tag("AnnotationRef"), {"ID": note_id})
    annotations = root.find("ome:StructuredAnnotations", ns)
    if annotations is None:
        annotations = ET.SubElement(root, canonical._tag("StructuredAnnotations"))
    note = ET.SubElement(annotations, canonical._tag("MapAnnotation"), {"ID": note_id, "Namespace": ZMART_NOTE})
    value = ET.SubElement(note, canonical._tag("Value"))
    entries = (
        ("position_origin", "corner"),
        ("pyramid", "mean"),
        ("omero", json.dumps(omero_of(capture), sort_keys=True)),
    )
    for key, text in entries:
        ET.SubElement(value, canonical._tag("M"), {"K": key}).text = text
    text = ET.tostring(root, encoding="unicode")
    return b"<?xml version='1.0' encoding='utf-8'?>\n" + text.encode("ascii", "xmlcharrefreplace")
