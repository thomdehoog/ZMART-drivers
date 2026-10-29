"""Taking a picture: the ``acquire`` step, and the note it keeps for saving (:class:`PendingCapture`).

``acquire`` runs the loaded ZEN experiment once (or takes one snap with it)
and leaves the CZI file where ZEN wrote it, in ZEN's image folder. Nothing
is moved yet. Instead it notes what only the moment of the capture can tell
(where the stage stood, which experiment ran, the state of the microscope,
the experiment's z-stack and channels) and keeps the note with the session
as a :class:`PendingCapture`. Moving the file into the run's folder and
describing its planes is the ``save_data`` procedure.

A change that was sent but could not be confirmed is recorded and the
capture carries on, so the workflow is told rather than stopped. The
operator may move the stage in ZEN between two calls of the workflow, and
the driver cannot see that as it happens. So ``acquire`` reads the stage
once, just before the capture, and places every plane from that reading.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # the driver's version, for the record; never fail a capture over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001
    _DRIVER_VERSION = None

from . import changes
from . import readings as _readers
from .absolute_coordinate_system import get_xyz
from .save_data.naming import Naming, free_stem
from .state import get_state

#: This driver, as the capture record names it.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

#: How a capture can be triggered: the whole loaded experiment, or one snap with its channels.
TRIGGERS = ("full", "snap")

log = logging.getLogger(__name__)


def try_(fn):
    """Call ``fn()``; any failure becomes None, because a note must never fail a capture."""
    try:
        return fn()
    except Exception as why:  # noqa: BLE001 -- the note is best effort
        log.debug("a note for the capture could not be taken: %s", why)
        return None


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``full`` runs the whole loaded experiment, ``snap`` takes one picture with it.

    ``full`` is the default: a z-stack, a time series or tiles set up in the
    experiment are then acquired as ZEN would acquire them. ``snap`` takes
    one image with the experiment's active channels, whatever else it has.
    Which experiment runs is part of the state (``set_state``).
    """
    handle.require_open()
    return {"trigger": {"options": list(TRIGGERS), "default": "full", "active": "full"}}


def _with_defaults(handle, settings: dict | None) -> dict:
    """The settings checked against the menu, with what was left out filled in."""
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(
                f"unknown acquisition setting {name!r}; the settings are {sorted(menu)}"
            )
        if value not in menu[name]["options"]:
            raise ValueError(
                f"{value!r} is not a choice for {name!r}; the choices are {menu[name]['options']}"
            )
        resolved[name] = value
    return resolved


def _next_position_label(handle) -> str:
    """The next per-session label ("000000", "000001", ...), for a capture given none."""
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _export_state(handle, *, stem: str, experiment: dict) -> dict:
    """What only the moment of the capture can tell, for the record saved beside the file.

    ``controller_state`` is ``get_state()`` whole and ``xyz`` is ``get_xyz()``
    as the controller defines them; ``vendor`` is this driver's and ZEN's own:
    how a position is made from the readings, and what ZEN reported.
    """
    xyz = try_(lambda: get_xyz(handle)) or {}
    return {
        "controller_state": try_(lambda: get_state(handle)),
        "xyz": {axis: xyz[axis] for axis in ("x", "y", "z") if axis in xyz} or None,
        "driver": {"name": DRIVER_PACKAGE, "version": _DRIVER_VERSION},
        "vendor": {
            "position": {
                "made as": "reading - origin - objective translation; z is ZEN's focus drive",
                "origin": dict(handle.origin or {}),
                "objective translation (um)": xyz.get("objective_translation_um"),
            },
            "software": {
                "zen_api": (handle.client.runtime or {}).get("zen_api_version"),
                "api": "zen-api-grpc",
            },
            "experiment": dict(experiment),
            "objective": try_(lambda: _readers.get_objective(handle.client)),
            "stem": stem,
            "session": handle.hash6,
            "noted at": datetime.now(UTC).isoformat(timespec="seconds"),
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
    """Run the loaded experiment and keep a note of the capture, for ``save_data``.

    Args:
        position_label: What the workflow calls this place. The file is named
            after the acquisition type and the label (``overview_A1.czi``);
            a name already used gets ``_001``, ``_002``, ... so no capture
            is ever overwritten. When omitted, the next per-session counter
            value is used.
        acquisition_settings: Values from :func:`get_acquisition_settings`:
            the trigger, ``full`` or ``snap``. Unknown keys or values raise.
        acquisition_type: The kind of acquisition in the workflow's run,
            such as ``"overview"``; it names the file and is kept in the record.
        output_folder: Where the capture will be saved. When None, this
            session's folder under the microscope's data location.

    Returns ``{"position_label", "acquisition_type", "experiment"}``. Raises
    ``RuntimeError`` when no experiment is loaded, the stage cannot be read,
    ZEN's image folder is unknown, or the capture itself fails, and
    ``ValueError`` for a label still waiting to be saved.
    """
    handle.require_open()
    resolved = _with_defaults(handle, acquisition_settings)
    experiment = handle.experiment
    if not experiment:
        raise RuntimeError(
            "no experiment is loaded; choose one with set_state({'experiment': ...}) or "
            "connect with experiment=<name>, then capture again"
        )
    output_root = (
        Path(output_folder)
        if output_folder is not None
        else Path(handle.output_root()) / handle.hash6
    )
    zen_folder = handle.image_output_path or _readers.get_image_output_path(
        handle.client
    )
    if not zen_folder:
        raise RuntimeError(
            "ZEN did not say where it writes its images, so the capture could not be found afterwards"
        )
    handle.image_output_path = zen_folder

    # Where the stage stands right now, read once before the capture, so every
    # plane is placed from what ZEN really did.
    noted_before = len(handle.unconfirmed)
    last_seen = handle.driven_to
    fresh = try_(lambda: get_xyz(handle))
    at = handle.driven_to if fresh is not None else last_seen
    if at is None:
        raise RuntimeError(
            "the driver does not know where the stage is and could not read it from ZEN"
        )
    if last_seen is not None and fresh is not None and _moved(last_seen, at):
        handle.note_unconfirmed(
            "acquire: the stage or the objective changed",
            "the stage position or the objective is not what the driver last reported (changed in ZEN?); "
            "the capture is placed from the fresh reading",
        )
    objective_translation = (
        list(fresh.get("objective_translation_um", [0.0, 0.0, 0.0]))
        if fresh
        else [0.0, 0.0, 0.0]
    )

    label = (
        position_label if position_label is not None else _next_position_label(handle)
    )
    if any(waiting.label == label for waiting in handle.unsaved):
        raise ValueError(
            f"a capture labelled {label!r} is still waiting to be saved; run save_data first, or choose "
            "another label, so that no capture overwrites another"
        )
    stem = free_stem(
        f"{acquisition_type}_{label}", output_root, handle.zen_images or zen_folder
    )
    naming = Naming(stem=stem)
    snap = resolved["trigger"] == "snap"
    # What the experiment holds, read before the run: its z-stack and channels place the planes.
    zstack = (
        None
        if snap
        else try_(lambda: _readers.get_zstack(handle.client, experiment["id"]))
    )
    channels = _readers.active_channels(
        try_(lambda: _readers.get_tracks(handle.client, experiment["id"]))
    )

    acq = changes.take_picture(
        handle.client, experiment["id"], experiment["name"], stem, snap=snap
    )

    status = try_(
        lambda: _readers.get_experiment_status(handle.client, experiment["id"])
    )
    state = _export_state(handle, stem=stem, experiment=experiment)
    handle.unsaved.append(
        PendingCapture(
            acq=acq,
            label=label,
            acquisition_type=acquisition_type,
            naming=naming,
            state=state,
            at=at,
            output_root=output_root,
            zen_folder=zen_folder,
            zstack=zstack,
            channels=channels,
            status=status,
            origin=dict(handle.origin),
            objective_translation_um=objective_translation,
            session_hash6=handle.hash6,
            asked=dict(acquisition_settings or {}),
            settings=resolved,
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {
        "position_label": label,
        "acquisition_type": acquisition_type,
        "experiment": experiment["name"],
    }


#: How far the stage may differ from the last reported position before the
#: difference is worth a note. Below this it is just the stage settling.
STAGE_CHANGE_NOTE_UM = 1.0


def _moved(before: dict, after: dict) -> bool:
    """Whether the stage position, in the frame, differs by more than a settling."""
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
    at: dict
    output_root: Path
    zen_folder: str
    zstack: dict | None
    channels: list
    status: dict | None
    origin: dict
    objective_translation_um: list
    session_hash6: str
    asked: dict
    settings: dict
    unconfirmed: list = field(default_factory=list)
