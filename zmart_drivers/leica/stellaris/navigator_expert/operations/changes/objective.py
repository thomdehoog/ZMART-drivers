"""Changing the objective: ``set_objective``, its profile and its confirmation.

This is the bare change, without moving the stage. The recipe that also keeps
the sample in view is the procedure
``procedures/keep_in_view/``; it passes ``before_fire``
to take its reading just before the change is sent.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any

from ...primitives.cam_api import waiting
from ...dispatcher.dispatch import make_log_entry, make_timing
from ...primitives.cam_api.parsing import _hw_get
from ...primitives.cam_api.profile import CommandProfile
from ...primitives.cam_api.prechecks import check_idle
from . import confirming, run_change

log = logging.getLogger(__name__)

#: Four confirmation windows, sent again between them; a slow turret is
#: absorbed by the wait for idle before each send.
OBJECTIVE = CommandProfile(pre_check_fn=check_idle)


def objective_by_slot(hw_info: dict | None) -> dict[int, dict[str, Any]]:
    """``{slot_index: objective}`` for every occupied slot of the turret.

    Empty slots (``objectiveNumber == 0``) are left out: sending one to LAS X
    can open an error dialog that blocks it.
    """
    by_slot: dict[int, dict[str, Any]] = {}
    for obj in (hw_info or {}).get("Microscope", {}).get("objectives", []):
        if obj.get("objectiveNumber", 0) == 0:
            continue
        slot = obj.get("slotIndex")
        if slot is not None:
            by_slot[int(slot)] = obj
    return by_slot


def find_objective(hw_info, slot_index=None, name=None, magnification=None):
    """``(slot, name)`` of the objective with this slot, name or magnification; ``(None, None)`` if none."""
    for obj in objective_by_slot(hw_info).values():
        s = _hw_get(obj, "slotIndex")
        n = _hw_get(obj, "name", "").strip()
        m = _hw_get(obj, "magnification")
        if slot_index is not None and s == slot_index:
            return s, n
        if name is not None and n == name.strip():
            return s, n
        if magnification is not None and m == magnification:
            return s, n
    return None, None


def set_objective(
    client,
    job_name,
    hw_info,
    slot_index=None,
    name=None,
    magnification=None,
    *,
    max_retries=None,
    before_fire=None,
):
    """Change the objective, by its slot, name or magnification (exactly one), without moving the stage.

    The limits are asked once the objective is found, with its slot, whichever
    way it was chosen. ``before_fire`` is called with ``{"slot", "name"}`` just
    before the change is sent; it may return a refusal, and then nothing is sent.
    """
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return run_change.refused(
            f"exactly one of slot_index, name, or magnification must be provided (got {len(selectors)})"
        )
    slot, target_name = find_objective(hw_info, slot_index=slot_index, name=name, magnification=magnification)
    if slot is None:
        available = [(_hw_get(o, "slotIndex"), _hw_get(o, "name", "").strip()) for o in objective_by_slot(hw_info).values()]
        return run_change.refused(
            f"Could not find objective: slot_index={slot_index}, name={name}, mag={magnification}. Available: {available}"
        )

    # The limits may fence automation to some slots (objective_slot; [] is unrestricted).
    gated_values = {"job_name": job_name, "objective_slot": slot}
    if before_fire is not None:
        refusal = before_fire({"slot": slot, "name": target_name})
        if refusal:
            refusal.setdefault("timing", make_timing(total_s=0.0, attempts=0))
            return refusal

    def setup(m):
        m.JobName = job_name
        m.ObjectiveSlotIndex = slot

    return run_change.dispatch_change(
        client,
        "PyApiSetObjectiveSlotByJobName",
        f"Objective -> {target_name} (slot {slot})",
        OBJECTIVE,
        command="set_objective",
        values=gated_values,
        setup_fn=setup,
        confirm_fn=partial(confirm_objective, job_name=job_name, target_slot=slot, target_name=target_name),
        max_retries=max_retries,
    )


def confirm_objective(client, *, job_name, target_slot, target_name=None, poll_window=None):
    """Read the job's objective until it is the one in ``target_slot``, for one window."""
    poll_window = waiting.WINDOW_S if poll_window is None else poll_window
    started = time.monotonic()
    deadline = started + poll_window
    label = target_name or f"slot {target_slot}"

    while time.monotonic() < deadline:
        settings = confirming.readback(client, job_name, deadline=deadline)
        if settings is not None:
            try:
                actual_slot = settings["objective"]["slotIndex"]
                log.debug("Objective confirm: target_slot=%s actual_slot=%s", target_slot, actual_slot)
                if actual_slot == target_slot:
                    return {"success": True, "logs": []}
            except (KeyError, TypeError, AttributeError):
                pass
        time.sleep(waiting.POLL_S)

    msg = f"Objective timeout after {time.monotonic() - started:.1f}s — target={label} (slot {target_slot})"
    log.warning(msg)
    return {"success": False, "logs": [make_log_entry("warning", msg)]}
