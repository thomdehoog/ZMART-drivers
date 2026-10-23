"""The one door to the dispatcher: read a value, or change one, safely.

The rest of the driver calls the dispatcher here and never needs to know which
of its two engines does the work. :func:`run` is the one entry: a reading by
its name, or a change as plain data (for LAS X, a ``Write`` from
``primitives/cam_api/writes.py``). :func:`read` and :func:`write` are the older
two doors, used by the changes not yet ported to :func:`run`:

- :func:`read` waits for an answer to a question about the microscope. It asks
  the sources one after the other (``reader.py``) and is safe to repeat: the
  reader asks again, never the hardware.
- :func:`write` changes something on the microscope: it sends the command,
  checks for an error (``send_loop.py``), reads the value back until it matches, and
  sends again where that is safe (``confirm_loop.py``). It first asks the limits gate
  (``gate.py``) and sends nothing if the limits refuse. Changing a setting or moving to an
  absolute position can be repeated. The one command that must never be sent
  again is starting an acquisition, which says so in its profile
  (``never_resend``).

The confirm loop uses the reader to confirm a change, so the two share one
waiting rule. This file knows nothing of any one microscope: the microscope's
own sources, command profiles, error sorting and waiting numbers are handed in.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import time

# =============================================================================
# The shape every command result shares: timing and log entries
# =============================================================================


def make_log_entry(level, msg):
    """Build a timestamped log entry dict.

    Every pluggable function in the pipeline accumulates these entries in
    its ``logs`` list. The backbone collects them into a single ordered
    trace so the caller has full visibility into what happened.

    Args:
        level: One of "debug", "info", "warning", "error".
        msg: Human-readable log message.

    Returns:
        {"ts": float, "level": str, "msg": str}
    """
    return {"ts": time.time(), "level": level, "msg": msg}


def make_timing(
    pre_check_s=0.0,
    setup_s=0.0,
    fire_s=0.0,
    check_s=0.0,
    confirm_s=0.0,
    total_s=0.0,
    attempts=1,
    confirm_attempts=0,
    method="async",
):
    """Build a timing dict for command result envelopes.

    Args:
        pre_check_s: Time spent in pre-fire check (e.g. idle wait).
        setup_s: Time writing parameters to the model.
        fire_s: Time for sending the request through the transport.
        check_s: Time for the error check.
        confirm_s: Time spent in confirm_fn.
        total_s: Wall-clock time for the entire operation.
        attempts: Number of fire-block attempts (1 + retries).
        confirm_attempts: Number of confirm-wrapper attempts.
        method: 'sync' or 'async'.

    Returns:
        Timing dict with all keys above.
    """
    return {
        "pre_check_s": pre_check_s,
        "setup_s": setup_s,
        "fire_s": fire_s,
        "check_s": check_s,
        "confirm_s": confirm_s,
        "total_s": total_s,
        "attempts": attempts,
        "confirm_attempts": confirm_attempts,
        "method": method,
    }


def refused(message, *, logged=False, **extra):
    """The result of a change that was not sent: ``success`` False, with the reason.

    The same shape as a sent change's result. ``logged`` also puts the reason
    in the result's log. ``extra`` adds the keys a change's answer always
    carries, such as ``position=None``.
    """
    return {
        "success": False,
        "confirmed": None,
        "message": message,
        "timing": make_timing(total_s=0.0, attempts=0),
        "logs": [make_log_entry("error", message)] if logged else [],
        **extra,
    }


def run(profile, client, primitive, *, max_retries=None, **asked):
    """Run one primitive: a reading (its name) or a change (a write); the one entry to the dispatcher.

    A reading waits by the one rule (:func:`read.wait_for`, with *asked*:
    ``accept``, ``about``, ``mode``, ``deadline`` and the others). A change
    first asks the limits gate, once, before anything is made or sent: a
    refused change never touches the client. Then the request is made and the
    write loops run (:func:`write.confirm_and_fire`): wait until the
    microscope is ready, send, check the reply, read back, send again where
    that is safe. The result says, under ``route``, which way the change went
    (None when nothing was sent).

    Everything of the microscope comes from *profile*: for a reading, its
    sources (``spec``, ``legs``, ``ask``, ``waiting``, ...); for a change,
    ``limits_setup_hint``, ``make_request(client, write)``,
    ``check_reply(client, write)`` and ``WRITING``, how the write loops send.
    A change is read by its fields: ``command`` and ``values`` (for the
    limits), ``description``, ``fill`` (writes the request), ``ready`` and
    ``confirm`` (readings to wait for: ``reading``, ``accept``, ``about``,
    ``mode``, ``fallback``), ``without_receipt`` and ``never_resend``.
    ``max_retries`` overrides how often a change is sent again after a
    temporary error.
    """
    from . import limits as gate, read as reader, write as writer

    if isinstance(primitive, str):
        return reader.wait_for(profile, primitive, client, **asked)

    change = primitive
    message = gate.check_refusal(client, change.command, change.values, profile.limits_setup_hint)
    if message is not None:
        return refused(message, logged=True)
    request = profile.make_request(client, change)
    sending = profile.WRITING
    result = writer.confirm_and_fire(
        client,
        request,
        change.description,
        profile=sending,
        setup_fn=change.fill,
        pre_check_fn=writer.wait_ready(profile, client, change.ready) if change.ready else None,
        # Sent without a receipt, the reply is blank: there is nothing to check.
        error_check_fn=None if change.without_receipt else (lambda: profile.check_reply(client, change)),
        confirm_fn=writer.read_back(profile, client, change.confirm) if change.confirm else None,
        never_resend=change.never_resend,
        max_retries=max_retries,
        send_without_receipt=change.without_receipt,
    )
    result["route"] = getattr(sending.transport, "carried_by", None)
    return result


def read(vendor, datum, client, **keywords):
    """Wait for a reading of *datum* from the microscope's sources; see :func:`reader.wait_for`."""
    from . import read as reader

    return reader.wait_for(vendor, datum, client, **keywords)


def write(client, request, description, *, command, values, make_request=None, **keywords):
    """Send a change, check it, and confirm it; see :func:`confirm_loop.confirm_and_fire`.

    ``command`` is the name of the change and ``values`` is what it would set.
    Before anything is sent, the limits gate is asked whether this connection's
    limits allow it. If not, nothing is sent and the answer is a refused result
    with the reason. Because the check sits here, no change can reach the
    microscope without passing it, whichever recipe asked. ``make_request``,
    when given, makes the request only once the limits allow the change, so a
    refused change never touches the client. The result also says, under
    ``route``, which way the command went.
    """
    from . import write as confirm_loop, limits as gate

    hint = getattr(keywords.get("profile"), "limits_setup_hint", None)
    message = gate.check_refusal(client, command, values, hint)
    if message is not None:
        return refused(message, logged=True)
    if make_request is not None:
        request = make_request()
    result = confirm_loop.confirm_and_fire(client, request, description, **keywords)

    # Which way the command went, when the transport keeps several ("direct", "cam"):
    # None when nothing was sent.
    result["route"] = getattr(
        getattr(keywords.get("profile"), "transport", None), "carried_by", None
    )
    return result
