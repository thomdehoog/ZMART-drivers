"""The order in which LAS X is asked and told: the readings and their sources, and the routes of a change.

Readings. Every reading is declared here once (``DATUMS``): the answer each
source can give (the CAM API's from ``cam_api/reads.py``, the log's from
``log/profile.py``, the direct reader's from ``READS`` below) and what counts as
an answer. ``STATE_READERS`` says which source each reading asks first and which
answers when it cannot. This module is what the read loop
(``dispatcher/read.py``) is handed: it reads the table through ``spec``,
``legs``, ``ask`` and the others below, and the waiting numbers through
``waiting``. A source the reading does not have fails closed with
``UnsupportedSource``, and ``hybrid`` asks the sources that exist.

Routes. Two ways of sending a change: the direct route (``transfer.py``, straight
to LAS X's hardware server) and the CAM API (``cam_api/transfer.py``). The direct
route is tried first; the CAM API carries a command when the direct route could
not send it (the hardware server is not reachable, or the command has no direct
translation). A route that has sent a command is never followed by another for
the same command.

The order of the routes can be changed without editing code, for a comparison or
to switch the direct route off: set the environment variable
``ZMART_WRITE_ROUTES`` to ``cam`` (the CAM API only), ``direct`` or
``direct,cam`` (the default). Every change sends through :data:`ROUTED`, which
reads this policy for each command.

Writes. This module is also what the dispatcher's ``run()`` is handed for a
change (a ``Write`` from ``cam_api/writes.py``): ``limits_setup_hint``,
``make_request``, ``check_reply`` and ``WRITING``, how the write loops send.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from ...dispatcher.read import Reading, counts
from ...dispatcher.write import TransportChain
from ..cam_api import parsing, profile as waiting
from ..cam_api.profile import check_reply  # noqa: F401 -- what the write loops ask this profile for
from ..cam_api import reads as api_reader
from ..cam_api import transfer as _transport
from ..log import profile as log_profile
from ..log import reads as log_reader
from . import reads as direct_reader
from .client import HardwareServerUnavailable
from .transfer import DIRECT

log = logging.getLogger(__name__)


# =============================================================================
# Which source each reading asks
# =============================================================================


@dataclass(frozen=True)
class StateReaderProfile:
    """Profile-controlled backend selection for passive state reads.

    These modes govern routed state reads, including command confirmation.
    Freshness limits reject old log state; transition-sensitive confirmations
    additionally reject observations from before the command. Reads that
    produce persisted or foundational correctness artifacts still use this
    policy. Callers never pin a backend; source availability belongs to the
    table of readings (``DATUMS``) and source preference belongs here.

    Nothing here says how long to wait or how often to look: that is the one
    rule in ``cam_api/profile.py``. A mode says which sources take part; how
    old a log answer may be and still count is ``log/profile.py``'s
    ``MAX_AGE_S``, because each kind of state goes stale at its own pace.

    Every reading asks the direct reader first (``direct``: LAS X's
    hardware server's state tree; Thom's decision, 2026-10-13). When it
    fails, for whatever reason (the hardware server not reachable, another
    LAS X version laying the tree out differently, an answer that does not
    count), the hybrid reader (CAM API and log) answers; the reading names
    its source. Measured on the simulator, 2026-10-13: XY read directly in
    1.1 ms against the CAM API's 20 ms (worst 57 ms), and a new stage
    position seen 3.3-4.2 ms after a move against 22-58 ms.
    """

    # --- Which source answers when the first one cannot ---------------------
    # Each reading asks the source named by its ``*_mode`` first. When that
    # source cannot answer, one of these families takes its place, and the
    # reading names the source it came from. Each is "api", "log" or "hybrid"
    # (the API and the log asked together).
    #
    # An ordinary reading falls back to the CAM API alone: the direct reader
    # first, then the API (Thom, 2026-10-21). "hybrid" would let the log answer
    # too, which can still show the state from before.
    reading_fallback: str = "api"
    # A reading a command waits on (is the scanner idle, did the stage arrive)
    # falls back to the API alone. The log writes a value only when it changes,
    # so it can still show the state from before the command.
    waiting_fallback: str = "api"
    # Confirming a job change: when the direct reader cannot answer, this
    # takes its place. Hybrid asks the API's job list, then the log's
    # ``CurrentBlock`` event. The API's list can be stale on the real scope
    # (15 s and more, the wrong job), so a job change whose pre-change API
    # answer is missing, or already the target, is confirmed by the log alone
    # (``procedures/changes.py``, ``select_job``).
    selected_job_confirm_fallback: str = "hybrid"

    xy_mode: str = "direct"

    job_settings_mode: str = "direct"

    # The job list comes from the direct reader, with the API as its fallback:
    # the log stream only reports the ACTIVE job, so its list is incomplete (a
    # job not re-dumped this session is absent). Confirmed on the bench
    # (2026-07-06). selected_job (which job is active) is what the log can
    # see, so its fallback is hybrid.
    jobs_mode: str = "direct"

    selected_job_mode: str = "direct"
    # Before a job change confirmed through the log, ask the API for every
    # job's settings, so that LAS X writes the whole job cluster to the log.
    selected_job_log_prime_cluster: bool = False
    # How old the log's selected job may be when it decides that the job is
    # already selected; None: the log's own (``log/profile.py``).
    selected_job_log_cluster_max_age_s: float | None = None

    # Where a z drive is: "direct", the hardware server; any other mode, the job
    # settings, and for the drive carrying the job's z-stack, whose settings
    # value is stale, the experiment LAS X saves (``parsing.z_um_from_settings``).
    z_mode: str = "direct"

    hardware_info_mode: str = "direct"

    scan_status_mode: str = "direct"

    def __post_init__(self):
        for name in (
            "reading_fallback",
            "waiting_fallback",
            "selected_job_confirm_fallback",
        ):
            if getattr(self, name) not in ("api", "log", "hybrid"):
                raise ValueError(
                    f"{name} must be 'api', 'log' or 'hybrid', not {getattr(self, name)!r}"
                )


#: Looked up each time a reading is taken, so a test or a validator can replace it.
STATE_READERS = StateReaderProfile()


# =============================================================================
# What counts as an answer
# =============================================================================


class UnsupportedSource(RuntimeError):
    """The requested source family has no leg for this datum."""


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def scan_status_known(value, *, job_name=None) -> bool:
    """A scanner status LAS X actually reported; ``Unknown`` is no answer."""
    return isinstance(value, str) and value not in ("", "Unknown")


def hardware_info_present(value, *, job_name=None) -> bool:
    return isinstance(value, dict) and bool(value)


def xy_present(value, *, job_name=None) -> bool:
    """Both stage coordinates, as finite numbers."""
    return (
        isinstance(value, dict)
        and _finite(value.get("x_um"))
        and _finite(value.get("y_um"))
    )


def jobs_listed(value, *, job_name=None) -> bool:
    """A job list whose every entry names its job; an empty list is a real answer."""
    return isinstance(value, list) and all(
        isinstance(j, dict) and j.get("Name") for j in value
    )


def selected_job_named(value, *, job_name=None) -> bool:
    return isinstance(value, dict) and bool(value.get("Name"))


def job_settings_complete(value, *, job_name=None) -> bool:
    """Settings for the job asked about, with an objective slot and image geometry.

    The objective slot is what a job or objective change reads to keep the
    sample point; settings without it once let a job switch be refused after
    LAS X had confirmed it. Settings naming another job are a stray reply to
    an earlier request. Blank geometry is LAS X still repopulating after a
    zoom or format change.
    """
    if not isinstance(value, dict):
        return False
    if job_name is not None and value.get("jobName") not in (None, job_name):
        return False
    objective = value.get("objective")
    if not isinstance(objective, dict) or objective.get("slotIndex") is None:
        return False
    return parsing.settings_geometry_ready(value)


# =============================================================================
# The table of readings
# =============================================================================


@dataclass(frozen=True)
class DatumSpec:
    """Source capabilities for one datum, and what counts as its answer.

    Passive legs are optional callables. ``mode_attr`` names a
    ``StateReaderProfile`` attribute, so every choice stays in the profile;
    ``name`` keys the log's ``MAX_AGE_S``. ``success(value, *, job_name)`` says
    when an answer from either source counts; the first answer that does,
    wins. How long to wait is not per datum: it is the one rule in
    ``cam_api/profile.py``.
    """

    name: str
    mode_attr: str
    success: Callable
    api_fn: Callable | None = None  # (client, *, deadline, should_stop) -> raw value
    log_fn: Callable | None = None  # (snapshot, *, max_age_s[, job_name])
    age_key: str | None = None  # key for log_reader.age_for_snapshot()
    direct_fn: Callable | None = None  # ([job_name]) -> value, from the direct reader


#: The direct reader's answer to each reading it can answer.
READS = {
    "scan_status": direct_reader.get_scan_status,
    "job_settings": direct_reader.get_job_settings,
    "hardware_info": direct_reader.get_hardware_info,
    "xy": direct_reader.get_xy,
    "jobs": direct_reader.get_jobs,
    "selected_job": direct_reader.get_selected_job,
}

#: What counts as an answer to each reading; its keys are the readings there are.
SUCCESS = {
    "scan_status": scan_status_known,
    "job_settings": job_settings_complete,
    "hardware_info": hardware_info_present,
    "xy": xy_present,
    "jobs": jobs_listed,
    "selected_job": selected_job_named,
}

DATUMS = {
    name: DatumSpec(
        name=name,
        mode_attr=f"{name}_mode",
        success=success,
        api_fn=api_reader.READS.get(name),
        log_fn=log_profile.READS.get(name),
        age_key=log_profile.AGE_KEYS.get(name),
        direct_fn=READS.get(name),
    )
    for name, success in SUCCESS.items()
}


def spec(datum) -> DatumSpec:
    """The row of the table of readings for *datum*."""
    found = DATUMS.get(datum)
    if found is None:
        raise ValueError(f"unknown datum {datum!r}; known: {sorted(DATUMS)}")
    return found


# =============================================================================
# What the read loop asks of LAS X
# =============================================================================


def profile():
    """The reading policy of this driver: which source each datum prefers."""
    return STATE_READERS


def mode_for(datum):
    """The source family the policy gives *datum* (``api``, ``log``, ``hybrid`` or ``direct``)."""
    return getattr(profile(), spec(datum).mode_attr)


def on_demand_mode(datum):
    """The direct reader when the policy reads *datum* directly, else the API: the sources that answer on demand.

    For readings a command waits on (whether the scanner is idle, where the
    stage went), which the log cannot settle: it writes a value only when it
    changes, so it can still show the state from before. Pair it with
    :func:`on_demand_fallback`.
    """
    return "direct" if mode_for(datum) == "direct" else "api"


def on_demand_fallback():
    """The family that answers a waited-on reading when the direct reader cannot (the profile's ``waiting_fallback``)."""
    return profile().waiting_fallback


def source_arguments(about, source_kwargs):
    """What the CAM API and the direct reader are asked with: the job a reading is about, and what the caller adds."""
    return {**({"job_name": about} if about is not None else {}), **(source_kwargs or {})}


def z_um_direct(drive, *, mode=None):
    """Where one z drive truly is (um), from the direct reader; None when it does not answer or fails.

    It is asked in ``direct`` mode (the profile's ``z_mode`` unless
    *mode* says); None also when the hardware server cannot be reached, so
    the caller reads the job settings instead.
    """
    mode = mode if mode is not None else profile().z_mode
    if mode != "direct":
        return None
    try:
        return direct_reader.read_z_um(drive)
    except HardwareServerUnavailable as why:
        log.debug("%s: %s; the job settings answer", drive, why)
    except Exception as why:
        log.warning(
            "the direct reader failed on %s (%s: %s); the job settings answer",
            drive,
            type(why).__name__,
            why,
        )
    return None


def max_age_s(spec):
    """How old a log entry may be and still count; None when the datum has no limit."""
    return log_profile.MAX_AGE_S.get(spec.name)


class DirectAnswerDidNotCount(RuntimeError):
    """The direct reader answered, but not with an answer that counts for the datum."""


def direct_read(spec, direct_kwargs) -> Reading:
    """One answer from the direct reader; any error it raises is carried, never raised.

    The hardware server not reachable is expected wherever LAS X is not
    running; any other error means the tree is not as this reader knows it
    (another LAS X version, another instrument), which is worth a warning.
    Either way the fallback answers.
    """
    try:
        value = spec.direct_fn(**direct_kwargs)
        return Reading(value=value, source="direct", observed_at=time.time(), age_s=0.0)
    except HardwareServerUnavailable as exc:
        return Reading(
            value=None, source="direct", observed_at=time.time(), age_s=None, error=exc
        )
    except Exception as exc:
        log.warning(
            "the direct reader failed (%s: %s); its fallback answers",
            type(exc).__name__,
            exc,
        )
        return Reading(
            value=None, source="direct", observed_at=time.time(), age_s=None, error=exc
        )


def unsupported(mode, datum) -> Reading:
    return Reading(
        value=None,
        source=mode,
        observed_at=None,
        age_s=None,
        error=UnsupportedSource(f"datum {datum!r} has no {mode} leg"),
    )


def legs(spec, datum, mode):
    """Which sources take part for *mode*; an ``UnsupportedSource`` reading if none can."""
    if mode == "api":
        return ("api",) if spec.api_fn is not None else unsupported("api", datum)
    if mode == "log":
        return ("log",) if spec.log_fn is not None else unsupported("log", datum)
    if mode == "hybrid":
        legs = tuple(
            leg
            for leg, fn in (("api", spec.api_fn), ("log", spec.log_fn))
            if fn is not None
        )
        return legs or unsupported("hybrid", datum)
    raise ValueError(f"unknown state-reader mode {mode!r}")


def success(spec, value, about):
    """Whether *value* counts as an answer to the reading; for LAS X, *about* is the job asked about."""
    return spec.success(value, job_name=about)


def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
    """Ask one source of LAS X, and again until an answer counts or *until*; return the last Reading.

    This is what the shared reader calls for each source of a window. The
    reader does not know how LAS X is asked: the CAM API one request at a time,
    the log through its shared snapshot. For LAS X, *about* is the name of the
    job a reading is about.
    """
    if leg == "api":
        return api_reader._ask_api(
            spec,
            client,
            source_arguments(about, source_kwargs),
            until=until,
            answer_counts=answer_counts,
        )
    return log_reader._ask_log(
        spec, about, max_age_s(spec), until=until, answer_counts=answer_counts
    )


def first_stage(spec, mode, fallback):
    """The source tried alone before the others, and the mode for the rest.

    In ``direct`` mode the direct reader goes first, in the caller's own thread
    (its COM connection belongs to the thread that made it). It is not raced:
    when the hardware server cannot be reached, or the datum has no direct leg,
    the *fallback* family answers instead, within the same window.
    """
    fallback = profile().reading_fallback if fallback is None else fallback
    if mode != "direct":
        return None, mode
    if spec.direct_fn is None:
        return None, fallback
    return partial(watch_direct, spec), fallback


def watch_direct(
    spec, *, succeeded, deadline, observed_after, kwargs, waiting_for_change, about=None
):
    """Ask the direct reader until an answer counts; a carried error at once when it fails.

    A caller waiting for a change (*waiting_for_change*: a confirmation's ``accept`` or
    ``observed_after``) is asked again every ``POLL_S`` until *deadline*,
    and gets None when nothing counted in time. Any other caller gets one
    answer: one that does not count is a failure, so the fallback answers
    at once instead of after the whole wait. *about* is the job a reading
    is about.
    """
    while True:
        reading = direct_read(spec, source_arguments(about, kwargs))
        if reading.error is not None or counts(reading, succeeded, observed_after):
            return reading
        if not waiting_for_change:
            return Reading(
                value=None,
                source="direct",
                observed_at=reading.observed_at,
                age_s=None,
                error=DirectAnswerDidNotCount(
                    f"the direct reader answered {reading.value!r}"
                ),
            )
        if time.monotonic() + waiting.POLL_S >= deadline:
            return None
        time.sleep(waiting.POLL_S)


# =============================================================================
# The routes of a change
# =============================================================================

#: Every way of sending that exists, by name.
ROUTES = {"direct": DIRECT, "cam": _transport.LASX}

#: The routes tried for a change, best first. Each name must be in ``ROUTES``.
WRITE_ROUTES = tuple(
    name.strip()
    for name in os.environ.get("ZMART_WRITE_ROUTES", "direct,cam").split(",")
    if name.strip()
)


def write_routes():
    """The routes to try now, as ``[(name, transport), ...]``, best first."""
    unknown = [name for name in WRITE_ROUTES if name not in ROUTES]
    if unknown:
        raise ValueError(
            f"WRITE_ROUTES names {unknown}, but the routes that exist are {sorted(ROUTES)}"
        )
    return [(name, ROUTES[name]) for name in WRITE_ROUTES]


#: The transport every change sends through.
ROUTED = TransportChain(write_routes)


# =============================================================================
# What the dispatcher's run() asks of LAS X for a change
# =============================================================================


@dataclass(frozen=True)
class WriteProfile:
    """How the write loops send a change to LAS X: the fields ``dispatcher/write.py`` reads.

    Every change is sent through :data:`ROUTED` by the one waiting rule:
    ``WINDOWS`` tries in all after a temporary error, ``WINDOWS`` confirmation
    windows, and sent again between them. What differs per change (sent
    without a receipt, never sent again) is on the ``Write`` and handed to the
    loops by ``run()``.
    """

    transport: object = ROUTED
    waiting: object = waiting
    max_retries: int = waiting.WINDOWS - 1
    max_confirm_attempts: int = waiting.WINDOWS
    refire_on_unconfirmed: bool = True
    send_without_receipt: bool = False
    never_resend: bool = False


#: How every change is sent.
WRITING = WriteProfile()

#: Where the operator creates the limits files, said when a change is refused
#: because no limits are installed (and by ``driver_setup/limits``).
limits_setup_hint = "driver_setup/limits/notebooks/set_limits.ipynb"


def make_request(client, write):
    """The CAM API request that carries *write*, with what opens a capture's experiment; made only once the limits allow it."""
    return _transport.request_of(client, write.request, write.experiment)
