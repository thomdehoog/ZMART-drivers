"""Turn a staged capture (NIS's TIFF and the note taken at the capture) into one OME-Zarr store.

``acquire`` has NIS save each picture as a TIFF in the output folder's
``.staging`` folder, and writes beside it a note of everything only the
moment of the capture can tell: where the stage stood, the state of the
microscope, NIS's answer. :func:`convert` reads both and writes the picture
as one OME-Zarr store in ``data``, with the metadata the ZMART metadata
convention asks for, and the capture's log in ``log``. Only when the store
is written and read back are the TIFF and its note deleted. When anything
goes wrong, they stay, and :func:`save_staged` converts them later, also in
another session.

Where the picture lies: the field of view is centred where the stage stood
just before the capture, in the experiment's coordinates (the frame
``set_xyz`` takes). Every channel shares that place.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/save_data/save_waiting.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib import metadata as package_metadata
from pathlib import Path

import numpy as np

from zmart_drivers import metadata_convention as convention

from ...driver_setup.orientation import Orientation, reorient_array
from ...primitives.nis_python.reads import calibration_from
from . import naming, ome_zarr
from .ome_xml import VENDOR
from .product import (
    AcquisitionMetadata,
    ChannelMetadata,
    Instrument,
    PlaneIndex,
    PlaneMetadata,
)
from .tiff import read_staged

log = logging.getLogger(__name__)

AXES = ("x", "y", "z")


def note_path(tif: Path) -> Path:
    """The note taken at the capture, beside its TIFF."""
    return tif.with_suffix(".json")


def when(seconds: float | None) -> str | None:
    """A time from the clock as an ISO 8601 moment in UTC."""
    if not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def controller_version() -> str | None:
    """The installed ZMART Controller's version; None when it is not installed."""
    try:
        return package_metadata.version("zmart-controller")
    except package_metadata.PackageNotFoundError:
        return None


def notes_for(note: dict, description: str | None) -> convention.Notes:
    """What the store says besides its pixels: the convention's three annotations, and NIS's own words."""
    record = {
        "position_label": note["position_label"],
        "acquisition_type": note["acquisition_type"],
        "acquisition_settings": {"asked": note["asked"], "used": note["used"]},
        "started": when(note["snap"]["started"]),
        "finished": when(note["snap"]["finished"]),
        "xyz": note["at"],
        "driver": note["driver"],
        "controller": {"version": controller_version()},
    }
    facts = {
        "position": {
            "made as": "NIS's stage reading minus the origin; no objective calibration",
            "origin": note["origin"],
        },
        "stage before the capture (um)": note["at_raw"],
        "stage after the capture (um)": note.get("after_raw"),
        "capture": note["snap"],
        "objective": note["objective"],
        "optical configuration": note["optical_configuration"],
        "nis version": note["nis_version"],
        "microscope": note["microscope"],
        "session": note["session"],
        "unconfirmed": note["unconfirmed"],
    }
    return convention.Notes(
        controller_state=note["controller_state"],
        capture_record=record,
        pixels_to_position=tuple(
            tuple(row) for row in note["orientation"]["image_to_stage"]
        ),
        vendor=VENDOR,
        vendor_facts=facts,
        vendor_descriptions=(description,) if description else (),
        image_description=note["snap"].get("document"),
    )


def convert(tif: str | Path) -> dict:
    """Write the staged capture *tif* as its OME-Zarr store, then delete the TIFF and its note; the capture's record.

    Raises ``RuntimeError`` when the picture cannot be saved (no pixel size, a
    TIFF this driver cannot read); then the TIFF and its note stay, to be
    converted once the cause is fixed.
    """
    tif = Path(tif)
    note = json.loads(note_path(tif).read_text(encoding="utf-8"))
    output_root = Path(note["output_root"])
    stem = note["stem"]
    snap = note["snap"]

    calibration = calibration_from(
        **{k: snap["calibration"][k] for k in ("objective", "pixel", "aspect", "unit")}
    )
    if calibration["pixel_size_um"] is None:
        raise RuntimeError(
            f"the picture at {tif} has no pixel size ({calibration['why']}), so it cannot be placed on the sample. "
            "Calibrate the objective in NIS-Elements (Calibration), then run the save_data procedure to save it."
        )
    bits = int(snap["image"]["bits"])
    picture = read_staged(tif, bits=bits, name=note["optical_configuration"])

    orientation = Orientation(
        rotate_deg=note["orientation"]["rotate_deg"],
        mirrored=note["orientation"]["mirrored"],
    )
    channels = reorient_array(picture.channels, orientation)
    pixel_x = calibration["pixel_size_um"]
    pixel_y = pixel_x * calibration["aspect"]
    if orientation.swaps_axes:
        pixel_x, pixel_y = pixel_y, pixel_x
    count, ny, nx = channels.shape
    volume = channels[np.newaxis, :, np.newaxis]

    at = note["at"]
    centre = {axis: float(at[axis]["position"]) for axis in AXES}
    readings = tuple(
        (f"{axis}/motoric", float(at[axis]["actuator_readings"]["motoric"]))
        for axis in AXES
    )
    plane = PlaneMetadata(
        position_x_um=centre["x"],
        position_y_um=centre["y"],
        position_z_um=centre["z"],
        actuators=readings,
    )
    metadata = AcquisitionMetadata(
        size_x=nx,
        size_y=ny,
        size_c=count,
        pixel_type=str(volume.dtype),
        physical_size_x_um=pixel_x,
        physical_size_y_um=pixel_y,
        significant_bits=bits if picture.axes != "YXS" and bits else None,
        acquisition_date=datetime.fromtimestamp(snap["started"], tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        channels=tuple(
            ChannelMetadata(index=c, name=picture.names[c], color=picture.colours[c])
            for c in range(count)
        ),
        instrument=Instrument(
            model=f"NIS-Elements {note['nis_version'] or ''}".strip(),
            objective=note["objective"].get("name"),
        ),
        planes={PlaneIndex(t=0, z=0, c=c): plane for c in range(count)},
        annotations=tuple(
            (key, str(value))
            for key, value in (
                ("NIS document", snap.get("document")),
                ("optical configuration", note["optical_configuration"]),
                ("objective", note["objective"].get("name")),
                ("objective position", note["objective"].get("position")),
                ("TIFF layout", picture.axes),
                ("camera bits", bits),
            )
            if value not in (None, "")
        ),
    )
    capture = ome_zarr.Capture(
        volume=volume,
        metadata=metadata,
        translation=ome_zarr.translation(
            centre["x"],
            centre["y"],
            centre["z"],
            nx=nx,
            ny=ny,
            pixel_x_um=pixel_x,
            pixel_y_um=pixel_y,
        ),
    )
    store = ome_zarr.write_store(
        capture,
        naming.store_path(output_root, stem),
        notes=notes_for(note, picture.description),
    )

    planes = [
        {
            "path": str(store),
            "c": c,
            "z": 0,
            "t": 0,
            "x_um": centre["x"],
            "y_um": centre["y"],
            "z_um": centre["z"],
        }
        for c in range(count)
    ]
    log_path = naming.write_log(
        output_root,
        stem,
        {
            "position_label": note["position_label"],
            "acquisition_type": note["acquisition_type"],
            "asked": note["asked"],
            "used": note["used"],
            "capture": {
                "started": when(snap["started"]),
                "finished": when(snap["finished"]),
                "nis": snap,
            },
            "stage before (um)": note["at_raw"],
            "stage after (um)": note.get("after_raw"),
            "unconfirmed": note["unconfirmed"],
            "planes": [
                {**p, "path": Path(p["path"]).relative_to(output_root).as_posix()}
                for p in planes
            ],
        },
    )
    tif.unlink()
    note_path(tif).unlink()
    try:
        tif.parent.rmdir()  # only when nothing else waits there
    except OSError:
        pass
    return {
        "position_label": note["position_label"],
        "acquisition_type": note["acquisition_type"],
        "trigger": note["used"]["trigger"],
        "files": [str(store), str(log_path)],
        "planes": planes,
        "store": str(store),
        "log": str(log_path),
        "unconfirmed": note["unconfirmed"],
    }


def staged(output_root: str | Path) -> list[Path]:
    """The captures waiting in *output_root*'s staging folder: each TIFF that has its note."""
    folder = naming.staging_dir(output_root)
    if not folder.is_dir():
        return []
    return sorted(tif for tif in folder.glob("*.tif") if note_path(tif).is_file())


def save_staged(output_root: str | Path) -> dict:
    """Convert every capture waiting in *output_root*; ``{"saved": [records], "failed": {tif: reason}}``."""
    saved, failed = [], {}
    for tif in staged(output_root):
        try:
            saved.append(convert(tif))
        except Exception as why:  # noqa: BLE001 -- one that cannot be saved does not stop the others
            log.warning("%s could not be saved: %s", tif, why)
            failed[str(tif)] = str(why)
    return {"saved": saved, "failed": failed}
