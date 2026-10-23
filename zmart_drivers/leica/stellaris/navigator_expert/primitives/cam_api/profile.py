"""The CAM API's profile: the waiting rule every class follows, and LAS X's reply checked for an error, sorted temporary or permanent.

How long the driver waits on LAS X: the one rule and its four numbers.

Every wait on LAS X follows the same rule, whether it is a reading, the
confirmation of a command, or the delivery of a message. The dispatcher does
not know these numbers. This microscope hands them in (``profile.waiting`` for
a command, ``sources.waiting`` for a reading), so another microscope can wait
by a rule of its own.

- **Four windows of three seconds** (``WINDOWS`` x ``WINDOW_S``). Within a
  window the driver watches both sources, the CAM API and the LAS X log, and
  the first answer that counts as a success wins, from whichever source gives
  it. Between windows something is sent again: a command is fired again, a
  read that got no answer is requested again, a message is delivered again.
  After four windows a reading is unknown, a change is unconfirmed, a
  delivery has failed.
- **Look again every** ``POLL_S``: a new look at the source that shows the
  state, a new read request to the API or a new read of the LAS X log. One
  read of the log took 64-71 ms (median 66 ms over 30 reads) on the LAS X
  simulator PC on 2026-10-10, and one API read 60-80 ms, so looking faster
  gains little and keeps a CPU core busy re-reading the log. To be confirmed
  on the microscope PC.
- **Check for the answer every** ``ANSWER_POLL_S``: whether a request already
  sent has been answered, a field on the local API object. At ``POLL_S`` a
  65 ms answer would only be noticed at 100 ms.

Acquisition is the one command that is never sent again (a second acquire is
a second acquisition); it keeps its own watch in ``ACQUIRE``. Waits on a
physical process, such as a file being written, are not waits on LAS X and
keep their own numbers.

The numbers are read at call time, so a test can shrink them with monkeypatch.
The command profiles copy them into their field defaults when they are
imported.

Sorting LAS X's trouble into kinds of problem, and deciding what a kind means.

Each microscope reports trouble in its own way: LAS X writes a message into
an echo, another vendor raises an exception, a third answers with a status.
This file is where a driver turns what its vendor reported into one of a
short list of kinds (:class:`Kind`), and says what each kind means for the
driver. The only decision that can change an experiment is whether a command
may be sent again, so that is the rule written here: a temporary problem may
be sent again, nothing else may. The send loop in ``dispatcher/`` does not
interpret messages; it follows the "transient" answer this file gives.

Two responsibilities follow from that:

1. **Classification**: ``classify_error`` decides which kind an error message
   from LAS X is. Pattern lists are checked in priority order: permanent
   patterns first, then temporary patterns; an unknown message defaults to
   permanent, which is the safe choice.
2. **Detection**: ``_check_api_error`` reads the ``PyApiCommandEcho`` model
   after a command fire and returns the raw error information.
   ``_default_error_check`` wraps this into the structured result the fire
   block expects from any ``error_check_fn``.

The first four kinds are how the vendor software can fail. The others are
outcomes the driver reaches on its own; they are listed so that every case
has one name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
from enum import Enum
from ...dispatcher.dispatch import make_log_entry as _make_log_entry


WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01


log = logging.getLogger(__name__)


class Kind(str, Enum):
    """One kind of problem."""

    #: The request itself is wrong: an unknown option, a value the vendor calls invalid.
    BAD_REQUEST = "bad_request"
    #: The software is busy for now: a short timeout, a scan in progress. Trying again can work.
    TEMPORARY = "temporary"
    #: The vendor reports a failure that trying again will not fix, such as a hardware fault.
    PERMANENT = "permanent"
    #: The vendor software was closed or can no longer be reached.
    CONNECTION_LOST = "connection_lost"
    #: A request was sent but its reply never came, so it may or may not have happened.
    OUTCOME_UNKNOWN = "outcome_unknown"
    #: The target lies outside the allowed range, so nothing was sent.
    REFUSED_BY_LIMITS = "refused_by_limits"
    #: A reading could not be had (a stale log entry, a read that timed out).
    UNKNOWN_READING = "unknown_reading"
    #: The command was accepted, but the readback never matched.
    UNCONFIRMED = "unconfirmed"
    #: The user asked to stop.
    STOPPED_BY_USER = "stopped_by_user"


#: The kinds for which sending the same command again is safe and useful.
_SEND_AGAIN = frozenset({Kind.TEMPORARY})


def sends_again(kind: Kind) -> bool:
    """Whether a command that ran into *kind* may be sent again (within its retry limit)."""
    return kind in _SEND_AGAIN


# =============================================================================
# Error classification
# =============================================================================

_PERMANENT_PATTERNS = [
    "out of range",
    "is invalid",
    "invalid block identifier",
    "invalid detector",
    "invalid light source",
    "not defined",
    "not found",
    "has been adjusted",
    "not implemented",
]

# The permanent patterns that mean the request itself was wrong.
_BAD_REQUEST_PATTERNS = {
    "out of range",
    "is invalid",
    "invalid block identifier",
    "invalid detector",
    "invalid light source",
    "not defined",
    "not found",
}

_TRANSIENT_PATTERNS = [
    "being scanned",
    "cannot be set while",
    "block is being",
    "busy",
    "locked",
    "timeout",
    "timed out",
]


def classify_error(msg):
    """Sort an error message from LAS X into one of the shared kinds of problem.

    Args:
        msg: Error message string from LAS X.

    Returns:
        ``Kind.TEMPORARY`` for a busy or timed-out LAS X, ``Kind.BAD_REQUEST``
        for a request LAS X calls invalid or out of range, and
        ``Kind.PERMANENT`` for everything else. Permanent patterns are checked
        first and take priority, and a message that matches nothing is
        treated as permanent, which is the safe choice.
    """
    if not msg:
        return Kind.PERMANENT
    lower = msg.lower()
    for pat in _PERMANENT_PATTERNS:
        if pat in lower:
            return Kind.BAD_REQUEST if pat in _BAD_REQUEST_PATTERNS else Kind.PERMANENT
    for pat in _TRANSIENT_PATTERNS:
        if pat in lower:
            return Kind.TEMPORARY
    log.warning("Unclassified error (treated as permanent): %s", msg)
    return Kind.PERMANENT


def _is_transient_error(msg):
    """True when sending the command again is worthwhile (the rules say so for this kind)."""
    return sends_again(classify_error(msg))


# =============================================================================
# Error checking — raw API echo inspection
# =============================================================================

_RESULT_MAP = {0: "NotDefined", 1: "Success", 2: "Failure", 3: "NotImplemented"}


def _read_echo_details(echo):
    """Read additional diagnostic attributes from the echo model.

    LAS X may populate these with context about silent rejections or
    parameter adjustments. Returns a dict of non-empty attribute values.
    """
    details = {}
    for attr in (
        "Description",
        "ErrorDescription",
        "Message",
        "ErrorMessage",
        "Warning",
        "Info",
        "Details",
    ):
        try:
            val = getattr(echo, attr, None)
            if val is not None and val != "":
                details[attr] = str(val)
        except Exception:
            pass
    return details


def _check_api_error(client):
    """Check PyApiCommandEcho for errors after a fire.

    Reads the echo model and interprets the Result enum and HasError flag.
    Warnings (HasError with "warning" in the message) are treated as
    success — LAS X uses these for non-fatal parameter adjustments.
    Additional echo attributes are captured for diagnostics.

    Args:
        client: The connected LAS X API client.

    Returns:
        None on success, or {"error": str, "result": str, "result_code": int,
        "details": dict} on failure.
    """
    echo = client.PyApiCommandEcho.Model
    details = _read_echo_details(echo)

    try:
        result_code = int(echo.Result)
    except Exception:
        # Can't read Result enum
        if echo.HasError:
            error_msg = echo.Error if echo.Error else "(no message)"
            return {
                "error": error_msg,
                "result": "Unknown",
                "result_code": -1,
                "details": details,
            }
        return None

    result_str = _RESULT_MAP.get(result_code, "Unknown")
    error_msg = echo.Error if echo.Error else ""

    # NotImplemented → always error
    if result_code == 3:
        if not error_msg:
            error_msg = "Command not implemented on this hardware"
        return {
            "error": error_msg,
            "result": result_str,
            "result_code": result_code,
            "details": details,
        }

    # HasError with "warning" → success (non-fatal adjustment)
    if echo.HasError and "warning" in error_msg.lower():
        if details:
            log.debug("Warning accepted with details: %s", details)
        return None

    # HasError without warning → error
    if echo.HasError:
        if not error_msg:
            error_msg = "(HasError set, no message)"
        return {
            "error": error_msg,
            "result": result_str,
            "result_code": result_code,
            "details": details,
        }

    # Failure result code without HasError
    if result_code == 2:
        return {
            "error": error_msg or "Unknown failure",
            "result": result_str,
            "result_code": result_code,
            "details": details,
        }

    # Success or NotDefined with no error
    if details:
        log.debug("Echo details on success: %s", details)
    return None


# =============================================================================
# Default error_check_fn — structured adapter for the fire block
# =============================================================================


def _default_error_check(client):
    """Check for API errors and return a structured result dict.

    This is the default ``error_check_fn`` used by the fire block. It
    wraps ``_check_api_error`` and ``_is_transient_error`` into the
    standard error-check return shape. The fire block reads
    ``result["success"]`` to decide whether the command succeeded, and
    ``result["transient"]`` to decide whether a retry is warranted.

    99% of commands use this default. Only override when a command needs
    custom error interpretation (e.g. treating a specific permanent error
    as acceptable).

    Args:
        client: The connected LAS X API client.

    Returns:
        {
            "success": True,
            "error": None,
            "transient": None,
            "logs": [...]
        }
        on success, or:
        {
            "success": False,
            "error": str,
            "transient": bool,
            "logs": [...]
        }
        on failure.
    """
    logs = []
    err = _check_api_error(client)

    if err is None:
        return {"success": True, "error": None, "transient": None, "logs": logs}

    error_msg = err.get("error", "")
    details = err.get("details", {})
    kind = classify_error(error_msg)
    transient = sends_again(kind)
    level = "warning" if transient else "error"

    log_msg = f"API error: {error_msg}"
    if details:
        log_msg += f" | details: {details}"
    logs.append(_make_log_entry(level, log_msg))

    return {
        "success": False,
        "error": error_msg,
        "kind": kind,
        "transient": transient,
        "logs": logs,
    }


def check_reply(client, write):
    """LAS X's reply to *write*, checked for an error; a reply the write accepts means "already so".

    Such a reply (resonant scanning already on, say) is not an error: the
    change is then confirmed by reading it back, like any other.
    """
    result = _default_error_check(client)
    error = (result.get("error") or "").lower()
    if not result["success"] and any(reply in error for reply in write.accepted_replies):
        msg = f"{write.description} | LAS X answered {result['error']!r}: already so; the readback confirms it"
        return {"success": True, "error": None, "transient": None, "logs": [_make_log_entry("info", msg)]}
    return result
