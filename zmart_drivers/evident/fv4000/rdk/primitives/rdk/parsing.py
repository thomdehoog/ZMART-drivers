"""Reading FLUOVIEW's answers: its units, its result codes, and what each kind of trouble means.

FLUOVIEW's RDK answers every call with a dictionary that always holds a
``result``: ``"OK"`` when the call worked, or one of a short list of words
when it did not (the manual's "Result Code" table). Positions are whole
numbers of nanometres. This file turns those into what the rest of the driver
works with: micrometres, a plain yes or no, and one of a short list of kinds
of problem, each with a rule for whether sending again is worthwhile.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
from enum import Enum

log = logging.getLogger(__name__)

#: FLUOVIEW counts positions in nanometres; the driver speaks micrometres.
NM_PER_UM = 1000


def um_to_nm(value_um: float) -> int:
    """A length in micrometres as the whole number of nanometres FLUOVIEW takes.

    Refuses anything that is not a finite number: a NaN would otherwise pass
    every comparison and reach the stage.
    """
    if isinstance(value_um, bool) or not isinstance(value_um, (int, float)):
        raise ValueError(f"{value_um!r} is not a number of micrometres")
    if not math.isfinite(value_um):
        raise ValueError(f"{value_um!r} is not a finite number of micrometres")
    return round(float(value_um) * NM_PER_UM)


def nm_to_um(value_nm) -> float:
    """A length FLUOVIEW reported in nanometres, in micrometres."""
    return float(value_nm) / NM_PER_UM


# =============================================================================
# FLUOVIEW's result codes, and what each means for a change
# =============================================================================


class Kind(str, Enum):
    """One kind of problem, in words every driver shares."""

    #: The request itself is wrong: a parameter FLUOVIEW calls invalid or unsupported.
    BAD_REQUEST = "bad_request"
    #: FLUOVIEW cannot do it right now (the wrong state, a scan running). Trying again can work.
    TEMPORARY = "temporary"
    #: FLUOVIEW reports a failure that trying again will not fix.
    PERMANENT = "permanent"
    #: FLUOVIEW was closed or can no longer be reached.
    CONNECTION_LOST = "connection_lost"
    #: The target lies outside the allowed range, so nothing was sent.
    REFUSED_BY_LIMITS = "refused_by_limits"
    #: A reading could not be had.
    UNKNOWN_READING = "unknown_reading"
    #: The command was accepted, but the read-back never matched.
    UNCONFIRMED = "unconfirmed"


#: The manual's result codes (section 3.3), and the kind each one is.
RESULT_KINDS = {
    "ERROR": Kind.PERMANENT,
    # "Unavailable state": the call is not allowed in FLUOVIEW's present state
    # (section 3.6 says which call is allowed when). A scan that is running
    # ends, so this is worth trying again.
    "UNAVAILABLE": Kind.TEMPORARY,
    "OUT_OF_MEMORY": Kind.PERMANENT,
    "INVALID_PARAMETERS": Kind.BAD_REQUEST,
    "NO_FRAME": Kind.BAD_REQUEST,
    "UNSUPPORTED_IMAGE": Kind.BAD_REQUEST,
    "UNSUPPORTED_PARAMETERS": Kind.BAD_REQUEST,
    "FILE_NOT_FOUND": Kind.BAD_REQUEST,
    "UNSUPPORTED_FILE_VERSION": Kind.BAD_REQUEST,
}

#: The kinds for which sending the same command again is safe and useful.
_SEND_AGAIN = frozenset({Kind.TEMPORARY})


def classify_result(code: str | None) -> Kind:
    """The kind of problem a result code stands for; a code this file does not know counts as permanent."""
    if not code:
        return Kind.PERMANENT
    kind = RESULT_KINDS.get(str(code).upper())
    if kind is None:
        log.warning("unknown FLUOVIEW result code %r (treated as permanent)", code)
        return Kind.PERMANENT
    return kind


def sends_again(kind: Kind) -> bool:
    """Whether a command that ran into *kind* may be sent again, within its retry limit."""
    return kind in _SEND_AGAIN


def is_ok(reply) -> bool:
    """Whether a reply says the call worked."""
    return isinstance(reply, dict) and str(reply.get("result", "")).upper() == "OK"


class RdkError(RuntimeError):
    """FLUOVIEW answered a call with a result other than OK.

    ``code`` is the result code and ``kind`` what it means; ``method`` is the
    call that was answered, so the message says what was asked.
    """

    def __init__(self, method: str, code: str, reply: dict | None = None):
        self.method = method
        self.code = code
        self.kind = classify_result(code)
        self.reply = reply
        super().__init__(f"{method} answered {code}")


def require_ok(method: str, reply) -> dict:
    """*reply* when it says OK; otherwise :class:`RdkError` naming the call and the code."""
    if not isinstance(reply, dict):
        raise RdkError(method, "ERROR", {"result": repr(reply)})
    if not is_ok(reply):
        raise RdkError(method, str(reply.get("result", "ERROR")), reply)
    return reply


def as_bytes(value) -> bytes:
    """The bytes of a binary value, whether the XML-RPC library wrapped it or not."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    data = getattr(value, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise TypeError(f"not binary data: {type(value).__name__}")
