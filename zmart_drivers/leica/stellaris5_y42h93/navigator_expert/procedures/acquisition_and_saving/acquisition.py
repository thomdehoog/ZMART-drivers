"""Taking a picture and saving it: the ``acquire`` command and what it records.

``acquire`` runs the selected LAS X job and saves what LAS X captured, in one
step. Every file name starts with the acquisition type and the position label
the workflow gave, so the files of one place stay together. Along with the
pictures the driver saves a small log of what it did, and it embeds the
state of the machine in each saved plane.

A change that was sent but could not be confirmed is recorded and the capture
carries on, so the workflow is told rather than stopped.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

try:  # driver version for embedded export state; never fail acquire over it
    from ... import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001 -- best-effort provenance
    _DRIVER_VERSION = None

from ...driver_setup import image_stage_registration as _orientation
from .. import session_state as _session_state
from . import command_log as _log
from . import clear_cache as _release
from . import save as _save
from ...vendor_specific.naming import Naming, free_stem
from ...vendor_specific.primitive_handling import state as _readers
from ...vendor_specific.primitive_handling.profiles import IMAGE_SAVE
from . import acquire as _capture
from .. import session as _info
from ..absolute_coordinate_system.frame import get_xyz
from ..instrument import get_state
from ..session import ZmartHandle, require_open, try_

log = logging.getLogger(__name__)


def _loaded_orientation(handle: ZmartHandle):
    """The camera-to-stage turn loaded for this connection (identity if none).

    Read from the per-connection session registry the driver populated at
    connect, so every saved image in a session uses the same orientation the
    connection loaded — rather than each save re-reading the file.
    """
    loaded = _session_state.get(handle.client)
    if loaded is not None:
        return loaded.orientation
    # No per-connection state for this handle (built without a ZmartDriver,
    # or its registry entry was uninstalled). Fall back to reading the file
    # fresh — loudly, because this re-read can differ from what the connection
    # loaded and can differ from what the connection loaded.
    log.warning(
        "no per-connection orientation is installed for this handle; reading "
        "orientation.json fresh for this save"
    )
    return _orientation.rig_orientation()


def get_acquisition_settings(handle: ZmartHandle) -> dict:
    """How a capture is triggered: ``full``, the whole of the selected LAS X job.

    That is the only way this driver captures, so it is the only option and
    the default. Which job runs is part of the state (``set_state``). How the
    pictures are saved -- format, where the pixels are read from, the
    projection, and whether LAS X then lets go of the capture -- comes from
    this microscope's profile (``IMAGE_SAVE``), and where they go from its
    data location, set up once.
    """
    require_open(handle)
    return {"trigger": {"options": ["full"], "default": "full", "active": "full"}}


def _with_defaults(handle: ZmartHandle, settings: dict | None) -> dict:
    """Validate settings against the menu, filling omissions from the active values."""
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(f"unknown acquisition setting {name!r}")
        if value not in menu[name]["options"]:
            raise ValueError(
                f"invalid value {value!r} for acquisition setting {name!r} "
                f"(available: {menu[name]['options']!r})"
            )
        resolved[name] = value
    return resolved


def _next_position_label(handle: ZmartHandle) -> str:
    """The next per-session position label ("000000", "000001", ...).

    Consumes one counter value. Only used when :func:`acquire` gets no
    explicit ``position_label``.
    """
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _export_state(
    handle: ZmartHandle,
    *,
    stem: str,
    job: str,
) -> dict:
    """A JSON-serialisable snapshot of machine/software state at export time.

    Every field is captured best-effort: a missing reader degrades to
    ``None`` rather than failing the save. Embedded per-plane by ``save``.
    """
    machine_state = try_(lambda: get_state(handle))
    return {
        "software": {
            "driver_version": _DRIVER_VERSION,
            "client": handle.connection.get("client"),
            "api": "navigator-expert",
        },
        "hardware": try_(lambda: _readers.get_hardware_info(handle.client)),
        "job_settings": try_(lambda: _readers.get_job_settings(handle.client, job)),
        "job_state": (machine_state or {}).get("changeable") if machine_state else None,
        "position": try_(lambda: get_xyz(handle)),
        "provenance": {
            "stem": stem,
            "job": job,
            "session_hash6": handle.hash6,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def acquire(
    handle: ZmartHandle,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
    acquisition_type: str,
    output_folder: str | None,
) -> dict:
    """Run the job, wait for the export, and persist the OME-TIFF product.

    Args:
        position_label: What the workflow calls this place. Every file name
            is the acquisition type, then the label, then the time point,
            channel and plane (``Naming`` makes it safe for a file name).
            When omitted, the next per-session counter value ("000000",
            "000001", ...) is used (an explicit label does NOT consume a
            counter value). A name already used in the folder saved to gets
            ``_001``, ``_002``, ... so no capture is ever overwritten.
        acquisition_settings: Values from :func:`get_acquisition_settings`:
            the trigger, which is always ``full``. Unknown keys or values
            raise.
        acquisition_type: The kind of acquisition in the workflow's run,
            such as ``"overview"``: always a non-empty text, which the
            controller fills in (``"acquisition"`` when the workflow gave
            none). It names the files and is kept in the lineage, the log
            and the answer.
        output_folder: Where the capture is saved, as text. The driver
            writes every file itself, from LAS X's cache, so it saves
            exactly there. When None, it saves in this session's staging
            folder under the microscope's data location.

    The selected LAS X job is what runs; choose it with :func:`set_state`.
    The session hash (``handle.hash6``) rides along only in lineage and
    provenance. The machine/software state is captured and embedded in each
    saved plane's OME-XML (no sidecar).

    Returns a record with the resolved job and settings, ``files`` (every file
    saved) and ``planes`` (which file holds which channel, z and t, and where
    on the sample it was taken).
    Raises on any unrecoverable step — job selection, capture, export
    detection, or persistence.
    """
    require_open(handle)
    resolved = _with_defaults(handle, acquisition_settings)
    if output_folder is not None:
        output_root = Path(output_folder)
    else:
        # Staging lives under the folder saved to, so moving a capture into
        # its run is a rename on the same disk, never a copy. Where that is
        # was set up once on this microscope; without it, this says how.
        output_root = Path(_info.output_root(handle)) / ".staging" / handle.hash6
    # The job that runs is the one selected in LAS X: part of the state, chosen
    # with set_state or in LAS X itself, never by a capture setting.
    job = _info.selected_job_name(handle)
    if not job:
        raise RuntimeError(
            "which LAS X job is selected could not be read, or none is; "
            "choose one with set_state({'job': ...}) and capture again"
        )

    # Where the stage stands, as last read, taken before anything moves: every
    # plane is placed from it (the state printed below reads it again, after).
    at = handle.driven_to
    if at is None:
        raise RuntimeError(
            "the driver does not know where the stage is: read it with get_xyz or move it "
            "with set_xyz before acquiring"
        )
    noted_before = len(handle.unconfirmed)
    if IMAGE_SAVE.cleanup_cache:
        _release.prepare()

    acq = _capture.acquire(handle.client, job)

    label = (
        position_label if position_label is not None else _next_position_label(handle)
    )
    naming = Naming(
        stem=free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem)
    )
    state = _export_state(handle, stem=naming.stem, job=job)
    saved = _save.save(
        handle.client,
        acq,
        output_root,
        naming,
        lineage={
            "position_label": label,
            "acquisition_type": acquisition_type,
            "stem": naming.stem,
            "job": job,
            "session_hash6": handle.hash6,
        },
        state=state,
        # Rig image->stage orientation, applied to the saved planes behind the
        # scenes so the workflow only ever sees stage-aligned images. Loaded once
        # at connect (from the set_orientation setup notebook's file); a separate
        # concern from pixel-scale calibration and limits.
        orientation=_loaded_orientation(handle),
        cleanup_cache=IMAGE_SAVE.cleanup_cache,
        projection=IMAGE_SAVE.projection,
        # Where on the sample each plane was taken, the same answer the
        # acquisition gives below, so a store lies where its planes say.
        placement=lambda plane_z: _where_the_planes_are(handle, at, job, plane_z),
    )

    written = sorted(saved.image_paths.items())
    depths = [
        int(getattr(index, "z", ordinal))
        for ordinal, (index, _path) in enumerate(written)
    ]
    taken_at = _where_the_planes_are(handle, at, job, depths)
    # Depth 0 is the lowest slice, as in a store, whichever way LAS X swept.
    upward = sorted(set(depths), key=lambda z: taken_at(z)["z_um"])
    planes = [
        {
            "t": int(getattr(index, "t", 0)),
            "z": upward.index(depths[ordinal]),
            "c": int(getattr(index, "c", ordinal)),
            # Where the pixels are, and where inside them. A flat OME-TIFF
            # holds exactly the one plane t/c/z name; were this a store, the
            # same three would index into it and only the path would change.
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
    unconfirmed = list(handle.unconfirmed[noted_before:])
    log = _log.write(
        output_root,
        naming,
        {
            "position_label": label,
            "acquisition_type": acquisition_type,
            "stem": naming.stem,
            "asked": dict(acquisition_settings or {}),
            "settings": resolved,
            "capture": {
                "started": _when(acq.started_at),
                "finished": _when(acq.finished_at),
                "answered": getattr(acq, "command_result", None),
            },
            "unconfirmed": unconfirmed,
            # Paths from where the run starts, so they stay true when the
            # capture is moved into a run folder of the same shape.
            "planes": [
                {**plane, "path": _from(output_root, plane["path"])} for plane in planes
            ],
            "files": [_from(output_root, path) for path in files],
        },
    )
    return {
        "position_label": label,
        "acquisition_type": acquisition_type,
        "trigger": resolved["trigger"],
        "job": job,
        "format": "ome-zarr",
        # Every file this acquisition saved, under the name the ZMART
        # Controller's contract fixes, so a workflow finds them on any
        # microscope: the images, then the vendor's metadata, then the state.
        "files": [*files, str(log)],
        # The manifest a workflow needs to tell channels from z and t, and
        # where on the sample each plane was taken.
        "planes": planes,
        "vendor_metadata": vendor_metadata,
        # What the driver printed about this capture: the state, beside the
        # images in ``data/metadata``. A client moving a record moves this too.
        "metadata": printed,
        # The changes made for this capture that were sent but never
        # confirmed by a readback.
        # Empty when everything confirmed. The capture itself went ahead.
        "unconfirmed": unconfirmed,
        # The driver's log of this capture: what it was asked, and what it did.
        "log": [str(log)],
        # Projected from a capture with more than one plane, beside its data.
        "projections": projections,
    }


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


def _where_the_planes_are(handle: ZmartHandle, at: dict, job: str, plane_z: list[int]):
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
