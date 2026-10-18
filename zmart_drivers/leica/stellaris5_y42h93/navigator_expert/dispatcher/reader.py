"""The shared reader: one way to wait for an answer, whichever source gives it.

Every reading follows the one waiting rule (below): four windows of three
seconds. Within a window the sources the profile allows are asked one after
the other, the CAM API first and then the LAS X log, and the first answer
that counts as a success wins. Each source gets its own slice of the window,
so that a slow source cannot use the whole window up. What counts as a
success is declared once per datum in :mod:`capabilities` (``success``), and
a caller can narrow it with ``accept``. A read the API did not answer in a
window is requested again at the next one. After four windows the reading is
unknown.

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

A reading comes back as a :class:`Reading`: the value, the source that gave it,
when it was seen and how old it was. Callers that only want the value ask for
it plainly; callers that want to know how far to trust it ask for the whole
reading.

This file knows nothing of any one microscope. The microscope's own sources
(its table of readings, and one question to the API, the log or the direct
reader) are handed in as ``vendor``. For LAS X these are in
``vendor_interface/primitive_handling/sources.py``, and the named readings the
rest of the driver asks for (``get_xy`` and the others) are in
``vendor_interface/primitive_handling/state.py``, which call :func:`wait_for`.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import threading
import time

from dataclasses import dataclass


log = logging.getLogger(__name__)


# =============================================================================
# The waiting rule: how long the driver waits, whichever source answers
# =============================================================================
#
# How the driver waits on LAS X: the one rule and its four numbers.
#
# Every wait on LAS X follows the same rule, whether it is a reading, the
# confirmation of a command, or the delivery of a message:
#
# - **Four windows of three seconds** (``WINDOWS`` x ``WINDOW_S``). Within a
#   window the driver watches both sources, the CAM API and the LAS X log, and
#   the first answer that counts as a success wins, from whichever source gives
#   it. Between windows something is sent again: a command is fired again, a
#   read that got no answer is requested again, a message is delivered again.
#   After four windows a reading is unknown, a change is unconfirmed, a
#   delivery has failed.
# - **Look again every** ``POLL_S``: a new look at the source that shows the
#   state, a new read request to the API or a new read of the LAS X log. One
#   read of the log took 64-71 ms (median 66 ms over 30 reads) on the LAS X
#   simulator PC on 2026-10-10, and one API read 60-80 ms, so looking faster
#   gains little and keeps a CPU core busy re-reading the log. To be confirmed
#   on the microscope PC.
# - **Check for the answer every** ``ANSWER_POLL_S``: whether a request already
#   sent has been answered, a field on the local API object. At ``POLL_S`` a
#   65 ms answer would only be noticed at 100 ms.
#
# Acquisition is the one command that is never sent again (a second acquire is
# a second acquisition); it keeps its own watch in ``ACQUIRE``. Waits on a
# physical process, such as a file being written, are not waits on LAS X and
# keep their own numbers.
#
# Runtime consumers read these at call time (``WINDOW_S``), so a test
# can shrink them with monkeypatch. ``actions.profiles`` captures them into
# dataclass field defaults at import.

WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01


@dataclass(frozen=True)
class Reading:
    """A source-tagged state reading used by routed callers."""

    value: object
    source: str
    observed_at: float | None
    age_s: float | None
    error: Exception | None = None

    def _replace_value_none(self):
        """This reading with the value stripped (kept: source/timing/error)."""
        if self.value is None:
            return self
        return Reading(
            value=None,
            source=self.source,
            observed_at=self.observed_at,
            age_s=self.age_s,
            error=self.error,
        )


def plain_or_diagnostic(reading, diagnostics):
    """The whole reading when *diagnostics* is asked for, otherwise just its value."""
    if diagnostics:
        return reading
    return None if reading is None else reading.value


def derive(reading, value):
    """A reading with a new value, keeping the source, timing and error of *reading*."""
    if reading is None:
        return None
    return Reading(
        value=value,
        source=reading.source,
        observed_at=reading.observed_at,
        age_s=reading.age_s,
        error=reading.error,
    )


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
                self._changed.wait(min(remaining, POLL_S))
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


def watch_direct(
    vendor, spec, *, succeeded, deadline, observed_after, direct_kwargs, waiting
):
    """Ask the direct reader until an answer counts; a carried error at once when it fails.

    A caller *waiting* for a change (a confirmation's ``accept`` or
    ``observed_after``) is asked again every ``POLL_S`` until *deadline*,
    and gets None when nothing counted in time. Any other caller gets one
    answer: one that does not count is a failure, so the fallback answers
    at once instead of after the whole wait.
    """
    while True:
        reading = vendor.direct_read(spec, direct_kwargs)
        if reading.error is not None or _counts(reading, succeeded, observed_after):
            return reading
        if not waiting:
            return Reading(
                value=None,
                source="direct",
                observed_at=reading.observed_at,
                age_s=None,
                error=vendor.DirectAnswerDidNotCount(
                    f"the direct reader answered {reading.value!r}"
                ),
            )
        if time.monotonic() + POLL_S >= deadline:
            return None
        time.sleep(POLL_S)


def _ask_api(vendor, spec, client, api_kwargs, *, until, answer_counts):
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
        if not _API_TURNS.take(key, until=until, stop=_NEVER_STOPPED):
            break
        done = threading.Event()
        answers = []

        def request(answers=answers, done=done):
            try:
                answers.append(
                    vendor.api_read(
                        lambda: spec.api_fn(client, deadline=until, **api_kwargs)
                    )
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
        time.sleep(max(0.0, min(POLL_S, until - time.monotonic())))
    return reading


def _ask_log(vendor, spec, job_name, max_age_s, *, until, answer_counts):
    """Read the LAS X log, and again every ``POLL_S`` until an answer counts or *until*.

    Each read takes the shared live snapshot (``log_reader.LIVE_LOG``), so
    however many readings are taken at once, the log is parsed by one parse
    at a time.
    """
    reading = None
    while True:
        reading = vendor.snapshot_read(spec, max_age_s=max_age_s, job_name=job_name)
        remaining = until - time.monotonic()
        if answer_counts(reading) or remaining <= 0:
            return reading
        time.sleep(min(POLL_S, remaining))


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
    vendor,
    datum,
    client,
    *,
    mode,
    succeeded,
    deadline,
    job_name,
    observed_after,
    api_kwargs,
):
    """Watch one window: ask each allowed source in turn, and return the first answer that counts.

    The sources are asked one after the other, the API first and then the
    log, each within its own share of the time left until *deadline*. Returns
    the winning Reading; when nothing counted, the latest error-carrying
    reading with its value stripped (so diagnostics show why), else None. An
    ``UnsupportedSource`` reading comes back at once.
    """
    spec = vendor.spec(datum)
    legs = vendor.legs(spec, datum, mode)
    if isinstance(legs, Reading):
        return legs

    def answer_counts(reading):
        return reading is not None and _counts(reading, succeeded, observed_after)

    failed = None
    began = time.monotonic()
    for turn, leg in enumerate(legs, start=1):
        until = began + (deadline - began) * turn / len(legs)
        if leg == "api":
            reading = _ask_api(
                vendor,
                spec,
                client,
                api_kwargs,
                until=until,
                answer_counts=answer_counts,
            )
        else:
            reading = _ask_log(
                vendor,
                spec,
                job_name,
                vendor.max_age_s(spec),
                until=until,
                answer_counts=answer_counts,
            )
        if answer_counts(reading):
            return reading
        if reading is not None and reading.error is not None:
            failed = reading._replace_value_none()
    return failed


def _success_for(vendor, datum, job_name, accept):
    spec = vendor.spec(datum)

    def succeeded(value):
        if not spec.success(value, job_name=job_name):
            return False
        return accept is None or bool(accept(value))

    return succeeded


def wait_for(
    vendor,
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
        vendor: The microscope's own sources: the object that knows its
            readings table and how to ask the API, the log and the direct
            reader once (for LAS X, ``primitive_handling/sources.py``). The
            reader itself knows nothing of any one microscope.
        datum: A row of the vendor's table of readings.
        client: The connected microscope client.
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
    spec = vendor.spec(datum)
    mode = mode if mode is not None else vendor.mode_for(datum)
    succeeded = _success_for(vendor, datum, job_name, accept)
    if mode == "direct":
        if spec.direct_fn is not None:
            until = (
                deadline
                if deadline is not None
                else time.monotonic() + WINDOWS * WINDOW_S
            )
            reading = watch_direct(
                vendor,
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
        return _watch(vendor, datum, client, deadline=deadline, **kwargs)
    failed = None
    for window in range(1, WINDOWS + 1):
        reading = _watch(
            vendor, datum, client, deadline=time.monotonic() + WINDOW_S, **kwargs
        )
        if reading is not None and reading.error is None:
            return reading
        if reading is not None:
            if isinstance(reading.error, vendor.UnsupportedSource):
                return reading
            failed = reading
        log.debug("%s: no answer counted in window %d/%d", datum, window, WINDOWS)
    log.warning(
        "%s%s: no answer counted as a success in %d windows of %ss",
        datum,
        f" for '{job_name}'" if job_name else "",
        WINDOWS,
        WINDOW_S,
    )
    return failed
