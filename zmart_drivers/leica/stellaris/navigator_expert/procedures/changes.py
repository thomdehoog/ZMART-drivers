"""The named changes of a LAS X session: move the stage, change the objective, change a setting.

Each one checks its arguments, builds its change as plain data
(``primitives/cam_api/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Then it adds what the
caller needs to the result (``move_xy`` adds the target position). A change
refused before anything was sent answers the same shape, with ``success``
False.

Result contract: ``result["success"]`` means the change was *accepted*, not
that it took effect. A caller that needs proof the change landed checks
``result["confirmed"]``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import dispatch
from ..primitives.cam_api import writes
from ..primitives.cam_api.parsing import _hw_get
from ..primitives.direct import profile as direct_profile


def move_xy(client, x, y, unit="um", *, max_retries=None, tolerance=None):
    """Move the XY stage to an absolute position.

    Args:
        client: LAS X API client.
        x, y: Target coordinates in ``unit``: 'um', 'mm' or 'm'.
        tolerance: How close the readback must come, in micrometres.

    Returns:
        The change's result; ``position`` is the *target* (in metres and
        micrometres), not a readback: ``confirmed`` says whether the stage
        was seen there. None when nothing was sent.
    """
    if unit not in writes.UM_PER_UNIT:
        return dispatch.refused(f"unknown unit {unit!r} (expected 'um', 'mm', or 'm')", position=None)
    try:
        change = writes.move_xy(x, y, unit, tolerance=tolerance)
    except TypeError as e:
        return dispatch.refused(str(e), position=None)
    result = dispatch.run(direct_profile, client, change, max_retries=max_retries)
    x_um, y_um = change.values["x_um"], change.values["y_um"]
    sent = result["timing"].get("attempts", 0) > 0
    result["position"] = {"x": x_um * 1e-6, "y": y_um * 1e-6, "x_um": x_um, "y_um": y_um} if sent else None
    return result


def move_z(client, job_name, z, unit="um", z_mode="galvo", *, max_retries=None, tolerance=None):
    """Move a z drive, ``galvo`` or ``zwide``, to an absolute position.

    Args:
        client: LAS X API client.
        job_name: The job whose z drive is moved.
        z: Target position in ``unit`` (it can be negative).
        unit: 'um', 'mm' or 'm'.
        z_mode: The drive: 'galvo' or 'zwide'.
        tolerance: How close the readback must come, in micrometres.
    """
    if z_mode not in writes.Z_DRIVE_ENUM:
        return dispatch.refused(f"Unknown z_mode '{z_mode}'. Use: {list(writes.Z_DRIVE_ENUM)}")
    if unit not in writes.UM_PER_UNIT:
        return dispatch.refused(f"unknown unit {unit!r}. Use: {list(writes.UM_PER_UNIT)}")
    try:
        change = writes.move_z(job_name, z, unit, z_mode, tolerance=tolerance)
    except TypeError as e:
        return dispatch.refused(str(e))
    return dispatch.run(direct_profile, client, change, max_retries=max_retries)


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

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes ``before_fire`` to take its
    reading just before the change is sent. The limits are asked once the
    objective is found, with its slot, whichever way it was chosen.
    ``before_fire`` is called with ``{"slot", "name"}``; it may return a
    refusal, and then nothing is sent.
    """
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return dispatch.refused(
            f"exactly one of slot_index, name, or magnification must be provided (got {len(selectors)})"
        )
    slot, target_name = find_objective(hw_info, slot_index=slot_index, name=name, magnification=magnification)
    if slot is None:
        available = [(_hw_get(o, "slotIndex"), _hw_get(o, "name", "").strip()) for o in objective_by_slot(hw_info).values()]
        return dispatch.refused(
            f"Could not find objective: slot_index={slot_index}, name={name}, mag={magnification}. Available: {available}"
        )
    if before_fire is not None:
        refusal = before_fire({"slot": slot, "name": target_name})
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return dispatch.run(direct_profile, client, writes.set_objective(job_name, slot, target_name), max_retries=max_retries)


def change(client, name: str, job_name: str, *, max_retries=None, tolerance=None, **given) -> dict:
    """Change the setting ``name`` of the job, and read it back.

    The keywords are what the setting takes (``writes.SETTINGS[name].takes``),
    such as ``value=2.0`` for the zoom; a wrong name or argument raises
    ValueError. The limits gate is shown the new value first, and nothing is
    sent if it refuses. ``tolerance`` overrides how close the readback must
    come.
    """
    write = writes.change(name, job_name, tolerance=tolerance, **given)
    if isinstance(write, str):
        return dispatch.refused(write)
    return dispatch.run(direct_profile, client, write, max_retries=max_retries)
