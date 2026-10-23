"""Taking a picture: the ``acquire`` step, and the note it keeps for saving (:class:`PendingCapture`).

``acquire`` runs the selected LAS X job and leaves the picture in LAS X's
cache. It writes no files and clears nothing. Instead it notes what only the
moment of the capture can tell (where the stage stood, which job ran, the
state of the microscope) and keeps the note with the session, as a
:class:`PendingCapture`. Saving the files is the
``save_data`` procedure, and letting LAS X go of the picture is the
``clear_cache`` procedure.

A change that was sent but could not be confirmed is recorded and the capture
carries on, so the workflow is told rather than stopped.

The operator may move the stage or change the objective in LAS X between two
calls of the workflow, and the driver cannot see that as it happens. So
``acquire`` reads the stage once, just before the capture, and places every
plane from that reading. If it differs from what the workflow last saw, a note
is added to the result, and the capture goes ahead.

The note kept for a capture whose files are not written yet.

The stage position and the microscope's state can only be known at the moment
of the capture, so they are written down then. ``save_data`` uses the note
later, whenever it is run, so the files describe the capture as it was and not
as things stand by then.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from .save_data.naming import Naming


try:  # driver version for embedded export state; never fail acquire over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001 -- best-effort provenance
    _DRIVER_VERSION = None

from .save_data.naming import free_stem
from . import readings as _readers
from . import changes
from .absolute_coordinate_system import get_xyz
from .state import get_state

#: This driver, as the capture record names it.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

log = logging.getLogger(__name__)


def try_(fn):
    """Call ``fn()``; degrade any failure to ``None`` (state must not fail save)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- provenance capture is best-effort
        log.debug("export-state field unavailable: %s", exc)
        return None


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``full``, the whole of the selected LAS X job.

    That is the only way this driver captures, so it is the only option and
    the default. Which job runs is part of the state (``set_state``). How the
    pictures are saved -- format, where the pixels are read from, the
    projection, and whether LAS X then lets go of the capture -- comes from
    this microscope's profile (``IMAGE_SAVE``), and where they go from its
    data location, set up once.
    """
    handle.require_open()
    return {"trigger": {"options": ["full"], "default": "full", "active": "full"}}


def _with_defaults(handle, settings: dict | None) -> dict:
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


def _next_position_label(handle) -> str:
    """The next per-session position label ("000000", "000001", ...).

    Consumes one counter value. Only used when :func:`acquire` gets no
    explicit ``position_label``.
    """
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _export_state(
    handle,
    *,
    stem: str,
    job: str,
) -> dict:
    """What only the moment of the capture can tell, for the store it will be saved as.

    In the controller's words, for the ZMART metadata convention
    (``zmart_drivers.metadata_convention``): ``controller_state`` is ``get_state()`` whole,
    ``xyz`` is ``get_xyz()`` as the contract defines it, and ``driver`` names
    this driver. ``vendor`` is this driver's and LAS X's own, for layer 4:
    how a position is made from the readings, the hardware report, the job's
    settings. Every field is read best-effort: a missing reader leaves
    ``None`` rather than failing the capture.
    """
    xyz = try_(lambda: get_xyz(handle)) or {}
    return {
        "controller_state": try_(lambda: get_state(handle)),
        "xyz": {axis: xyz[axis] for axis in ("x", "y", "z") if axis in xyz} or None,
        "driver": {"name": DRIVER_PACKAGE, "version": _DRIVER_VERSION},
        "vendor": {
            "position": {
                "made as": "reading - origin - objective translation; z is z-wide + z-galvo",
                "origin": dict(handle.origin or {}),
                "objective translation (um)": xyz.get("objective_translation_um"),
            },
            "software": {
                "client": handle.connection.get("client"),
                "api": "navigator-expert",
            },
            "hardware": try_(lambda: _readers.get_hardware_info(handle.client)),
            "job settings": try_(lambda: _readers.get_job_settings(handle.client, job)),
            "job": job,
            "stem": stem,
            "session": handle.hash6,
            "noted at": datetime.now(timezone.utc).isoformat(),
        },
    }


def acquire(
    handle,
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
    handle.require_open()
    resolved = _with_defaults(handle, acquisition_settings)
    if output_folder is not None:
        output_root = Path(output_folder)
    else:
        # Staging lives under the folder saved to, so moving a capture into
        # its run is a rename on the same disk, never a copy. Where that is
        # was set up once on this microscope; without it, this says how.
        output_root = Path(handle.output_root()) / ".staging" / handle.hash6
    # The job that runs is the one selected in LAS X: part of the state, chosen
    # with set_state or in LAS X itself, never by a capture setting.
    job = handle.selected_job_name()
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
        handle.note_unconfirmed("acquire: the stage or the objective changed",
            "the stage position or the objective is not what get_xyz or set_xyz "
            "last reported (changed in LAS X?); the capture is placed from the "
            "fresh reading",
        )
    acq = changes.take_picture(handle.client, job)

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


@dataclass
class PendingCapture:
    """One captured picture, waiting to be saved, with the notes taken as it was captured."""

    acq: Any
    label: str
    acquisition_type: str
    naming: Naming
    state: dict
    job: str
    at: dict
    output_root: Path
    session_hash6: str
    asked: dict
    settings: dict
    unconfirmed: list = field(default_factory=list)
