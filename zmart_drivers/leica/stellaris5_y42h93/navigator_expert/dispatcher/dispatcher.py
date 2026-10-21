"""The one door to the dispatcher: read a value, or change one, safely.

The rest of the driver calls the dispatcher here and never needs to know which
of its two engines does the work:

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


def read(vendor, datum, client, **keywords):
    """Wait for a reading of *datum* from the microscope's sources; see :func:`reader.wait_for`."""
    from . import reader

    return reader.wait_for(vendor, datum, client, **keywords)


def write(client, request, description, *, command, values, **keywords):
    """Send a change, check it, and confirm it; see :func:`confirm_loop.confirm_and_fire`.

    ``command`` is the name of the change and ``values`` is what it would set.
    Before anything is sent, the limits gate is asked whether this connection's
    limits allow it. If not, nothing is sent and the answer is a refused result
    with the reason. Because the check sits here, no change can reach the
    microscope without passing it, whichever recipe asked.
    """
    from . import confirm_loop, gate

    hint = getattr(keywords.get("profile"), "limits_setup_hint", None)
    message = gate.check_refusal(client, command, values, hint)
    if message is not None:
        return {
            "success": False,
            "confirmed": None,
            "message": message,
            "timing": make_timing(total_s=0.0, attempts=0),
            "logs": [make_log_entry("error", message)],
        }
    return confirm_loop.confirm_and_fire(client, request, description, **keywords)
