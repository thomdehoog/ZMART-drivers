"""The gateway's profile: what the dispatcher needs to handle the one class of primitive.

This is the object the dispatcher's ``run()`` is handed, for a reading and
for a change (``docs/vendor-contract.md`` lists what it asks of it):

- **The waiting rule**: four windows of three seconds. Within a window the
  reading is asked again every ``POLL_S``. ZEN's calls answer when they are
  done, so a read-back usually shows the change in the first look; the
  windows are for a slow stage or a busy ZEN.
- **The table of readings** (``DATUMS``): each reading the gateway answers
  (``reads.READS``) and what counts as an answer. There is one class, so the
  read order is short: the gateway, and nothing when it cannot answer.
- **How a change is sent** (``WRITING``, ``make_request``, ``check_reply``):
  through the gateway's transport, and ZEN's reply sorted into kinds of
  trouble. Only a temporary problem is sent again.

Sorting ZEN's trouble. ZEN answers a failed call with a gRPC *status*, a
short word such as ``UNAVAILABLE`` or ``OUT_OF_RANGE``. This file turns the
status into one of a short list of kinds (:class:`Kind`) and says what each
means for the driver. The one decision that can change an experiment is
whether a command may be sent again, so that is the rule written here: a
temporary problem may be sent again, nothing else may. The dispatcher does
not read messages; it follows the ``transient`` answer this file gives.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ...dispatcher.dispatch import make_log_entry
from ...dispatcher.read import Reading
from . import reads
from .transfer import GATEWAY, request_of

log = logging.getLogger(__name__)


# =============================================================================
# The waiting rule
# =============================================================================

WINDOWS = 4
WINDOW_S = 3.0
POLL_S = 0.1
ANSWER_POLL_S = 0.01


class _Waiting:
    """The four numbers, read when they are asked for, so a test can shrink them."""

    @property
    def WINDOWS(self) -> int:
        return WINDOWS

    @property
    def WINDOW_S(self) -> float:
        return WINDOW_S

    @property
    def POLL_S(self) -> float:
        return POLL_S

    @property
    def ANSWER_POLL_S(self) -> float:
        return ANSWER_POLL_S


#: The waiting numbers, as the dispatcher reads them.
waiting = _Waiting()

#: The one class of primitive, as the read order names it.
SOURCE = "gateway"


# =============================================================================
# The table of readings
# =============================================================================


class UnsupportedSource(RuntimeError):
    """The reading has no source of the kind asked for."""


def _finite(value) -> bool:
    """Whether *value* is a number that is neither NaN nor infinite."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def xy_present(value) -> bool:
    return (
        isinstance(value, dict)
        and _finite(value.get("x_um"))
        and _finite(value.get("y_um"))
    )


def z_present(value) -> bool:
    return isinstance(value, dict) and _finite(value.get("z_um"))


def objective_present(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("position"), int)


def listed(value) -> bool:
    """A list is an answer, even an empty one: ZEN said what it has."""
    return isinstance(value, list)


def text(value) -> bool:
    return isinstance(value, str)


def status_present(value) -> bool:
    return isinstance(value, dict) and "running" in value


def stack_present(value) -> bool:
    return isinstance(value, dict) and "is_zstack" in value


@dataclass(frozen=True)
class DatumSpec:
    """One reading: how the gateway answers it, and what counts as an answer."""

    name: str
    read: Callable
    success: Callable[[object], bool]


#: What counts as an answer to each reading; its keys are the readings there are.
SUCCESS = {
    "xy": xy_present,
    "z": z_present,
    "objective": objective_present,
    "objectives": listed,
    "experiments": listed,
    "image_output_path": text,
    "experiment_status": status_present,
    "activity": status_present,
    "zstack": stack_present,
    "tracks": listed,
}

DATUMS = {
    name: DatumSpec(name=name, read=reads.READS[name], success=success)
    for name, success in SUCCESS.items()
}


def spec(datum) -> DatumSpec:
    """The row of the table of readings for *datum*."""
    found = DATUMS.get(datum)
    if found is None:
        raise ValueError(
            f"unknown reading {datum!r}; the readings are {sorted(DATUMS)}"
        )
    return found


def mode_for(datum) -> str:
    """The class every reading asks first: the gateway, the only one there is."""
    return SOURCE


def first_stage(spec, mode, fallback):
    """No class is tried alone before the others: there is only one."""
    return None, mode


def legs(spec, datum, mode):
    """The classes that take part: the gateway, or nothing for a class that does not exist."""
    if mode != SOURCE:
        return Reading(
            value=None,
            source=mode,
            observed_at=None,
            age_s=None,
            error=UnsupportedSource(
                f"reading {datum!r} has no {mode!r} source; only {SOURCE!r}"
            ),
        )
    return (SOURCE,)


def success(spec, value, about) -> bool:
    """Whether *value* counts as an answer to the reading. *about* names an experiment, which the check does not need."""
    return spec.success(value)


def source_arguments(about, source_kwargs) -> dict:
    """What the reading is asked with: the experiment it is about, and what the caller adds."""
    return {
        **({"experiment_id": about} if about is not None else {}),
        **(source_kwargs or {}),
    }


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask the gateway, and again every ``POLL_S`` until an answer counts or the moment *until*.

    An error from ZEN (the gateway not answering, a service missing) is
    carried in the reading at once; the read loop asks again at its next
    window.
    """
    arguments = source_arguments(about, source_kwargs)
    while True:
        try:
            value = spec.read(client, **arguments)
        except Exception as why:  # noqa: BLE001 -- carried, so the loop and the caller can see why
            return Reading(
                value=None,
                source=SOURCE,
                observed_at=time.time(),
                age_s=None,
                error=why,
            )
        reading = Reading(
            value=value, source=SOURCE, observed_at=time.time(), age_s=0.0
        )
        if answer_counts(reading) or time.monotonic() + POLL_S >= until:
            return reading
        time.sleep(POLL_S)


# =============================================================================
# Sorting ZEN's trouble
# =============================================================================


class Kind(str, Enum):
    """One kind of problem."""

    #: The request itself is wrong: a position outside the stage, an experiment that does not exist.
    BAD_REQUEST = "bad_request"
    #: ZEN or the gateway is busy or slow for now. Sending again can work.
    TEMPORARY = "temporary"
    #: ZEN refuses, and sending again will not help: control is not allowed, the service is missing.
    PERMANENT = "permanent"
    #: The gateway or ZEN can no longer be reached.
    CONNECTION_LOST = "connection_lost"


#: gRPC statuses that mean "try again".
TEMPORARY_STATUSES = frozenset(
    {"UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED", "RESOURCE_EXHAUSTED", "CANCELLED"}
)

#: gRPC statuses that mean the request itself was wrong.
BAD_REQUEST_STATUSES = frozenset(
    {"INVALID_ARGUMENT", "OUT_OF_RANGE", "NOT_FOUND", "ALREADY_EXISTS"}
)

#: What to do about the statuses an operator can fix, said with the error.
ADVICE = {
    "PERMISSION_DENIED": (
        "ZEN does not allow control from outside: enable Unsupervised API Mode in ZEN "
        "(Tools > Options > ZEN API; ZEN core: Maintenance > General Options > ZEN API)"
    ),
    "UNAUTHENTICATED": "the control token is wrong; copy it again from the gateway's tray icon",
    "UNIMPLEMENTED": "this ZEN does not provide the service; check that the ZEN API toolkit is active",
}


def status_name(error: BaseException) -> str | None:
    """The gRPC status an error carries, as its upper-case name, or None for an error without one."""
    status = getattr(error, "status", None)
    if status is None:
        return None
    name = getattr(status, "name", None)
    return str(name if name is not None else status).upper()


def classify(error: BaseException) -> tuple[Kind, str]:
    """Sort an error ZEN or the connection raised into a kind, with the text to report."""
    name = status_name(error)
    message = getattr(error, "message", None) or str(error)
    if isinstance(error, TimeoutError):
        return Kind.TEMPORARY, f"timed out: {message}"
    if name is None:
        if isinstance(error, (OSError, ConnectionError)):
            return Kind.CONNECTION_LOST, f"the connection failed: {message}"
        log.warning(
            "unclassified error (treated as permanent): %s: %s",
            type(error).__name__,
            message,
        )
        return Kind.PERMANENT, f"{type(error).__name__}: {message}"
    detail = f"{name}: {message}"
    if name in TEMPORARY_STATUSES:
        return Kind.TEMPORARY, detail
    if name in BAD_REQUEST_STATUSES:
        return Kind.BAD_REQUEST, detail
    if name in ADVICE:
        return Kind.PERMANENT, f"{detail} ({ADVICE[name]})"
    if name not in ("FAILED_PRECONDITION", "INTERNAL", "UNKNOWN", "DATA_LOSS"):
        log.warning("unclassified gRPC status (treated as permanent): %s", detail)
    return Kind.PERMANENT, detail


def sends_again(kind: Kind) -> bool:
    """Whether a change that ran into *kind* may be sent again, within its retry limit."""
    return kind is Kind.TEMPORARY


# =============================================================================
# What the dispatcher's run() asks of the gateway for a change
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change to ZEN: the fields ``dispatcher/write.py`` reads."""

    transport: object = GATEWAY
    waiting: object = waiting
    max_retries: int = WINDOWS - 1
    max_confirm_attempts: int = WINDOWS
    refire_on_unconfirmed: bool = True
    send_without_receipt: bool = False
    never_resend: bool = False


#: How every change is sent.
WRITING = WriteProfile()

#: Where the operator creates the limits file, said when a change is refused
#: because no limits are installed.
limits_setup_hint = "driver_setup/limits/notebooks/set_limits.ipynb"


def make_request(client, write):
    """The request that carries *write*; made only once the limits allowed it."""
    return request_of(client, write.request)


def check_reply(client, write) -> dict:
    """ZEN's reply to *write*, checked: ``{"success", "error", "transient", "kind", "logs"}``."""
    reply = client.reply
    if reply is None:
        return {
            "success": False,
            "error": "no reply from ZEN",
            "transient": True,
            "kind": Kind.TEMPORARY,
            "logs": [
                make_log_entry("warning", f"{write.description} | no reply from ZEN")
            ],
        }
    if reply.error is None:
        return {"success": True, "error": None, "transient": None, "logs": []}
    kind, detail = classify(reply.error)
    transient = sends_again(kind)
    return {
        "success": False,
        "error": detail,
        "transient": transient,
        "kind": kind,
        "logs": [
            make_log_entry(
                "warning" if transient else "error",
                f"{write.description} | ZEN answered {detail}",
            )
        ],
    }


def reply_value(client):
    """What ZEN answered to the last change, when it answered without error; None otherwise.

    A procedure reads it after a successful ``run()`` for what the response
    carries: the id of a loaded experiment, the name of a written file, a
    focus position.
    """
    reply = client.reply
    return None if reply is None or reply.error is not None else reply.value
