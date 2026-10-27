"""The profile the dispatcher is handed: the readings and their class, the waiting rule, and the bridge's reply checked.

Readings. Every reading is declared here once (``DATUMS``): which class
answers it (``dll`` from ``reads.py``, ``macro`` from ``macro/reads.py``),
and what counts as an answer. This driver has one class per reading, so
there is no read order to choose: a reading asks its class, and a class that
cannot answer (the bridge gone) ends the reading with that error. The read
loop (``dispatcher/read.py``) reads the table through ``spec``, ``legs``,
``ask`` and the others below.

The waiting rule. Four windows of three seconds, as every ZMART driver waits.
Within a window a reading is asked again every ``POLL_S``; the ``macro``
class has a slower pace of its own (``macro/profile.py``), because each of
its reads costs NIS-Elements about a tenth of a second on its main thread.

Writes. This module is also what ``run()`` is handed for a change:
``limits_setup_hint``, ``make_request``, ``check_reply`` and ``WRITING``.
Every change, of either class, goes through the one bridge transfer.

The bridge's reply, sorted. The bridge turns NIS's trouble into exceptions,
and ``check_reply`` sorts them: a request the bridge dropped because it had
not started in time is temporary (sending it again is safe); a request still
running when the driver stopped waiting is not (it may finish on its own);
a wrong request, a refusal of NIS, or a lost connection is permanent. An
error that matches nothing counts as permanent, which is the safe choice.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ...dispatcher.dispatch import make_log_entry as _make_log_entry
from ...dispatcher.read import Reading
from ..macro import profile as macro_profile
from . import reads as dll_reads
from .client import BridgeLost
from .transfer import BRIDGE, request_of

log = logging.getLogger(__name__)

# The waiting rule's four numbers. They are read at call time, so a test can
# shrink them. A dll read answers in about a millisecond on the Ti2 simulator
# (the bridge's README), so looking every 100 ms is unhurried.
WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01


# =============================================================================
# The kinds of problem, and the rule for each
# =============================================================================


class Kind(str, Enum):
    """One kind of problem the bridge can report."""

    #: The request itself is wrong: an unknown name, a value out of what the bridge takes.
    BAD_REQUEST = "bad_request"
    #: The bridge had not started the request when the driver stopped waiting; it was dropped.
    TEMPORARY = "temporary"
    #: NIS-Elements refused or failed; sending the same again will not change that.
    PERMANENT = "permanent"
    #: The request was still running in NIS when the driver stopped waiting; it may finish.
    OUTCOME_UNKNOWN = "outcome_unknown"
    #: The bridge could not be reached, or the connection dropped.
    CONNECTION_LOST = "connection_lost"


#: The kinds for which sending the same request again is safe and useful.
_SEND_AGAIN = frozenset({Kind.TEMPORARY})


def sends_again(kind: Kind) -> bool:
    """Whether a change that ran into *kind* may be sent again (within its retry limit)."""
    return kind in _SEND_AGAIN


def classify_error(error: Exception) -> Kind:
    """Sort an error the bridge raised into one of the kinds above."""
    if isinstance(error, BridgeLost):
        return Kind.CONNECTION_LOST
    if isinstance(error, ValueError):
        return Kind.BAD_REQUEST
    text = str(error).lower()
    if "did not start within" in text:
        return Kind.TEMPORARY
    if "still running" in text:
        return Kind.OUTCOME_UNKNOWN
    if "busy" in text:
        return Kind.TEMPORARY
    return Kind.PERMANENT


def check_reply(client, write) -> dict:
    """The bridge's answer to *write*, checked: ``{"success", "error", "transient", "logs"}``.

    The transfer left the answer in ``client.reply``. No answer at all means
    the send never happened, which is reported as a permanent failure rather
    than guessed at.
    """
    answered = client.reply
    if answered is None:
        return {
            "success": False,
            "error": "no answer was recorded for the request",
            "transient": False,
            "logs": [
                _make_log_entry("error", f"{write.description} | no answer recorded")
            ],
        }
    if answered.error is None:
        return {"success": True, "error": None, "transient": None, "logs": []}
    kind = classify_error(answered.error)
    transient = sends_again(kind)
    message = f"{type(answered.error).__name__}: {answered.error}"
    return {
        "success": False,
        "error": message,
        "kind": kind,
        "transient": transient,
        "logs": [
            _make_log_entry(
                "warning" if transient else "error",
                f"{write.description} | {kind.value}: {message}",
            )
        ],
    }


# =============================================================================
# The table of readings
# =============================================================================


class UnsupportedSource(RuntimeError):
    """The reading has no class of the name asked for."""


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def xyz_present(value) -> bool:
    """All three coordinates, as finite numbers."""
    return isinstance(value, dict) and all(
        _finite(value.get(key)) for key in ("x_um", "y_um", "z_um")
    )


def limits_present(value) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), list) and len(value[axis]) == 2
        for axis in ("x", "y", "z")
    )


def names_listed(value) -> bool:
    """A list of names; an empty list is a real answer (NIS has no configuration yet)."""
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


def objectives_present(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("objectives"), dict)


def pfs_present(value) -> bool:
    return isinstance(value, dict) and "present" in value and "on" in value


def version_present(value) -> bool:
    return isinstance(value, str) and bool(value)


@dataclass(frozen=True)
class Datum:
    """One reading: the class that answers it, its function, what counts, and the class's pace."""

    name: str
    source: str  # "dll" or "macro"
    read: Callable  # read(client) -> the value
    success: Callable  # success(value) -> bool
    poll_s: Callable[[], float]  # how long to wait before asking the class again


#: What counts as an answer to each reading of the dll class.
SUCCESS = {
    "xyz": xyz_present,
    "limits": limits_present,
    "optical_configurations": names_listed,
    "objectives": objectives_present,
    "pfs": pfs_present,
    "version": version_present,
}


def _dll_poll_s() -> float:
    return sys.modules[__name__].POLL_S


DATUMS = {
    **{
        name: Datum(name, "dll", dll_reads.READS[name], success, _dll_poll_s)
        for name, success in SUCCESS.items()
    },
    **{
        name: Datum(
            name, "macro", macro_profile.READS[name], success, macro_profile.poll_s
        )
        for name, success in macro_profile.SUCCESS.items()
    },
}


def spec(datum) -> Datum:
    """The row of the table of readings for *datum*."""
    found = DATUMS.get(datum)
    if found is None:
        raise ValueError(
            f"unknown reading {datum!r}; the readings are {sorted(DATUMS)}"
        )
    return found


# =============================================================================
# What the read loop asks of this microscope
# =============================================================================


class _Waiting:
    """The waiting numbers, read from this module at call time so a test can shrink them."""

    @property
    def WINDOWS(self):
        return sys.modules[__name__].WINDOWS

    @property
    def WINDOW_S(self):
        return sys.modules[__name__].WINDOW_S

    @property
    def POLL_S(self):
        return sys.modules[__name__].POLL_S

    @property
    def ANSWER_POLL_S(self):
        return sys.modules[__name__].ANSWER_POLL_S


waiting = _Waiting()


def mode_for(datum) -> str:
    """The class that answers *datum*: there is one per reading."""
    return spec(datum).source


def first_stage(spec, mode, fallback):
    """No class is tried alone before the others: each reading has one class."""
    return None, mode


def legs(spec, datum, mode):
    """The classes that take part: the reading's own, or an ``UnsupportedSource`` reading."""
    if mode == spec.source:
        return (mode,)
    return Reading(
        value=None,
        source=mode,
        observed_at=None,
        age_s=None,
        error=UnsupportedSource(
            f"reading {datum!r} is answered by the {spec.source} class, not {mode}"
        ),
    )


def success(spec, value, about) -> bool:
    """Whether *value* counts as an answer; this driver's readings are about nothing in particular."""
    return spec.success(value)


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask the reading's class, and again at its pace until an answer counts or *until*.

    A class that cannot answer (the bridge gone, NIS refusing) hands back a
    reading that carries the error; the read loop then has no other class
    to ask, and the reading ends with that error.
    """
    while True:
        try:
            value = spec.read(client)
        except Exception as why:  # noqa: BLE001 -- the error is carried, never raised
            log.warning(
                "%s: the %s class could not answer (%s: %s)",
                spec.name,
                leg,
                type(why).__name__,
                why,
            )
            return Reading(
                value=None, source=leg, observed_at=time.time(), age_s=None, error=why
            )
        reading = Reading(value=value, source=leg, observed_at=time.time(), age_s=0.0)
        if answer_counts(reading) or time.monotonic() + spec.poll_s() >= until:
            return reading
        time.sleep(spec.poll_s())


# =============================================================================
# What the dispatcher's run() asks of this microscope for a change
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change: the fields ``dispatcher/write.py`` reads.

    Every change is sent through the bridge by the one waiting rule:
    ``WINDOWS`` tries in all after a temporary error, ``WINDOWS`` confirmation
    windows, and sent again between them. What differs per change (never
    sent again) is on the ``Write`` and handed to the loops by ``run()``.
    """

    transport: object = BRIDGE
    waiting: object = waiting
    max_retries: int = WINDOWS - 1
    max_confirm_attempts: int = WINDOWS
    refire_on_unconfirmed: bool = True
    send_without_receipt: bool = False
    never_resend: bool = False


#: How every change is sent.
WRITING = WriteProfile()

#: Where the operator creates the limits file, said when a change is refused
#: because no limits are installed (and by ``driver_setup/limits``).
limits_setup_hint = "driver_setup/limits/notebooks/set_limits.ipynb"


def make_request(client, write) -> dict:
    """The bridge request that carries *write*; made only once the limits allowed it."""
    return request_of(write.request, write.timeout_s)
