"""The changes that move the lens and then correct the stage: ``set_objective`` and ``select_job``.

Changing the objective, or choosing a job that carries another objective, moves
the sample out from under the field of view. These two recipes record where the
stage and the focus were before the change, make the change, and then move the
stage by the calibrated offset so the same point of the sample stays in view.

The correction is made only when the change was accepted. When the correction
cannot be made, the command answers ``success: False`` and names the step, so
a picture is never taken at a position that no longer points at the sample.

Each is built on the single-command changes in
``vendor_interface/primitive_handling/changes.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import time

from ...dispatcher.dispatcher import (
    make_log_entry as _make_log_entry,
    make_timing as _make_timing,
)
from ...vendor_interface.parsing import _hw_get
from ...vendor_interface.primitive_handling import changes as _changes
from ...vendor_interface.primitive_handling.confirm_select_job import (
    prepare_select_job,
    select_job_confirm_legs,
)
from ...vendor_interface.primitive_handling.confirmations import confirm_objective
from ...vendor_interface.primitive_handling.profiles import OBJECTIVE, SELECT_JOB
from functools import partial

from . import objective_shift as _objective_shift
from .objectives import objective_by_slot

log = logging.getLogger(__name__)


# =============================================================================
# Changing the objective
# =============================================================================


def _resolve_objective(hw_info, slot_index=None, name=None, magnification=None):
    """Resolve an objective to (slot, display_name) from hardware info.

    Any one of *slot_index*, *name*, or *magnification* identifies the
    objective.  Returns ``(None, None)`` if not found.
    """
    # Empty turret slots (objectiveNumber 0) are excluded by objective_by_slot;
    # sending those to LAS X can trigger modal error dialogs that block the app.
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
    compensate=None,
    max_retries=None,
):
    """Set objective by slot index, name, or magnification.

    Resolves the input to a slot index, then fires the command.
    Exactly one of slot_index, name, or magnification must be provided.

    ``compensate`` controls whether the calibrated stage move that keeps
    the sample point runs after the swap: ``None`` (default) follows the
    session's connect-time calibration policy, ``True`` requires it, and
    ``False`` asks for a bare swap. See
    :func:`objective_shift.record_before_change`.
    """
    # The limits gate is consulted once, below, after the selector resolves
    # to a physical slot — that one check covers both the fail-closed
    # session state and the objective allow-list. (Malformed selectors get
    # a usage error first; they fire nothing, so nothing needs gating.)
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "exactly one of slot_index, name, or magnification must be "
                f"provided (got {len(selectors)})"
            ),
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }
    slot, target_name = _resolve_objective(
        hw_info, slot_index=slot_index, name=name, magnification=magnification
    )

    if slot is None:
        available = [
            (_hw_get(o, "slotIndex"), _hw_get(o, "name", "").strip())
            for o in objective_by_slot(hw_info).values()
        ]
        return {
            "success": False,
            "confirmed": None,
            "message": f"Could not find objective: slot_index={slot_index}, "
            f"name={name}, mag={magnification}. "
            f"Available: {available}",
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    # The flat limits file may fence automation to specific slots
    # (objective_slot; [] — the default — is unrestricted). The resolved
    # physical slot is checked regardless of whether the caller selected it
    # by slot, name, or magnification.
    refused = _changes.limits_refusal(
        client,
        "set_objective",
        {"job_name": job_name, "objective_slot": slot},
    )
    if refused:
        return refused

    # Record the motoric XY and z-wide position BEFORE the objective change —
    # while they still mean "where the current objective is looking" — so the
    # calibrated translation can keep the sample point afterwards. Armed only
    # for clients the driver connected; a failed pre-read refuses the change
    # while nothing has happened yet.
    try:
        before = _objective_shift.record_before_change(
            client, job_name, compensate=compensate
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "refusing the objective change: could not record the "
                f"pre-change position for objective compensation: {exc}"
            ),
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    api_obj = client.PyApiSetObjectiveSlotByJobName

    def setup(m):
        m.JobName = job_name
        m.ObjectiveSlotIndex = slot

    result = _changes.dispatch_change(
        client,
        api_obj,
        f"Objective -> {target_name} (slot {slot})",
        OBJECTIVE,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_objective,
            job_name=job_name,
            target_slot=slot,
            target_name=target_name,
        ),
        max_retries=max_retries,
    )
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(
                client, job_name, before, new_slot=slot
            ),
        )
    return result


# =============================================================================
# Job selection
# =============================================================================


def select_job(client, job_name, *, compensate=None):
    """Select a job by name.

    Routes through the backbone. The SELECT_JOB profile pre-checks scanner
    idle before firing (``check_idle``), and confirms like every command:
    four windows of ``WINDOW_S``, fired again between. Source policy
    (api / log / hybrid) lives entirely
    in the confirmation layer: ``prepare_select_job`` owns the
    "already selected" decision and the api baseline, and
    ``select_job_confirm_legs`` builds the confirmation legs the dispatch
    race runs.

    Args:
        client: LAS X API client.
        job_name: Name of job to select.
        compensate: A job change can swap the objective with it; this
            controls the calibrated stage move that keeps the sample point
            afterwards. ``None`` (default) follows the session's
            connect-time calibration policy, ``True`` requires it,
            ``False`` asks for a bare change. See
            :func:`objective_shift.record_before_change`.
    """
    refused = _changes.limits_refusal(client, "select_job", {"job_name": job_name})
    if refused:
        return refused

    t0 = time.perf_counter()
    noop, context = prepare_select_job(client, job_name)
    if noop is not None:
        noop["timing"] = _make_timing(total_s=time.perf_counter() - t0, attempts=0)
        return noop

    # A job change can carry an objective change with it. Record the motoric
    # XY and z-wide position NOW — before the switch, while they still mean
    # "where the current objective is looking" — so the compensation after
    # the switch can keep the sample point. Armed only for clients the driver
    # connected (per-connection config installed); a failed pre-read refuses
    # the change while nothing has happened yet.
    try:
        before = _objective_shift.record_before_change(
            client, context.get("api_baseline_name"), compensate=compensate
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "refusing the job change: could not record the pre-change "
                f"position for objective compensation: {exc}"
            ),
            "timing": _make_timing(total_s=time.perf_counter() - t0, attempts=0),
            "logs": [],
        }

    api_obj = client.PyApiSelectJobByName

    def setup(m):
        m.JobName = job_name

    command_started_at = time.time()
    api_confirm, log_leg, budget_s = select_job_confirm_legs(
        job_name,
        command_started_at=command_started_at,
        api_baseline_name=context["api_baseline_name"],
        timeout=SELECT_JOB.confirm_poll_s,
    )
    result = _changes.dispatch_change(
        client,
        api_obj,
        f"SelectJob '{job_name}'",
        SELECT_JOB,
        setup_fn=setup,
        confirm_fn=api_confirm,
        log_confirm_fn=log_leg,
        confirm_race_budget_s=budget_s,
    )
    if context["api_said_selected"]:
        result.setdefault("logs", []).append(
            _make_log_entry(
                "info",
                "API reported target already selected before SelectJob, "
                "but the log-participating confirmation still fired the "
                "command",
            )
        )
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(client, job_name, before),
        )
    return result
