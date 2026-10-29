r"""Collect a picture straight from LAS X's Data Container cache: a capture, or a tile scan's fields.

While LAS X takes a picture it writes the pixels, uncompressed, into its
Data Container cache (the folder named under ``Data Port`` in the registry,
``D:\Temp2`` on the STELLARIS Y42H93), one file per picture:

- ``V2LMSDC_MMF<n>_0_<pid>.tmp`` holds the raw pixels: no header, x fastest,
  then y, each channel and each plane starting where the description says;
- ``V2LMSDC_OBJ<n>_<pid>.tmp`` describes them: a run of records, each
  ``p\0\0\0`` + a 4-byte length + the payload. The second record is the
  LAS X XML (size, bit depth, axis lengths, where each channel starts, a time
  stamp per plane); the third starts with the picture's name.

``<pid>`` is a number in hex that changes each time LAS X starts.

The plain Navigator names its pictures by what made them: the window's
Capture Image makes ``Image001``, ``Image002``...; its Preview over a list
of positions makes one ``Mosaic<n>`` holding every field one after the
other (LAS X dimension 10), each field's position in the picture's
``TileScanInfo`` attachment. :func:`collect_picture` finds the picture a
capture made (by its name and by when the scan began), waits until LAS X
has finished writing it, and describes it one field at a time, so a tile
scan is saved as one store per field, each placed where its tile was taken
(``metadata.py``). Checked on a three-field Preview from the simulator:
every field identical to the raw bytes (2026-10-15).

The cache belongs to LAS X and is only ever read: deleting or changing a file
in it corrupts LAS X projects. Once the picture is saved, LAS X itself can be
asked to let go of it, which is clean (``procedures/clear_cache/clear.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import re
import struct
import time
from pathlib import Path

from . import metadata as described
from .product import (
    AcquisitionMetadata,
    AcquisitionResult,
    ExportedAcquisition,
    ExportedPosition,
    PlaneIndex,
    RawPlaneSource,
    VendorMetadataSource,
)

__all__ = ["collect_picture", "data_container_root"]

log = logging.getLogger(__name__)

#: Where LAS X names its cache folder, under HKEY_LOCAL_MACHINE.
REGISTRY_KEY = r"SOFTWARE\Leica Microsystems\Data Container V2"
REGISTRY_VALUE = "Data Port"

DESCRIPTION = re.compile(r"V2LMSDC_OBJ(\d+)_([0-9a-fA-F]+)\.tmp")

#: What LAS X names each kind of picture: the Capture Image's, and the Preview's tile scan.
PICTURE_NAMES = {
    "capture": re.compile(r"Image\d+"),
    "tile_scan": re.compile(r"Mosaic\d+"),
}

#: How long to wait for a picture to appear and be finished after the scan
#: ran, before saying LAS X did not write it. A large picture is written
#: within seconds; the wait is bounded so a stuck LAS X is reported.
DEFAULT_EXPORT_COMPLETION_TIMEOUT_S = 60.0
DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S = 0.5

# The cache lives on a local disk, but file times are coarse and the clock
# can drift from the moment the driver noted the scan's start. Without an
# allowance, a genuinely fresh picture can be rejected as old.
MTIME_SKEW_ALLOWANCE_S = 2.0


def collect_picture(
    acq: AcquisitionResult,
    *,
    cache_root: str | Path | None = None,
    timeout_s: float = DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
) -> list[ExportedAcquisition]:
    """The picture *acq* took, one capture per field, read from the cache once LAS X has finished it.

    A capture is a picture of one field; a tile scan one picture whose
    fields follow one another, each placed where its tile was taken. It is
    finished when its description carries a time stamp for every plane of
    every field: LAS X rewrites the description once, after the last plane
    is in. *timeout_s* bounds the wait, and passing it is reported in words
    the operator can act on. Every field names the one picture it came from
    (``release_target``), so ``clear_cache`` lets go of it once, after every
    field is saved.
    """
    named = PICTURE_NAMES.get(acq.picture)
    if named is None:
        raise ValueError(f"no picture of kind {acq.picture!r}; the kinds are {sorted(PICTURE_NAMES)}")
    root = Path(cache_root) if cache_root is not None else data_container_root()
    deadline = time.perf_counter() + timeout_s
    while True:
        found = pictures(root, acq.started_at, named)
        if len(found) > 1:
            names = ", ".join(f"{name} ({path.name})" for path, name, _ in found)
            raise RuntimeError(
                f"LAS X's cache holds more than one new picture named {named.pattern}: "
                f"{names}; refusing to guess which one this acquisition took"
            )
        if found:
            path, name, xml = found[0]
            stamped, planes = time_stamps(xml), planes_expected(xml)
            if stamped >= planes:
                break
            waiting = f"{name} has a time stamp for {stamped} of {planes} planes"
        else:
            waiting = f"no new picture named {named.pattern.replace(chr(92) + 'd+', '')}<n> appeared in LAS X's cache {root}"
        if time.perf_counter() >= deadline:
            raise RuntimeError(
                f"{waiting} within {timeout_s:.0f} s after the scan finished; "
                "LAS X did not finish writing the picture"
            )
        time.sleep(poll_interval_s)

    number, pid = DESCRIPTION.fullmatch(path.name).groups()
    pixels = root / f"V2LMSDC_MMF{number}_0_{pid}.tmp"
    if not pixels.is_file():
        raise RuntimeError(f"LAS X's cache has the description of {name} but not its pixels {pixels}")
    _collected.add(_picture_key(root, path))
    description = (VendorMetadataSource(name=f"{name}.lasx-description.xml", data=xml.encode("utf-8")),)
    return [
        ExportedAcquisition(
            source_root=root,
            source_dir=root,
            positions=[ExportedPosition(t=0, planes=planes)],
            metadata=metadata,
            method="lasx_data_container",
            relative_path=pixels.name,
            source_exporter="lasx_data_container",
            release_target=(int(number), name),
            vendor_metadata_sources=description,
        )
        for metadata, planes in (layout(xml, pixels, field=field) for field in range(fields(xml)))
    ]


def data_container_root() -> Path:
    """The folder LAS X keeps its Data Container cache in, as the registry names it."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE)
    except OSError as why:
        raise RuntimeError(
            f"LAS X's cache folder is not named in the registry "
            f"(HKEY_LOCAL_MACHINE\\{REGISTRY_KEY}, {REGISTRY_VALUE!r}): {why}"
        ) from why
    return Path(value)


def pictures(root: Path, started_at: float, own_name: re.Pattern) -> list[tuple[Path, str, str]]:
    """The pictures named *own_name* whose pixels were written since *started_at*: (description, name, XML).

    The copies LAS X makes for its own display (``_pmd_<level>``,
    ``_histo``) are named otherwise and are passed over. A picture LAS X has
    let go of keeps its description, rewritten after the release, but not
    its pixels, and the next capture may get its name again; so a picture is
    one only while its pixels are there. LAS X also writes a description
    again about 4 s after the capture, so a picture is new when its pixels
    were written since the capture began; the description's time only
    narrows the search. And a picture this driver collected already is never
    new again.
    """
    since = started_at - MTIME_SKEW_ALLOWANCE_S
    found = []
    for path in root.glob("V2LMSDC_OBJ*_*.tmp"):
        try:
            if path.stat().st_mtime < since:
                continue
            records = read_records(path.read_bytes())
        except OSError:
            continue
        if len(records) < 3:
            continue
        name = first_text(records[2])
        if name is None or not own_name.fullmatch(name):
            continue
        xml = first_text(records[1])
        if xml is None:
            continue
        detailed = DESCRIPTION.fullmatch(path.name)
        if detailed is None:
            continue
        number, pid = detailed.groups()
        try:
            if (root / f"V2LMSDC_MMF{number}_0_{pid}.tmp").stat().st_mtime < since:
                continue
            if _picture_key(root, path) in _collected:
                continue
        except OSError:
            continue
        found.append((path, name, xml))
    return found


#: The pictures collected so far, by :func:`_picture_key`.
_collected: set[tuple[str, str, int]] = set()


def _picture_key(root: Path, description: Path) -> tuple[str, str, int]:
    """A picture, told apart from one LAS X writes later into the same files: its pixels' write time."""
    number, pid = DESCRIPTION.fullmatch(description.name).groups()
    pixels = root / f"V2LMSDC_MMF{number}_0_{pid}.tmp"
    return str(root), description.name, pixels.stat().st_mtime_ns


def read_records(data: bytes) -> list[bytes]:
    """The payloads of a description file's records, in order."""
    records, at = [], 0
    while at + 8 <= len(data) and data[at : at + 4] == b"p\0\0\0":
        (length,) = struct.unpack_from("<I", data, at + 4)
        records.append(data[at + 8 : at + 8 + length])
        at += 8 + length
    return records


def first_text(payload: bytes) -> str | None:
    """The text field a record starts with: ``*``, a 4-byte count of characters, UTF-16."""
    if len(payload) < 5 or payload[:1] != b"*":
        return None
    (count,) = struct.unpack_from("<i", payload, 1)
    if count < 0 or 5 + 2 * count > len(payload):
        return None
    return payload[5 : 5 + 2 * count].decode("utf-16-le", errors="replace")


def time_stamps(xml: str) -> int:
    match = re.search(r'<TimeStampList[^>]*NumberOfTimeStamps="(\d+)"', xml)
    return int(match.group(1)) if match else 0


def planes_expected(xml: str) -> int:
    """Channels times planes times fields: LAS X stamps every plane of every channel once."""
    image = described.image_of(xml)
    channels = len(image.findall("./ImageDescription/Channels/ChannelDescription"))
    planes = 1
    for dimension in image.findall("./ImageDescription/Dimensions/DimensionDescription"):
        if int(dimension.get("DimID", "0")) not in (described.X_AXIS, described.Y_AXIS):
            planes *= int(dimension.get("NumberOfElements", "1"))
    return channels * planes


def fields(xml: str) -> int:
    """How many fields a picture holds: a tile scan's, or one."""
    return described.fields(described.image_of(xml))


def layout(
    xml: str, pixels: Path, field: int = 0
) -> tuple[AcquisitionMetadata, dict[PlaneIndex, RawPlaneSource]]:
    """What the picture (or one field of a tile scan) is, from its description, and where every plane is in the pixel file.

    Every size, step and start is the description's own; anything this
    reader does not know how to lay out is refused, never assumed. A tile
    scan keeps its fields one after the other, each its own capture.
    """
    metadata = described.metadata_from_description(xml, field=field)
    image = described.image_of(xml)
    dims = described.axes(image)
    pixel_bytes = 1 if metadata.pixel_type == "uint8" else 2
    nx, x_step, y_step = metadata.size_x, dims[described.X_AXIS]["step"], dims[described.Y_AXIS]["step"]
    if x_step != pixel_bytes or y_step != nx * pixel_bytes:
        raise RuntimeError(
            f"the picture's rows are not packed {pixel_bytes}-byte pixels "
            f"(x step {x_step}, y step {y_step} bytes for {nx} pixels a row)"
        )
    z_step = dims[described.Z_AXIS]["step"] if described.Z_AXIS in dims else 0
    field_start = field * dims[described.TILE_AXIS]["step"] if described.TILE_AXIS in dims else 0
    channels = image.findall("./ImageDescription/Channels/ChannelDescription")
    planes = {
        PlaneIndex(t=0, z=plane, c=c): RawPlaneSource(
            path=pixels,
            offset=field_start + int(channel.get("BytesInc")) + plane * z_step,
            shape_yx=(metadata.size_y, nx),
            dtype=metadata.pixel_type,
        )
        for c, channel in enumerate(channels)
        for plane in range(metadata.size_z)
    }
    return metadata, planes


def _relative_posix(path: Path, base: Path, *, fallback_to_str: bool) -> str | None:
    """*path* relative to *base* with forward slashes; the whole path (or None) when it lies elsewhere."""
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/") if fallback_to_str else None
