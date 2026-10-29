"""Saving the pictures that are waiting: the ``save_data`` procedure, and how this microscope saves (``IMAGE_SAVE``).

Taking a picture leaves it in LAS X's cache with a note of how it was taken (a
:class:`~..acquire.PendingCapture`). This procedure reads each waiting picture
from the cache and writes it to disk as OME-Zarr image stores, one per field,
with its max projection when it has several planes, and a log of what was
asked and done. It never clears LAS X's cache: that is the ``clear_cache``
procedure, run when the operator chooses. What it does record is which cache
picture each saved capture came from, so that ``clear_cache`` knows what it
may let go of.

A picture that cannot be saved stays waiting, so a later call can try again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zmart_drivers import metadata_convention as convention

from ...driver_setup import orientation as _orientation
from . import naming as _log
from . import save as _save

log = logging.getLogger(__name__)


def save_waiting(handle, label: str | None = None) -> list[dict]:
    """Save the pictures taken and not yet saved; answer one record for each.

    With *label*, only the picture of that name is saved. Each record lists
    the files written, which plane is in which file and where on the sample
    it was taken. A picture that cannot be saved stays waiting and the error
    is raised.
    """
    handle.require_open()
    chosen = [c for c in handle.unsaved if label is None or c.label == label]
    if label is not None and not chosen:
        raise ValueError(f"no capture waiting to be saved is labelled {label!r}")

    records = []
    for pending in chosen:
        record, saved = save_pending(pending, orientation=_loaded_orientation(handle))
        handle.unsaved.remove(pending)
        # Remember which cache picture this was, for clear_cache: once per picture.
        targets = {s.release_target for s in saved if s.release_target is not None}
        for target in targets:
            if target not in handle.in_cache:
                handle.in_cache.append(target)
        records.append(record)
    return records


def save_pending(pending, *, orientation: Any) -> tuple[dict, list]:
    """Save one waiting picture and answer ``(record, saved)``, *saved* one entry per field.

    Raises when the picture cannot be read from LAS X's cache or the copy
    does not read back the same.
    """
    p = pending
    saved = _save.save(
        p.acq,
        p.output_root,
        p.namings,
        place=place_fields(p),
        lineage={
            "position_label": p.label,
            "acquisition_type": p.acquisition_type,
            "stem": p.namings[0].stem,
            "picture": p.acq.picture,
            "session_hash6": p.session_hash6,
        },
        notes=store_notes(p),
        # The rig's image-to-stage orientation, applied to the saved planes
        # behind the scenes so the workflow only ever sees stage-aligned images.
        orientation=orientation,
        projection=IMAGE_SAVE.projection,
    )

    planes, files, projections, vendor_metadata, printed = [], [], [], [], []
    for number, stored in enumerate(saved):
        taken_at = place_fields(p)(number, None, [int(getattr(i, "z", 0)) for i in stored.image_paths])
        written = sorted(stored.image_paths.items())
        depths = [int(getattr(index, "z", ordinal)) for ordinal, (index, _path) in enumerate(written)]
        # Depth 0 is the lowest slice, as in a store, whichever way LAS X swept.
        upward = sorted(set(depths), key=lambda z: taken_at(z)["z_um"])
        planes += [
            {
                "field": number,
                "t": int(getattr(index, "t", 0)),
                "z": upward.index(depths[ordinal]),
                "c": int(getattr(index, "c", ordinal)),
                "path": str(path),
                **taken_at(depths[ordinal]),
            }
            for ordinal, (index, path) in enumerate(written)
        ]
        projections += [str(path) for path in stored.projection_paths]
        vendor_metadata += [str(path) for path in stored.vendor_metadata_paths]
        printed += [str(path) for path in stored.state_paths]
    files = list(dict.fromkeys([*(plane["path"] for plane in planes), *projections, *vendor_metadata, *printed]))

    log_path = _log.write(
        p.output_root,
        p.namings[0],
        {
            "position_label": p.label,
            "acquisition_type": p.acquisition_type,
            "picture": p.acq.picture,
            "stems": [n.stem for n in p.namings],
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
            "planes": [{**plane, "path": _from(p.output_root, plane["path"])} for plane in planes],
            "files": [_from(p.output_root, path) for path in files],
        },
    )
    record = {
        "position_label": p.label,
        "acquisition_type": p.acquisition_type,
        "trigger": p.settings["trigger"],
        "picture": p.acq.picture,
        "fields": len(saved),
        "format": _save.FORMAT,
        # Every file this capture saved, under the name the ZMART Controller's
        # contract fixes: the images, then the vendor's metadata, then the state.
        "files": [*files, str(log_path)],
        # The manifest a workflow needs to tell channels from z and t, and
        # where on the sample each plane was taken.
        "planes": planes,
        "vendor_metadata": list(dict.fromkeys(vendor_metadata)),
        "metadata": list(dict.fromkeys(printed)),
        "unconfirmed": list(p.unconfirmed),
        "log": [str(log_path)],
        "projections": projections,
    }
    return record, saved


def place_fields(pending):
    """A ``place`` for ``save``: where on the sample every plane of every field of *pending* was taken.

    The fields are the frame positions noted when the picture was taken
    (``PendingCapture.fields``): the frame ``move_to`` takes for a capture
    of the workflow, so a position handed back can be handed straight back
    in. Each slice of a z-stack is the frame displaced by how far the slice
    sits from where the stack's own drive stood, from the z-stack settings
    noted at the capture; a stack whose settings do not place its slices is
    refused, because a guessed height is worse than none.
    """
    stack = (pending.settings_at_capture or {}).get("z_stack")

    def place(number, stage_um, depths):
        at = pending.fields[number]
        numbers = sorted(set(depths))
        slices, standing = stack_slices(stack, len(numbers), at)
        if slices is None or standing is None:
            if len(numbers) > 1:
                raise RuntimeError(
                    f"the {len(numbers)} slices of field {number} cannot be placed: the z-stack "
                    "settings noted at the capture do not say where they are or which drive made them"
                )
            slices, standing = [0.0], 0.0

        def where(z: int) -> dict:
            return {"x_um": at["x"], "y_um": at["y"], "z_um": at["z"] + slices[numbers.index(z)] - standing}

        return where

    return place


def stack_slices(stack, expected: int, at: dict):
    """Where each slice of the z-stack sits (um of its drive), and where that drive stood; ``(None, None)`` when unsaid."""
    if not isinstance(stack, dict):
        return None, None
    try:
        begin, end, sections = float(stack["begin"]), float(stack["end"]), int(stack["sections"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if sections != expected or sections < 1:
        return None, None
    standing = {"z-wide": at.get("z_wide_um"), "z-galvo": at.get("z_galvo_um")}.get(stack.get("mode"))
    if sections == 1:
        return [begin], standing
    step = (end - begin) / (sections - 1)
    return [begin + step * index for index in range(sections)], standing


def store_notes(pending) -> convention.Notes:
    """What the store says about a picture besides its pixels, from the note taken at the capture."""
    noted = pending.state or {}
    record = {
        "position_label": pending.label,
        "acquisition_type": pending.acquisition_type,
        "acquisition_settings": {"asked": dict(pending.asked or {}), "used": pending.settings},
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
    return convention.Notes(controller_state=noted.get("controller_state"), capture_record=record, vendor_facts=vendor)


def _controller_version() -> str | None:
    """The installed ZMART Controller's version; None when it is not installed."""
    from importlib import metadata

    try:
        return metadata.version("zmart-controller")
    except metadata.PackageNotFoundError:
        return None


def _loaded_orientation(handle):
    """The camera-to-stage turn loaded for this connection (read afresh, loudly, when none was)."""
    if handle.orientation is not None:
        return handle.orientation
    log.warning(
        "no per-connection orientation is installed for this handle; reading orientation.json fresh for this save"
    )
    return _orientation.rig_orientation()


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
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


@dataclass(frozen=True)
class ImageSaveProfile:
    """How this microscope saves: with the active camera-to-stage orientation, and which projection.

    The pixels are collected from LAS X's own cache, read as the picture is
    taken; LAS X's AutoSave is not used, so it stays off.
    """

    apply_orientation: bool = True
    #: What a capture with more than one plane also gets, in
    #: ``<acquisition>/projections`` (``save.PROJECTIONS``): its ``"max"``
    #: projection over z, or ``"none"``.
    projection: str = "max"


IMAGE_SAVE = ImageSaveProfile()
