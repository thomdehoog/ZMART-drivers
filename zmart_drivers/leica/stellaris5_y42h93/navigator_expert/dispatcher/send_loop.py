"""The send loop: send a command to the microscope, and check that it accepted it.

This is the first of the two loops of a change. It runs four steps:

1. **Pre-check**: ask the microscope whether it is ready (for example, idle).
2. **Setup**: have the transport write the command's values into the request.
3. **Fire**: send the request. How a request is sent and its reply awaited is
   the business of the command's ``profile.transport`` (for LAS X,
   ``vendor_specific/primitive_handling/profiles/transport.py``); this file only asks
   it to fill in the request, clear the reply, send, wait for the reply and
   report an open dialog.
4. **Error check**: read what the microscope answered. A temporary problem is
   sent again, up to ``max_retries`` times, with an optional pause that can
   grow. A permanent problem stops the loop at once.

A command that must reach the microscope only once, such as starting an
acquisition, has ``max_retries`` of 0 in its profile and is never sent again.

The loop is deliberately dumb. It owns the order of the steps, the retry limit
and the timing. It does not know what a zoom, an objective or a stage is; it
calls functions it is handed and acts on the dictionaries they return. It does
not poll for the microscope's state: that is the second loop, in
``confirm_loop.py``.

Commands are sent one at a time. A microscope may answer every request in one
shared reply, which is cleared, sent and read without a lock, so two threads
sending on one client would trample each other's reply.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import time

from .door import make_log_entry as _make_log_entry

log = logging.getLogger(__name__)


def confirmation_detail(result):
    """Return compact non-log readback details from a confirmation result."""
    if not isinstance(result, dict):
        return ""
    details = {
        key: value for key, value in result.items() if key not in {"success", "logs"}
    }
    if not details:
        return ""
    return f"; last_confirmation={details!r}"


def append_dialog_warning(transport, logs, command_started_at, description):
    msg = transport.dialog_warning_since(command_started_at, description)
    if msg is None:
        return ""
    log.warning(msg)
    logs.append(_make_log_entry("warning", msg))
    return f"; dialog={msg!r}"


def resolve_profile(profile, **overrides):
    """Apply explicit overrides to a command's profile; the microscope hands the profile in."""
    from dataclasses import replace

    if profile is None:
        raise ValueError("a command profile must be handed in: the dispatcher has no default of its own")
    changes = {name: value for name, value in overrides.items() if value is not None}
    return replace(profile, **changes) if changes else profile


def fire_block(
    client,
    request,
    description,
    *,
    profile=None,
    setup_fn=None,
    pre_check_fn=None,
    error_check_fn=None,
    max_retries=None,
    retry_backoff=None,
    retry_escalate=None,
    skip_reply_check=None,
    send_without_receipt=None,
):
    """Execute the four-step fire pipeline with transient retry.

    Steps:
        1. Pre-check - call ``pre_check_fn()`` (zero-arg, returns result dict).
        2. Setup - the transport calls ``setup_fn`` on the request to write parameters.
        3. Fire - clear the reply, send the request through the transport.
        4. Error check - call ``error_check_fn()`` (zero-arg, returns result dict).

    Steps 1-4 repeat on transient errors, up to ``max_retries`` + 1
    total attempts. Pre-check functions own their own polling internally.
    The fire block never knows what it is checking.

    Args:
        client: The connected microscope client.
        request: The request to send, in whatever form the transport expects
            (the dispatcher never looks inside it).
        description: Human-readable label for logging.
        setup_fn: Callable that writes the parameters; the transport decides
            what it is called with. None to skip setup.
            None to skip setup.
        pre_check_fn: Zero-arg callable -> result dict with "success" and
            "logs". None to skip step 1.
        error_check_fn: Zero-arg callable -> result dict with "success",
            "error", "transient", and "logs". None skips the check (the
            default check is applied at profile level, not here).
        max_retries: Max retries after the first attempt. Total attempts =
            max_retries + 1.
        retry_backoff: Base delay in seconds between transient error retries.
            None for immediate retry. First retry is always immediate;
            subsequent retries use the backoff delay.
        retry_escalate: If True, double the delay after each retry
            (exponential backoff: 0s, base, 2×base, 4×base, ...).
            If False, use a fixed delay. Ignored when retry_backoff is None.
        skip_reply_check: If True, skip the check of the microscope's reply entirely (the
            confirm_fn is then the authoritative completion signal).
        send_without_receipt: If True, send without waiting for the microscope to
            confirm receipt.

    Returns:
        {
            "success": bool,
            "message": str,
            "timing": {pre_check_s, setup_s, fire_s, check_s},
            "attempts": int,
            "logs": [...],
        }
    """
    profile = resolve_profile(
        profile,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        retry_escalate=retry_escalate,
        skip_reply_check=skip_reply_check,
        send_without_receipt=send_without_receipt,
    )
    transport = profile.transport
    t_pre_check = 0.0
    t_setup = 0.0
    t_fire = 0.0
    t_check = 0.0
    attempts = 0
    all_logs = []

    for attempt in range(profile.max_retries + 1):
        attempts = attempt + 1

        # --- Step 1: Pre-check ---
        if pre_check_fn is not None:
            t0 = time.perf_counter()
            try:
                result = pre_check_fn()
            except Exception as e:
                # Same contract as steps 2/3: a raising check becomes a
                # structured failure, never an exception out of a set_* call.
                msg = f"{description} | Pre-check raised: {type(e).__name__}: {e}"
                log.error(msg)
                result = {"success": False, "logs": [_make_log_entry("error", msg)]}
            t_pre_check += time.perf_counter() - t0
            all_logs.extend(result.get("logs", []))

            if not result["success"]:
                return {
                    "success": False,
                    "message": f"{description} | Pre-check failed",
                    "timing": {
                        "pre_check_s": t_pre_check,
                        "setup_s": t_setup,
                        "fire_s": t_fire,
                        "check_s": t_check,
                    },
                    "attempts": attempts,
                    "logs": all_logs,
                }

        # --- Step 2: Setup ---
        t0 = time.perf_counter()
        try:
            if setup_fn is not None:
                transport.fill(request, setup_fn)
        except Exception as e:
            t_setup += time.perf_counter() - t0
            msg = f"{description} | Setup exception: {e}"
            log.error(msg)
            all_logs.append(_make_log_entry("error", msg))
            return {
                "success": False,
                "message": msg,
                "timing": {
                    "pre_check_s": t_pre_check,
                    "setup_s": t_setup,
                    "fire_s": t_fire,
                    "check_s": t_check,
                },
                "attempts": attempts,
                "logs": all_logs,
            }
        t_setup += time.perf_counter() - t0

        # --- Step 3: Fire + await the reply ---
        t0 = time.perf_counter()
        try:
            transport.clear_reply(client)
            delivered, answered = transport.send(
                client, request, description, asynchronous=profile.send_without_receipt
            )
            for message in answered:
                all_logs.append(_make_log_entry("warning", message))
            if delivered and not profile.skip_reply_check and not profile.send_without_receipt:
                if not transport.await_reply(client):
                    # The reply never settled: a rejection arriving after this
                    # window is invisible to the error check below (it reads a
                    # still-cleared reply). Only the readback confirmation can
                    # catch it — leave a trace.
                    msg = f"{description} | The reply did not settle; the error check may miss a late rejection"
                    log.warning(msg)
                    all_logs.append(_make_log_entry("warning", msg))
        except Exception as e:
            t_fire += time.perf_counter() - t0
            msg = f"{description} | Fire exception: {e}"
            log.error(msg)
            all_logs.append(_make_log_entry("error", msg))
            return {
                "success": False,
                "message": msg,
                "timing": {
                    "pre_check_s": t_pre_check,
                    "setup_s": t_setup,
                    "fire_s": t_fire,
                    "check_s": t_check,
                },
                "attempts": attempts,
                "logs": all_logs,
            }
        t_fire += time.perf_counter() - t0

        if not delivered:
            msg = (
                f"{description} | Transport failure: "
                f"not received after {profile.waiting.WINDOWS} tries of {profile.waiting.WINDOW_S}s"
            )
            log.error(msg)
            all_logs.append(_make_log_entry("error", msg))
            return {
                "success": False,
                "message": msg,
                "timing": {
                    "pre_check_s": t_pre_check,
                    "setup_s": t_setup,
                    "fire_s": t_fire,
                    "check_s": t_check,
                },
                "attempts": attempts,
                "logs": all_logs,
            }

        # --- Step 4: Error check ---
        t0 = time.perf_counter()
        if error_check_fn is not None:
            try:
                err_result = error_check_fn()
            except Exception as e:
                # A raising check (a transient fault reading the reply) must
                # not escape as an exception; classify it transient so the
                # normal retry path decides.
                msg = f"{description} | Error check raised: {type(e).__name__}: {e}"
                log.warning(msg)
                err_result = {
                    "success": False,
                    "error": msg,
                    "transient": True,
                    "logs": [_make_log_entry("warning", msg)],
                }
        else:
            err_result = {"success": True, "logs": []}
        t_check += time.perf_counter() - t0
        all_logs.extend(err_result.get("logs", []))

        if err_result["success"]:
            # No error - the send succeeded
            break

        error_msg = err_result.get("error", "")
        # The vendor's own error check says whether sending again is worthwhile.
        transient = err_result.get("transient", False)

        if transient and attempt < profile.max_retries:
            # Backoff: first retry is immediate, then base × 2^(attempt-1)
            if profile.retry_backoff is not None and attempt > 0:
                delay = (
                    profile.retry_backoff * (2 ** (attempt - 1))
                    if profile.retry_escalate
                    else profile.retry_backoff
                )
                log.warning(
                    "%s | Transient error (attempt %d/%d), retrying in %.1fs: %s",
                    description,
                    attempts,
                    profile.max_retries + 1,
                    delay,
                    error_msg,
                )
                time.sleep(delay)
            else:
                log.warning(
                    "%s | Transient error (attempt %d/%d): %s",
                    description,
                    attempts,
                    profile.max_retries + 1,
                    error_msg,
                )
            continue

        # Permanent error, or transient but out of retries
        if transient:
            log.error(
                "%s | Transient error exhausted retries: %s", description, error_msg
            )
        else:
            log.error("%s | Permanent error: %s", description, error_msg)

        return {
            "success": False,
            "message": f"{description} | Error: {error_msg}",
            "timing": {
                "pre_check_s": t_pre_check,
                "setup_s": t_setup,
                "fire_s": t_fire,
                "check_s": t_check,
            },
            "attempts": attempts,
            "logs": all_logs,
        }

    # Fire block succeeded
    return {
        "success": True,
        "message": description,
        "timing": {
            "pre_check_s": t_pre_check,
            "setup_s": t_setup,
            "fire_s": t_fire,
            "check_s": t_check,
        },
        "attempts": attempts,
        "logs": all_logs,
    }
