"""Changing the objective or the job without losing the sample: the change, and the stage shift after it.

Changing the objective moves the picture: each lens looks at a slightly
different spot and focus. Before a change, these recipes record where the
stage is. After it, they move the stage by the measured difference between the
two lenses, so the same sample point stays in view. A job change can carry an
objective change with it, so it is handled here too. The measured offsets come
from the objective calibration saved by ``driver_setup/optical_calibration``,
and the coordinates they relate to are explained in
``procedures/absolute_coordinate_system``.

The changes that move the lens and then correct the stage: ``set_objective`` and ``select_job``.

Changing the objective, or choosing a job that carries another objective, moves
the sample out from under the field of view. These two recipes record where the
stage and the focus were before the change, make the change, and then move the
stage by the calibrated offset so the same point of the sample stays in view.

The correction is made only when the change was accepted. When the correction
cannot be made, the command answers ``success: False`` and names the step, so
a picture is never taken at a position that no longer points at the sample.

Each one wraps the bare change of the same name, ``procedures/changes.py``'s
``set_objective`` and ``select_job``, which
change the lens or the job and nothing else. Use those
when the stage should not be corrected.

Keep the sample point when a job or objective change swaps the lens.

A Navigator Expert job carries its own objective choice, so changing the job
can change the objective with it; changing the objective does so by
definition. The stage does not move on its own during either change, but the
new objective looks at a different spot (parcentricity) and a different focal
plane (parfocality). The adopted objective-pair calibration records exactly
that difference per lens, so the driver can keep the operator's sample point.
The order is the whole point:

1. BEFORE the change, record the motoric XY and z-wide positions — while
   they still mean "where the old objective was looking" — and which
   objective is in (:func:`record_before_change`).
2. Perform the change (the command wrapper does this as usual).
3. If the objective changed, add the difference between the two lenses'
   calibration translations to the recorded values and move there
   (:func:`compensate_after_change`).

A later frame move to the same coordinates computes the identical absolute
target — the adapter's per-move compensation uses the same translation table
relative to the origin's objective — so nothing is ever compensated twice.

This lives in the command layer, below the controller adapter, so EVERY
driver-performed job or objective change is covered no matter who asked for
it — but the behaviour is explicit, not hidden: ``set_objective`` and
``select_job`` take a ``compensate`` parameter (decision §8). The default
follows the session's connect-time policy: the caller hands in the
``translations`` its session loaded, and compensation runs when there are
some. It stays off when there are none: for a client the driver never
connected through (bare command-level use, tests, the setup notebooks) or a
session that declined calibration (``load_calibration=False``). The delta
math itself is single-sourced in
:func:`navigator_expert.calibration.core.model.translation_delta_um`,
shared with the adapter's per-move frame mapping.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from ..dispatcher import dispatch
from . import changes
from typing import Any


log = logging.getLogger(__name__)


def set_objective(
    client,
    job_name,
    hw_info,
    slot_index=None,
    name=None,
    magnification=None,
    *,
    translations=None,
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
            recorded["before"] = record_before_change(
                client, job_name, translations=translations, compensate=compensate
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
            return dispatch.refused(
                "refusing the objective change: could not record the "
                f"pre-change position for objective compensation: {exc}"
            )
        return None

    result = changes.set_objective(
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
        result = merge_into_result(
            result,
            compensate_after_change(
                client, job_name, before, new_slot=recorded["slot"]
            ),
        )
    return result


def select_job(client, job_name, *, translations=None, compensate=None):
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
            recorded["before"] = record_before_change(
                client,
                context.get("api_baseline_name"),
                translations=translations,
                compensate=compensate,
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
            return dispatch.refused(
                "refusing the job change: could not record the pre-change "
                f"position for objective compensation: {exc}"
            )
        return None

    result = changes.select_job(client, job_name, before_fire=record_position)
    before = recorded.get("before")
    if before is not None and result.get("success"):
        result = merge_into_result(
            result,
            compensate_after_change(client, job_name, before),
        )
    return result


def record_before_change(
    client: Any,
    job_name: str | None = None,
    *,
    translations: dict | None = None,
    compensate: bool | None = None,
) -> dict | None:
    """Record motoric XY, z-wide, and the active objective BEFORE a change.

    ``translations`` is the per-slot translation table the session loaded at
    connect. ``None`` means compensation is off: the driver never loaded one
    for this client (bare command-level use, tests, the setup notebooks), or
    the connection declined calibration (``load_calibration=False``). An
    empty dict means the connection *wanted* calibration but none is usable
    (unreadable file, or no pairs measured); an objective swap then fails
    the command's result rather than silently leaving the stage at the old
    lens's spot.

    ``compensate`` makes the behaviour explicit at the call site:

    - ``None`` (default) — follow the session's connect-time policy:
      compensate when calibration was loaded, stay bare when the session is
      unarmed or declined calibration (``load_calibration=False``).
    - ``False`` — bare change, no recording, no compensation.
    - ``True`` — require compensation: raises when the session carries no
      calibration to compensate with, instead of proceeding bare.

    Returns ``None`` when compensation is off, in which case the caller
    proceeds exactly as it always has. Raises when compensation is on but
    the pre-change position cannot be read: a change whose starting point
    is unknown could never be compensated afterwards, so it must not be
    fired at all.
    """
    if compensate is False:
        return None
    if translations is None:
        if compensate:
            raise RuntimeError(
                "compensate=True was requested but this session has no "
                "calibration loaded (connect with load_calibration=True, or "
                "adopt a calibration first)"
            )
        return None

    from . import readings as _readers

    if job_name is None:
        selected = _readers.get_selected_job(client) or {}
        job_name = selected.get("Name")
        if not job_name:
            raise RuntimeError("could not determine the selected job")
    xy = _readers.get_xy(client) or {}
    if "x_um" not in xy or "y_um" not in xy:
        raise RuntimeError(f"get_xy returned no readback: {xy}")
    settings = _readers.get_job_settings(client, job_name) or {}
    slot = (settings.get("objective") or {}).get("slotIndex")
    z_wide = _readers.read_zwide_um(client, job_name)
    if z_wide is None:
        raise RuntimeError(f"z-wide readback unavailable for job {job_name!r}")
    return {
        "job": job_name,
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_wide_um": float(z_wide),
        "slot": None if slot is None else int(slot),
        "translations": translations,
    }


def compensate_after_change(
    client: Any,
    job_name: str,
    before: dict,
    new_slot: int | None = None,
) -> dict:
    """After the change: if the objective swapped, re-realize the sample point.

    ``before`` is what :func:`record_before_change` returned. ``new_slot``
    can be passed when the caller already knows the objective it commanded
    (``set_objective``); otherwise the new job's settings are read.

    Never raises — the change has already happened, so every problem is
    reported in the returned dict instead: ``{"ok": bool, "objective_changed":
    bool | None, "applied_translation_um": [x, y, z] | None, "message":
    str | None}``. The calling command turns ``ok=False`` into a failed
    result so nobody acquires at an uncompensated position by accident.
    """

    def _failed(message: str, *, changed: bool | None = True) -> dict:
        return {
            "ok": False,
            "objective_changed": changed,
            "applied_translation_um": None,
            "message": message,
        }

    try:
        from . import readings as _readers

        if new_slot is None:
            settings = _readers.get_job_settings(client, job_name) or {}
            new_slot = (settings.get("objective") or {}).get("slotIndex")
        new_slot = None if new_slot is None else int(new_slot)
        old_slot = before["slot"]
        if old_slot == new_slot:
            return {
                "ok": True,
                "objective_changed": False,
                "applied_translation_um": None,
                "message": None,
            }
        if old_slot is None or new_slot is None:
            return _failed(
                f"the objective may have changed (slot {old_slot!r} -> "
                f"{new_slot!r}) but could not be identified on both sides, so "
                "the position was NOT compensated — re-check the objective and "
                "position before acquiring",
                changed=None,
            )
        # The delta math and the missing-pair policy live in ONE place —
        # calibration/core/model.py — shared with the adapter's per-move
        # frame mapping, so the two layers can never drift apart.
        from ..driver_setup.optical_calibration import model as _cal_model

        try:
            delta = _cal_model.translation_delta_um(
                before["translations"], old_slot, new_slot
            )
        except RuntimeError as exc:
            return _failed(
                f"the change swapped the objective but {exc}; the stage was "
                f"NOT moved and still points at the old lens's spot — "
                f"compensate afterwards or reposition manually."
            )
        if delta != (0.0, 0.0, 0.0):
            log.info(
                "the change to %r swapped the objective (slot %s -> %s); "
                "moving by the calibrated translation (%+.2f, %+.2f, %+.2f) um "
                "to keep the sample point",
                job_name,
                old_slot,
                new_slot,
                *delta,
            )
            # The moves go through the ordinary gated wrappers, so the
            # compensated targets are checked against the machine limits
            # exactly like any other motion; a refusal fails the change.
            xy_result = changes.move_xy(
                client, before["x_um"] + delta[0], before["y_um"] + delta[1], unit="um"
            )
            if not xy_result.get("success") or xy_result.get("confirmed") is False:
                return _failed(
                    f"the compensating XY move after the objective change "
                    f"failed: {xy_result.get('message', xy_result)}"
                )
            z_result = changes.move_z(
                client,
                job_name,
                before["z_wide_um"] + delta[2],
                unit="um",
                z_mode="zwide",
            )
            if not z_result.get("success") or z_result.get("confirmed") is False:
                return _failed(
                    f"the compensating z-wide move after the objective change "
                    f"failed: {z_result.get('message', z_result)}"
                )
        return {
            "ok": True,
            "objective_changed": True,
            "applied_translation_um": list(delta),
            "message": None,
        }
    except Exception as exc:  # noqa: BLE001 -- the change already fired; report, never mask
        return _failed(
            f"objective compensation failed after the change: {exc}", changed=None
        )


def merge_into_result(result: dict, compensation: dict | None) -> dict:
    """Fold a compensation report into a command result dict.

    A failed compensation fails the whole command result: the change itself
    went through, but leaving ``success: True`` would invite acquiring at a
    position that no longer points at the sample.
    """
    if compensation is None:
        return result
    result["objective_compensation"] = {
        "objective_changed": compensation["objective_changed"],
        "applied_translation_um": compensation["applied_translation_um"],
    }
    if not compensation["ok"]:
        result["success"] = False
        prefix = result.get("message")
        result["message"] = (
            f"{prefix} | {compensation['message']}"
            if prefix
            else compensation["message"]
        )
    return result
