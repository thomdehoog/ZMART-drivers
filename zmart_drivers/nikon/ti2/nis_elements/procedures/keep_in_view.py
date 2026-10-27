"""Changing the objective without losing the sample: the change, and the stage shift after it.

Each objective looks at a slightly different spot and a slightly different
focal plane. So when the nosepiece turns, the feature that was in the middle
of the picture is no longer in the middle, or in focus. This procedure
records where the stage stands before the change, turns the nosepiece, and
then moves the stage and the focus drive by the measured offset between the
two objectives, so the same point of the sample stays in view.

The offset comes from the objective calibration the session loaded
(``driver_setup/optical_calibration``). The order matters, and the refusal
comes first: when the calibration does not cover the pair, the change is
refused before the nosepiece turns, so the objective never ends up looking
at the wrong spot. A session that declined the calibration
(``load_calibration=False``) turns the nosepiece and moves nothing, and the
result says so.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch
from ..driver_setup.optical_calibration import model as _calibration
from . import absolute_coordinate_system as _frame
from . import changes

log = logging.getLogger(__name__)


def set_objective(handle, slot: int) -> dict:
    """Turn the nosepiece to *slot* and move the stage by the calibrated offset, so the sample stays in view.

    Answers the change's result, with ``compensation``: the move made after
    the change (``None`` when none was needed or the session declined the
    calibration). A change whose compensating move failed answers
    ``success`` False and says so: a picture must not be taken where the
    objective no longer points at the sample.
    """
    handle.require_open()
    recorded: dict = {}

    def plan(target_slot):
        # Everything that can refuse is decided here, before anything is sent.
        try:
            before = _frame.hardware_snapshot(handle)
        except RuntimeError as why:
            return dispatch.refused(
                f"refusing the objective change: the stage could not be read first ({why})"
            )
        current = before["objective"]
        if current is None:
            return dispatch.refused(
                "refusing the objective change: NIS-Elements reports no nosepiece"
            )
        if current["slot"] == target_slot:
            recorded["unchanged"] = True
            return None
        if handle.translations is None:
            recorded["delta"] = (
                None  # the session declined the calibration: a bare change
            )
        else:
            try:
                recorded["delta"] = _calibration.translation_delta_um(
                    handle.translations, current["slot"], target_slot
                )
            except RuntimeError as why:
                return dispatch.refused(f"refusing the objective change: {why}")
        recorded["before"] = before
        return None

    result = changes.set_objective(handle.client, slot, before_fire=plan)
    result["compensation"] = None
    if not result.get("success") or recorded.get("unchanged"):
        return result
    delta = recorded.get("delta")
    if delta is None:
        log.warning(
            "objective changed to slot %s without compensation: the calibration was not loaded",
            slot,
        )
        result["compensation"] = {
            "made": False,
            "why": "the session declined the objective calibration",
        }
        return result
    before = recorded["before"]
    target = (
        before["x_um"] + delta[0],
        before["y_um"] + delta[1],
        before["z_um"] + delta[2],
    )
    moved = changes.move_xyz(handle.client, *target)
    result["compensation"] = {
        "made": moved.get("success", False),
        "delta_um": list(delta),
        "move": moved,
    }
    if not moved.get("success"):
        result["success"] = False
        result["message"] = (
            f"the objective changed to slot {slot}, but the move that keeps the sample in view was "
            f"refused or failed: {moved.get('message')}"
        )
    elif not moved.get("confirmed"):
        result["confirmed"] = False
        result["message"] = (
            f"{result.get('message')} (the compensating move was not confirmed)"
        )
    return result
