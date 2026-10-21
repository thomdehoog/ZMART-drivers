"""The changes that move the lens and then correct the stage: ``set_objective`` and ``select_job``.

Changing the objective, or choosing a job that carries another objective, moves
the sample out from under the field of view. These two recipes record where the
stage and the focus were before the change, make the change, and then move the
stage by the calibrated offset so the same point of the sample stays in view.

The correction is made only when the change was accepted. When the correction
cannot be made, the command answers ``success: False`` and names the step, so
a picture is never taken at a position that no longer points at the sample.

Each one wraps the bare change of the same name, ``operations/changes/objective.py``'s
``set_objective`` and ``operations/changes/select_job.py``'s ``select_job``, which
change the lens or the job and nothing else. Use those
when the stage should not be corrected.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging

from ...dispatcher.door import make_timing as _make_timing
from ...operations.changes import objective as _objective
from ...operations.changes import select_job as _select_job
from . import objective_shift as _objective_shift

log = logging.getLogger(__name__)


def _refusal(message):
    return {
        "success": False,
        "confirmed": None,
        "message": message,
        "timing": _make_timing(total_s=0.0, attempts=0),
        "logs": [],
    }


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
    """Set objective by slot index, name, or magnification, keeping the sample point.

    Exactly one of slot_index, name, or magnification must be provided.

    ``compensate`` controls whether the calibrated stage move that keeps
    the sample point runs after the swap: ``None`` (default) follows the
    session's connect-time calibration policy, ``True`` requires it, and
    ``False`` asks for a bare swap. See
    :func:`objective_shift.record_before_change`.
    """
    recorded = {}

    def record_position(chosen):
        # Record the motoric XY and z-wide position BEFORE the objective change —
        # while they still mean "where the current objective is looking" — so the
        # calibrated translation can keep the sample point afterwards. Armed only
        # for clients the driver connected; a failed pre-read refuses the change
        # while nothing has happened yet.
        recorded["slot"] = chosen["slot"]
        try:
            recorded["before"] = _objective_shift.record_before_change(
                client, job_name, compensate=compensate
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
            return _refusal(
                "refusing the objective change: could not record the "
                f"pre-change position for objective compensation: {exc}"
            )
        return None

    result = _objective.set_objective(
        client,
        job_name,
        hw_info,
        slot_index=slot_index,
        name=name,
        magnification=magnification,
        max_retries=max_retries,
        before_fire=record_position,
    )
    before = recorded.get("before")
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(
                client, job_name, before, new_slot=recorded["slot"]
            ),
        )
    return result


def select_job(client, job_name, *, compensate=None):
    """Select a job by name, keeping the sample point.

    A job change can carry an objective change with it. ``compensate``
    controls the calibrated stage move that keeps the sample point
    afterwards. ``None`` (default) follows the session's connect-time
    calibration policy, ``True`` requires it, ``False`` asks for a bare change.
    See :func:`objective_shift.record_before_change`.
    """
    recorded = {}

    def record_position(context):
        # Record the motoric XY and z-wide position NOW — before the switch,
        # while they still mean "where the current objective is looking" — so
        # the compensation after the switch can keep the sample point. Armed
        # only for clients the driver connected (per-connection config
        # installed); a failed pre-read refuses the change while nothing has
        # happened yet.
        try:
            recorded["before"] = _objective_shift.record_before_change(
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
                "logs": [],
            }
        return None

    result = _select_job.select_job(client, job_name, before_fire=record_position)
    before = recorded.get("before")
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(client, job_name, before),
        )
    return result
