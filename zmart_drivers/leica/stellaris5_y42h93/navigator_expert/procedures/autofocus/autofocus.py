"""Autofocus: run a LAS X autofocus job, capture only, and put the selection back.

Nothing is saved. The answer is the focus read back right after the run.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from ..acquisition import run_job as _capture
from ..objective_change_with_compensation import compensated_changes as _compensated
from ...session import handle as _info
from ..absolute_coordinate_system.frame import delta_or_warn, hardware_snapshot
from ...session.handle import ZmartHandle, job_catalog, note_unconfirmed

log = logging.getLogger(__name__)


def run_autofocus(handle: ZmartHandle, procedure: dict) -> dict:
    """Select the autofocus job, run it capture-only, restore the selection.

    ``job`` names the autofocus job; it may be omitted when the instrument
    has exactly one. Nothing is saved — the result is the focus readback
    right after the run (before the selection is restored), in both
    hardware and frame terms. Like every capture it never touches the
    scanning template: positions are never made in the Navigator Expert.
    """
    _, autofocus = job_catalog(handle)
    names = [j["Name"] for j in autofocus if j.get("Name")]
    if not names:
        raise RuntimeError("no autofocus job exists on this instrument")
    job = procedure.get("job")
    if job is None:
        if len(names) > 1:
            raise ValueError(
                f"multiple autofocus jobs; pass 'job' (available: {names})"
            )
        job = names[0]
    elif job not in names:
        raise ValueError(f"{job!r} is not an autofocus job (available: {names})")

    original = _info.selected_job_name(handle)
    if job != original:
        selected = _compensated.select_job(handle.client, job)
        if not selected.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {selected}")
        if not selected.get("confirmed"):
            note_unconfirmed(handle, f"autofocus: select_job('{job}')", selected)
    try:
        acq = _capture.acquire(handle.client, job)
        # Read the focus result BEFORE restoring the selection: restoring
        # could reposition (jobs own objective state).
        snap = hardware_snapshot(handle)
    finally:
        if original is None:
            log.warning(
                "the job selected before autofocus was unknown, so none is restored"
            )
        elif job != original:
            restored = _compensated.select_job(handle.client, original)
            if not restored.get("success"):
                log.warning(
                    "could not restore job %r after autofocus: %s", original, restored
                )
            elif not restored.get("confirmed"):
                note_unconfirmed(
                    handle, f"autofocus: restore select_job('{original}')", restored
                )
    focus = snap["z_wide_um"] + snap["z_galvo_um"]
    dt = delta_or_warn(handle, snap)
    return {
        "ran": "autofocus",
        "job": job,
        "focus_um": focus,
        "frame_z_um": focus - handle.origin["z_focus_um"] - dt[2],
        "duration_s": acq.finished_at - acq.started_at,
    }
