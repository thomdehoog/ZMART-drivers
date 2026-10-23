"""The write loops: the send loop, the confirm loop, and the chain of ways to send.

This is the first of the two loops of a change. It runs four steps:

1. **Pre-check**: ask the microscope whether it is ready (for example, idle).
2. **Setup**: have the transport write the command's values into the request.
3. **Fire**: send the request. How a request is sent and its reply awaited is
   the business of the command's ``profile.transport`` (for LAS X,
   ``primitives/direct/profile.py``, which may hold more than
   one way of sending, tried in order); this file only asks
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
the confirm loop below.

Commands are sent one at a time. A microscope may answer every request in one
shared reply, which is cleared, sent and read without a lock, so two threads
sending on one client would trample each other's reply.

The confirm loop: check that the change took effect, and send again if it did not.

This is the second loop of a change, and the one that joins the two. It sends
the command (the send loop above), then reads the microscope until the
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

For a change handed to ``dispatch.run()``, the pre-check and the confirmation
are both readings by the read loop: :func:`wait_ready` waits for one (the
scanner idle) over the rule's windows before every send, and :func:`read_back`
watches one for a single window, starting when it is asked.

Several ways of sending one command, tried in order.

A microscope may be reached more than one way: through its vendor interface,
and also directly. The send loop asks for one transport. This class is that one
transport, and behind it it holds an ordered list: the first route that can send
is used, and the next one only when the first could not.

"Could not" has a narrow meaning, so that a command is never done twice. A
route raises :class:`TransportUnavailable` only when nothing has left yet (the
connection was not there, the route has no way to say this command). Anything
else, such as a send that went out but was never acknowledged, is not a reason to
try another route: the command may already have been carried out, and sending it
again is the send loop's decision, made under the command's own rules (an
acquisition is never sent again).

The routes and their order are the microscope's choice, handed in as a function
so that a change of policy takes effect on the next command.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import reprlib
import time
from . import read as reader
from .dispatch import make_log_entry as _make_log_entry
from .dispatch import make_timing as _make_timing


log = logging.getLogger(__name__)


def confirmation_detail(result):
    """Return compact non-log readback details from a confirmation result.

    A long value (a job's whole settings) is cut short, so the message stays one line.
    """
    if not isinstance(result, dict):
        return ""
    details = {
        key: value for key, value in result.items() if key not in {"success", "logs"}
    }
    if not details:
        return ""
    return f"; last_confirmation={reprlib.repr(details)}"


# =============================================================================
# Waiting for a reading: before a send, and to confirm one
# =============================================================================


def watch(vendor, client, expect, **window):
    """One wait for the reading *expect* names until its ``accept`` holds; ``(reading, last value seen)``.

    *vendor* is the microscope's sources (what :func:`read.wait_for` is
    handed); *expect* names the reading (``reading``), what it is about
    (``about``), the read order (``mode``, ``fallback``) and what it must show
    (``accept``). The last value seen is the last answer that counted as a
    reading but did not show what was expected, for the message.
    """
    seen = {}

    def accept(value):
        seen["value"] = value
        return expect.accept(value)

    reading = reader.wait_for(
        vendor,
        expect.reading,
        client,
        accept=accept,
        about=expect.about,
        mode=expect.mode,
        fallback=expect.fallback,
        **window,
    )
    shown = reading is not None and reading.error is None
    return shown, seen.get("value")


def wait_ready(vendor, client, expect):
    """A pre-check for :func:`confirm_and_fire`: wait for *expect* (the scanner idle) by the one rule.

    The reading is waited for over the ``WINDOWS`` windows of the waiting rule;
    a microscope that is still not ready after them fails the check, and
    nothing is sent.
    """

    def ready():
        started = time.perf_counter()
        shown, last = watch(vendor, client, expect)
        if shown:
            return {"success": True, "logs": []}
        msg = (
            f"{expect.reading} not ready after {time.perf_counter() - started:.1f}s "
            f"({vendor.waiting.WINDOWS} windows of {vendor.waiting.WINDOW_S}s, "
            f"last status: {last or 'unknown'})"
        )
        log.warning(msg)
        return {"success": False, "logs": [_make_log_entry("warning", msg)]}

    return ready


def read_back(vendor, client, expect, *, since=None):
    """A confirmation for :func:`confirm_and_fire`: one window of *expect*'s reading, starting now.

    Only answers seen after *since* (a ``time.time()``; None: when the window
    began) count, so a reading from before the change cannot confirm it.
    ``run()`` passes the moment the change was handed to the write loops: a
    source that dates its answer by the event (the log) may have written it
    while the change was still being sent. The confirm loop runs the windows
    and sends the change again between them. Answers ``{"success", "logs"}``,
    and the last value read under ``last_value`` when the change did not show.
    """

    def confirm():
        started = time.monotonic()
        shown, last = watch(
            vendor,
            client,
            expect,
            observed_after=time.time() if since is None else since,
            deadline=started + vendor.waiting.WINDOW_S,
        )
        if shown:
            return {"success": True, "logs": []}
        msg = (
            f"{expect.reading} did not show the change within "
            f"{time.monotonic() - started:.1f}s (last read {reprlib.repr(last)})"
        )
        log.warning(msg)
        return {"success": False, "logs": [_make_log_entry("warning", msg)], "last_value": last}

    return confirm


def append_dialog_warning(transport, logs, command_started_at, description):
    msg = transport.dialog_warning_since(command_started_at, description)
    if msg is None:
        return ""
    log.warning(msg)
    logs.append(_make_log_entry("warning", msg))
    return f"; dialog={msg!r}"


#: What a command marked ``never_resend`` is held to: one send, one window, no send again.
SENT_ONCE = {
    "max_retries": 0,
    "max_confirm_attempts": 1,
    "refire_on_unconfirmed": False,
}


def resolve_profile(profile, **overrides):
    """Apply explicit overrides to a command's profile; the microscope hands the profile in.

    A profile that says ``never_resend``, or a command handed in with
    ``never_resend=True``, such as the start of an acquisition, is sent once
    whatever a caller or the profile itself asks for: not again after a
    temporary error, and not again after an unconfirmed window. Sending it
    twice would do the work twice. The rule is the dispatcher's, so it holds
    for every microscope's profiles.
    """
    from dataclasses import replace

    if profile is None:
        raise ValueError(
            "a command profile must be handed in: the dispatcher has no default of its own"
        )
    changes = {name: value for name, value in overrides.items() if value is not None}
    if changes.get("never_resend") or getattr(profile, "never_resend", False):
        changes.update(SENT_ONCE)
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
        send_without_receipt: If True, send without waiting for the microscope to
            confirm receipt, and without checking its reply (the confirmation
            is then the authoritative completion signal).

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
            carried_by = getattr(transport, "carried_by", None)
            if carried_by:
                all_logs.append(
                    _make_log_entry(
                        "info", f"{description} | sent through the {carried_by} route"
                    )
                )
            if delivered and not profile.send_without_receipt:
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
    send_without_receipt=None,
    never_resend=None,
):
    """Fire a command and optionally confirm the result, with correction.

    This is the single entry point through which all commands are
    dispatched. It calls ``fire_block`` to execute steps 1-4, then
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
        never_resend: True for a command that must reach the microscope at
            most once; see :func:`resolve_profile`.

    A change whose confirmation windows all run out is still a success, with
    ``confirmed`` False: ``success=True`` alone does NOT prove the setting
    took effect, so a caller that needs proof checks ``confirmed``.

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
    profile = resolve_profile(
        profile,
        max_retries=max_retries,
        max_confirm_attempts=max_confirm_attempts,
        refire_on_unconfirmed=refire_on_unconfirmed,
        send_without_receipt=send_without_receipt,
        never_resend=never_resend,
    )
    command_started_at = time.time()
    t_wall_start = time.perf_counter()
    t_confirm_total = 0.0
    all_logs = []
    total_attempts = 0
    confirm_attempts = 0

    # --- Fire block (first attempt) ---
    fb = fire_block(
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
        dialog_detail = append_dialog_warning(
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
            fb = fire_block(
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
                        f"{append_dialog_warning(profile.transport, all_logs, command_started_at, description)}"
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
        f"{confirmation_detail(last_confirm_result)}"
    )
    dialog_detail = append_dialog_warning(
        profile.transport, all_logs, command_started_at, description
    )
    log.warning("%s%s (%.3fs)", msg, dialog_detail, time.perf_counter() - t_wall_start)
    all_logs.append(_make_log_entry("warning", msg))
    return {
        "success": True,
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


class TransportUnavailable(RuntimeError):
    """A route could not send this command, and nothing has left yet, so another route may try."""


class TransportChain:
    """One transport made of an ordered list of routes; the first that can send does.

    *routes* is a function that returns the routes as ``[(name, transport), ...]``,
    best first. It is called for every command.
    """

    def __init__(self, routes):
        self._routes = routes
        self.carried_by = None  # the name of the route that carried the last command; None if none did

    def fill(self, request, setup_fn):
        """Write the command's values into the request, as the preferred route does.

        All routes share one request, so it is filled once, before any route sends.
        """
        routes = self._routes()
        if not routes:
            raise TransportUnavailable("no route is configured")
        routes[0][1].fill(request, setup_fn)

    def clear_reply(self, client):
        """Nothing to do yet: each route clears its own reply just before it sends."""

    def send(self, client, request, label, *, asynchronous):
        """Send through the first route that can; say so when a later one had to.

        Returns ``(arrived, answered)`` like a single transport. Raises
        :class:`TransportUnavailable` when no route could send.
        """
        self.carried_by = None
        skipped = []
        for name, transport in self._routes():
            try:
                transport.clear_reply(client)
                arrived, answered = transport.send(
                    client, request, label, asynchronous=asynchronous
                )
            except TransportUnavailable as why:
                log.warning("%s: the %s route could not send: %s", label, name, why)
                skipped.append(f"the {name} route could not send ({why})")
                continue
            self._active = transport
            self.carried_by = name
            if skipped:
                answered = [
                    f"{label} | {'; '.join(skipped)}; sent through the {name} route instead",
                    *answered,
                ]
            return arrived, answered
        raise TransportUnavailable(
            f"{label} | no route could send: {'; '.join(skipped) or 'none is configured'}"
        )

    def await_reply(self, client):
        """Wait on the route that carried the command."""
        return self._active.await_reply(client)

    def dialog_warning_since(self, command_started_at, description):
        """Ask the route that carried the command whether a dialog is in the way."""
        active = getattr(self, "_active", None)
        if active is None:
            return None
        return active.dialog_warning_since(command_started_at, description)
