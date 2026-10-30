"""The named changes of a session: move the stage, change a setting or the objective, take a picture, take a tile scan.

Each one checks its arguments, builds its change as plain data
(``primitives/direct/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Each hands ``run()``
LAS X's dialogs (``answer_dialogs.py``), answered around every send. Then it
adds what the caller needs to the result (``move_xy`` adds the target
position). A change refused before anything was sent answers the same
shape, with ``success`` False.

A capture and a tile scan are sent once, then the scan is watched
(:func:`confirm_scan`): it must be seen running, then idle twice in a row.
Each names the LAS X project its picture goes into, chosen by
``clear_cache/data_container.experiment_for_capture`` as the Navigator
Expert driver chooses it: the STELLARIS refused a tile scan sent into project
``0`` (2026-10-15). ``take_picture`` and ``take_tile_scan`` are what the
rest of the driver calls.

Result contract: ``result["success"]`` means the change was *accepted*, not
that it took effect. A caller that needs proof the change landed checks
``result["confirmed"]``. The one exception is a capture whose scan never
starts: it fails.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..dispatcher import dispatch
from ..dispatcher import write as write_loops
from ..dispatcher.dispatch import make_log_entry
from ..primitives.direct import profile as direct_profile
from ..primitives.direct import writes
from . import answer_dialogs
from . import readings as readers
from .clear_cache import data_container
from .save_data.product import AcquisitionResult

log = logging.getLogger(__name__)


def run(client, change, *, max_retries=None):
    """One change through the dispatcher, with LAS X's dialogs answered around every send."""
    return dispatch.run(direct_profile, client, change, max_retries=max_retries, dialogs=answer_dialogs)


# =============================================================================
# The stage and the z drives
# =============================================================================


def move_xy(client, x_um, y_um, *, max_retries=None, tolerance=None):
    """Move the stage to (*x_um*, *y_um*), in micrometres of the stage's own coordinates.

    The result's ``position`` is the target (micrometres and metres), not a
    read-back: ``confirmed`` says whether the stage was seen there. It is
    None when nothing was sent.
    """
    try:
        change = writes.move_xy(float(x_um), float(y_um), tolerance=tolerance)
    except (TypeError, ValueError) as why:
        return dispatch.refused(f"a stage move takes two numbers: {why}", position=None)
    result = run(client, change, max_retries=max_retries)
    sent = result["timing"].get("attempts", 0) > 0
    result["position"] = (
        {"x": change.values["x_um"] * 1e-6, "y": change.values["y_um"] * 1e-6, **change.values}
        if sent
        else None
    )
    return result


def move_z(client, drive, z_um, *, max_retries=None, tolerance=None):
    """Move the z drive *drive* (``"z-galvo"`` or ``"z-wide"``) to *z_um*, in micrometres.

    LAS X's plain Navigator moves z by a step, so the step is worked out
    from where the drive stands just before every send (``writes.move_z``).
    """
    try:
        change = writes.move_z(drive, float(z_um), tolerance=tolerance)
    except (TypeError, ValueError) as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


# =============================================================================
# The settings and the objective
# =============================================================================


def change(client, name: str, value: float, *, max_retries=None, tolerance=None) -> dict:
    """Change the setting *name* (``writes.SETTINGS``: zoom, scan_speed, ...) to *value*, and read it back.

    A wrong name or a value that is not a number raises ``ValueError``. The
    limits gate is shown the new value first, and nothing is sent if it
    refuses. *tolerance* overrides how close the read-back must come.
    """
    return run(client, writes.setting(name, value, tolerance=tolerance), max_retries=max_retries)


def objective_by_slot(objectives) -> dict[int, dict[str, Any]]:
    """``{slot: objective}`` for every occupied slot of the turret (``readings.get_objectives``).

    Empty slots (``objectiveNumber`` 0) are left out: sending one to LAS X
    can open an error dialog that blocks it.
    """
    by_slot = {}
    for objective in objectives or []:
        if not objective.get("objectiveNumber"):
            continue
        slot = objective.get("slotIndex")
        if slot is not None:
            by_slot[int(slot)] = objective
    return by_slot


def find_objective(objectives, slot_index=None, name=None, magnification=None):
    """``(slot, name)`` of the objective with this slot, name or magnification; ``(None, None)`` if none."""
    for objective in objective_by_slot(objectives).values():
        slot = objective.get("slotIndex")
        found = (objective.get("name") or "").strip()
        magnified = objective.get("magnification")
        if slot_index is not None and slot == slot_index:
            return slot, found
        if name is not None and found == name.strip():
            return slot, found
        if magnification is not None and magnified == magnification:
            return slot, found
    return None, None


def set_objective(
    client, objectives, slot_index=None, name=None, magnification=None, *, max_retries=None, before_fire=None
):
    """Change the objective, by its slot, name or magnification (exactly one), without moving the stage.

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes *before_fire* to take its
    reading just before the change is sent. *before_fire* is called with
    ``{"slot", "name"}``; it may return a refusal, and then nothing is sent.
    """
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return dispatch.refused(
            f"exactly one of slot_index, name, or magnification must be provided (got {len(selectors)})"
        )
    slot, target_name = find_objective(objectives, slot_index=slot_index, name=name, magnification=magnification)
    if slot is None:
        available = [(o.get("slotIndex"), (o.get("name") or "").strip()) for o in objective_by_slot(objectives).values()]
        return dispatch.refused(
            f"Could not find objective: slot_index={slot_index}, name={name}, mag={magnification}. "
            f"Available: {available}"
        )
    if before_fire is not None:
        refusal = before_fire({"slot": slot, "name": target_name})
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return run(client, writes.set_objective(slot, target_name), max_retries=max_retries)


# =============================================================================
# Taking a picture
# =============================================================================

#: How many idle scan-status reads in a row prove a scan has finished (a
#: single idle read can fall between two frames). A rule, not a tuning.
IDLE_STREAK_REQUIRED = 2


def picture(client, change, **watching) -> dict:
    """Send *change* (a capture or a tile scan) once, then watch the scan run; the change's result.

    A picture whose scan never starts fails: ``success`` False, and the
    message says so. One that started but was not seen to finish is a
    success with ``confirmed`` False, like any unconfirmed change.
    """
    handed_over = time.time()
    result = run(client, change)
    if not result["success"]:
        return result

    t0 = time.perf_counter()
    watched = confirm_scan(client, **{k: v for k, v in watching.items() if v is not None})
    watch_s = time.perf_counter() - t0
    result["logs"].extend(watched["logs"])
    result["timing"].update(confirm_s=watch_s, total_s=result["timing"]["total_s"] + watch_s, confirm_attempts=1)
    result["confirmed"] = watched["success"]
    if watched["success"]:
        return result
    dialog = write_loops.append_dialog_warning(
        direct_profile.WRITING.transport, result["logs"], handed_over, change.description
    )
    if not watched["started"]:
        result["success"] = False
        result["message"] = f"{change.description} | the scan never started{dialog}"
        return result
    msg = f"{change.description} | UNCONFIRMED: the scan started, but was not seen to finish"
    log.warning(msg)
    result["logs"].append(make_log_entry("warning", msg))
    result["message"] = f"{change.description} (readback unconfirmed){dialog}"
    return result


def capture(client, project=None, **watching) -> dict:
    """Take one picture with the current settings; see :func:`picture`.

    *project* is the LAS X project the picture goes into; None chooses one
    as the Navigator Expert driver does (an open project, or a new
    ``ZMART_Experiment<N>``).
    """
    try:
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.capture(into)
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"no LAS X project for the picture: {why}", logged=True)
    return picture(client, change, **watching)


def tile_scan(client, positions_um, project=None, **watching) -> dict:
    """Take a tile scan over *positions_um* (x, y, z per field, micrometres); see :func:`picture`."""
    try:
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.tile_scan(positions_um, into)
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the tile scan cannot be made: {why}", logged=True)
    return picture(client, change, **watching)


def after_tile_scan(client, x_um, y_um, z_um, *, max_retries=None) -> dict:
    """Put back what a tile scan changed: the stage to (*x_um*, *y_um*, *z_um*), scan mode xyz, autofocus on."""
    return run(client, writes.after_tile_scan(float(x_um), float(y_um), float(z_um)), max_retries=max_retries)


def take_picture(client: Any, project=None, **watching) -> AcquisitionResult:
    """Take a capture and answer when it ran; RuntimeError when it was not sent or its scan never started."""
    started_at = time.time()
    result = capture(client, project, **watching)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(f"the capture failed: {result.get('message') if result else result}")
    return AcquisitionResult(picture="capture", started_at=started_at, finished_at=finished_at, command_result=result)


def take_tile_scan(client: Any, positions_um, project=None, **watching) -> AcquisitionResult:
    """Take a tile scan and answer when it ran, then put back what it changed; RuntimeError when it failed.

    The put-back (``after_tile_scan``) returns the stage to the first
    position; a put-back that could not be confirmed is noted in the
    result's ``command_result["put_back"]``, and the caller decides. It is
    sent whenever the tile scan was sent, also when its scan never started:
    LAS X may have taken the preparation (the scan mode xyt, say) before it
    refused to start.
    """
    positions = [tuple(float(v) for v in p) for p in positions_um]
    started_at = time.time()
    result = tile_scan(client, positions, project, **watching)
    finished_at = time.time()
    if result.get("timing", {}).get("attempts", 0) > 0:
        result["put_back"] = after_tile_scan(client, *positions[0])
    if not result.get("success"):
        put_back = result.get("put_back")
        undone = "" if put_back is None else f"; put back: {put_back.get('message')}"
        raise RuntimeError(f"the tile scan failed: {result.get('message')}{undone}")
    return AcquisitionResult(picture="tile_scan", started_at=started_at, finished_at=finished_at, command_result=result)


def confirm_scan(client, *, start_timeout=15.0, heartbeat_interval=30.0, timeout=None, poll_interval=0.1):
    """Watch the scan status until the picture has been taken; reads only, never sends.

    First the scan must be seen running within *start_timeout*. Then
    ``IDLE_STREAK_REQUIRED`` idle reads in a row show it has finished. Only
    readings taken after the watch began count. A scan never seen running
    within *start_timeout* did not start, and the picture fails.

    Returns ``{"success": bool, "started": bool, "logs": [...]}``.
    """
    logs = []
    observed_after = time.time()
    t_start = time.perf_counter()
    last_heartbeat = t_start
    saw_scanning = False
    consecutive_idle = 0
    start_deadline = t_start + start_timeout
    deadline = t_start + (timeout if timeout is not None else 1e9)

    def watched(success):
        return {"success": success, "started": saw_scanning, "logs": logs}

    while time.perf_counter() < deadline:
        reading = readers.get_scan_status(client, diagnostics=True, deadline=time.monotonic() + poll_interval)
        fresh = reading is not None and reading.observed_at is not None and reading.observed_at > observed_after
        status = reading.value if fresh else None
        elapsed = time.perf_counter() - t_start

        if status is None or status == "Unknown":
            # A failed or stale read is evidence of neither scanning nor idle.
            consecutive_idle = 0
        elif "Idle" not in status:
            saw_scanning = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        if not saw_scanning and time.perf_counter() > start_deadline:
            msg = (
                f"the scan was never seen running within {start_timeout:.0f}s "
                f"({elapsed:.0f}s in all): either it did not start, or it finished between two looks"
            )
            log.warning(msg)
            logs.append(make_log_entry("warning", msg))
            return watched(False)

        now = time.perf_counter()
        if now - last_heartbeat > heartbeat_interval:
            msg = f"Scanning: {status or 'Unknown'}, {elapsed:.0f}s elapsed"
            log.info(msg)
            logs.append(make_log_entry("info", msg))
            last_heartbeat = now

        if consecutive_idle >= IDLE_STREAK_REQUIRED and saw_scanning:
            return watched(True)

        time.sleep(poll_interval)

    msg = f"the scan was not seen to finish within {time.perf_counter() - t_start:.1f}s"
    log.warning(msg)
    logs.append(make_log_entry("warning", msg))
    return watched(False)
