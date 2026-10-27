"""Changing the objective without losing the sample: the change, and the stage shift after it.

Each objective looks at a slightly different spot and focuses at a slightly
different height. Before a change of objective this recipe records where the
stage and the focus are; after it, it moves them by the measured difference
between the two objectives (the calibration of ``driver_setup/optical_calibration``),
so the same point of the sample stays in view. The correction is made only
when the change was accepted; when it cannot be made, the answer says so and
``success`` is False, so a picture is never taken at a position that no
longer points at the sample.

The order matters: the position is read while it still means "where the old
objective was looking", then the objective turns, then the stage follows.
A later frame move to the same coordinates computes the identical target,
because the frame arithmetic uses the same translation table relative to the
origin's objective, so nothing is ever compensated twice.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch
from ..dispatcher.dispatch import make_log_entry
from ..driver_setup.optical_calibration.model import translation_delta_um
from . import changes, readings

log = logging.getLogger(__name__)


def set_objective(
    client,
    objectives,
    *,
    slot=None,
    name=None,
    translations=None,
    compensate=None,
    max_retries=None,
) -> dict:
    """Turn the nosepiece to the objective with this slot or name, and keep the sample point.

    *translations* is the calibration this session loaded (``{slot: (x, y, z)}``).
    *compensate* None follows it: the stage is corrected when there are
    translations, and left alone when the session has none (None: it declined
    the calibration). ``True`` requires the correction and refuses the change
    when the pair is not calibrated; ``False`` asks for a bare change.
    """
    recorded = {}

    def record_position(chosen):
        recorded["slot"] = chosen["slot"]
        try:
            recorded["before"] = record_before_change(
                client, translations=translations, compensate=compensate
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed before the change is sent
            return dispatch.refused(
                f"refusing the objective change: could not record the position before it ({exc})"
            )
        if recorded["before"] is not None:
            from_slot, to_slot = recorded["before"]["slot"], chosen["slot"]
            if from_slot != to_slot:
                try:
                    translation_delta_um(translations, from_slot, to_slot)
                except RuntimeError as exc:
                    return dispatch.refused(f"refusing the objective change: {exc}")
        return None

    result = changes.set_objective(
        client,
        objectives,
        slot=slot,
        name=name,
        max_retries=max_retries,
        before_fire=record_position,
    )
    if not result.get("success") or recorded.get("before") is None:
        return result
    return compensate_after_change(
        client, recorded["before"], recorded["slot"], translations, result
    )


def record_before_change(client, *, translations, compensate):
    """Where the stage and focus stand, and which objective is in, before the change; None when no correction is wanted.

    With *compensate* None, a correction is wanted when the session holds
    translations (an empty table means it wanted a calibration and has none:
    the correction is still wanted, and the pair check refuses).
    """
    if compensate is False or (compensate is None and translations is None):
        return None
    xy = readings.get_xy(client)
    z = readings.get_z(client)
    objective = readings.get_objective(client)
    if not xy or z is None or not objective:
        raise RuntimeError("the stage, the focus or the objective could not be read")
    return {
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_um": float(z),
        "slot": int(objective["slot"]),
    }


def compensate_after_change(
    client, before: dict, to_slot: int, translations, result: dict
) -> dict:
    """Move the stage by T[to] − T[from] after the objective turned, so the sample point stays in view."""
    from_slot = before["slot"]
    if from_slot == to_slot:
        return result
    dx, dy, dz = translation_delta_um(translations, from_slot, to_slot)
    moved = changes.move_xy(client, before["x_um"] + dx, before["y_um"] + dy)
    result.setdefault("logs", []).extend(moved.get("logs", []))
    if not moved.get("success"):
        result["success"] = False
        result["message"] = (
            f"{result.get('message')} | the objective turned, but the stage correction failed: {moved.get('message')}"
        )
        return result
    focused = changes.move_z(client, before["z_um"] + dz)
    result["logs"].extend(focused.get("logs", []))
    if not focused.get("success"):
        result["success"] = False
        result["message"] = (
            f"{result.get('message')} | the objective turned, but the focus correction failed: {focused.get('message')}"
        )
        return result
    result["compensation_um"] = [dx, dy, dz]
    if not (moved.get("confirmed") and focused.get("confirmed")):
        result["confirmed"] = False
        result["logs"].append(
            make_log_entry(
                "warning",
                "the stage correction after the objective change was not confirmed",
            )
        )
    return result
