"""Collect a picture straight from LAS X's Data Container cache.

While LAS X takes a picture it writes the pixels, uncompressed, into its
Data Container cache (the folder named under ``Data Port`` in the registry,
``D:\\Temp2`` on the STELLARIS Y42H93), one file per picture:

- ``V2LMSDC_MMF<n>_0_<pid>.tmp`` holds the raw pixels: no header, x fastest,
  then y, each channel and each plane starting where the description says.
- ``V2LMSDC_OBJ<n>_<pid>.tmp`` describes them: a run of records, each
  ``p\\0\\0\\0`` + a 4-byte length + the payload. The second record is the
  LAS X XML (size, bit depth, axis lengths, where each channel starts, a time
  stamp per plane); the third starts with the picture's name.

``<pid>`` is a number in hex that changes each time LAS X starts. The pixels are byte for byte those LAS X's AutoSave writes into its
OME-TIFF (checked on 31 pictures: 8, 12 and 16 bit, two channels, a 17-plane
stack), so this collector gives ``save`` the same product without waiting
for AutoSave.

The cache belongs to LAS X and is only ever read: deleting or changing a file
in it corrupts LAS X projects. Once the picture is saved, LAS X itself can be
asked to let go of it, which is clean (see ``lasx_cache_release``).
"""

from __future__ import annotations

import functools
import re
import struct
import time
from pathlib import Path
from typing import Any

from ..actions.acquire import AcquisitionResult
from . import lasx_cache_release as cache_release
from . import lasx_description as described
from .files import (
    DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
    DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    MTIME_SKEW_ALLOWANCE_S,
)
from .product import (
    AcquisitionMetadata,
    ExportedAcquisition,
    ExportedPosition,
    PlaneIndex,
    RawPlaneSource,
    VendorMetadataSource,
)

__all__ = ["collect_lasx_data_container", "data_container_root"]

#: Where LAS X names its cache folder, under HKEY_LOCAL_MACHINE.
REGISTRY_KEY = r"SOFTWARE\Leica Microsystems\Data Container V2"
REGISTRY_VALUE = "Data Port"

DESCRIPTION = re.compile(r"V2LMSDC_OBJ(\d+)_([0-9a-fA-F]+)\.tmp")

#: The job a picture's description says made it.
JOB = re.compile(r'<LDM_Block_Sequential\b[^>]*\bBlockName="([^"]*)"')



def collect_lasx_data_container(
    client: Any,
    acq: AcquisitionResult,
    *,
    cache_root: str | Path | None = None,
    timeout_s: float = DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
) -> ExportedAcquisition:
    """Find the picture *acq* took in the cache, wait until it is finished, and describe it.

    The picture is the one LAS X made during *acq* and named after its job
    (``HiRes`` makes ``HiRes001``, ``HiRes002``...). It is finished when its
    description carries a time stamp for every plane: LAS X rewrites the
    description once, after the last plane is in. *timeout_s* bounds the
    wait for both, and passing it is reported in words the operator can act
    on. All metadata comes from the description (see ``lasx_description``).
    """
    root = Path(cache_root) if cache_root is not None else data_container_root()
    deadline = time.perf_counter() + timeout_s
    while True:
        found = pictures_of(root, acq)
        if len(found) > 1:
            names = ", ".join(f"{name} ({path.name})" for path, name, _ in found)
            raise RuntimeError(
                f"LAS X's cache holds more than one new picture of job {acq.job!r}: "
                f"{names}; refusing to guess which one this acquisition took"
            )
        if found:
            path, name, xml = found[0]
            stamped, planes = time_stamps(xml), planes_expected(xml)
            if stamped >= planes:
                break
            waiting = f"{name} has a time stamp for {stamped} of {planes} planes"
        else:
            waiting = f"no picture of job {acq.job!r} appeared in LAS X's cache {root}"
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
    # The description is the record of the capture itself, exact and from
    # the moment it was taken, so nothing is taken over from the job's
    # settings (which round the pixel size to two decimals).
    metadata, planes = layout(xml, pixels)
    return ExportedAcquisition(
        source_root=root,
        source_dir=root,
        positions=[ExportedPosition(t=0, planes=planes)],
        metadata=metadata,
        method="lasx_data_container",
        relative_path=pixels.name,
        source_exporter="lasx_data_container",
        release=functools.partial(cache_release.delete, int(number), name),
        vendor_metadata_sources=(
            VendorMetadataSource(name=f"{name}.lasx-description.xml", data=xml.encode("utf-8")),
        ),
    )


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


def pictures_of(root: Path, acq: AcquisitionResult) -> list[tuple[Path, str, str]]:
    """The pictures of *acq*'s job written since it started: (description, name, XML).

    A picture is named after its job with a number behind it; the copies LAS
    X makes for its own display (``_pmd_<level>``, ``_histo``) and the
    ``TileScan`` copy are named otherwise and are passed over. The name alone
    is not enough: job "HiRes2" makes "HiRes2001", which reads as "HiRes"
    with a number too. So the job the description says made the picture
    (``BlockName``) must be *acq*'s job as well.

    A picture LAS X has let go of (``output.lasx_cache_release``) keeps its
    description, rewritten after the release, but not its pixels, and the
    next capture may get its name again ("HiRes009" twice, simulator,
    2026-10-14). So a picture is one only while its pixels are there; one
    still being written is found once they are.

    LAS X also writes a picture's description again about 4 s after the
    capture, so with two captures of one job close together, the first one's
    description was written after the second began (simulator, 2026-10-15).
    Its pixels were not: the pixel file is written during the scan and left
    alone. So a picture is new when its pixels were written since the
    capture began; the description's time only narrows the search.
    """
    own_name = re.compile(re.escape(acq.job) + r"\d+")
    since = acq.started_at - MTIME_SKEW_ALLOWANCE_S
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
        made_by = JOB.search(xml)
        if made_by is not None and made_by.group(1) != acq.job:
            continue
        described = DESCRIPTION.fullmatch(path.name)
        if described is None:
            continue
        number, pid = described.groups()
        try:
            if (root / f"V2LMSDC_MMF{number}_0_{pid}.tmp").stat().st_mtime < since:
                continue
        except OSError:
            continue
        found.append((path, name, xml))
    return found


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
    """Channels times planes: LAS X stamps every plane of every channel once."""
    image = described.image_of(xml)
    channels = len(image.findall("./ImageDescription/Channels/ChannelDescription"))
    planes = 1
    for dimension in image.findall("./ImageDescription/Dimensions/DimensionDescription"):
        if int(dimension.get("DimID", "0")) not in (described.X_AXIS, described.Y_AXIS):
            planes *= int(dimension.get("NumberOfElements", "1"))
    return channels * planes


def layout(xml: str, pixels: Path) -> tuple[AcquisitionMetadata, dict[PlaneIndex, RawPlaneSource]]:
    """What the picture is, from its description, and where every plane is in the pixel file.

    Every size, step and start is the description's own; anything this
    reader does not know how to lay out is refused, never assumed.
    """
    metadata = described.metadata_from_description(xml)
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
    channels = image.findall("./ImageDescription/Channels/ChannelDescription")
    planes = {
        PlaneIndex(t=0, z=plane, c=c): RawPlaneSource(
            path=pixels,
            offset=int(channel.get("BytesInc")) + plane * z_step,
            shape_yx=(metadata.size_y, nx),
            dtype=metadata.pixel_type,
        )
        for c, channel in enumerate(channels)
        for plane in range(metadata.size_z)
    }
    return metadata, planes
