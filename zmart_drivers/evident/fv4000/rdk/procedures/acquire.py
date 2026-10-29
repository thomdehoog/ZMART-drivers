"""Taking a picture: the ``acquire`` step, and the note it keeps for saving (:class:`PendingCapture`).

``acquire`` starts the imaging set up in FLUOVIEW and waits for it to finish.
FLUOVIEW writes the picture to a file of its own; the driver writes nothing
yet. Instead it notes what only the moment of the capture can tell (where the
stage stood, the state of the microscope, which file FLUOVIEW named) and
keeps the note with the session. Saving the files is the ``save_data``
procedure, whenever it is run, so the files describe the capture as it was.

The operator may move the stage or change the objective in FLUOVIEW between
two calls of the workflow, and the driver cannot see that as it happens. So
``acquire`` reads the stage once, just before the capture, and places every
plane from that reading. If it differs from what the workflow last saw, a
note is added, and the capture goes ahead.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import changes
from .absolute_coordinate_system import get_xyz
from .save_data.naming import Naming, free_stem
from .state import get_state

try:  # the driver's version for the capture record; never fail a capture over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001
    _DRIVER_VERSION = None

#: This driver, as the capture record names it.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

log = logging.getLogger(__name__)

#: How far the stage may differ from the last reported position before the
#: difference is worth a note. Below this it is just the stage settling.
STAGE_CHANGE_NOTE_UM = 1.0


def try_(fn):
    """Call ``fn()``; a failure becomes None, because a note must never fail a capture."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.debug("capture note unavailable: %s", exc)
        return None


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``full``, the imaging set up in FLUOVIEW (one picture, a z-stack, a time series).

    That is the only trigger this driver has, so it is the only option and
    the default. What the imaging is (its z-stack, channels, lasers) is part
    of the state (``set_state``).
    """
    handle.require_open()
    return {"trigger": {"options": ["full"], "default": "full", "active": "full"}}


def _with_defaults(handle, settings: dict | None) -> dict:
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(f"unknown acquisition setting {name!r}")
        if value not in menu[name]["options"]:
            raise ValueError(
                f"invalid value {value!r} for acquisition setting {name!r} (available: {menu[name]['options']!r})"
            )
        resolved[name] = value
    return resolved


def _export_state(handle, *, stem: str, target: str) -> dict:
    """What only the moment of the capture can tell, in the words of the ZMART metadata convention."""
    xyz = try_(lambda: get_xyz(handle)) or {}
    return {
        "controller_state": try_(lambda: get_state(handle)),
        "xyz": {axis: xyz[axis] for axis in ("x", "y", "z") if axis in xyz} or None,
        "driver": {"name": DRIVER_PACKAGE, "version": _DRIVER_VERSION},
        "vendor": {
            "position": {
                "made as": "FLUOVIEW's reading - origin - objective translation; z is the focus motor",
                "origin": dict(handle.origin or {}),
                "objective translation (um)": xyz.get("objective_translation_um"),
            },
            "software": {
                "api": "fluoview-rdk",
                "address": handle.connection.get("host"),
                "version": handle.fluoview_version,
            },
            "file": target,
            "stem": stem,
            "session": handle.hash6,
            "noted at": datetime.now(UTC).isoformat(),
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
    """Start the imaging set up in FLUOVIEW, wait for it, and keep a note of the capture for ``save_data``.

    *position_label* is what the workflow calls this place; the files are
    named after *acquisition_type* and it (``Naming`` makes the name safe), and
    a name already used in the folder saved to gets ``_001``, ``_002``, ...
    so no capture is ever overwritten. When omitted, the next per-session
    counter value is used. *output_folder* is where the capture will be
    saved; None means this session's folder under the data location.

    Answers ``{"position_label", "acquisition_type", "fluoview_file"}``.
    Raises when the stage is unknown, the label is still waiting to be saved,
    or the capture itself fails.
    """
    handle.require_open()
    resolved = _with_defaults(handle, acquisition_settings)
    output_root = (
        Path(output_folder)
        if output_folder is not None
        else Path(handle.output_root()) / ".staging" / handle.hash6
    )

    noted_before = len(handle.unconfirmed)
    last_seen = handle.driven_to
    fresh = try_(lambda: get_xyz(handle))
    at = handle.driven_to if fresh is not None else last_seen
    if at is None:
        raise RuntimeError(
            "the driver does not know where the stage is and could not read it: check that FLUOVIEW answers, then capture again"
        )
    if last_seen is not None and fresh is not None and _moved(last_seen, at):
        handle.note_unconfirmed(
            "acquire: the stage or the objective changed",
            "the stage position or the objective is not what get_xyz or set_xyz last reported "
            "(changed in FLUOVIEW?); the capture is placed from the fresh reading",
        )
    # The label is checked before the scan, so a refused label never costs a picture.
    label = (
        position_label
        if position_label is not None
        else f"{handle.position_counter:06d}"
    )
    if any(waiting.label == label for waiting in handle.unsaved):
        raise ValueError(
            f"a capture labelled {label!r} is still waiting to be saved; run save_data first, or choose another label"
        )
    acq = changes.take_picture(handle.client)
    if position_label is None:
        handle.position_counter += 1  # counted only once the picture was taken
    named = Naming(
        stem=free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem)
    )
    state = _export_state(handle, stem=named.stem, target=acq.target)
    handle.unsaved.append(
        PendingCapture(
            acq=acq,
            label=label,
            acquisition_type=acquisition_type,
            naming=named,
            state=state,
            at=dict(at),
            output_root=output_root,
            session_hash6=handle.hash6,
            asked=dict(acquisition_settings or {}),
            settings=resolved,
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {
        "position_label": label,
        "acquisition_type": acquisition_type,
        "fluoview_file": acq.target,
    }


def _moved(before: dict, after: dict) -> bool:
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
    session_hash6: str
    asked: dict
    settings: dict
    unconfirmed: list = field(default_factory=list)
