"""The three places LAS X can be asked, and which of them a reading may use.

A reading in the table of :mod:`readings` can have up to three legs: the CAM
API (asked on demand), the LAS X log (written only when a value changes) and
the direct reader (the hardware server's state tree). This file holds the one
function per leg that asks it once, and turns whatever happens into a
:class:`~..dispatcher.read.Reading`. A leg that fails never raises: the
error rides along in the reading so the shared reader in
``dispatcher/reader.py`` can decide what to do next.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import threading
import time
from functools import partial

from ...dispatcher.reader import Reading, counts
from ...connection.hardware_server import HardwareServerUnavailable
from ...primitives.log import log_reader
from ..sending import command_profiles as profiles
from . import reading_table as capabilities
from ...connection import waiting  # how long the shared reader waits: this microscope's numbers

log = logging.getLogger(__name__)


def spec(datum):
    """The row of the table of readings for *datum*."""
    return capabilities.spec(datum)


#: Handed to the shared reader so that it can tell a missing leg from a failure.
UnsupportedSource = capabilities.UnsupportedSource


def profile():
    """The reading policy of this driver: which source each datum prefers."""
    return profiles.STATE_READERS


def mode_for(datum):
    """The source family the policy gives *datum* (``api``, ``log``, ``hybrid`` or ``direct``)."""
    return getattr(profile(), capabilities.spec(datum).mode_attr)


def max_age_s(spec):
    """How old a log entry may be and still count, from the policy; None when the datum has no limit."""
    return (
        None
        if spec.log_max_age_attr is None
        else getattr(profile(), spec.log_max_age_attr)
    )


def api_read(fn) -> Reading:
    try:
        value = fn()
        # API has no independent freshness timestamp. observed_at/age_s mark
        # call completion, not proof that LAS X returned newly-produced state.
        return Reading(value=value, source="api", observed_at=time.time(), age_s=0.0)
    except Exception as exc:
        log.debug("api reader failed", exc_info=True)
        return Reading(
            value=None, source="api", observed_at=time.time(), age_s=None, error=exc
        )


def snapshot_read(spec, *, max_age_s, job_name=None) -> Reading:
    try:
        snapshot = log_reader.parse_log()
        if job_name is None:
            value = spec.log_fn(snapshot, max_age_s=max_age_s)
        else:
            value = spec.log_fn(snapshot, max_age_s=max_age_s, job_name=job_name)
        age_s = capabilities.age_for_snapshot(
            snapshot, age_key=spec.age_key, job_name=job_name, max_age_s=max_age_s
        )
        observed_at = None if age_s is None else snapshot.now - age_s
        return Reading(value=value, source="log", observed_at=observed_at, age_s=age_s)
    except Exception as exc:
        log.debug("log reader failed", exc_info=True)
        return Reading(
            value=None, source="log", observed_at=time.time(), age_s=None, error=exc
        )


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
        error=capabilities.UnsupportedSource(f"datum {datum!r} has no {mode} leg"),
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
        return _ask_api(
            spec, client, source_kwargs, until=until, answer_counts=answer_counts
        )
    return _ask_log(
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


# =============================================================================
# One CAM API request at a time per connection
# =============================================================================


class _ApiTurns:
    """Who may talk to the CAM API on each connection, one request at a time.

    LAS X answers a request by writing into a shared reply field with no
    request/response correlation, so two requests in flight on one
    connection can read each other's reply. A request holds the connection's
    turn until its own check for the answer ends. A reader that finds the
    turn taken waits for it, until its own deadline, instead of giving up:
    giving up was how a reading came back empty in 0.06 s right after a job
    switch whose confirmation request was still running.
    """

    def __init__(self):
        self._busy = set()
        self._changed = threading.Condition()

    def take(self, key, *, until, stop, poll_s):
        """Wait for the turn on *key* until *until* (monotonic); True when taken."""
        with self._changed:
            while key in self._busy:
                remaining = until - time.monotonic()
                if remaining <= 0 or stop.is_set():
                    return False
                self._changed.wait(min(remaining, poll_s))
            if stop.is_set():
                return False
            self._busy.add(key)
            return True

    def give_back(self, key):
        with self._changed:
            self._busy.discard(key)
            self._changed.notify_all()


_API_TURNS = _ApiTurns()

#: An event that is never set, for callers that have no reason to stop waiting for a turn.
_NEVER_STOPPED = threading.Event()


def _client_api_key(client):
    return id(client)


def watch_direct(spec, *, succeeded, deadline, observed_after, kwargs, waiting):
    """Ask the direct reader until an answer counts; a carried error at once when it fails.

    A caller *waiting* for a change (a confirmation's ``accept`` or
    ``observed_after``) is asked again every ``POLL_S`` until *deadline*,
    and gets None when nothing counted in time. Any other caller gets one
    answer: one that does not count is a failure, so the fallback answers
    at once instead of after the whole wait.
    """
    while True:
        reading = direct_read(spec, kwargs)
        if reading.error is not None or counts(reading, succeeded, observed_after):
            return reading
        if not waiting:
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


def _ask_api(spec, client, api_kwargs, *, until, answer_counts):
    """Request the read, and request it again every ``POLL_S`` until an answer counts or *until*.

    One request at a time per connection: a busy connection is waited for,
    never skipped. Each request runs in a small helper thread that this
    function waits for until *until*, so that a LAS X call that never returns
    cannot freeze the driver: the caller moves on, and the connection's turn
    is handed back when the call finally ends, so that no second request is
    piled onto a hung one. Returns the last reading, or None when no answer
    came in time.
    """
    key = _client_api_key(client)
    reading = None
    while time.monotonic() < until:
        if not _API_TURNS.take(
            key, until=until, stop=_NEVER_STOPPED, poll_s=waiting.POLL_S
        ):
            break
        done = threading.Event()
        answers = []

        def request(answers=answers, done=done):
            try:
                answers.append(
                    api_read(lambda: spec.api_fn(client, deadline=until, **api_kwargs))
                )
            finally:
                _API_TURNS.give_back(key)
                done.set()

        threading.Thread(target=request, name="lasx-api-read", daemon=True).start()
        if not done.wait(timeout=max(0.0, until - time.monotonic())):
            break
        reading = answers[0]
        if answer_counts(reading):
            return reading
        time.sleep(max(0.0, min(waiting.POLL_S, until - time.monotonic())))
    return reading


def _ask_log(spec, job_name, max_age_s, *, until, answer_counts):
    """Read the LAS X log, and again every ``POLL_S`` until an answer counts or *until*.

    Each read takes the shared live snapshot (``log_reader.LIVE_LOG``), so
    however many readings are taken at once, the log is parsed by one parse
    at a time.
    """
    reading = None
    while True:
        reading = snapshot_read(spec, max_age_s=max_age_s, job_name=job_name)
        remaining = until - time.monotonic()
        if answer_counts(reading) or remaining <= 0:
            return reading
        time.sleep(min(waiting.POLL_S, remaining))
