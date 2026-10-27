"""Taking a picture: the ``acquire`` step, and the note it keeps for saving (:class:`PendingCapture`).

``acquire`` has NIS-Elements capture one picture with the settings as they
are and save it as a TIFF in a staging folder, and notes what only the moment
of the capture can tell: where the stage stood, which objective and channel
were in use, the state of the microscope. The note is kept with the session;
``save_data`` later turns the staged TIFF into the capture's OME-Zarr store,
with the note in it, and removes the TIFF.

The operator may move the stage or change the objective in NIS-Elements
between two calls of the workflow, and the driver cannot see that as it
happens. So ``acquire`` reads the stage once, just before the capture, and
places the picture from that reading. If it differs from what the workflow
last saw, a note is added, and the capture goes ahead.

:func:`snap_picture` takes a picture and hands back its pixels without
keeping anything, for the setup steps that measure the microscope.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import changes
from .absolute_coordinate_system import get_xyz
from .save_data.naming import Naming, free_stem, staging_dir
from .state import get_state

try:  # the driver's version, for the capture record; never fail a capture over it
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
    """Call ``fn()``; turn any failure into None (a note must never fail a capture)."""
    try:
        return fn()
    except Exception as why:  # noqa: BLE001
        log.debug("a note of the capture could not be taken: %s", why)
        return None


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``snap``, one picture with the settings as they are.

    That is the only way this driver captures, so it is the only option and
    the default. Which channel, exposure and objective are used is part of
    the state (``set_state``); a z-stack is a series of ``set_xyz`` and
    ``acquire`` calls by the workflow.
    """
    handle.require_open()
    return {"trigger": {"options": ["snap"], "default": "snap", "active": "snap"}}


def _with_defaults(handle, settings: dict | None) -> dict:
    """Check the settings against the menu, filling what was left out from the active values."""
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(
                f"unknown acquisition setting {name!r}; the settings are {list(menu)}"
            )
        if value not in menu[name]["options"]:
            raise ValueError(
                f"{value!r} is not a value of {name!r}; the options are {menu[name]['options']!r}"
            )
        resolved[name] = value
    return resolved


def snap_picture(handle):
    """Take one picture and hand back its pixels as an array; nothing is kept.

    For the setup steps that measure the microscope. The picture goes to a
    temporary file that NIS-Elements writes and this reads, then removes.
    NIS's pixel size for the objective in use, when it has one, is
    remembered by the session.
    """
    import tifffile

    handle.require_open()
    with tempfile.TemporaryDirectory(prefix="zmart_nikon_snap_") as folder:
        path = Path(folder) / "picture.tif"
        answered = changes.take_picture(handle.client, path)
        _remember_pixel_size(handle, answered.get("pixel_size_um"))
        return tifffile.imread(path)


def acquire(
    handle,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
    acquisition_type: str,
    output_folder: str | None,
) -> dict:
    """Take one picture with the settings as they are, and keep a note of it for ``save_data``.

    Args:
        position_label: What the workflow calls this place. Every file name
            is the acquisition type, then the label. When omitted, the next
            per-session number ("000000", "000001", ...) is used. A name
            already used in the folder saved to gets ``_001``, ``_002``, ...,
            so no capture is ever overwritten.
        acquisition_settings: Values from :func:`get_acquisition_settings`:
            the trigger, which is always ``snap``. Unknown keys raise.
        acquisition_type: The kind of acquisition in the workflow's run, such
            as ``"overview"``; the controller fills in ``"acquisition"``
            when the workflow gave none. It names the files.
        output_folder: Where the capture will be saved. When None, this
            session's folder under the microscope's data location.

    Answers ``{"position_label", "acquisition_type", "staged"}``. Raises
    ``RuntimeError`` when the stage cannot be read or the capture fails, and
    ``ValueError`` for a label still waiting to be saved.
    """
    handle.require_open()
    resolved = _with_defaults(handle, acquisition_settings)
    output_root = (
        Path(output_folder) if output_folder is not None else Path(handle.output_root())
    )
    label = (
        position_label if position_label is not None else _next_position_label(handle)
    )
    if any(waiting.label == label for waiting in handle.unsaved):
        raise ValueError(
            f"a capture labelled {label!r} is still waiting to be saved; run save_data first, or "
            "choose another label, so that no capture overwrites another"
        )
    naming = Naming(
        stem=free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem)
    )

    # Where the stage stands right now, read once before the capture, so the
    # picture is placed from what NIS-Elements really did.
    noted_before = len(handle.unconfirmed)
    last_seen = handle.driven_to
    xyz = get_xyz(handle)
    at = dict(handle.driven_to)
    if last_seen is not None and _moved(last_seen, at):
        handle.note_unconfirmed(
            "acquire: the stage or the objective changed",
            "the stage position is not what get_xyz or set_xyz last reported (moved in "
            "NIS-Elements?); the capture is placed from the fresh reading",
        )
    state = try_(lambda: get_state(handle)) or {}

    staging = staging_dir(output_root, handle.hash6)
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{naming.stem}.tif"
    started = time.time()
    answered = changes.take_picture(handle.client, path)
    finished = time.time()
    _remember_pixel_size(handle, answered.get("pixel_size_um"))

    handle.unsaved.append(
        PendingCapture(
            path=path,
            pixel_size_um=answered.get("pixel_size_um"),
            label=label,
            acquisition_type=acquisition_type,
            naming=naming,
            output_root=output_root,
            at=at,
            xyz={axis: xyz[axis] for axis in ("x", "y", "z")},
            state=state,
            channel=handle.channel,
            exposure_ms=((state.get("changeable") or {}).get("exposure_ms")),
            objective=at.get("objective"),
            session_hash6=handle.hash6,
            asked=dict(acquisition_settings or {}),
            settings=resolved,
            started_at=started,
            finished_at=finished,
            answered=answered.get("result"),
            driver={"name": DRIVER_PACKAGE, "version": _DRIVER_VERSION},
            noted_at=datetime.now(UTC).isoformat(timespec="seconds"),
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {
        "position_label": label,
        "acquisition_type": acquisition_type,
        "staged": str(path),
    }


def _remember_pixel_size(handle, pixel_size_um) -> None:
    """Keep NIS's pixel size for the objective in use, when it reports one."""
    if isinstance(pixel_size_um, (int, float)) and pixel_size_um > 0:
        handle.pixel_size_um = float(pixel_size_um)


def _next_position_label(handle) -> str:
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _moved(before: dict, after: dict) -> bool:
    """Whether the stage position differs by more than a settling, in the frame."""
    return any(
        abs(after[axis] - before[axis]) > STAGE_CHANGE_NOTE_UM
        for axis in ("x", "y", "z")
    )


@dataclass
class PendingCapture:
    """One captured picture, staged as a TIFF, waiting to be saved, with the notes taken as it was captured."""

    path: Path
    pixel_size_um: float | None
    label: str
    acquisition_type: str
    naming: Naming
    output_root: Path
    at: dict  # where the stage stood, in the frame, with the objective
    xyz: dict  # get_xyz's answer at the capture, per axis
    state: dict
    channel: str | None
    exposure_ms: float | None
    objective: dict | None
    session_hash6: str
    asked: dict
    settings: dict
    started_at: float
    finished_at: float
    answered: Any
    driver: dict
    noted_at: str
    unconfirmed: list = field(default_factory=list)
