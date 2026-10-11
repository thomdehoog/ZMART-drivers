"""Collect a picture straight from LAS X's Data Container cache.

While LAS X takes a picture it writes the pixels, uncompressed, into its
Data Container cache (the folder named under ``Data Port`` in the registry,
``D:\\Temp`` on our machines), one file per picture:

- ``V2LMSDC_MMF<n>_0_<pid>.tmp`` holds the raw pixels: no header, x fastest,
  then y, each channel and each plane starting where the description says.
- ``V2LMSDC_OBJ<n>_<pid>.tmp`` describes them: a run of records, each
  ``p\\0\\0\\0`` + a 4-byte length + the payload. The second record is the
  LAS X XML (size, bit depth, axis lengths, where each channel starts, a time
  stamp per plane); the third starts with the picture's name.

``<pid>`` is the LAS X data server's process number in hex, new each time LAS
X starts. The pixels are byte for byte those LAS X's AutoSave writes into its
OME-TIFF (checked on 31 pictures: 8, 12 and 16 bit, two channels, a 17-plane
stack), so this collector gives ``save`` the same product without waiting
for AutoSave.

The cache belongs to LAS X and is only ever read: deleting or changing a file
in it corrupts LAS X projects (only LAS X's own delete is clean).
"""

from __future__ import annotations

import re
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..actions.acquire import AcquisitionResult
from . import ome_canonical as canonical
from .files import (
    DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
    DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    MTIME_SKEW_ALLOWANCE_S,
)
from .product import (
    AcquisitionMetadata,
    ChannelMetadata,
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

#: LAS X's dimension numbers for the axes a picture may have here. A time
#: series (4) or a tile scan (10) is taken one picture at a time by the
#: workflow, so any other dimension is refused rather than guessed at.
X_AXIS, Y_AXIS, Z_AXIS = 1, 2, 3


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
    on. Physical sizes come from the description, then from the job's live
    settings where those give them, as for AutoSave's files.
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
    metadata, planes = layout(xml, pixels)
    metadata = canonical.metadata_with_job_physical_sizes(metadata, client, acq.job)
    return ExportedAcquisition(
        source_root=root,
        source_dir=root,
        positions=[ExportedPosition(t=0, planes=planes)],
        metadata=metadata,
        method="lasx_data_container",
        relative_path=pixels.name,
        source_exporter="lasx_data_container",
        cleanup_source_supported=False,
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
    ``TileScan`` copy are named otherwise and are passed over.
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
        if xml is not None:
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
    description = image_description(xml)
    channels = len(description.findall("./Channels/ChannelDescription"))
    planes = 1
    for dimension in description.findall("./Dimensions/DimensionDescription"):
        if int(dimension.get("DimID", "0")) not in (X_AXIS, Y_AXIS):
            planes *= int(dimension.get("NumberOfElements", "1"))
    return channels * planes


def image_description(xml: str) -> ET.Element:
    description = ET.fromstring(xml).find("./Image/ImageDescription")
    if description is None:
        raise RuntimeError("LAS X's description of the picture has no ImageDescription")
    return description


def layout(xml: str, pixels: Path) -> tuple[AcquisitionMetadata, dict[PlaneIndex, RawPlaneSource]]:
    """Where every plane is in the pixel file, and what the picture is, from the description.

    Every size, step and start is the description's own; anything this
    reader does not know how to lay out is refused, never assumed.
    """
    description = image_description(xml)
    dimensions = {}
    for dimension in description.findall("./Dimensions/DimensionDescription"):
        dim_id = int(dimension.get("DimID", "0"))
        if dim_id not in (X_AXIS, Y_AXIS, Z_AXIS):
            raise RuntimeError(
                f"the picture has LAS X dimension {dim_id}; only x, y and z (1, 2, 3) are "
                "read from the cache, a time series or tile scan is taken picture by picture"
            )
        dimensions[dim_id] = {
            "count": int(dimension.get("NumberOfElements")),
            "length_m": float(dimension.get("Length")),
            "step": int(dimension.get("BytesInc")),
            "unit": dimension.get("Unit"),
        }
    if X_AXIS not in dimensions or Y_AXIS not in dimensions:
        raise RuntimeError("LAS X's description of the picture gives no x or no y axis")
    z = dimensions.get(Z_AXIS, {"count": 1, "length_m": 0.0, "step": 0, "unit": "m"})
    for axis in dimensions.values():
        if axis["unit"] != "m":
            raise RuntimeError(f"LAS X gives an axis in {axis['unit']!r}, not metres")

    channels = description.findall("./Channels/ChannelDescription")
    if not channels:
        raise RuntimeError("LAS X's description of the picture lists no channels")
    bits = {int(channel.get("Resolution")) for channel in channels}
    if len(bits) != 1:
        raise RuntimeError(f"the picture's channels have different bit depths: {sorted(bits)}")
    (significant_bits,) = bits
    if significant_bits > 16:
        raise RuntimeError(f"a {significant_bits}-bit picture is not read from the cache (8 to 16 bit are)")
    dtype = "uint8" if significant_bits <= 8 else "uint16"
    pixel_bytes = 1 if dtype == "uint8" else 2

    nx, ny = dimensions[X_AXIS]["count"], dimensions[Y_AXIS]["count"]
    if dimensions[X_AXIS]["step"] != pixel_bytes or dimensions[Y_AXIS]["step"] != nx * pixel_bytes:
        raise RuntimeError(
            f"the picture's rows are not packed {pixel_bytes}-byte pixels "
            f"(x step {dimensions[X_AXIS]['step']}, y step {dimensions[Y_AXIS]['step']} bytes "
            f"for {nx} pixels a row)"
        )

    planes = {
        PlaneIndex(t=0, z=plane, c=c): RawPlaneSource(
            path=pixels,
            offset=int(channel.get("BytesInc")) + plane * z["step"],
            shape_yx=(ny, nx),
            dtype=dtype,
        )
        for c, channel in enumerate(channels)
        for plane in range(z["count"])
    }
    metadata = AcquisitionMetadata(
        size_x=nx,
        size_y=ny,
        size_t=1,
        size_z=z["count"],
        size_c=len(channels),
        pixel_type=dtype,
        # Length over pixels, as LAS X's own OME-TIFF gives the pixel size.
        physical_size_x_um=dimensions[X_AXIS]["length_m"] / nx * 1e6,
        physical_size_y_um=dimensions[Y_AXIS]["length_m"] / ny * 1e6,
        # The z length spans the steps between the planes: 17 planes over
        # 38.55 um are 2.41 um apart (LAS X's own plane heights say so).
        physical_size_z_um=(z["length_m"] / (z["count"] - 1) * 1e6 if z["count"] > 1 else None),
        channels=tuple(ChannelMetadata(index=c) for c in range(len(channels))),
        significant_bits=significant_bits,
    )
    return metadata, planes
