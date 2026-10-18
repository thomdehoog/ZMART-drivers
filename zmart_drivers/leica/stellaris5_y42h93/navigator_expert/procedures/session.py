"""The session a controller command works in: the handle, and the questions every command asks.

A :class:`ZmartHandle` is what ``connect`` hands back to the controller. It
holds the open connection to LAS X and everything the driver remembers about
this session: the zero point of the frame, the objective calibration, the
data location, and the changes that were sent but could not be confirmed.
The small functions here are the ones nearly every command needs: refuse a
closed session, record an unconfirmed change, name the selected job, list the
jobs, and say where files are saved.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..vendor_specific.primitive_handling import state as _readers

log = logging.getLogger(__name__)

#: What to run when no data location was set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with "
    "driver_setup/data_location/notebooks/set_data_location.ipynb, "
    "or connect with an output_root"
)


@dataclass
class ZmartHandle:
    """Opaque controller handle: the CAM client plus all adapter state.

    Attributes:
        client: Connected LAS X CAM client (process-lifetime, no close).
        connection: The connection dict this session was opened with
            (carries ``output_root`` and the connect params).
        hash6: SESSION hash, minted at connect. Travels in lineage /
            ``session_hash6`` provenance only — each :func:`acquire` mints
            its own acquired-position hash for the output Naming.
        origin: The frame zero point: stage XY, both z drives, their focus
            sum, and the objective it was captured under. :func:`connect`
            fills it from the newest origin saved by :func:`set_origin`.
            When no origin has been saved, it stays all-zero, which means
            the frame is simply the absolute stage coordinates.
        position_counter: Next per-session position index handed to an
            unlabeled :func:`acquire` (formatted 6-digit zero-padded). An
            explicit ``position_label`` does not consume a counter value.
        translations: Per-objective-slot translation triples (µm) from the
            active calibration, loaded at connect; ``None`` when it could not
            be read (cross-objective moves are then refused, reads warn).
        closed: Set by :func:`disconnect`; every op refuses a closed
            handle.
        unconfirmed: Every change this session sent that was accepted but
            could not be confirmed by a readback, newest last: what it was,
            when, and the driver's message. The driver does its best to
            confirm every change; when it cannot, it says so here (and in a
            warning) and carries on, so a workflow is told rather than
            stopped. ``get_state`` reports the list under ``observed``.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(
        default_factory=lambda: {
            "x_um": 0.0,
            "y_um": 0.0,
            "z_wide_um": 0.0,
            "z_galvo_um": 0.0,
            "z_focus_um": 0.0,
            "objective": None,
        }
    )
    #: Where :func:`get_xyz` last read the stage (and :func:`set_xyz` ends by
    #: reading it): the frame position, with the z-wide and galvo readings.
    #: Remembered rather than re-read, so that the capture path holds no extra
    #: call that can hang. The drives ride along because a job states its stack
    #: in its drive's own positions, and without them those slices cannot be
    #: put in the frame.
    driven_to: dict[str, float] | None = None
    position_counter: int = 0
    translations: dict[int, tuple[float, float, float]] | None = None
    #: This microscope's data location, set up once and loaded at connect
    #: (driver_setup/data_location); None when it was never set up.
    data_location: Path | None = None
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)


def note_unconfirmed(handle: ZmartHandle, what: str, result: Any) -> None:
    """Record a change that was sent and accepted but never confirmed, and carry on.

    The driver confirms what it can. When a readback never matched in
    time, the change is not known to have failed, only not known to have
    happened, so it is written down here, said in a warning, and the
    command goes on. The workflow sees the list in ``get_state``.
    """
    message = result.get("message") if isinstance(result, dict) else str(result)
    note = {
        "what": what,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": message or "readback did not confirm the change",
    }
    handle.unconfirmed.append(note)
    del handle.unconfirmed[:-50]  # keep the newest fifty
    log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])


def require_open(handle: ZmartHandle) -> None:
    """Refuse to drive a disconnected handle."""
    if handle.closed:
        raise RuntimeError("session is disconnected")


def selected_job_name(handle: ZmartHandle) -> str | None:
    """Name of the currently selected LAS X job, or ``None`` when it cannot be read.

    A reading that fails is unknown, not a stop. The commands that only
    use this to decide whether a selection is needed go ahead and select;
    the ones that need the name to compute a move say so and refuse.
    """
    selected = _readers.get_selected_job(handle.client)
    name = selected.get("Name") if selected else None
    if not name:
        log.warning("could not determine the selected LAS X job")
        return None
    return name


def job_catalog(handle: ZmartHandle) -> tuple[list[dict], list[dict]]:
    """The live job catalog split into (normal, autofocus) jobs.

    Autofocus jobs are a separate category: they never appear as acquisition
    or state options and run only through the ``autofocus`` procedure.
    """
    jobs = _readers.get_jobs(handle.client) or []
    normal = [dict(j) for j in jobs if not j.get("IsAutofocus")]
    autofocus = [dict(j) for j in jobs if j.get("IsAutofocus")]
    return normal, autofocus


def output_root(handle: Any) -> Path:
    """The folder this session saves under: its own ``output_root``, else the set-up data location.

    Raises ``RuntimeError`` saying how to set one up when neither is there:
    data saved somewhere nobody chose is data lost.
    """
    root = handle.connection.get("output_root") or handle.data_location
    if not root:
        raise RuntimeError(
            f"this microscope does not know where to save its data: {SET_UP}"
        )
    return Path(root).expanduser().resolve()


def try_(fn):
    """Call ``fn()``; degrade any failure to ``None`` (state must not fail save)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- provenance capture is best-effort
        log.debug("export-state field unavailable: %s", exc)
        return None
