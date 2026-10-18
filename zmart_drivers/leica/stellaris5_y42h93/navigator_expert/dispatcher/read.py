"""The shared reader: one way to wait for an answer, whichever source gives it.

Every reading follows the one rule in :mod:`tuning`: four windows of three
seconds. Within a window both sources the profile allows are watched, the
CAM API and the LAS X log, and the first answer that counts as a success
wins, from whichever source gives it; no source is preferred. What counts as
a success is declared once per datum in :mod:`capabilities` (``success``),
and a caller can narrow it with ``accept``. A read the API did not answer in
a window is requested again at the next one. After four windows the reading
is unknown.

The source family (``api``, ``log``, ``hybrid``, ``direct``) is profile policy
and says which sources take part, never how long to wait. Asking a family for
a leg the datum does not have fails closed at once with ``UnsupportedSource``.

``direct`` is the direct reader (LAS X's hardware server's state tree), read
in the caller's own thread (its COM connection belongs to the thread that
made it) and looked at
again every ``POLL_S`` until an answer counts. It is not raced: when the
hardware server cannot be reached, or the datum has no direct leg, the
*fallback* family answers instead (``hybrid`` unless the caller says), within
the same window.

Public functions keep the plain return shapes by default; pass
``diagnostics=True`` to receive a :class:`Reading` with its source and age.
A caller that already runs its own window, such as a command confirmation,
passes ``deadline=`` to watch only until then.

The legs themselves (one question to the API, the log or the direct reader)
live in ``vendor_interface/primitive_handling/sources.py``; the named readings
the rest of the driver asks for (``get_xy`` and the others) live in
``vendor_interface/primitive_handling/state.py`` and call :func:`wait_for` here.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from ..vendor_interface.primitive_handling import readings as capabilities
from ..vendor_interface.primitive_handling import sources
from . import tuning
from .reading import Reading

log = logging.getLogger(__name__)


def on_demand_mode(datum):
    """The direct reader when the policy reads *datum* directly, else the API: the sources that answer on demand.

    For readings a command waits on (whether the scanner is idle, where the
    stage went), which the log cannot settle: it writes a value only when it
    changes, so it can still show the state from before. Pair it with
    ``fallback="api"``.
    """
    return "direct" if sources.mode_for(datum) == "direct" else "api"


# =============================================================================
# One API request at a time per connection
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

    def take(self, key, *, until, stop):
        """Wait for the turn on *key* until *until* (monotonic); True when taken."""
        with self._changed:
            while key in self._busy:
                remaining = until - time.monotonic()
                if remaining <= 0 or stop.is_set():
                    return False
                self._changed.wait(min(remaining, tuning.POLL_S))
            if stop.is_set():
                return False
            self._busy.add(key)
            return True

    def give_back(self, key):
        with self._changed:
            self._busy.discard(key)
            self._changed.notify_all()


_API_TURNS = _ApiTurns()


def _client_api_key(client):
    return id(client)


def watch_direct(spec, *, succeeded, deadline, observed_after, direct_kwargs, waiting):
    """Ask the direct reader until an answer counts; a carried error at once when it fails.

    A caller *waiting* for a change (a confirmation's ``accept`` or
    ``observed_after``) is asked again every ``POLL_S`` until *deadline*,
    and gets None when nothing counted in time. Any other caller gets one
    answer: one that does not count is a failure, so the fallback answers
    at once instead of after the whole wait.
    """
    while True:
        reading = sources.direct_read(spec, direct_kwargs)
        if reading.error is not None or _counts(reading, succeeded, observed_after):
            return reading
        if not waiting:
            return Reading(
                value=None,
                source="direct",
                observed_at=reading.observed_at,
                age_s=None,
                error=sources.DirectAnswerDidNotCount(
                    f"the direct reader answered {reading.value!r}"
                ),
            )
        if time.monotonic() + tuning.POLL_S >= deadline:
            return None
        time.sleep(tuning.POLL_S)


def _api_worker(spec, client, api_kwargs, *, deadline, stop, answers):
    """Request the read, and request it again every ``POLL_S`` until stopped or the window ends.

    One request at a time per connection: a busy connection is waited for,
    never skipped. A request already sent is seen through to its answer or
    the window's end, even when the race is won meanwhile: abandoning
    questions mid-flight, which happened on every look the log won, made
    LAS X drop the next one under a burst of commands (LAS X simulator,
    2026-10-11). Once the race is won, no new request is sent.
    """
    key = _client_api_key(client)
    while not stop.is_set() and time.monotonic() < deadline:
        if not _API_TURNS.take(key, until=deadline, stop=stop):
            return
        try:
            reading = sources.api_read(
                lambda: spec.api_fn(client, deadline=deadline, **api_kwargs)
            )
        finally:
            _API_TURNS.give_back(key)
        answers.put(reading)
        if stop.wait(tuning.POLL_S):
            return


def _log_worker(spec, job_name, max_age_s, *, deadline, stop, answers):
    """Read the LAS X log, and again every ``POLL_S`` until stopped or the window ends.

    A worker whose race is already won starts no parse. Each read takes the
    shared live snapshot (``log_reader.LIVE_LOG``), so however many readings
    race at once, the log is parsed by one parse at a time.
    """
    while not stop.is_set():
        answers.put(sources.snapshot_read(spec, max_age_s=max_age_s, job_name=job_name))
        if stop.wait(tuning.POLL_S) or time.monotonic() >= deadline:
            return


def _counts(reading, succeeded, observed_after):
    """Whether *reading* is an answer that counts as a success."""
    if reading.error is not None or reading.value is None:
        return False
    if observed_after is not None and (
        reading.observed_at is None or reading.observed_at <= observed_after
    ):
        return False
    try:
        return bool(succeeded(reading.value))
    except Exception:
        log.debug("success check raised", exc_info=True)
        return False


def _watch(
    datum, client, *, mode, succeeded, deadline, job_name, observed_after, api_kwargs
):
    """Watch one window: the first answer that counts, from either source, or no answer.

    Returns the winning Reading; when nothing counted by *deadline*, the
    latest error-carrying reading with its value stripped (so diagnostics
    show why), else None. An ``UnsupportedSource`` reading comes back at
    once. Workers post every answer to one queue; this function blocks on
    the queue, so it needs no interval of its own.
    """
    spec = capabilities.spec(datum)
    legs = sources.legs(spec, datum, mode)
    if isinstance(legs, Reading):
        return legs
    answers = queue.Queue()
    stop = threading.Event()
    if "api" in legs:
        threading.Thread(
            target=_api_worker,
            args=(spec, client, api_kwargs),
            kwargs=dict(deadline=deadline, stop=stop, answers=answers),
            name=f"lasx-{datum}-api",
            daemon=True,
        ).start()
    if "log" in legs:
        max_age_s = sources.max_age_s(spec)
        threading.Thread(
            target=_log_worker,
            args=(spec, job_name, max_age_s),
            kwargs=dict(deadline=deadline, stop=stop, answers=answers),
            name=f"lasx-{datum}-log",
            daemon=True,
        ).start()
    failed = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                reading = answers.get(timeout=remaining)
            except queue.Empty:
                break
            if _counts(reading, succeeded, observed_after):
                return reading
            if reading.error is not None:
                failed = reading._replace_value_none()
    finally:
        stop.set()
    return failed


def _success_for(datum, job_name, accept):
    spec = capabilities.spec(datum)

    def succeeded(value):
        if not spec.success(value, job_name=job_name):
            return False
        return accept is None or bool(accept(value))

    return succeeded


def wait_for(
    datum,
    client,
    *,
    accept=None,
    job_name=None,
    observed_after=None,
    mode=None,
    deadline=None,
    api_kwargs=None,
    fallback="hybrid",
):
    """Wait for a reading by the one rule; return the winning Reading, or a failure.

    Args:
        datum: A row of the readings table in :mod:`capabilities`.
        client: The connected LAS X API client.
        accept: Optional ``accept(value) -> bool`` that narrows the datum's
            own success rule for this caller (a target value, a field the
            caller needs). Applied on top of the rule, never instead of it.
        job_name: The job a per-job datum is about.
        observed_after: Reject answers observed at or before this wall-clock
            time; a confirmation passes the moment its command was sent.
        mode: ``api`` / ``log`` / ``hybrid`` / ``direct``; None takes the profile's.
        deadline: A ``time.monotonic()`` time to watch until, for a caller
            that runs its own window (a command confirmation). None means
            the rule: ``WINDOWS`` windows of ``WINDOW_S``, requesting an
            unanswered read again at each new window.
        api_kwargs: Extra keyword arguments for the API leg (``job_name``).
        fallback: The family that answers in ``direct`` mode when the hardware
            server cannot be reached or the datum has no direct leg.

    Returns:
        The winning :class:`Reading`; else the latest error-carrying reading
        with its value stripped; else None.
    """
    spec = capabilities.spec(datum)
    mode = mode if mode is not None else sources.mode_for(datum)
    succeeded = _success_for(datum, job_name, accept)
    if mode == "direct":
        if spec.direct_fn is not None:
            until = (
                deadline
                if deadline is not None
                else time.monotonic() + tuning.WINDOWS * tuning.WINDOW_S
            )
            reading = watch_direct(
                spec,
                succeeded=succeeded,
                deadline=until,
                observed_after=observed_after,
                direct_kwargs=api_kwargs or {},
                waiting=accept is not None or observed_after is not None,
            )
            if reading is None or reading.error is None:
                return reading
            log.debug("%s: %s; the %s reader answers", datum, reading.error, fallback)
        mode = fallback
    kwargs = dict(
        mode=mode,
        succeeded=succeeded,
        job_name=job_name,
        observed_after=observed_after,
        api_kwargs=api_kwargs or {},
    )
    if deadline is not None:
        return _watch(datum, client, deadline=deadline, **kwargs)
    failed = None
    for window in range(1, tuning.WINDOWS + 1):
        reading = _watch(
            datum, client, deadline=time.monotonic() + tuning.WINDOW_S, **kwargs
        )
        if reading is not None and reading.error is None:
            return reading
        if reading is not None:
            if isinstance(reading.error, capabilities.UnsupportedSource):
                return reading
            failed = reading
        log.debug(
            "%s: no answer counted in window %d/%d", datum, window, tuning.WINDOWS
        )
    log.warning(
        "%s%s: no answer counted as a success in %d windows of %ss",
        datum,
        f" for '{job_name}'" if job_name else "",
        tuning.WINDOWS,
        tuning.WINDOW_S,
    )
    return failed
