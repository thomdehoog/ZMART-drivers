"""Read the planes of an exported capture, and write its vendor metadata beside the images."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from . import ome_canonical as _canonical
from ...primitives.capture.product import (
    PlaneSource,
    RawPlaneSource,
    VendorMetadataSource,
)


def save_vendor_metadata_atomic(source: VendorMetadataSource, dest: Path) -> None:
    """Persist raw vendor metadata as provenance, not output truth."""
    tmp = _with_tmp_suffix(dest)
    try:
        if source.data is not None:
            tmp.write_bytes(source.data)
        elif source.path is not None:
            shutil.copy2(str(source.path), str(tmp))
        else:
            raise RuntimeError(f"vendor metadata source has no data/path: {source}")
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    os.replace(str(tmp), str(dest))


def _read_source_plane(image_src: PlaneSource | RawPlaneSource):
    import tifffile

    if isinstance(image_src, RawPlaneSource):
        return _read_raw_plane(image_src)

    if image_src.page_index is None:
        return tifffile.imread(str(image_src.path))
    with tifffile.TiffFile(str(image_src.path)) as tif:
        if image_src.page_index < 0 or image_src.page_index >= len(tif.pages):
            raise RuntimeError(
                f"TIFF page index {image_src.page_index} out of range for {image_src.path}"
            )
        return tif.pages[image_src.page_index].asarray()


def _read_raw_plane(image_src: RawPlaneSource):
    """Read one plane of raw pixels, refusing a file that holds fewer than it should."""
    import numpy as np

    dtype = np.dtype(image_src.dtype).newbyteorder("<")
    count = image_src.shape_yx[0] * image_src.shape_yx[1]
    pixels = np.fromfile(
        str(image_src.path), dtype=dtype, count=count, offset=image_src.offset
    )
    if pixels.size != count:
        raise RuntimeError(
            f"{image_src.path} holds {pixels.size} of the {count} pixels expected "
            f"from byte {image_src.offset}"
        )
    return pixels.reshape(image_src.shape_yx).astype(dtype.newbyteorder("="))


def extract_embedded_ome_xml(tiff_src: Path) -> bytes:
    return _canonical.extract_embedded_ome_xml(tiff_src)


def _with_tmp_suffix(p: Path) -> Path:
    # PID + random suffix: a fixed '.tmp' would let two writers of the same
    # destination clobber each other's temp file before os.replace.
    return p.with_name(f"{p.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
