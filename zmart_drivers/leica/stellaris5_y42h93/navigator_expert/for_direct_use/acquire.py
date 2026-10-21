"""Taking a picture: the ``acquire`` step, and the notes it keeps for saving.

``acquire`` runs the selected LAS X job and leaves the picture in LAS X's
cache. It writes no files and clears nothing. Instead it notes what only the
moment of the capture can tell (where the stage stood, which job ran, the
state of the microscope) and keeps the note with the session, as a
:class:`~.pending_capture.PendingCapture`. Saving the files is the
``save_data`` procedure, and letting LAS X go of the picture is the
``clear_cache`` procedure.

A change that was sent but could not be confirmed is recorded and the capture
carries on, so the workflow is told rather than stopped.

The operator may move the stage or change the objective in LAS X between two
calls of the workflow, and the driver cannot see that as it happens. So
``acquire`` reads the stage once, just before the capture, and places every
plane from that reading. If it differs from what the workflow last saw, a note
is added to the result, and the capture goes ahead.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

try:  # driver version for embedded export state; never fail acquire over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001 -- best-effort provenance
    _DRIVER_VERSION = None

from ..procedures.save_data.naming import Naming, free_stem
from .pending_capture import PendingCapture
from ..vendor_specific.primitive_handling.readings import named_readings as _readers
from ..vendor_specific.primitive_handling.commands import run_job as _capture
from ..session import handle as _info
from ..procedures.absolute_coordinate_system.frame import get_xyz
from .state import get_state
from ..session.handle import Session, note_unconfirmed, require_open, try_

log = logging.getLogger(__name__)


def get_acquisition_settings(handle: Session) -> dict:
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


def _with_defaults(handle: Session, settings: dict | None) -> dict:
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


def _next_position_label(handle: Session) -> str:
    """The next per-session position label ("000000", "000001", ...).

    Consumes one counter value. Only used when :func:`acquire` gets no
    explicit ``position_label``.
    """
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _export_state(
    handle: Session,
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
    handle: Session,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
    acquisition_type: str,
    output_folder: str | None,
) -> dict:
    """Run the selected job and keep a note of the capture, for ``save_data``.

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
        output_folder: Where the capture will be saved, as text. When None,
            it is this session's staging folder under the microscope's data
            location. Nothing is written until ``save_data`` runs.

    The selected LAS X job is what runs; choose it with :func:`set_state`.
    The session hash (``handle.hash6``) rides along only in lineage and
    provenance. The machine/software state is noted now and embedded in each
    plane's OME-XML when the capture is saved.

    Returns ``{"position_label", "acquisition_type", "job"}``. Raises when the
    job cannot be read, the stage is unknown, the label is still waiting to be
    saved, or the capture itself fails.
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

    # Where the stage stands right now, read once before the capture, so every
    # plane is placed from what LAS X really did. The operator may have moved
    # the stage or changed the objective in LAS X since the workflow last
    # looked; when that happened, it is noted in the result, never hidden.
    noted_before = len(handle.unconfirmed)
    last_seen = handle.driven_to
    fresh = try_(lambda: get_xyz(handle))
    at = handle.driven_to if fresh is not None else last_seen
    if at is None:
        raise RuntimeError(
            "the driver does not know where the stage is and could not read it: "
            "check that LAS X answers, then capture again"
        )
    if last_seen is not None and fresh is not None and _moved(last_seen, at):
        note_unconfirmed(
            handle,
            "acquire: the stage or the objective changed",
            "the stage position or the objective is not what get_xyz or set_xyz "
            "last reported (changed in LAS X?); the capture is placed from the "
            "fresh reading",
        )
    acq = _capture.acquire(handle.client, job)

    label = (
        position_label if position_label is not None else _next_position_label(handle)
    )
    if any(waiting.label == label for waiting in handle.unsaved):
        raise ValueError(
            f"a capture labelled {label!r} is still waiting to be saved; run save_data first, "
            "or choose another label, so that no capture overwrites another"
        )
    naming = Naming(
        stem=free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem)
    )
    state = _export_state(handle, stem=naming.stem, job=job)

    # Everything the files will need that only the moment of the capture can tell.
    handle.unsaved.append(
        PendingCapture(
            acq=acq,
            label=label,
            acquisition_type=acquisition_type,
            naming=naming,
            state=state,
            job=job,
            at=at,
            output_root=output_root,
            session_hash6=handle.hash6,
            asked=dict(acquisition_settings or {}),
            settings=resolved,
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {"position_label": label, "acquisition_type": acquisition_type, "job": job}


#: How far the stage may differ from the last reported position before the
#: difference is worth a note. Below this it is just the stage settling.
STAGE_CHANGE_NOTE_UM = 1.0


def _moved(before: dict, after: dict) -> bool:
    """Whether the stage position (in the driver's coordinates) differs by more than a settling."""
    return any(
        abs(after[axis] - before[axis]) > STAGE_CHANGE_NOTE_UM
        for axis in ("x", "y", "z")
    )
