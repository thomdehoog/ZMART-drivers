"""What the dispatcher is handed for FLUOVIEW: the table of readings, the one source, the waiting rule, and how a change is sent.

The dispatcher (``dispatcher/``) knows no microscope. For every reading it
asks this profile which source answers and what counts as an answer; for
every change, how the write loops send it and how the reply is read. The
FV4000 has one way in, the RDK, so every reading has one source, ``"rdk"``,
and there is nothing to fall back on: when FLUOVIEW does not answer, the
reading is unknown.

How long the driver waits on FLUOVIEW: four windows of three seconds, looked
at again every ``POLL_S``. The numbers are read at call time, so a test can
shrink them. A capture waits for idle as long as it takes, and the scan
itself is watched by ``procedures/changes.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...dispatcher.dispatch import make_log_entry
from ...dispatcher.read import Reading
from . import reads
from .client import CallInterrupted, ConnectionLost
from .parsing import RdkError, classify_result, sends_again
from .transfer import RDK

log = logging.getLogger(__name__)

# =============================================================================
# The waiting rule
# =============================================================================

#: How many windows a reading or a read-back gets, and how long each is.
WINDOWS = 4
WINDOW_S = 3
#: How often a source is looked at again within a window. One getParameter
#: call is one HTTP request; the manual warns against floods, so not faster.
POLL_S = 0.2
#: How often an already-sent request is checked for its answer. The RDK
#: answers inside the call, so this is only the pause between two looks.
ANSWER_POLL_S = 0.01

#: The one source every reading has.
SOURCE = "rdk"


class _Waiting:
    """The four numbers, read from this module at call time."""

    @property
    def WINDOWS(self):
        return WINDOWS

    @property
    def WINDOW_S(self):
        return WINDOW_S

    @property
    def POLL_S(self):
        return POLL_S

    @property
    def ANSWER_POLL_S(self):
        return ANSWER_POLL_S


waiting = _Waiting()


# =============================================================================
# The readings
# =============================================================================


class UnsupportedSource(RuntimeError):
    """The reading has no source of the family asked for (the FV4000 only has ``rdk``)."""


def spec(datum) -> reads.Read:
    """The row of the table of readings for *datum*."""
    found = reads.READS.get(datum)
    if found is None:
        raise ValueError(
            f"unknown reading {datum!r}; the readings are {sorted(reads.READS)}"
        )
    return found


def mode_for(datum) -> str:
    """The source every reading asks: the RDK."""
    return SOURCE


def first_stage(spec, mode, fallback):
    """No source is tried alone first: there is only one."""
    return None, mode


def legs(spec, datum, mode):
    """The sources that take part: the RDK, or an ``UnsupportedSource`` reading for any other name."""
    if mode != SOURCE:
        return Reading(
            None,
            mode,
            None,
            None,
            UnsupportedSource(f"reading {datum!r} has no {mode!r} source"),
        )
    return (SOURCE,)


def success(spec, value, about) -> bool:
    """Whether *value* counts as an answer to the reading."""
    try:
        return bool(spec.counts(value))
    except Exception:  # noqa: BLE001 -- a check that cannot judge the value is "not an answer"
        return False


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask FLUOVIEW, and again every ``POLL_S`` until an answer counts or the moment *until*; the last Reading.

    A refusal or a lost connection is carried in the Reading's ``error``, so
    the read loop can say why there was no answer; nothing is raised.
    """
    reading = None
    while True:
        try:
            value = spec.fetch(client, about)
            reading = Reading(
                value=value, source=SOURCE, observed_at=time.time(), age_s=0.0
            )
        except (RdkError, ConnectionLost, CallInterrupted) as why:
            reading = Reading(
                value=None,
                source=SOURCE,
                observed_at=time.time(),
                age_s=None,
                error=why,
            )
        except Exception as why:  # noqa: BLE001 -- an answer in a shape this driver does not know
            log.warning("the reading failed (%s: %s)", type(why).__name__, why)
            reading = Reading(
                value=None,
                source=SOURCE,
                observed_at=time.time(),
                age_s=None,
                error=why,
            )
        if answer_counts(reading) or time.monotonic() + POLL_S >= until:
            return reading
        time.sleep(POLL_S)


# =============================================================================
# The changes
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change to FLUOVIEW: the fields ``dispatcher/write.py`` reads."""

    transport: object = RDK
    waiting: object = waiting
    max_retries: int = WINDOWS - 1
    max_confirm_attempts: int = WINDOWS
    refire_on_unconfirmed: bool = True
    send_without_receipt: bool = False
    never_resend: bool = False


#: How every change is sent.
WRITING = WriteProfile()

#: Where the operator creates the limits files, said when a change is refused
#: because no limits are installed (and by ``driver_setup/limits``).
limits_setup_hint = "driver_setup/limits/notebooks/set_limits.ipynb"


def make_request(client, write) -> dict:
    """The RDK call that carries *write*: its method and an empty payload the transport fills in."""
    return {"method": write.request, "payload": {}}


def check_reply(client, write) -> dict:
    """FLUOVIEW's reply to *write*, read: OK is success; another code is a failure, temporary or not.

    ``UNAVAILABLE`` (the call is not allowed in FLUOVIEW's present state) is
    worth sending again once the state has changed; every other code is not
    (``parsing.RESULT_KINDS``).
    """
    reply = client.last_reply
    if reply is None:
        return {
            "success": False,
            "error": "no reply from FLUOVIEW",
            "transient": True,
            "logs": [
                make_log_entry(
                    "warning", f"{write.description} | no reply from FLUOVIEW"
                )
            ],
        }
    code = str(reply.get("result", "ERROR")).upper()
    if code == "OK":
        return {"success": True, "error": None, "transient": None, "logs": []}
    kind = classify_result(code)
    transient = sends_again(kind)
    message = f"FLUOVIEW answered {code} ({kind.value})"
    return {
        "success": False,
        "error": message,
        "kind": kind,
        "transient": transient,
        "logs": [
            make_log_entry(
                "warning" if transient else "error", f"{write.description} | {message}"
            )
        ],
    }
