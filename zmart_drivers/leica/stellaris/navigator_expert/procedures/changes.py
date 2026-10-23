"""The named changes of a LAS X session: move the stage, change the objective or a setting, select a job, take a picture.

Each one checks its arguments, builds its change as plain data
(``primitives/cam_api/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Each hands ``run()``
LAS X's dialogs (``answer_dialogs.py``), answered around every send. Then it
adds what the caller needs to the result (``move_xy`` adds the target
position). A change refused before anything was sent answers the same shape,
with ``success`` False.

``select_job`` first finds out which job is selected, so that nothing is sent
when the target already is, and which read order confirms the change.
``acquire`` sends the capture once, then watches the scan run
(:func:`confirm_acquire`); it hands the write what opens the experiment the
picture goes into (``clear_cache/data_container.experiment_for_capture``),
which the direct route asks for. ``take_picture`` is what the rest of the
driver calls. ``move_z`` hands its read-back the saved experiment, which
confirms the drive carrying the job's z-stack when the hardware server cannot
be reached.

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
from ..primitives.cam_api import writes
from ..primitives.cam_api.profile import _check_api_error, _is_transient_error
from ..primitives.cam_api.parsing import _hw_get
from ..primitives.direct import profile as direct_profile
from ..primitives.direct import reads as direct_reader
from ..primitives.direct import state_tree
from ..primitives.direct.client import HardwareServerUnavailable
from ..primitives.log import reads as log_reader
from ..primitives.log.profile import MAX_AGE_S
from . import answer_dialogs
from . import readings as readers
from .clear_cache.data_container import experiment_for_capture
from .save_data.product import AcquisitionResult

log = logging.getLogger(__name__)


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
    result = dispatch.run(direct_profile, client, change, max_retries=max_retries, dialogs=answer_dialogs)
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
        change = writes.move_z(job_name, z, unit, z_mode, tolerance=tolerance, saved=readers.saved_z(client, job_name))
    except TypeError as e:
        return dispatch.refused(str(e))
    return dispatch.run(direct_profile, client, change, max_retries=max_retries, dialogs=answer_dialogs)


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
    return dispatch.run(
        direct_profile,
        client,
        writes.set_objective(job_name, slot, target_name),
        max_retries=max_retries,
        dialogs=answer_dialogs,
    )


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
    return dispatch.run(direct_profile, client, write, max_retries=max_retries, dialogs=answer_dialogs)


# =============================================================================
# Selecting a job
# =============================================================================


def select_job(client, job_name, *, before_fire=None):
    """Select the job ``job_name``, without moving the stage.

    Nothing is sent when the job is already selected. ``before_fire`` is
    called with what :func:`prepare_select_job` found (``api_baseline_name``
    is the job selected before) just before the change is handed to the
    dispatcher; it may return a refusal, and then nothing is sent. The one
    that also keeps the sample in view is ``procedures/keep_in_view.py``.
    """
    t0 = time.perf_counter()
    noop, context = prepare_select_job(client, job_name)
    if noop is not None:
        noop["timing"] = dispatch.make_timing(total_s=time.perf_counter() - t0, attempts=0)
        return noop
    if before_fire is not None:
        refusal = before_fire(context)
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=time.perf_counter() - t0, attempts=0))
            return refusal
    profile = direct_profile.STATE_READERS
    change = writes.select_job(
        job_name,
        mode=admissible(profile.selected_job_mode, context, job_name),
        fallback=admissible(profile.selected_job_confirm_fallback, context, job_name),
    )
    result = dispatch.run(direct_profile, client, change, dialogs=answer_dialogs)
    if context["api_said_selected"]:
        result.setdefault("logs", []).append(
            make_log_entry(
                "info",
                "API reported target already selected before SelectJob, "
                "but the log-participating confirmation still fired the command",
            )
        )
    return result


def admissible(family, context, job_name):
    """The sources that may confirm this job change, of the *family* the profile names.

    The profile names the read order of the ``selected_job`` reading: the
    direct reader first (``selected_job_mode``), and the family that answers
    when it cannot (``selected_job_confirm_fallback``). ``hybrid`` asks the
    API's job list, then the log; but the API's list can be stale, so it may
    confirm only a change it can have seen happen: when its answer from before
    the change (``context["api_baseline_name"]``) is missing, or already the
    target (the A to B to A case), ``hybrid`` becomes ``log``, and the log
    alone confirms. Every other family is as named.
    """
    if family == "hybrid" and context["api_baseline_name"] in (None, job_name):
        return "log"
    return family


def selected_job_direct():
    """The selected job's name from the direct reader; None when it is not asked or not reachable.

    It is asked when the profile's ``selected_job_mode`` is ``direct``.
    It reads LAS X's live selection, so unlike the CAM API's job list it
    cannot be stale, and unlike the log it answers without a new event.
    """
    if direct_profile.STATE_READERS.selected_job_mode != "direct":
        return None
    try:
        selected = direct_reader.get_selected_job()
    except HardwareServerUnavailable as why:
        log.debug("selected job: %s", why)
        return None
    except Exception as why:
        log.warning("the direct reader failed on the selected job (%s: %s)", type(why).__name__, why)
        return None
    return selected["Name"] if selected else None


def api_jobs(client):
    """The job list from the API, by the one rule; ``(jobs, reason)``."""
    reading = readers.get_jobs(client, mode="api", diagnostics=True)
    if reading is None:
        return None, "api_timeout"
    if reading.error is not None:
        return None, f"api_error:{type(reading.error).__name__}"
    if not reading.value:
        return None, "api_no_jobs"
    return reading.value, "ok"


def api_baseline(client):
    """The job the API says is selected, with the job list and why; ``(name, jobs, reason)``."""
    jobs, reason = api_jobs(client)
    if not jobs:
        return None, None, reason
    selected = next((job.get("Name") for job in jobs if job.get("IsSelected")), None)
    return selected, jobs, "ok" if selected else "api_no_selected_job"


def prime_log_cluster(client, jobs):
    """Ask the API for every job's settings, so that LAS X writes the whole job cluster to the log.

    Only when the profile's ``selected_job_log_prime_cluster`` says so; best
    effort. It gives the log something to read; whether the job changed is
    still decided by what the log writes after the change.
    """
    if not direct_profile.STATE_READERS.selected_job_log_prime_cluster:
        return
    for job in jobs or []:
        name = job.get("Name") if isinstance(job, dict) else None
        if not name:
            continue
        try:
            readers.get_job_settings(client, name, mode="api")
        except Exception:
            log.debug("Could not prime log job cluster for %r", name, exc_info=True)


def selected_job_from_log():
    """The selected job's name from a fresh LAS X log, or None when there is none."""
    try:
        max_age_s = direct_profile.STATE_READERS.selected_job_log_cluster_max_age_s
        if max_age_s is None:
            max_age_s = MAX_AGE_S["selected_job"]
        selected = log_reader.get_selected_job(max_age_s=max_age_s)
    except Exception:
        log.debug("Could not read selected job from LAS X log", exc_info=True)
        return None
    return selected.get("Name") if selected else None


def already_selected(job_name, logs):
    """The result of a job change that needs no sending: the job is selected already."""
    return {"success": True, "confirmed": True, "message": f"'{job_name}' already selected", "logs": logs}


def prepare_select_job(client, job_name):
    """What is known before a job change: whether it is needed, and the job selected before.

    Returns ``(noop_result, context)``. ``noop_result`` is a result (without
    timing) when the target is already selected, else None. ``context``
    carries ``api_baseline_name``, the job selected before (which
    :func:`admissible` needs), and ``api_said_selected``, the API
    naming the target while the log decides.

    The direct reader reads the live selection, so when it answers it alone
    decides. Otherwise the family that confirms decides (the profile's
    ``selected_job_mode``, or its fallback when that is ``direct``): with
    ``api``, the API's job list; with ``log`` or ``hybrid``, only a fresh log
    line, because a re-selection of the selected job writes no new event, and
    a stale API list naming the target must never stop a change that is
    needed.
    """
    profile = direct_profile.STATE_READERS
    fallback = profile.selected_job_mode
    if fallback == "direct":
        fallback = profile.selected_job_confirm_fallback
    context = {"api_baseline_name": None, "api_baseline_reason": "not_attempted", "api_said_selected": False}

    selected = selected_job_direct()
    if selected == job_name:
        logs = [make_log_entry("info", "selected job already confirmed by the direct reader")]
        return already_selected(job_name, logs), context
    if selected is not None:
        context["api_baseline_name"] = selected
        context["api_baseline_reason"] = "direct"
        return None, context

    if fallback == "api":
        try:
            jobs = readers.get_jobs(client, mode="api")
        except Exception:
            log.debug("Could not check current job selection before select_job", exc_info=True)
            return None, context
        context["api_baseline_name"] = next((j.get("Name") for j in jobs or [] if j.get("IsSelected")), None)
        if context["api_baseline_name"] == job_name:
            return already_selected(job_name, []), context
        return None, context

    if selected_job_from_log() == job_name:
        logs = [make_log_entry("info", "selected job already confirmed from LAS X log")]
        return already_selected(job_name, logs), context
    if fallback == "log":
        try:
            if profile.selected_job_log_prime_cluster:
                jobs, reason = api_jobs(client)
                if jobs is not None:
                    prime_log_cluster(client, jobs)
                else:
                    log.debug("Could not enumerate jobs before log priming: %s", reason)
        except Exception:
            log.debug("Could not prime jobs before log-backed select_job", exc_info=True)
        return None, context
    try:
        name, jobs, reason = api_baseline(client)
        context["api_baseline_name"] = name
        context["api_baseline_reason"] = reason
        if jobs is not None:
            prime_log_cluster(client, jobs)
    except Exception:
        log.debug("Could not enumerate/prime jobs before select_job", exc_info=True)
    if context["api_baseline_name"] == job_name:
        context["api_said_selected"] = True
    return None, context


# =============================================================================
# Taking a picture
# =============================================================================

#: How many idle scan-status reads in a row prove an acquisition has finished
#: (a single idle read can fall between two frames). A rule, not a tuning.
ACQUIRE_IDLE_STREAK_REQUIRED = 2


def acquire(client, job_name, poll_interval=None, poll_timeout=None, heartbeat_interval=None, start_timeout=None):
    """Run the job once and wait until the scan has finished.

    The capture is sent once (``writes.acquire``), then the scan is watched
    (:func:`confirm_acquire`; the arguments given override its defaults, and
    ``poll_timeout`` is its ``timeout``). A capture whose scan never starts
    fails: ``success`` False, and the message says so. One that started but
    was not seen to finish is a success with ``confirmed`` False, like any
    unconfirmed change.

    Args:
        client: LAS X API client.
        job_name: The job to acquire.
        poll_interval: Seconds between scan-status reads.
        poll_timeout: Longest wait for the scan to finish, in seconds; None
            waits as long as it takes.
        heartbeat_interval: Seconds between log lines during a long scan.
        start_timeout: Seconds to wait for the scan to start.
    """
    watching = {
        name: value
        for name, value in (
            ("poll_interval", poll_interval),
            ("timeout", poll_timeout),
            ("heartbeat_interval", heartbeat_interval),
            ("start_timeout", start_timeout),
        )
        if value is not None
    }
    change = writes.acquire(job_name, experiment=experiment_for_capture)
    image_before = picture_number()
    handed_over = time.time()
    result = dispatch.run(direct_profile, client, change, dialogs=answer_dialogs)
    if not result["success"]:
        return result

    t0 = time.perf_counter()
    watched = confirm_acquire(client, image_before=image_before, **watching)
    watch_s = time.perf_counter() - t0
    result["logs"].extend(watched["logs"])
    result["timing"].update(confirm_s=watch_s, total_s=result["timing"]["total_s"] + watch_s, confirm_attempts=1)
    result["confirmed"] = watched["success"]
    if watched["success"]:
        return result
    dialog = write_loops.append_dialog_warning(
        direct_profile.WRITING.transport, result["logs"], handed_over, change.description
    )
    if watched["started"] is False:
        result["success"] = False
        result["message"] = f"{change.description} | the scan never started{dialog}"
        return result
    msg = (
        f"{change.description} | UNCONFIRMED: the scan started, but was not seen to finish"
        if watched["started"]
        else f"{change.description} | UNCONFIRMED: the scan was not seen, and without the "
        "hardware server it is not known whether it ran"
    )
    log.warning(msg)
    result["logs"].append(make_log_entry("warning", msg))
    result["message"] = f"{change.description} (readback unconfirmed){dialog}"
    return result


def take_picture(client: Any, job: str, **acquire_options) -> AcquisitionResult:
    """Acquire the job and answer when it ran; RuntimeError when it was not sent or its scan never started."""
    started_at = time.time()
    result = acquire(client, job, **acquire_options)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(f"acquire failed: {result}")
    return AcquisitionResult(job=job, started_at=started_at, finished_at=finished_at, command_result=result)


def picture_number():
    """The number of the last picture LAS X made, from the hardware server; None when it cannot be read."""
    try:
        return state_tree.last_image_id()
    except Exception:  # noqa: BLE001 -- unknown is an answer: the watch then cannot tell
        return None


def confirm_acquire(
    client, *, image_before=None, start_timeout=15.0, heartbeat_interval=30.0, timeout=None, poll_interval=0.1
):
    """Watch the scan status until the acquisition has run; reads only, never sends.

    First the scan must be seen running within *start_timeout*; a permanent
    LAS X error before then ends the watch at once. Then
    ``ACQUIRE_IDLE_STREAK_REQUIRED`` idle reads in a row show it has
    finished. The status is asked as the idle check asks: the direct reader
    when the profile reads the status directly (about 1 ms a reading), else,
    and when it fails, the API alone, never the log, which writes the status
    only when it changes. Only readings taken after the watch began count.

    A scan shorter than one polling gap can start and finish unseen. A new
    picture in LAS X (its number differs from *image_before*) shows that it
    ran. So the scan never started only when the status never left idle and
    the picture number did not change; without the number (*image_before*
    None, or the hardware server not answering) whether it ran is unknown.

    Args:
        client: The connected LAS X API client.
        start_timeout: Seconds to wait for the scan to start.
        heartbeat_interval: Seconds between log lines during a long scan.
        timeout: The longest watch in seconds; None waits as long as it takes.
        poll_interval: Seconds between scan-status reads.

    Returns:
        ``{"success": bool, "started": bool | None, "logs": [...]}``;
        ``started`` is True when the scan was seen running or made a new
        picture, False when it certainly did not start, None when unknown.
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
        reading = readers.get_scan_status(
            client,
            mode=direct_profile.on_demand_mode("scan_status"),
            fallback=direct_profile.on_demand_fallback(),
            diagnostics=True,
            deadline=time.monotonic() + poll_interval,
        )
        fresh = reading is not None and reading.observed_at is not None and reading.observed_at > observed_after
        status = reading.value if fresh else None
        elapsed = time.perf_counter() - t_start

        if status is None or status == "Unknown":
            # A failed or stale read is evidence of neither scanning nor idle:
            # it must not arm the second phase, and it breaks the idle streak.
            consecutive_idle = 0
        elif "Idle" not in status:
            saw_scanning = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        if not saw_scanning and image_before is not None and picture_number() not in (None, image_before):
            saw_scanning = True  # finished between two status reads: its picture is there

        if not saw_scanning:
            err = _check_api_error(client)
            if err is not None:
                error_msg = err.get("error", "?")
                if not _is_transient_error(error_msg):
                    msg = f"Acquire error (permanent): {error_msg}"
                    log.error(msg)
                    logs.append(make_log_entry("error", msg))
                    return watched(False)
                log.debug("Transient error during acquire start: %s", error_msg)

            if time.perf_counter() > start_deadline:
                msg = (
                    f"Scan was never observed non-idle within {start_timeout:.0f}s "
                    f"({elapsed:.0f}s total) — either it did not start, or it "
                    f"finished between two status polls"
                )
                log.warning(msg)
                logs.append(make_log_entry("warning", msg))
                known = image_before is not None and picture_number() is not None
                return {"success": False, "started": False if known else None, "logs": logs}

        now = time.perf_counter()
        if now - last_heartbeat > heartbeat_interval:
            msg = f"Scanning: {status or 'Unknown'}, {elapsed:.0f}s elapsed"
            log.info(msg)
            logs.append(make_log_entry("info", msg))
            last_heartbeat = now

        if consecutive_idle >= ACQUIRE_IDLE_STREAK_REQUIRED and saw_scanning:
            return watched(True)

        time.sleep(poll_interval)

    msg = f"Acquisition timeout after {time.perf_counter() - t_start:.1f}s"
    log.warning(msg)
    logs.append(make_log_entry("warning", msg))
    return watched(False)
