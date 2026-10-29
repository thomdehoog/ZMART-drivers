"""Changing the objective without losing the sample: the change, and the stage shift after it.

Each objective looks at a slightly different spot and focal plane. Before a
change, this recipe records where the stage and the focus are. After it, it
moves them by the measured difference between the two objectives, so the
same point of the sample stays in view. The measured offsets come from the
objective calibration (``driver_setup/optical_calibration``); the arithmetic
is explained in ``absolute_coordinate_system``.

The correction is made only when the change was accepted. When it cannot be
made, the command says so and names the step, so a picture is never taken at
a position that no longer points at the sample. The bare change that only
turns the changer is ``changes.set_objective``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch
from . import changes
from . import readings as _readers
from .absolute_coordinate_system import delta_between_objectives_um, hardware_snapshot

log = logging.getLogger(__name__)


def set_objective(handle, *, position=None, name=None, magnification=None) -> dict:
    """Change the objective and move the stage so the same sample point stays under the lens.

    The shift is T[new] − T[old], from the calibration. A pair the
    calibration does not cover is refused before anything changes, with
    how to calibrate it; the bare change is still there for a deliberate
    uncorrected change. An objective that is already in place changes
    nothing. The result is the change's, with ``objective`` (the one chosen)
    and ``correction_um`` (the shift made, or None).
    """
    handle.require_open()
    objectives = _readers.get_objectives(handle.client)
    if objectives is None:
        return dispatch.refused("could not read the objectives on the changer from ZEN")
    before = {}

    def just_before(found):
        """Read where the stage is, and whether the shift can be known, just before the change."""
        snap = hardware_snapshot(handle)
        if snap["objective"].get("position") == found["position"]:
            return {
                "success": True,
                "confirmed": True,
                "message": f"objective {found['position']} is already in place",
                "logs": [],
                "already": True,
            }
        try:
            shift = delta_between_objectives_um(
                {"objective": snap["objective"]}, handle.translations, found
            )
        except RuntimeError as why:
            return dispatch.refused(
                f"the objective change is refused: {why}", logged=True
            )
        before.update(snapshot=snap, shift=shift)
        return None

    result = changes.set_objective(
        handle.client,
        objectives,
        position=position,
        name=name,
        magnification=magnification,
        before_fire=just_before,
    )
    if result.get("already"):
        result["correction_um"] = None
        return result
    if not result.get("success"):
        result["correction_um"] = None
        return result
    if not result.get("confirmed"):
        handle.note_unconfirmed("set_objective", result)
    snap, shift = before["snapshot"], before["shift"]
    target = (snap["x_um"] + shift[0], snap["y_um"] + shift[1], snap["z_um"] + shift[2])
    if any(shift):
        moved = changes.move_xy(handle.client, target[0], target[1])
        if not moved.get("success"):
            result["success"] = False
            result["message"] = (
                f"{result['message']} | the objective changed, but the stage correction failed: {moved.get('message')}"
            )
            result["correction_um"] = None
            return result
        if not moved.get("confirmed"):
            handle.note_unconfirmed("set_objective: the xy correction", moved)
        focused = changes.move_z(handle.client, target[2])
        if not focused.get("success"):
            result["success"] = False
            result["message"] = (
                f"{result['message']} | the objective changed, but the focus correction failed: {focused.get('message')}"
            )
            result["correction_um"] = None
            return result
        if not focused.get("confirmed"):
            handle.note_unconfirmed("set_objective: the focus correction", focused)
    result["correction_um"] = list(shift)
    return result
