"""The confirm loop: check that the change took effect, and send again if it did not.

This is the second loop of a change, and the one that joins the two. It sends
the command (the send loop, ``send_loop.py``), then reads the microscope until the
state reaches the target (the reader). If it does not within a window, it sends
the command again and re-checks, up to ``max_confirm_attempts`` windows (four
windows of three seconds by the shared waiting rule).

Changing a setting, or moving to an absolute position, can safely be sent
again. Starting an acquisition cannot: it would take a second set of data. Its
profile says ``never_resend``, so it is sent once, and the loop only watches
whether the scanner is still scanning, for as long as the scan takes.

When every window is used up without the state being reached, the change is
reported as sent but unconfirmed, not as failed: the driver did its best and
records what it could not confirm. A readback that shows a different value is
a failure.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import time

from .dispatcher import make_log_entry as _make_log_entry
from .dispatcher import make_timing as _make_timing
from .send_loop import (
    append_dialog_warning as _append_dialog_warning,
    confirmation_detail as _confirmation_detail,
    fire_block as _fire_block,
    resolve_profile as _resolve_profile,
)

log = logging.getLogger(__name__)


def confirm_and_fire(
    client,
    request,
    description,
    *,
    profile=None,
    setup_fn=None,
    pre_check_fn=None,
    error_check_fn=None,
    confirm_fn=None,
    max_retries=None,
    max_confirm_attempts=None,
    refire_on_unconfirmed=None,
    retry_backoff=None,
    retry_escalate=None,
    skip_reply_check=None,
    send_without_receipt=None,
    success_on_unconfirmed=None,
):
    """Fire a command and optionally confirm the result, with correction.

    This is the single entry point through which all commands are
    dispatched. It calls ``_fire_block`` to execute steps 1-4, then
    runs ``confirm_fn`` to verify the result. If confirmation fails,
    the wrapper can run corrective actions and re-attempt.

    Confirmation strategy (when confirm_fn returns failure):
        1. If ``refire_on_unconfirmed`` is False, retry readback only.
           The command is not sent again.
        2. Otherwise, run the built-in idle correction (pre_check_fn),
           then re-fire.
        3. Re-confirm after each retry. All confirmation attempts count
           against ``max_confirm_attempts``.

    Args:
        client: The connected microscope client.
        request: The request to send, in the form the transport expects.
        description: Human-readable label for logging.
        setup_fn: Callable that writes the parameters into the request.
        pre_check_fn: Zero-arg callable -> result dict. None to skip.
        error_check_fn: Zero-arg callable -> error result dict. None
            skips the check (the default check is applied at profile
            level, not here).
        confirm_fn: Zero-arg callable -> result dict. None to skip
            confirmation entirely.
        max_retries: Transient error retries inside the fire block.
        max_confirm_attempts: How many times the confirm wrapper can
            re-run the fire-then-confirm cycle.
        refire_on_unconfirmed: If True, a failed confirmation sends the
            command again before the next confirmation attempt. If False,
            the command is not fired again; only readback is retried.
        retry_backoff: Base delay in seconds between transient error
            retries. None for immediate retry. Passed to ``_fire_block``.
        retry_escalate: If True, use exponential backoff (delay doubles
            each retry). Passed to ``_fire_block``.
        success_on_unconfirmed: If True, return success=True when all
            confirmation attempts are exhausted (every profile currently
            sets True — deliberate, see ``CommandProfile``). This means
            ``success=True`` alone does NOT prove the setting took
            effect: callers that need proof must check ``confirmed``.

    Returns:
        {
            "success": bool,
            "confirmed": bool | None,
            "message": str,
            "timing": dict,
            "logs": [...],
        }

        ``confirmed`` is True if confirm_fn succeeded, False if it
        failed, None if confirm_fn was not provided or the command
        failed before reaching confirmation.
    """
    profile = _resolve_profile(
        profile,
        max_retries=max_retries,
        max_confirm_attempts=max_confirm_attempts,
        refire_on_unconfirmed=refire_on_unconfirmed,
        retry_backoff=retry_backoff,
        retry_escalate=retry_escalate,
        skip_reply_check=skip_reply_check,
        send_without_receipt=send_without_receipt,
        success_on_unconfirmed=success_on_unconfirmed,
    )
    command_started_at = time.time()
    t_wall_start = time.perf_counter()
    t_confirm_total = 0.0
    all_logs = []
    total_attempts = 0
    confirm_attempts = 0

    # --- Fire block (first attempt) ---
    fb = _fire_block(
        client,
        request,
        description,
        profile=profile,
        setup_fn=setup_fn,
        pre_check_fn=pre_check_fn,
        error_check_fn=error_check_fn,
    )
    all_logs.extend(fb["logs"])
    total_attempts += fb["attempts"]
    fb_timing = fb["timing"]

    if not fb["success"]:
        dialog_detail = _append_dialog_warning(
            profile.transport, all_logs, command_started_at, description
        )
        return {
            "success": False,
            "confirmed": None,
            "message": f"{fb['message']}{dialog_detail}",
            "timing": _make_timing(
                pre_check_s=fb_timing["pre_check_s"],
                setup_s=fb_timing["setup_s"],
                fire_s=fb_timing["fire_s"],
                check_s=fb_timing["check_s"],
                total_s=time.perf_counter() - t_wall_start,
                attempts=total_attempts,
                confirm_attempts=0,
                method="async",
            ),
            "logs": all_logs,
        }

    # --- No confirm_fn: step 5 skipped ---
    if confirm_fn is None:
        log.info(
            "%s | OK (%.3fs) attempts=%d",
            description,
            time.perf_counter() - t_wall_start,
            total_attempts,
        )
        return {
            "success": True,
            "confirmed": None,
            "message": description,
            "timing": _make_timing(
                pre_check_s=fb_timing["pre_check_s"],
                setup_s=fb_timing["setup_s"],
                fire_s=fb_timing["fire_s"],
                check_s=fb_timing["check_s"],
                total_s=time.perf_counter() - t_wall_start,
                attempts=total_attempts,
                confirm_attempts=0,
                method="async",
            ),
            "logs": all_logs,
        }

    # --- Confirm wrapper loop ---
    # Accumulate timing from all fire block calls across confirm attempts
    acc_pre = fb_timing["pre_check_s"]
    acc_setup = fb_timing["setup_s"]
    acc_fire = fb_timing["fire_s"]
    acc_check = fb_timing["check_s"]
    last_confirm_result = None

    for ca in range(profile.max_confirm_attempts):
        confirm_attempts = ca + 1

        # Run confirm_fn
        t0 = time.perf_counter()
        try:
            conf_result = confirm_fn()
        except Exception as e:
            msg = f"{description} | Confirm exception: {e}"
            log.warning(msg)
            all_logs.append(_make_log_entry("warning", msg))
            conf_result = {"success": False, "logs": []}
        last_confirm_result = conf_result
        t_confirm_total += time.perf_counter() - t0
        all_logs.extend(conf_result.get("logs", []))

        if conf_result["success"]:
            log.info(
                "%s | OK (%.3fs) attempts=%d confirm_attempts=%d",
                description,
                time.perf_counter() - t_wall_start,
                total_attempts,
                confirm_attempts,
            )
            return {
                "success": True,
                "confirmed": True,
                "message": description,
                "timing": _make_timing(
                    pre_check_s=acc_pre,
                    setup_s=acc_setup,
                    fire_s=acc_fire,
                    check_s=acc_check,
                    confirm_s=t_confirm_total,
                    total_s=time.perf_counter() - t_wall_start,
                    attempts=total_attempts,
                    confirm_attempts=confirm_attempts,
                    method="async",
                ),
                "logs": all_logs,
            }

        # Confirmation failed - attempt correction if not last attempt
        if ca < profile.max_confirm_attempts - 1:
            if not profile.refire_on_unconfirmed:
                msg = (
                    f"{description} | Readback unconfirmed; retrying "
                    f"readback only "
                    f"(attempt {confirm_attempts + 1}/{profile.max_confirm_attempts})"
                )
                log.info(msg)
                all_logs.append(_make_log_entry("info", msg))
                continue

            # Built-in idle correction: wait for idle, then re-fire
            if pre_check_fn is not None:
                t0 = time.perf_counter()
                idle_result = pre_check_fn()
                acc_pre += time.perf_counter() - t0
                all_logs.extend(idle_result.get("logs", []))

            # Re-fire after correction
            log.info(
                "%s | Confirm failed, re-firing (attempt %d/%d)",
                description,
                confirm_attempts + 1,
                profile.max_confirm_attempts,
            )
            fb = _fire_block(
                client,
                request,
                description,
                profile=profile,
                setup_fn=setup_fn,
                pre_check_fn=None,  # Already waited for idle above
                error_check_fn=error_check_fn,
            )
            all_logs.extend(fb["logs"])
            total_attempts += fb["attempts"]
            acc_setup += fb["timing"]["setup_s"]
            acc_fire += fb["timing"]["fire_s"]
            acc_check += fb["timing"]["check_s"]

            if not fb["success"]:
                # Re-fire failed - give up
                return {
                    "success": False,
                    "confirmed": False,
                    "message": (
                        f"{fb['message']}"
                        f"{_append_dialog_warning(profile.transport, all_logs, command_started_at, description)}"
                    ),
                    "timing": _make_timing(
                        pre_check_s=acc_pre,
                        setup_s=acc_setup,
                        fire_s=acc_fire,
                        check_s=acc_check,
                        confirm_s=t_confirm_total,
                        total_s=time.perf_counter() - t_wall_start,
                        attempts=total_attempts,
                        confirm_attempts=confirm_attempts,
                        method="async",
                    ),
                    "logs": all_logs,
                }

    # All confirm attempts exhausted.
    msg = (
        f"{description} | UNCONFIRMED after {confirm_attempts} "
        f"readback attempt(s); command was sent successfully, but the "
        f"microscope's state readback did not confirm the requested value"
        f"{_confirmation_detail(last_confirm_result)}"
    )
    dialog_detail = _append_dialog_warning(
        profile.transport, all_logs, command_started_at, description
    )
    log.warning("%s%s (%.3fs)", msg, dialog_detail, time.perf_counter() - t_wall_start)
    all_logs.append(_make_log_entry("warning", msg))
    return {
        "success": profile.success_on_unconfirmed,
        "confirmed": False,
        "message": f"{description} (readback unconfirmed){dialog_detail}",
        "timing": _make_timing(
            pre_check_s=acc_pre,
            setup_s=acc_setup,
            fire_s=acc_fire,
            check_s=acc_check,
            confirm_s=t_confirm_total,
            total_s=time.perf_counter() - t_wall_start,
            attempts=total_attempts,
            confirm_attempts=confirm_attempts,
            method="async",
        ),
        "logs": all_logs,
    }
