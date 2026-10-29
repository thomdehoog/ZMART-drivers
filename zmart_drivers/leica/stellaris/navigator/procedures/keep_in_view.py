"""Changing the objective without losing the sample: the change, and the stage shift after it.

Changing the objective moves the picture: each lens looks at a slightly
different spot and focus. Before the change, this recipe records where the
stage and the focus are. After it, it moves the stage by the measured
difference between the two lenses, so the same sample point stays in view.
The measured offsets come from the objective calibration saved by
``driver_setup/optical_calibration``, and the coordinates they relate to are
explained in ``procedures/absolute_coordinate_system``.

The correction is made only when the change was accepted. When the correction
cannot be made, the command answers ``success: False`` and names the step, so
a picture is never taken at a position that no longer points at the sample.
This wraps the bare change of the same name, ``procedures/changes.py``'s
``set_objective``; use that one when the stage should not be corrected.

The order is the whole point:

1. BEFORE the change, record the stage and z-wide positions, while they
   still mean "where the old objective was looking", and which objective
   is in (:func:`record_before_change`).
2. Perform the change.
3. If the objective changed, add the difference between the two lenses'
   calibration translations to the recorded values and move there
   (:func:`compensate_after_change`).

A later move to the same frame coordinates computes the identical stage
target, because the frame arithmetic uses the same translation table
relative to the origin's objective, so nothing is ever compensated twice.
``compensate`` makes the behaviour explicit: ``None`` (the default) follows
the session's connect-time policy and compensates when a calibration was
loaded, ``True`` requires it, ``False`` asks for a bare change.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ..dispatcher import dispatch
from . import changes

log = logging.getLogger(__name__)


def set_objective(
    client,
    objectives,
    slot_index=None,
    name=None,
    magnification=None,
    *,
    translations=None,
    compensate=None,
    max_retries=None,
):
    """Change the objective by slot, name or magnification (exactly one), keeping the sample point.

    *objectives* is the turret as ``readings.get_objectives`` lists it.
    *translations* is the per-slot translation table the session loaded at
    connect; ``None`` means compensation is off. See
    :func:`record_before_change` for *compensate*.
    """
    recorded = {}

    def record_position(chosen):
        # Record the stage and z-wide BEFORE the objective change, while they
        # still mean "where the current objective is looking". A failed read
        # refuses the change while nothing has happened yet.
        recorded["slot"] = chosen["slot"]
        try:
            recorded["before"] = record_before_change(client, translations=translations, compensate=compensate)
        except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
            return dispatch.refused(
                f"refusing the objective change: could not record the pre-change position "
                f"for objective compensation: {exc}"
            )
        return None

    result = changes.set_objective(
        client,
        objectives,
        slot_index=slot_index,
        name=name,
        magnification=magnification,
        max_retries=max_retries,
        before_fire=record_position,
    )
    before = recorded.get("before")
    if before is not None and result.get("success"):
        result = merge_into_result(result, compensate_after_change(client, before, new_slot=recorded["slot"]))
    return result


def record_before_change(client: Any, *, translations: dict | None = None, compensate: bool | None = None) -> dict | None:
    """Record the stage, z-wide and the objective in place BEFORE a change; None when compensation is off.

    Raises when compensation is on but the position cannot be read: a
    change whose starting point is unknown could never be compensated
    afterwards, so it must not be fired at all. ``compensate=True`` with no
    calibration loaded raises too, instead of proceeding bare.
    """
    if compensate is False:
        return None
    if translations is None:
        if compensate:
            raise RuntimeError(
                "compensate=True was requested but this session has no calibration loaded "
                "(connect with load_calibration=True, or adopt a calibration first)"
            )
        return None

    from . import readings as _readers

    xy = _readers.get_xy(client) or {}
    if "x_um" not in xy or "y_um" not in xy:
        raise RuntimeError(f"the stage position could not be read: {xy}")
    objective = _readers.get_objective(client) or {}
    slot = objective.get("slotIndex")
    z_wide = _readers.read_zwide_um(client)
    if z_wide is None:
        raise RuntimeError("z-wide could not be read")
    return {
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_wide_um": float(z_wide),
        "slot": None if slot is None else int(slot),
        "translations": translations,
    }


def compensate_after_change(client: Any, before: dict, new_slot: int | None = None) -> dict:
    """After the change: if the objective swapped, bring the same sample point back into view.

    Never raises: the change has already happened, so every problem is
    reported in the returned dict instead: ``{"ok", "objective_changed",
    "applied_translation_um", "message"}``. The calling command turns
    ``ok=False`` into a failed result so nobody acquires at an
    uncompensated position by accident.
    """

    def _failed(message: str, *, changed: bool | None = True) -> dict:
        return {"ok": False, "objective_changed": changed, "applied_translation_um": None, "message": message}

    try:
        from . import readings as _readers

        if new_slot is None:
            new_slot = (_readers.get_objective(client) or {}).get("slotIndex")
        new_slot = None if new_slot is None else int(new_slot)
        old_slot = before["slot"]
        if old_slot == new_slot:
            return {"ok": True, "objective_changed": False, "applied_translation_um": None, "message": None}
        if old_slot is None or new_slot is None:
            return _failed(
                f"the objective may have changed (slot {old_slot!r} -> {new_slot!r}) but could not be "
                "identified on both sides, so the position was NOT compensated; re-check the objective "
                "and position before acquiring",
                changed=None,
            )
        # The arithmetic of the offset lives in one place, the calibration
        # model, shared with the frame arithmetic, so the two never drift apart.
        from ..driver_setup.optical_calibration import model as _cal_model

        try:
            delta = _cal_model.translation_delta_um(before["translations"], old_slot, new_slot)
        except RuntimeError as exc:
            return _failed(
                f"the change swapped the objective but {exc}; the stage was NOT moved and still points "
                "at the old lens's spot; compensate afterwards or reposition manually."
            )
        if delta != (0.0, 0.0, 0.0):
            log.info(
                "the objective changed (slot %s -> %s); moving by the calibrated translation "
                "(%+.2f, %+.2f, %+.2f) um to keep the sample point",
                old_slot, new_slot, *delta,
            )
            # The moves go through the ordinary gated changes, so the
            # compensated targets are checked against the machine limits
            # exactly like any other motion; a refusal fails the change.
            xy_result = changes.move_xy(client, before["x_um"] + delta[0], before["y_um"] + delta[1])
            if not xy_result.get("success") or xy_result.get("confirmed") is False:
                return _failed(
                    f"the compensating XY move after the objective change failed: "
                    f"{xy_result.get('message', xy_result)}"
                )
            z_result = changes.move_z(client, "z-wide", before["z_wide_um"] + delta[2])
            if not z_result.get("success") or z_result.get("confirmed") is False:
                return _failed(
                    f"the compensating z-wide move after the objective change failed: "
                    f"{z_result.get('message', z_result)}"
                )
        return {"ok": True, "objective_changed": True, "applied_translation_um": list(delta), "message": None}
    except Exception as exc:  # noqa: BLE001 -- the change already fired; report, never mask
        return _failed(f"objective compensation failed after the change: {exc}", changed=None)


def merge_into_result(result: dict, compensation: dict | None) -> dict:
    """Fold a compensation report into a command result.

    A failed compensation fails the whole result: the change itself went
    through, but leaving ``success: True`` would invite acquiring at a
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
        result["message"] = f"{prefix} | {compensation['message']}" if prefix else compensation["message"]
    return result
