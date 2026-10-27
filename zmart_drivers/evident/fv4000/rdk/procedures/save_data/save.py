"""Saving the captures that are waiting: the ``save_data`` procedure, and how this microscope saves (``IMAGE_SAVE``).

Taking a picture leaves it in the ``.oir`` file FLUOVIEW wrote, with a note
of how it was taken (a ``PendingCapture`` of ``procedures/acquire.py``).
This procedure reads each waiting picture back from FLUOVIEW (``collect.py``)
and writes it to disk as one OME-Zarr image store (``ome_zarr.py``), with
its max projection when it has several planes, and a log of what was asked
and done. FLUOVIEW's own file is left where FLUOVIEW put it, and named in
the capture record; a copy can be kept beside the store when ``IMAGE_SAVE``
says so and the file is reachable from this computer.

A capture that cannot be saved stays waiting, so a later call can try again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers import metadata_convention as convention

from ...driver_setup import frame as frame_record
from ...driver_setup.orientation import Orientation
from . import collect, naming, ome_zarr
from .ome_xml import VENDOR
from .product import SavedCapture

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageSaveProfile:
    """How a capture is saved: the orientation applied, which projection is made, whether FLUOVIEW's file is copied."""

    apply_orientation: bool = True
    #: ``"max"``: a max projection over z beside a stack, in ``projections``; ``"none"``: no projection.
    projection: str = "max"
    #: Copy FLUOVIEW's own ``.oir`` beside the store (``data/vendor``) when it can be reached from this computer.
    copy_vendor_file: bool = False


IMAGE_SAVE = ImageSaveProfile()
PROJECTIONS = ("max", "none")


def save_waiting(handle, label: str | None = None) -> list[dict]:
    """Save the captures taken and not yet saved; answer one record for each.

    With *label*, only the capture of that name is saved. Each record lists the
    files written, which plane is in which file and where on the sample it was
    taken. A capture that cannot be saved stays waiting and the error is raised.
    """
    handle.require_open()
    chosen = [c for c in handle.unsaved if label is None or c.label == label]
    if label is not None and not chosen:
        raise ValueError(f"no capture waiting to be saved is labelled {label!r}")
    records = []
    for pending in chosen:
        record, _saved = save_pending(
            handle.client, pending, orientation=handle.orientation or Orientation()
        )
        handle.unsaved.remove(pending)
        objective = (pending.at or {}).get("objective") or {}
        if record.get("pixel_size_um") and objective.get("slot") is not None:
            # The picture tells what one frame covers under this objective; remembered for the planning the controller does.
            frame_record.remember_frame(
                handle.frames,
                int(objective["slot"]),
                objective=objective.get("name"),
                image_size_px=record["image_size_px"],
                pixel_size_um=record["pixel_size_um"],
                fluoview_file=record.get("fluoview_file"),
            )
        records.append(record)
    return records


def save_pending(
    client, pending, *, orientation: Orientation, projection: str | None = None
) -> tuple[dict, SavedCapture]:
    """Save one waiting capture and answer ``(record, saved)``; raises when the picture cannot be read back."""
    p = pending
    projection = IMAGE_SAVE.projection if projection is None else projection
    if projection not in PROJECTIONS:
        raise ValueError(
            f"unknown projection {projection!r}; the projections are {PROJECTIONS}"
        )
    capture = collect.read_capture(client, p.acq.target)
    metadata = collect.metadata_of(
        capture,
        started_at=p.acq.started_at,
        objective=(p.at or {}).get("objective"),
        version=(p.state or {}).get("vendor", {}).get("software", {}).get("version"),
    )
    where = place_planes(p.at, capture.z_positions_um)
    metadata = replace(
        metadata,
        planes={
            index: replace(
                plane,
                position_x_um=where(index.z)["x_um"],
                position_y_um=where(index.z)["y_um"],
                position_z_um=where(index.z)["z_um"],
                actuators=tuple(where(index.z)["actuators"].items()),
            )
            for index, plane in metadata.planes.items()
        },
    )
    notes = store_notes(p, orientation)
    gathered = ome_zarr.gather(
        capture.volume,
        metadata,
        orientation=orientation if IMAGE_SAVE.apply_orientation else Orientation(),
        where=where,
    )
    store = naming.data_dir(p.output_root) / naming.store_name(p.naming)
    ome_zarr.write_store(gathered, store, notes=notes)
    projected = None
    if projection == "max" and metadata.size_z > 1:
        projected = naming.projections_dir(p.output_root) / naming.store_name(p.naming)
        record = dict(notes.capture_record or {})
        record["derived_from"] = {
            "store": Path(os.path.relpath(store, projected)).as_posix(),
            "operation": "max over z",
        }
        ome_zarr.write_store(
            ome_zarr.max_projection(gathered),
            projected,
            notes=replace(notes, capture_record=record),
        )
    vendor_copy = _copy_vendor_file(p.acq.target, p.output_root, p.naming)

    upward = sorted(range(capture.volume.shape[2]), key=lambda z: where(z)["z_um"])
    planes = [
        {
            "t": index.t,
            "z": upward.index(index.z),
            "c": index.c,
            "path": str(store),
            **{key: where(index.z)[key] for key in ("x_um", "y_um", "z_um")},
        }
        for index in sorted(metadata.planes)
    ]
    files = [
        str(store),
        *([str(projected)] if projected else []),
        *([str(vendor_copy)] if vendor_copy else []),
    ]
    log_path = naming.write_log(
        p.output_root,
        p.naming,
        {
            "position_label": p.label,
            "acquisition_type": p.acquisition_type,
            "stem": p.naming.stem,
            "asked": dict(p.asked or {}),
            "settings": p.settings,
            "capture": {
                "started": _when(p.acq.started_at),
                "finished": _when(p.acq.finished_at),
                "fluoview_file": p.acq.target,
                "answered": p.acq.command_result,
            },
            "unconfirmed": list(p.unconfirmed),
            "planes": [
                {**plane, "path": _from(p.output_root, plane["path"])}
                for plane in planes
            ],
            "files": [_from(p.output_root, path) for path in files],
        },
    )
    record = {
        "position_label": p.label,
        "acquisition_type": p.acquisition_type,
        "trigger": p.settings["trigger"],
        "format": "ome-zarr",
        "files": [*files, str(log_path)],
        "planes": planes,
        "fluoview_file": p.acq.target,
        "pixel_size_um": list(capture.pixel_size_um) if capture.pixel_size_um else None,
        "image_size_px": [int(capture.volume.shape[-1]), int(capture.volume.shape[-2])],
        "unconfirmed": list(p.unconfirmed),
        "log": [str(log_path)],
        "projections": [str(projected)] if projected else [],
    }
    saved = SavedCapture(
        store=store,
        planes=planes,
        projection=projected,
        vendor_copy=vendor_copy,
        read_plane=lambda index: ome_zarr.read_plane(
            store,
            gathered.store_position[
                collect.PlaneIndex(t=index[0], z=index[2], c=index[1])
            ],
        ),
    )
    return record, saved


def place_planes(at: dict, z_positions_um: list[float] | None):
    """Answer, per z slice number, where on the sample it was captured, in the frame the workflow drives in.

    *at* is where the stage stood (``x``, ``y``, ``z`` in the frame, and the
    drives' own readings). A stack's slices are placed by how far each lies
    from where the focus motor stood when the picture began, in FLUOVIEW's
    own coordinates; a single plane is at the focus itself.
    """
    motor = at.get("z_motor_um")

    def where(z: int) -> dict:
        if z_positions_um is not None and motor is not None and z < len(z_positions_um):
            offset = z_positions_um[z] - motor
            actuators = {"motor": z_positions_um[z], "piezo": at.get("z_piezo_um", 0.0)}
        else:
            offset = 0.0
            actuators = {
                "motor": motor if motor is not None else at["z"],
                "piezo": at.get("z_piezo_um", 0.0),
            }
        return {
            "x_um": at["x"],
            "y_um": at["y"],
            "z_um": at["z"] + offset,
            "actuators": actuators,
        }

    return where


def store_notes(pending, orientation: Orientation) -> convention.Notes:
    """What the store says about a capture besides its pixels, from the note taken at the capture."""
    noted = pending.state or {}
    record = {
        "position_label": pending.label,
        "acquisition_type": pending.acquisition_type,
        "acquisition_settings": {
            "asked": dict(pending.asked or {}),
            "used": pending.settings,
        },
        "started": _when(pending.acq.started_at),
        "finished": _when(pending.acq.finished_at),
        "xyz": noted.get("xyz"),
        "driver": noted.get("driver"),
        "controller": {"version": _controller_version()},
    }
    vendor = {
        **(noted.get("vendor") or {}),
        "file": pending.acq.target,
        "answered": pending.acq.command_result,
        "progress": pending.acq.progress,
        "unconfirmed": list(pending.unconfirmed),
    }
    return convention.Notes(
        controller_state=noted.get("controller_state"),
        capture_record=record,
        pixels_to_position=orientation.image_to_stage
        if IMAGE_SAVE.apply_orientation
        else Orientation().image_to_stage,
        vendor=VENDOR,
        vendor_facts=vendor,
        image_description=Path(pending.acq.target).name,
    )


def _copy_vendor_file(
    target: str, output_root: Path, named: naming.Naming
) -> Path | None:
    """FLUOVIEW's own file copied beside the store, when the profile asks for it and the file can be reached."""
    if not IMAGE_SAVE.copy_vendor_file:
        return None
    source = Path(target)
    if not source.is_file():
        log.info(
            "%s cannot be reached from this computer, so it is not copied beside the store",
            source,
        )
        return None
    destination = naming.vendor_dir(output_root) / f"{named.stem}{source.suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _controller_version() -> str | None:
    from importlib import metadata

    try:
        return metadata.version("zmart-controller")
    except metadata.PackageNotFoundError:
        return None


def _from(root: Path, path: str) -> str:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _when(seconds: float | None) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
