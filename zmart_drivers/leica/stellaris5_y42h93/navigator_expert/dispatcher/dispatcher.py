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

The confirm loop uses the reader to confirm a change, which is why the two share one
waiting rule, kept here. This file knows nothing of any one microscope: the
microscope's own sources, command profiles and error sorting are handed in.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import time

# =============================================================================
# The waiting rule: how long the driver waits, whichever source answers
# =============================================================================
#
# How the driver waits on LAS X: the one rule and its four numbers.
#
# Every wait on LAS X follows the same rule, whether it is a reading, the
# confirmation of a command, or the delivery of a message:
#
# - **Four windows of three seconds** (``WINDOWS`` x ``WINDOW_S``). Within a
#   window the driver watches both sources, the CAM API and the LAS X log, and
#   the first answer that counts as a success wins, from whichever source gives
#   it. Between windows something is sent again: a command is fired again, a
#   read that got no answer is requested again, a message is delivered again.
#   After four windows a reading is unknown, a change is unconfirmed, a
#   delivery has failed.
# - **Look again every** ``POLL_S``: a new look at the source that shows the
#   state, a new read request to the API or a new read of the LAS X log. One
#   read of the log took 64-71 ms (median 66 ms over 30 reads) on the LAS X
#   simulator PC on 2026-10-10, and one API read 60-80 ms, so looking faster
#   gains little and keeps a CPU core busy re-reading the log. To be confirmed
#   on the microscope PC.
# - **Check for the answer every** ``ANSWER_POLL_S``: whether a request already
#   sent has been answered, a field on the local API object. At ``POLL_S`` a
#   65 ms answer would only be noticed at 100 ms.
#
# Acquisition is the one command that is never sent again (a second acquire is
# a second acquisition); it keeps its own watch in ``ACQUIRE``. Waits on a
# physical process, such as a file being written, are not waits on LAS X and
# keep their own numbers.
#
# Runtime consumers read these at call time (``WINDOW_S``), so a test
# can shrink them with monkeypatch. ``actions.profiles`` captures them into
# dataclass field defaults at import.

WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01


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
        fire_s: Time for UpdateAwaitReceipt transport.
        check_s: Time for API error check.
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


def write(client, api_obj, description, *, command, values, **keywords):
    """Send a change, check it, and confirm it; see :func:`confirm_loop.confirm_and_fire`.

    ``command`` is the name of the change and ``values`` is what it would set.
    Before anything is sent, the limits gate is asked whether this connection's
    limits allow it. If not, nothing is sent and the answer is a refused result
    with the reason. Because the check sits here, no change can reach the
    microscope without passing it, whichever recipe asked.
    """
    from . import confirm_loop, gate

    message = gate.check_refusal(client, command, values)
    if message is not None:
        return {
            "success": False,
            "confirmed": None,
            "message": message,
            "timing": make_timing(total_s=0.0, attempts=0),
            "logs": [make_log_entry("error", message)],
        }
    return confirm_loop.confirm_and_fire(client, api_obj, description, **keywords)
