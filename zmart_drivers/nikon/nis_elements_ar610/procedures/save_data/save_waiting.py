"""Saving the captures that are waiting: the ``save_data`` procedure.

Taking a picture leaves a TIFF in the staging folder with a note of how it
was taken (a :class:`~..acquire.PendingCapture`). This procedure reads each
waiting picture, turns it to the stage's axes with the measured orientation,
writes it as an OME-Zarr store with the note in it (``ome_zarr.py``), writes
a log of what was asked and done, and removes the TIFF. A capture that cannot
be saved stays waiting, so a later call can try again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers import metadata_convention as convention

from ...driver_setup.orientation import Orientation
from . import naming as _naming
from . import ome_zarr

log = logging.getLogger(__name__)

#: What the vendor is called in the store's annotations.
VENDOR = "nis-elements"


def save_waiting(handle, label: str | None = None) -> list[dict]:
    """Save the captures taken and not yet saved; answer one record for each.

    With *label*, only the capture of that name is saved. Each record lists
    the files written and the one plane with where on the sample it was
    taken, in the shape the controller's ``acquire`` answers. A capture that
    cannot be saved stays waiting and the error is raised.
    """
    handle.require_open()
    chosen = [c for c in handle.unsaved if label is None or c.label == label]
    if label is not None and not chosen:
        raise ValueError(f"no capture waiting to be saved is labelled {label!r}")
    records = []
    for pending in chosen:
        records.append(
            save_pending(pending, orientation=handle.orientation or Orientation())
        )
        handle.unsaved.remove(pending)
    return records


def save_pending(pending, *, orientation: Orientation) -> dict:
    """Save one waiting capture and answer its record."""
    import tifffile

    p = pending
    pixels = tifffile.imread(p.path)
    if pixels.ndim != 2:
        raise RuntimeError(
            f"the picture at {p.path} has shape {pixels.shape}; this driver saves monochrome pictures "
            "of one plane. Set the camera to monochrome in NIS-Elements."
        )
    pixels = orientation.apply(pixels)
    store = _naming.data_dir(p.output_root) / _naming.store_name(p.naming)
    store.parent.mkdir(parents=True, exist_ok=True)
    plane = ome_zarr.Plane(
        x_um=float(p.at["x"]),
        y_um=float(p.at["y"]),
        z_um=float(p.at["z"]),
        actuator_readings={
            f"{axis}/{motor}": reading
            for axis in ("x", "y", "z")
            for motor, reading in (p.xyz[axis].get("actuator_readings") or {}).items()
        },
        exposure_ms=p.exposure_ms,
    )
    camera = ((p.state.get("observed") or {}).get("camera")) if p.state else None
    capture = ome_zarr.Capture(
        name=p.naming.stem,
        pixels=pixels,
        pixel_size_um=p.pixel_size_um,
        plane=plane,
        channel=p.channel,
        objective=p.objective,
        camera=camera,
        nis_version=((p.state.get("observed") or {}).get("nis_elements"))
        if p.state
        else None,
        acquired_at=datetime.fromtimestamp(p.started_at, tz=UTC),
        position_label=p.label,
        description=f"NIS-Elements snap, optical configuration {p.channel or 'unknown'}",
    )
    ome_zarr.write_store(store, capture, store_notes(p, orientation))
    p.path.unlink(missing_ok=True)
    # The session's staging folder, and ``.staging`` itself, go once they are empty.
    _remove_if_empty(p.path.parent)
    _remove_if_empty(p.path.parent.parent)

    planes = [
        {
            "t": 0,
            "z": 0,
            "c": 0,
            "path": str(store),
            "x_um": plane.x_um,
            "y_um": plane.y_um,
            "z_um": plane.z_um,
        }
    ]
    log_path = _naming.write_log(
        p.output_root,
        p.naming,
        {
            "position_label": p.label,
            "acquisition_type": p.acquisition_type,
            "stem": p.naming.stem,
            "asked": dict(p.asked or {}),
            "settings": p.settings,
            "capture": {
                "started": _when(p.started_at),
                "finished": _when(p.finished_at),
                "answered": p.answered,
            },
            "unconfirmed": list(p.unconfirmed),
            "planes": [
                {**one, "path": _from(p.output_root, one["path"])} for one in planes
            ],
            "files": [_from(p.output_root, str(store))],
        },
    )
    return {
        "position_label": p.label,
        "acquisition_type": p.acquisition_type,
        "trigger": p.settings["trigger"],
        "format": "ome-zarr",
        "files": [str(store), str(log_path)],
        "planes": planes,
        "unconfirmed": list(p.unconfirmed),
        "log": [str(log_path)],
        "metadata": [],
        "projections": [],
    }


def store_notes(pending, orientation: Orientation) -> convention.Notes:
    """What the store says about a capture besides its pixels, from the note taken at the capture."""
    p = pending
    record = {
        "position_label": p.label,
        "acquisition_type": p.acquisition_type,
        "acquisition_settings": {"asked": dict(p.asked or {}), "used": p.settings},
        "started": _when(p.started_at),
        "finished": _when(p.finished_at),
        "xyz": p.xyz,
        "driver": p.driver,
        "controller": {"version": _controller_version()},
    }
    vendor = {
        "software": {
            "nis_elements": (p.state.get("observed") or {}).get("nis_elements")
            if p.state
            else None
        },
        "position": {
            "made as": "NIS-Elements' stage reading - origin - objective translation"
        },
        "channel": p.channel,
        "objective": p.objective,
        "pixel_size_um": p.pixel_size_um,
        "answered": p.answered,
        "unconfirmed": list(p.unconfirmed),
        "noted at": p.noted_at,
        "session": p.session_hash6,
    }
    return convention.Notes(
        controller_state=p.state or None,
        capture_record=record,
        pixels_to_position=orientation.picture_to_stage,
        vendor=VENDOR,
        vendor_facts=vendor,
    )


def _controller_version() -> str | None:
    from importlib import metadata

    try:
        return metadata.version("zmart-controller")
    except metadata.PackageNotFoundError:
        return None


def _remove_if_empty(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def _from(root: Path, path: str) -> str:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _when(seconds: float | None) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
