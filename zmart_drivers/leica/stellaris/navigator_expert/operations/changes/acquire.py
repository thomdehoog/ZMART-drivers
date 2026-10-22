"""Taking a picture: ``acquire``, its profile and its confirmation, and ``take_picture``.

``acquire`` is the change: it asks LAS X to run the job once and watches the
scan status until it has run. It is never sent twice: a second acquire would
be a second picture. ``take_picture`` is what the rest of the driver calls: it
acquires and answers when and what was taken (``AcquisitionResult``), or
raises when the acquisition failed. Neither knows how files are saved.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any

from ...dispatcher.dispatch import make_log_entry
from ...procedures.save_data.product import AcquisitionResult
from ...procedures import readings as readers
from ...primitives.cam_api.profile import CommandProfile
from ...primitives.cam_api.error_classification import _check_api_error, _is_transient_error
from ...primitives.cam_api.prechecks import check_idle
from . import confirming, run_change

log = logging.getLogger(__name__)

#: How many idle scan-status reads in a row prove an acquisition has finished
#: (a single idle read can fall between two frames). A rule, not a tuning.
ACQUIRE_IDLE_STREAK_REQUIRED = 2

ACQUIRE = CommandProfile(
    pre_check_fn=check_idle,
    error_check_fn=None,
    # The one change that is never sent again: a second send is a second
    # acquisition. It declines both ways of sending again (max_retries=0 and
    # refire_on_unconfirmed=False), and the dispatcher holds a never_resend
    # change to these values whatever is asked. Unconfirmed is still reported,
    # not failed; saving checks that the picture is new.
    never_resend=True,
    max_retries=0,
    max_confirm_attempts=1,
    refire_on_unconfirmed=False,
    poll_interval=0.1,
    poll_timeout=None,
    start_timeout=15.0,
    heartbeat_interval=30.0,
    skip_reply_check=True,
    send_without_receipt=True,
)


def acquire(client, job_name, poll_interval=None, poll_timeout=None, heartbeat_interval=None, start_timeout=None):
    """Run the job once and wait until the scan has finished.

    If LAS X never reports scanning within ``start_timeout``, the result is a
    failure; the acquire is not sent again.

    Args:
        client: LAS X API client.
        job_name: The job to acquire.
        poll_interval: Seconds between scan-status reads.
        poll_timeout: Longest wait for the scan to finish, in seconds; None
            waits as long as it takes.
        heartbeat_interval: Seconds between log lines during a long scan.
        start_timeout: Seconds to wait for the scan to start.
    """
    gated_values = {"job_name": job_name}

    def setup(m):
        m.JobName = job_name

    def given(value, name):
        return getattr(ACQUIRE, name) if value is None else value

    return run_change.dispatch_change(
        client,
        "PyApiAcquireJob",
        f"Acquire '{job_name}'",
        ACQUIRE,
        command="acquire",
        values=gated_values,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_acquire,
            start_timeout=given(start_timeout, "start_timeout"),
            heartbeat_interval=given(heartbeat_interval, "heartbeat_interval"),
            timeout=given(poll_timeout, "poll_timeout"),
            poll_interval=given(poll_interval, "poll_interval"),
        ),
    )


def take_picture(client: Any, job: str, **acquire_options) -> AcquisitionResult:
    """Acquire the job and answer when it ran; RuntimeError when it did not."""
    started_at = time.time()
    result = acquire(client, job, **acquire_options)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(f"acquire failed: {result}")
    return AcquisitionResult(job=job, started_at=started_at, finished_at=finished_at, command_result=result)


def confirm_acquire(
    client,
    *,
    start_timeout=15.0,
    heartbeat_interval=30.0,
    timeout=None,
    poll_interval=0.1,
):
    """Poll until acquisition completes, or return False if scan never starts.

    Pure status-polling function: reads only, never fires. When the scan
    has not started within *start_timeout*, returns ``{"success": False}``.
    Acquisition profiles fire the command once and treat that as a
    failed acquisition, not as permission to send another acquire command.

    Known limitation: detection is level-based polling at *poll_interval*
    (each poll also costs an API round trip), so an acquisition shorter
    than one polling gap can start and finish unobserved — reported here
    as a failure even though data was acquired. ``save()``'s freshness
    check is the backstop that recovers the data in that case.

    Phase 1 — wait up to *start_timeout* for scan to go non-idle.
              Returns False immediately on permanent error.
              Returns False if scan hasn't started.
    Phase 2 — wait for consecutive idle reads to confirm completion.

    Args:
        client: The connected LAS X API client.
        start_timeout: Seconds to wait for scan to start before
            returning acquisition failure.
        heartbeat_interval: Seconds between heartbeat log messages
            during long scans.
        timeout: Hard ceiling in seconds. None means wait indefinitely.
        poll_interval: Seconds between scan status polls.

    Returns:
        {"success": bool, "logs": [...]}
    """
    logs = []
    observed_after = time.time()
    t_start = time.perf_counter()
    last_heartbeat = t_start
    saw_scanning = False
    consecutive_idle = 0

    start_deadline = t_start + start_timeout
    deadline = t_start + (timeout if timeout is not None else 1e9)

    while time.perf_counter() < deadline:
        # Asked as the idle check asks: the direct reader when the profile
        # reads the status directly (about 1 ms a reading, so a short scan is
        # less likely to fall between two), else, and when it fails, the API
        # alone, never the log, which writes the status only when it changes.
        status = confirming.reading_value_after(
            readers.get_scan_status(
                client,
                mode=readers.on_demand_mode("scan_status"),
                fallback=readers.on_demand_fallback(),
                diagnostics=True,
                deadline=time.monotonic() + poll_interval,
            ),
            observed_after,
        )
        elapsed = time.perf_counter() - t_start

        if status is None or status == "Unknown":
            # Failed or stale status read (the API reader reports "Unknown"
            # on failure): evidence of neither scanning nor idle. It must
            # not set saw_scanning — that would arm phase 2 and skip both
            # the permanent-error check and the start timeout, confirming
            # an acquisition that may never have run. It also breaks the
            # idle streak, so completion needs consecutive observed reads.
            consecutive_idle = 0
        elif "Idle" not in status:
            saw_scanning = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        # Phase 1: check for permanent errors before scan starts
        if not saw_scanning:
            err = _check_api_error(client)
            if err is not None:
                error_msg = err.get("error", "?")
                if not _is_transient_error(error_msg):
                    msg = f"Acquire error (permanent): {error_msg}"
                    log.error(msg)
                    logs.append(make_log_entry("error", msg))
                    return {"success": False, "logs": logs}
                log.debug("Transient error during acquire start: %s", error_msg)

            # Start timeout: return acquisition failure. The acquire
            # profile does not re-fire acquisition commands.
            if time.perf_counter() > start_deadline:
                msg = (
                    f"Scan was never observed non-idle within {start_timeout:.0f}s "
                    f"({elapsed:.0f}s total) — either it did not start, or it "
                    f"finished between two status polls"
                )
                log.warning(msg)
                logs.append(make_log_entry("warning", msg))
                return {"success": False, "logs": logs}

        # Heartbeat for long scans
        now = time.perf_counter()
        if now - last_heartbeat > heartbeat_interval:
            msg = f"Scanning: {status or 'Unknown'}, {elapsed:.0f}s elapsed"
            log.info(msg)
            logs.append(make_log_entry("info", msg))
            last_heartbeat = now

        # Phase 2: completion — consecutive idle reads after saw scanning
        if consecutive_idle >= ACQUIRE_IDLE_STREAK_REQUIRED and saw_scanning:
            return {"success": True, "logs": logs}

        time.sleep(poll_interval)

    msg = f"Acquisition timeout after {time.perf_counter() - t_start:.1f}s"
    log.warning(msg)
    logs.append(make_log_entry("warning", msg))
    return {"success": False, "logs": logs}
