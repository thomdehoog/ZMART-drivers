"""Saving the captures that are waiting: the ``save_data`` procedure.

Taking a picture leaves it in LAS X's cache with a note of how it was taken (a
:class:`~..acquisition.pending_capture.PendingCapture`). This procedure reads
each waiting picture from the cache and writes it to disk as an OME-Zarr image
store, with its max projection when it has several planes, and a log of what
was asked and done. It never clears LAS X's cache: that is the ``clear_cache``
procedure, run when the operator chooses. What it does record is which cache
picture each saved capture came from, so that ``clear_cache`` knows what it may
let go of.

A capture that cannot be saved stays waiting, so a later call can try again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...driver_setup import orientation as _orientation
from .save_settings import IMAGE_SAVE
from .. import readings as _readers
from ... import loaded_config as _session_state
from ...session import Session, require_open
from . import command_log as _log
from . import save as _save
from zmart_drivers import metadata_convention as convention

log = logging.getLogger(__name__)


def save_waiting(handle: Session, label: str | None = None) -> list[dict]:
    """Save the captures taken and not yet saved; answer one record for each.

    With *label*, only the capture of that name is saved. Each record lists the
    files written, which plane is in which file and where on the sample it was
    taken. A capture that cannot be saved stays waiting and the error is raised.
    """
    require_open(handle)
    chosen = [c for c in handle.unsaved if label is None or c.label == label]
    if label is not None and not chosen:
        raise ValueError(f"no capture waiting to be saved is labelled {label!r}")

    records = []
    for pending in chosen:
        record, saved = save_pending(
            handle.client,
            pending,
            orientation=_loaded_orientation(handle),
            where_the_planes_are=lambda plane_z, p=pending: _where_the_planes_are(
                handle, p.at, p.job, plane_z
            ),
        )
        handle.unsaved.remove(pending)

        # Remember which cache picture this was, for clear_cache.
        release_target = getattr(saved, "release_target", None)
        if release_target is not None:
            handle.in_cache.append(release_target)
        records.append(record)
    return records


def save_pending(
    client: Any, pending, *, orientation: Any, where_the_planes_are
) -> tuple[dict, Any]:
    """Save one waiting capture and answer ``(record, saved)``.

    *where_the_planes_are* answers, for the depths of a capture, where on the
    sample each plane was taken. Raises when the picture cannot be read from
    LAS X's cache or the copy does not read back the same.
    """
    p = pending
    saved = _save.save(
        client,
        p.acq,
        p.output_root,
        p.naming,
        lineage={
            "position_label": p.label,
            "acquisition_type": p.acquisition_type,
            "stem": p.naming.stem,
            "job": p.job,
            "session_hash6": p.session_hash6,
        },
        notes=store_notes(p),
        # Rig image->stage orientation, applied to the saved planes behind the
        # scenes so the workflow only ever sees stage-aligned images.
        orientation=orientation,
        projection=IMAGE_SAVE.projection,
        placement=where_the_planes_are,
    )

    written = sorted(saved.image_paths.items())
    depths = [
        int(getattr(index, "z", ordinal))
        for ordinal, (index, _path) in enumerate(written)
    ]
    taken_at = where_the_planes_are(depths)

    # Depth 0 is the lowest slice, as in a store, whichever way LAS X swept.
    upward = sorted(set(depths), key=lambda z: taken_at(z)["z_um"])
    planes = [
        {
            "t": int(getattr(index, "t", 0)),
            "z": upward.index(depths[ordinal]),
            "c": int(getattr(index, "c", ordinal)),
            "path": str(path),
            **taken_at(depths[ordinal]),
        }
        for ordinal, (index, path) in enumerate(written)
    ]
    vendor_metadata = [
        str(path) for path in getattr(saved, "vendor_metadata_paths", ())
    ]
    printed = [str(path) for path in getattr(saved, "state_paths", ())]
    projections = [str(path) for path in getattr(saved, "projection_paths", ())]
    files = list(
        dict.fromkeys(
            [
                *(plane["path"] for plane in planes),
                *projections,
                *vendor_metadata,
                *printed,
            ]
        )
    )

    log_path = _log.write(
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
                "answered": getattr(p.acq, "command_result", None),
            },
            "unconfirmed": list(p.unconfirmed),
            # Paths from where the run starts, so they stay true when the
            # capture is moved into a run folder of the same shape.
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
        "job": p.job,
        "format": "ome-zarr",
        # Every file this capture saved, under the name the ZMART Controller's
        # contract fixes: the images, then the vendor's metadata, then the state.
        "files": [*files, str(log_path)],
        # The manifest a workflow needs to tell channels from z and t, and
        # where on the sample each plane was taken.
        "planes": planes,
        "vendor_metadata": vendor_metadata,
        # What the driver printed about this capture, beside the images.
        "metadata": printed,
        # Changes made for this capture that were sent but never confirmed by
        # a readback. Empty when everything confirmed.
        "unconfirmed": list(p.unconfirmed),
        "log": [str(log_path)],
        # Projected from a capture with more than one plane, beside its data.
        "projections": projections,
    }
    return record, saved


def store_notes(pending) -> convention.Notes:
    """What the store says about a capture besides its pixels, from the note taken at the capture.

    The capture record is in the keys of the ZMART metadata convention
    (``zmart_drivers.metadata_convention``); what is this driver's own (how LAS X answered, the
    changes it could not confirm) goes with the rest of the driver's own facts.
    """
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
        "answered": getattr(pending.acq, "command_result", None),
        "unconfirmed": list(pending.unconfirmed),
    }
    return convention.Notes(
        controller_state=noted.get("controller_state"),
        capture_record=record,
        vendor_facts=vendor,
    )


def _controller_version() -> str | None:
    """The installed ZMART Controller's version; None when it is not installed."""
    from importlib import metadata

    try:
        return metadata.version("zmart-controller")
    except metadata.PackageNotFoundError:
        return None


def _loaded_orientation(handle: Session):
    """The camera-to-stage turn loaded for this connection (identity if none).

    Read from the per-connection session registry the driver populated at
    connect, so every saved image in a session uses the same orientation the
    connection loaded — rather than each save re-reading the file.
    """
    loaded = _session_state.get(handle.client)
    if loaded is not None:
        return loaded.orientation
    # No per-connection state for this handle (built without a ZmartControllerPlugin,
    # or its registry entry was uninstalled). Fall back to reading the file
    # fresh — loudly, because this re-read can differ from what the connection
    # loaded and can differ from what the connection loaded.
    log.warning(
        "no per-connection orientation is installed for this handle; reading "
        "orientation.json fresh for this save"
    )
    return _orientation.rig_orientation()


def _where_the_planes_are(handle: Session, at: dict, job: str, plane_z: list[int]):
    """Answer, per plane number in z, where on the sample it was captured.

    In the frame :func:`set_xyz` takes, so a position handed back can be
    handed straight back in. A saved file cannot say this and neither can the
    drive alone: each slice is the frame the drive was sent to, displaced by
    how far the slice sits from where the stack's own drive stood when the
    drive arrived -- z-wide for a z-wide stack, the galvo for a z-galvo one.
    The channels of a plane share its height, so the stack is matched against
    the planes in z, not against the files. Nothing is assumed about where in
    its stack the drive stands. A stack whose settings do not place its
    slices is refused: every plane says where it is, and a guessed height is
    worse than none. *at* is where the stage stood, as :func:`get_xyz` read it.
    """
    settings = _readers.get_job_settings(handle.client, job) or {}
    numbers = sorted(set(plane_z))
    slices = _readers.stack_slices_um(settings, len(numbers))
    standing = {"z-wide": at.get("z_wide_um"), "z-galvo": at.get("z_galvo_um")}.get(
        _readers.stack_drive(settings)
    )
    if slices is None or standing is None:
        if len(numbers) > 1:
            raise RuntimeError(
                f"the {len(numbers)} slices of job {job!r} cannot be placed: its stack settings "
                "do not say where they are or which drive made them"
            )
        slices, standing = [0.0], 0.0

    def where(z: int) -> dict:
        return {
            "x_um": at["x"],
            "y_um": at["y"],
            "z_um": at["z"] + slices[numbers.index(z)] - standing,
        }

    return where


def _from(root: Path, path: str) -> str:
    """*path* from *root*, with forward slashes; as it is when it lies elsewhere."""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _when(seconds: float | None) -> str | None:
    """A time from the clock as an ISO 8601 moment in UTC; None when there is none."""
    if not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
