"""What the dispatcher needs to handle the direct class: the readings, the waiting rule, the reply check, how a change is sent.

Readings. Every reading is declared here once (``DATUMS``): the direct
reader's answer (``reads.py``) and what counts as an answer. This driver has
one class of primitive, so every reading asks the direct reader, within the
windows of the waiting rule, and there is no other source to fall back on:
when the hardware server cannot be reached the reading is unknown. The read
loop (``dispatcher/read.py``) reads the table through ``spec``, ``legs``,
``ask`` and the others below.

The waiting rule. Every wait on LAS X follows one rule: ``WINDOWS`` windows
of ``WINDOW_S`` seconds, looking again every ``POLL_S``. Within a window the
reading is asked again and again until an answer counts; between windows a
change is sent again where that is safe; after the last window a reading is
unknown and a change is unconfirmed. The numbers are read at call time, so
the offline tests can shrink them.

Writes. This module is also what ``run()`` is handed for a change (a
``Write`` from ``writes.py``): ``limits_setup_hint``, ``make_request``,
``check_reply`` and ``WRITING``, how the write loops send. LAS X's answer
to a refused element is sorted temporary or permanent here, and only a
temporary problem is sent again.

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

from ...dispatcher.dispatch import make_log_entry
from ...dispatcher.read import Reading
from . import reads as direct_reader
from .client import HardwareServerUnavailable
from .transfer import DIRECT

log = logging.getLogger(__name__)

# =============================================================================
# The waiting rule: four windows of three seconds
# =============================================================================

WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01

#: This module, as the dispatcher reads the numbers above through ``waiting``.
waiting = sys.modules[__name__]


# =============================================================================
# What counts as an answer
# =============================================================================


class UnsupportedSource(RuntimeError):
    """A reading was asked of a source this driver does not have."""


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def scan_status_known(value) -> bool:
    """A scanner status LAS X actually reported; ``Unknown`` is no answer."""
    return isinstance(value, str) and value not in ("", "Unknown")


def xy_present(value) -> bool:
    """Both stage coordinates, as finite numbers."""
    return isinstance(value, dict) and _finite(value.get("x_um")) and _finite(value.get("y_um"))


def number_present(value) -> bool:
    return not isinstance(value, bool) and _finite(value)


def dict_present(value) -> bool:
    return isinstance(value, dict) and bool(value)


def text_present(value) -> bool:
    return isinstance(value, str) and bool(value)


def listed(value) -> bool:
    """A list is a real answer, an empty one too: the plain Navigator has no jobs."""
    return isinstance(value, list)


def pair_present(value) -> bool:
    return isinstance(value, tuple) and len(value) == 2 and all(_finite(v) for v in value)


@dataclass(frozen=True)
class DatumSpec:
    """One reading: its name, how the direct reader answers it, and what counts as its answer.

    ``direct_fn`` is called with ``about`` when the reading is about one
    item (a z drive's name), else with nothing.
    """

    name: str
    direct_fn: Callable
    success: Callable
    takes_about: bool = False


DATUMS = {
    spec.name: spec
    for spec in (
        DatumSpec("scan_status", direct_reader.get_scan_status, scan_status_known),
        DatumSpec("scan_mode", direct_reader.get_scan_mode, text_present),
        DatumSpec("xy", direct_reader.get_xy, xy_present),
        DatumSpec("z", direct_reader.read_z_um, number_present, takes_about=True),
        DatumSpec("zoom", direct_reader.get_zoom, dict_present),
        DatumSpec("scan_speed", direct_reader.get_scan_speed, dict_present),
        DatumSpec("scan_speed_range", direct_reader.get_scan_speed_range, dict_present),
        DatumSpec("scan_field_rotation", direct_reader.get_scan_field_rotation, dict_present),
        DatumSpec("averaging", direct_reader.get_averaging, dict_present),
        DatumSpec("pinhole", direct_reader.get_pinhole, dict_present),
        DatumSpec("format", direct_reader.get_format, text_present),
        DatumSpec("objective", direct_reader.get_objective, dict_present),
        DatumSpec("objectives", direct_reader.get_objectives, listed),
        DatumSpec("fov", direct_reader.get_fov, pair_present),
        DatumSpec("pixel_size", direct_reader.get_pixel_size, pair_present),
        DatumSpec("bit_depth", direct_reader.get_bit_depth, number_present),
        DatumSpec("z_stack", direct_reader.get_z_stack, lambda v: v is None or dict_present(v)),
        DatumSpec("serial_number", direct_reader.get_serial_number, text_present),
        DatumSpec("jobs", direct_reader.get_jobs, listed),
        DatumSpec("hardware_info", direct_reader.get_hardware_info, dict_present),
    )
}


def spec(datum) -> DatumSpec:
    """The row of the table of readings for *datum*."""
    found = DATUMS.get(datum)
    if found is None:
        raise ValueError(f"unknown datum {datum!r}; known: {sorted(DATUMS)}")
    return found


# =============================================================================
# What the read loop asks of this driver
# =============================================================================


def mode_for(datum):
    """The one class every reading asks: the direct reader."""
    return "direct"


def first_stage(spec, mode, fallback):
    """No source is tried alone before the others: the direct reader is asked as the one leg."""
    return None, mode


def legs(spec, datum, mode):
    """The sources that take part: the direct reader, whatever *mode* says; it is the only one."""
    return ("direct",)


def success(spec, value, about):
    """Whether *value* counts as an answer to the reading."""
    return spec.success(value)


def direct_read(spec, about) -> Reading:
    """One answer from the direct reader; any error it raises is carried, never raised."""
    try:
        value = spec.direct_fn(about) if spec.takes_about else spec.direct_fn()
        return Reading(value=value, source="direct", observed_at=time.time(), age_s=0.0)
    except HardwareServerUnavailable as exc:
        return Reading(value=None, source="direct", observed_at=time.time(), age_s=None, error=exc)
    except Exception as exc:  # noqa: BLE001 -- the tree is not as this reader knows it: carried, with a warning
        log.warning("the direct reader failed (%s: %s)", type(exc).__name__, exc)
        return Reading(value=None, source="direct", observed_at=time.time(), age_s=None, error=exc)


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask the direct reader, and again every ``POLL_S`` until an answer counts or *until*; the last Reading.

    This is what the read loop calls for the one leg of a window. A reading
    that does not count yet ("not what we hoped for yet", or the hardware
    server not reachable just now) is asked again until the window ends.
    """
    if leg != "direct":
        return Reading(
            value=None,
            source=leg,
            observed_at=None,
            age_s=None,
            error=UnsupportedSource(f"this driver has no {leg} source for {spec.name!r}"),
        )
    while True:
        reading = direct_read(spec, about)
        if answer_counts(reading) or time.monotonic() + POLL_S >= until:
            return reading
        time.sleep(POLL_S)


# =============================================================================
# What the dispatcher's run() asks of this driver for a change
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change: the fields ``dispatcher/write.py`` reads.

    Every change is sent through the direct route by the one waiting rule:
    ``WINDOWS`` tries in all after a temporary error, ``WINDOWS``
    confirmation windows, and sent again between them. What differs per
    change (never sent again) is on the ``Write`` and handed to the loops by
    ``run()``.
    """

    transport: object = DIRECT
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


def make_request(client, write):
    """The request that carries *write*, empty until the change fills it in; made only once the limits allow it."""
    from .writes import Request

    return Request(write.request)


# --- LAS X's refusal, sorted temporary or permanent -------------------------------

#: Words in LAS X's reason that mean the request itself was wrong, or cannot be helped by sending again.
PERMANENT_WORDS = (
    "out of range",
    "is invalid",
    "invalid",
    "not defined",
    "not found",
    "has been adjusted",
    "not implemented",
    "deleted element",
)

#: Words that mean LAS X is busy for now: sending again can work.
TEMPORARY_WORDS = (
    "being scanned",
    "cannot be set while",
    "block is being",
    "busy",
    "locked",
    "timeout",
    "timed out",
)


def is_temporary(reason: str) -> bool:
    """Whether sending again is worthwhile after LAS X refused with *reason*.

    Permanent words are checked first and win; a reason that matches
    nothing counts as permanent, which is the safe choice.
    """
    lower = (reason or "").lower()
    if any(word in lower for word in PERMANENT_WORDS):
        return False
    if any(word in lower for word in TEMPORARY_WORDS):
        return True
    if lower:
        log.warning("LAS X's reason is not one the driver knows (treated as permanent): %s", reason)
    return False


def check_reply(client, write):
    """LAS X's reply to *write*, checked: ``{"success", "error", "transient", "logs"}``."""
    reply = client.reply
    if not reply:
        message = f"{write.description} | LAS X gave no answer"
        return {"success": False, "error": message, "transient": False, "logs": [make_log_entry("error", message)]}
    if reply.get("accepted"):
        return {"success": True, "error": None, "transient": None, "logs": []}
    reason = reply.get("text") or reply.get("answer") or "(no reason given)"
    transient = is_temporary(reason)
    message = f"{write.description} | LAS X refused: {reason}"
    return {
        "success": False,
        "error": reason,
        "transient": transient,
        "logs": [make_log_entry("warning" if transient else "error", message)],
    }
