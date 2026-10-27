"""The named changes of a FLUOVIEW session: move the stage, change the objective or a setting, take a picture.

Each one checks its arguments, builds its change as plain data
(``primitives/rdk/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Then it adds what
the caller needs to the result. A change refused before anything was sent
answers the same shape, with ``success`` False.

``acquire`` sends the capture once and then watches the scan run
(:func:`confirm_acquire`): FLUOVIEW must be seen scanning and then idle
again, or, for a scan too quick to be caught scanning, must be able to open
the picture it said it was writing. ``take_picture`` is what the rest of the
driver calls.

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
from ..dispatcher.dispatch import make_log_entry
from ..primitives.rdk import image_data, writes
from ..primitives.rdk import profile as rdk_profile
from ..primitives.rdk.reads import MANUAL_MAIN
from . import readings as readers
from .save_data.product import AcquisitionResult

log = logging.getLogger(__name__)


def _run(client, change, **options):
    return dispatch.run(rdk_profile, client, change, **options)


# =============================================================================
# The stage and the focus
# =============================================================================


def move_xy(
    client,
    x_um: float,
    y_um: float,
    *,
    escape: bool = False,
    tolerance_um: float | None = None,
    max_retries=None,
) -> dict:
    """Move the XY stage to an absolute position in micrometres, in FLUOVIEW's own coordinates, and read it back."""
    try:
        change = writes.move_xy(
            float(x_um), float(y_um), escape=escape, tolerance_um=tolerance_um
        )
    except (TypeError, ValueError) as why:
        return dispatch.refused(str(why))
    result = _run(client, change, max_retries=max_retries)
    result["target"] = {"x_um": float(x_um), "y_um": float(y_um)}
    return result


def move_z(
    client, z_um: float, *, tolerance_um: float | None = None, max_retries=None
) -> dict:
    """Move the focus motor to an absolute position in micrometres, in FLUOVIEW's own coordinates, and read it back."""
    try:
        change = writes.move_z(float(z_um), tolerance_um=tolerance_um)
    except (TypeError, ValueError) as why:
        return dispatch.refused(str(why))
    result = _run(client, change, max_retries=max_retries)
    result["target"] = {"z_um": float(z_um)}
    return result


def move_piezo(
    client, z_um: float, *, tolerance_um: float | None = None, max_retries=None
) -> dict:
    """Move the piezo to *z_um* micrometres from FLUOVIEW's Z origin, and read it back."""
    try:
        change = writes.move_piezo(float(z_um), tolerance_um=tolerance_um)
    except (TypeError, ValueError) as why:
        return dispatch.refused(str(why))
    result = _run(client, change, max_retries=max_retries)
    result["target"] = {"z_piezo_um": float(z_um)}
    return result


# =============================================================================
# The objective
# =============================================================================


def find_objective(
    objectives: list | None, slot: int | None = None, name: str | None = None
) -> tuple[int | None, str]:
    """``(slot, name)`` of the objective with this slot or this name among *objectives*; ``(None, "")`` if none."""
    for entry in objectives or []:
        if slot is not None and entry.get("slot") == slot:
            return int(entry["slot"]), str(entry.get("name", ""))
        if name is not None and str(entry.get("name", "")).strip() == name.strip():
            return int(entry["slot"]), str(entry.get("name", ""))
    return None, ""


def set_objective(
    client,
    objectives: list | None,
    slot: int | None = None,
    name: str | None = None,
    *,
    max_retries=None,
    before_fire=None,
) -> dict:
    """Turn the nosepiece to the objective with this slot or this name (exactly one), without moving the stage.

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes ``before_fire`` to take its
    reading just before the change is sent. ``before_fire`` is called with
    ``{"slot", "name"}``; it may return a refusal, and then nothing is sent.
    """
    if (slot is None) == (name is None):
        return dispatch.refused(
            "give exactly one of slot or name to choose the objective"
        )
    found, found_name = find_objective(objectives, slot=slot, name=name)
    if found is None:
        available = [(o.get("slot"), o.get("name")) for o in objectives or []]
        return dispatch.refused(
            f"no objective with slot={slot}, name={name!r}; FLUOVIEW reports {available}"
        )
    if before_fire is not None:
        refusal = before_fire({"slot": found, "name": found_name})
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return _run(
        client, writes.set_objective(found, found_name), max_retries=max_retries
    )


# =============================================================================
# The settings, and FLUOVIEW itself
# =============================================================================


def change(client, name: str, *, max_retries=None, **given) -> dict:
    """Change the setting *name* with the arguments *given* (``writes.SETTINGS``), and read it back.

    A wrong name or argument raises ``ValueError`` before anything is built;
    the limits gate is shown the new value first, and nothing is sent if it
    refuses.
    """
    writes.arguments_of(name, given)
    return _run(client, writes.change(name, **given), max_retries=max_retries)


def set_remote_mode(client, remote: bool = True) -> dict:
    """Put FLUOVIEW in REMOTE mode, in which it shows no pop-up dialogs, or back in LOCAL mode."""
    return _run(client, writes.set_remote_mode(bool(remote)))


def load_parameter(client, image_path: str) -> dict:
    """Load the imaging conditions saved in the picture *image_path*: the FV4000's way of recalling a whole setup."""
    if not isinstance(image_path, str) or not image_path.strip():
        return dispatch.refused(
            "load_parameter needs the path of a picture FLUOVIEW saved"
        )
    return _run(client, writes.load_parameter(image_path.strip()))


def repeat_scan(client, start: bool) -> dict:
    """Start or stop FLUOVIEW's live, repeating scan."""
    return _run(client, writes.repeat_scan(bool(start)))


def stop(client, execution_type: str = MANUAL_MAIN) -> dict:
    """Stop the imaging that is running, and see FLUOVIEW go idle."""
    return _run(client, writes.stop(execution_type))


# =============================================================================
# Taking a picture
# =============================================================================

#: How many idle readings in a row prove the scan has finished (one can fall
#: between two time points of a series, where FLUOVIEW says RESTING).
ACQUIRE_IDLE_STREAK_REQUIRED = 2


def acquire(
    client,
    execution_type: str = MANUAL_MAIN,
    *,
    poll_interval=None,
    poll_timeout=None,
    heartbeat_interval=None,
    start_timeout=None,
) -> dict:
    """Start the imaging set up in FLUOVIEW, once, and wait until the scan has finished.

    The capture is sent once (``writes.acquire``), then the scan is watched
    (:func:`confirm_acquire`). The result carries ``target``, the path of
    the picture FLUOVIEW is writing, and ``planned_s``, how long FLUOVIEW
    expects the scan to take. A capture whose scan never starts fails:
    ``success`` False, and the message says so. One that started but was not
    seen to finish is a success with ``confirmed`` False, like any
    unconfirmed change.
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
    change = writes.acquire(execution_type)
    result = _run(client, change)
    reply = client.last_reply or {}
    targets = [
        entry.get("name")
        for entry in reply.get("targetName") or []
        if isinstance(entry, dict)
    ]
    result["target"] = next((t for t in targets if t), None)
    result["planned_s"] = reply.get("wait")
    if not result["success"]:
        return result

    t0 = time.perf_counter()
    watched = confirm_acquire(
        client, target=result["target"], execution_type=execution_type, **watching
    )
    watch_s = time.perf_counter() - t0
    result["logs"].extend(watched["logs"])
    result["timing"].update(
        confirm_s=watch_s,
        total_s=result["timing"]["total_s"] + watch_s,
        confirm_attempts=1,
    )
    result["confirmed"] = watched["success"]
    result["progress"] = watched.get("progress")
    if watched["success"]:
        return result
    if not watched["started"]:
        result["success"] = False
        result["message"] = f"{change.description} | the scan never started"
        return result
    msg = f"{change.description} | UNCONFIRMED: the scan started, but was not seen to finish"
    log.warning(msg)
    result["logs"].append(make_log_entry("warning", msg))
    result["message"] = f"{change.description} (readback unconfirmed)"
    return result


def take_picture(
    client: Any, execution_type: str = MANUAL_MAIN, **acquire_options
) -> AcquisitionResult:
    """Take the picture and answer when it ran; ``RuntimeError`` when it was not sent or its scan never started."""
    started_at = time.time()
    result = acquire(client, execution_type, **acquire_options)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(
            f"acquire failed: {result.get('message') if result else result}"
        )
    if not result.get("target"):
        raise RuntimeError(
            "acquire ran, but FLUOVIEW did not say which file it wrote the picture to"
        )
    return AcquisitionResult(
        target=result["target"],
        execution_type=execution_type,
        started_at=started_at,
        finished_at=finished_at,
        command_result=result,
        progress=result.get("progress"),
    )


def confirm_acquire(
    client,
    *,
    target: str | None,
    execution_type: str = MANUAL_MAIN,
    start_timeout: float = 15.0,
    heartbeat_interval: float = 30.0,
    timeout: float | None = None,
    poll_interval: float = 0.25,
) -> dict:
    """Watch FLUOVIEW until the scan has run; reads only, never sends.

    The scan must be seen running (``SCANNING``, ``RESTING`` between time
    points, or ``WAITING``) within *start_timeout*, then idle
    ``ACQUIRE_IDLE_STREAK_REQUIRED`` readings in a row. A scan quicker than
    the polls is caught another way: when FLUOVIEW is idle and can already
    open the picture it named (*target*), the capture ran. Only readings
    taken after the watch began count.

    Answers ``{"success", "started", "logs", "progress"}``; ``progress`` is
    the last progress record read.
    """
    logs = []
    observed_after = time.time()
    t_start = time.perf_counter()
    last_heartbeat = t_start
    saw_scanning = False
    consecutive_idle = 0
    last_progress = None
    start_deadline = t_start + start_timeout
    deadline = t_start + (timeout if timeout is not None else 1e9)

    def watched(success):
        return {
            "success": success,
            "started": saw_scanning or success,
            "logs": logs,
            "progress": last_progress,
        }

    while time.perf_counter() < deadline:
        reading = readers.get_progress(
            client,
            execution_type,
            diagnostics=True,
            deadline=time.monotonic() + poll_interval,
        )
        fresh = (
            reading is not None
            and reading.observed_at is not None
            and reading.observed_at > observed_after
        )
        progress = reading.value if fresh and reading.error is None else None
        state = progress.get("state") if isinstance(progress, dict) else None
        elapsed = time.perf_counter() - t_start
        if progress is not None:
            last_progress = progress

        if state is None:
            consecutive_idle = 0
        elif state != "IDLING":
            saw_scanning = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        if (
            not saw_scanning
            and state == "IDLING"
            and target
            and image_data.can_open(client, target)
        ):
            msg = f"the scan finished before it was seen running; FLUOVIEW already opens {target}"
            logs.append(make_log_entry("info", msg))
            return watched(True)

        if not saw_scanning and time.perf_counter() > start_deadline:
            msg = f"the scan was never seen running within {start_timeout:.0f}s ({elapsed:.0f}s in all)"
            log.warning(msg)
            logs.append(make_log_entry("warning", msg))
            return watched(False)

        now = time.perf_counter()
        if now - last_heartbeat > heartbeat_interval:
            msg = f"scanning: {state or 'unknown'}, {elapsed:.0f}s elapsed"
            log.info(msg)
            logs.append(make_log_entry("info", msg))
            last_heartbeat = now

        if consecutive_idle >= ACQUIRE_IDLE_STREAK_REQUIRED and saw_scanning:
            return watched(True)
        time.sleep(poll_interval)

    msg = f"acquisition timeout after {time.perf_counter() - t_start:.1f}s"
    log.warning(msg)
    logs.append(make_log_entry("warning", msg))
    return watched(False)
