"""What the dispatcher needs to drive NIS-Elements through the bridge: the readings, the waiting rule, the sending.

This is the profile handed to the dispatcher's ``run()``.

- **For a reading** it holds the table of readings (:data:`DATUMS`: each with
  its read function from ``reads.py`` and what counts as an answer), the one
  source there is (the bridge), and how it is asked again until an answer
  counts (:func:`ask`). A reading that cannot change by asking again (the
  stage is not connected, the request is wrong) stops at once, instead of
  using up its windows.
- **For a change** it holds how the write loops send (:data:`WRITING`), how a
  request is made from a ``Write`` (:func:`make_request`), and how NIS's reply
  is checked (:func:`check_reply`). A reply is sorted by its kind alone, never
  by words in the error: only a request NIS never ran (``queued_timeout``) is
  sent again.

The waiting rule is four windows of three seconds, looked at every
``POLL_S``. One round trip through the bridge took about 11 ms on
NIS-Elements AR 7.01.03 (step 1, 26 October 2026), so looking every 50 ms
costs little. The numbers are read at call time, so a test can shrink them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from ...dispatcher.read import Reading
from . import reads
from . import server as bridge
from .client import BridgeUnavailable, NisRefused
from .transfer import BRIDGE, Request

log = logging.getLogger(__name__)

#: The one source every reading asks.
SOURCE = "bridge"


class Waiting:
    """The waiting rule's four numbers, read at call time."""

    WINDOWS = 4
    WINDOW_S = 3.0
    POLL_S = 0.05
    ANSWER_POLL_S = 0.01


waiting = Waiting

#: The shortest time one call of a reading is given, however little is left of its window.
SHORTEST_CALL_S = 0.5


# =============================================================================
# The table of readings
# =============================================================================


class UnsupportedSource(RuntimeError):
    """The reading has no source of the kind asked for; the bridge is the only one."""


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def xy_present(value, *, about=None) -> bool:
    return (
        isinstance(value, dict)
        and _finite(value.get("x_um"))
        and _finite(value.get("y_um"))
    )


def z_present(value, *, about=None) -> bool:
    return isinstance(value, dict) and _finite(value.get("z_um"))


def is_dict(value, *, about=None) -> bool:
    return isinstance(value, dict)


def is_text(value, *, about=None) -> bool:
    return isinstance(value, str) and bool(value)


def is_list(value, *, about=None) -> bool:
    return isinstance(value, list)


@dataclass(frozen=True)
class DatumSpec:
    """One reading: its name, how the bridge reads it, and what counts as its answer."""

    name: str
    read: Callable
    success: Callable


#: What counts as an answer to each reading; its keys are the readings there are.
SUCCESS = {
    "version": is_text,
    "xy": xy_present,
    "z": z_present,
    "stage_limits": is_dict,
    "optical_configurations": is_list,
    "selected_optical_configuration": is_dict,
    "nosepiece": is_dict,
    "pfs": is_dict,
    "image_info": is_dict,
    "calibration": is_dict,
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
    """The source a reading asks first: the bridge, for every reading."""
    return SOURCE


def legs(spec, datum, mode):
    """Which sources take part: the bridge; any other name has no source."""
    if mode != SOURCE:
        return Reading(
            None,
            mode,
            None,
            None,
            UnsupportedSource(f"reading {datum!r} has no {mode} source"),
        )
    return (SOURCE,)


def first_stage(spec, mode, fallback):
    """No source is tried alone first: the bridge is asked within the window like any source."""
    return None, mode


def success(spec, value, about):
    """Whether *value* counts as an answer to the reading."""
    return spec.success(value, about=about)


def lasting(error: BaseException | None) -> bool:
    """Whether a reading's error will not change by asking again within the window."""
    if isinstance(error, BridgeUnavailable):
        return True
    if isinstance(error, NisRefused):
        return (
            error.kind in (bridge.KIND_NOT_ALLOWED, bridge.KIND_BAD_REQUEST)
            or error.code in reads.LASTING_CODES
        )
    return False


def read_once(spec, client, until: float) -> Reading:
    """One answer from the bridge, given the time left before *until*; an error is carried, never raised."""
    timeout = max(SHORTEST_CALL_S, until - time.monotonic())
    try:
        with client.reading_within(timeout):
            value = spec.read(client)
        return Reading(value=value, source=SOURCE, observed_at=time.time(), age_s=0.0)
    except Exception as why:  # noqa: BLE001 -- every reason is kept for the caller
        return Reading(
            value=None, source=SOURCE, observed_at=time.time(), age_s=None, error=why
        )


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask the bridge, and again every ``POLL_S`` until an answer counts, *until*, or an error that will not change."""
    while True:
        reading = read_once(spec, client, until)
        if answer_counts(reading) or lasting(reading.error):
            return reading
        if time.monotonic() + waiting.POLL_S >= until:
            return reading
        time.sleep(waiting.POLL_S)


# =============================================================================
# What the dispatcher's run() asks of NIS for a change
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change to NIS: the fields ``dispatcher/write.py`` reads."""

    transport: object = BRIDGE
    waiting: object = Waiting
    max_retries: int = Waiting.WINDOWS - 1
    max_confirm_attempts: int = Waiting.WINDOWS
    refire_on_unconfirmed: bool = True
    send_without_receipt: bool = False
    never_resend: bool = False


#: How every change is sent.
WRITING = WriteProfile()

#: Where the operator creates the limits file, said when a change is refused for want of limits.
limits_setup_hint = "driver_setup/limits/notebooks/set_limits.ipynb"


def make_request(client, write):
    """The bridge request that carries *write*: a ``write`` of its NIS function, or a ``snap``."""
    return Request(write.op, write.request)


def check_reply(client, write) -> dict:
    """NIS's reply to *write*, checked: ``{"success", "error", "transient", "logs"}``.

    Only ``queued_timeout`` is temporary: NIS never ran the request, so
    sending it again is safe. Everything else is permanent, including
    ``running_timeout``, whose outcome is unknown. A device function that
    answered a DR code of 0 or below failed, with the code's name as the reason.
    """
    reply = client.last_reply
    if reply is None:
        return {
            "success": False,
            "error": "no reply from the bridge",
            "transient": False,
            "logs": [],
        }
    if not reply.get("ok"):
        error = str(reply.get("error") or "the bridge gave no reason")
        return {
            "success": False,
            "error": error,
            "transient": reply.get("kind") == bridge.KIND_QUEUED_TIMEOUT,
            "logs": [],
        }
    if write.dr_code:
        code = (reply.get("answer") or {}).get("return")
        if reads.is_failure_code(code):
            return {
                "success": False,
                "error": f"{write.request}: {reads.dr_name(code)}",
                "transient": False,
                "logs": [],
            }
    return {"success": True, "error": None, "transient": None, "logs": []}
