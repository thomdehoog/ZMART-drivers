"""The read loop: one way to wait for an answer, and asking each source for it.

Every reading follows the one waiting rule, whose numbers the microscope hands
in (``vendor.waiting``). Within a window the sources the policy allows are
asked one after the other, and the first answer that counts as a success wins.
Each source gets its own slice of the window, so that a slow source cannot use
the whole window up. What counts as a success is declared once per datum (its
``success``), and a caller can narrow it with ``accept``. A source that did not
answer in a window is asked again at the next one. After the last window the
reading is unknown.

Which sources take part is the microscope's policy (``vendor.legs``) and never
says how long to wait. Asking for a source the datum does not have fails closed
at once with an ``UnsupportedSource`` reading.

A microscope may have a source that is tried alone before the others, for
instance one that must be read in the caller's own thread. The vendor names it
(``vendor.first_stage``) and says which sources answer if it cannot.

Public functions keep the plain return shapes by default; pass
``diagnostics=True`` to receive a :class:`Reading` with its source and age.
A caller that already runs its own window, such as a command confirmation,
passes ``deadline=`` to watch only until then.

A reading comes back as a :class:`Reading`: the value, the source that gave it,
when it was seen and how old it was. Callers that only want the value ask for
it plainly; callers that want to know how far to trust it ask for the whole
reading.

This file knows nothing of any one microscope. What the reader asks of the
``vendor`` object it is handed:

- ``waiting``: the four waiting numbers (``WINDOWS``, ``WINDOW_S``, ``POLL_S``,
  ``ANSWER_POLL_S``);
- ``spec(datum)``, ``mode_for(datum)`` and ``legs(spec, datum, mode)``: the
  table of readings and which sources take part;
- ``success(spec, value, about)``: whether *value* counts as an answer to
  the reading; *about* is what a reading of one item is about (for LAS X, the
  name of a job), or None;
- ``ask(leg, spec, client, until=, answer_counts=, about=, source_kwargs=)``:
  ask one source, and again until an answer counts or *until*, and return a
  Reading;
- ``first_stage(spec, mode, fallback)``: the source tried alone first, if any,
  and the mode for the rest;
- ``UnsupportedSource``: the error class for a missing source.

For LAS X these are in this file, and the
named readings the rest of the driver asks for (``get_xy`` and the others) are
in ``procedures/readings.py``, which call :func:`wait_for`.

The three places LAS X can be asked, and which of them a reading may use.

A reading in the table of :mod:`readings` can have up to three legs: the CAM
API (asked on demand), the LAS X log (written only when a value changes) and
the direct reader (the hardware server's state tree). This file holds the one
function per leg that asks it once, and turns whatever happens into a
:class:`~..dispatcher.read.Reading`. A leg that fails never raises: the
error rides along in the reading so the shared reader in
the read loop can decide what to do next.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
import threading
from functools import partial
from ..primitives.direct.client import HardwareServerUnavailable
from ..primitives.log import reads as log_reader
from ..operations.readings import reading_settings
from ..operations.readings import reading_table as capabilities
from ..primitives.cam_api import waiting


log = logging.getLogger(__name__)


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


def counts(reading, succeeded, observed_after):
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
    about,
    observed_after,
    source_kwargs,
):
    """Watch one window: ask each allowed source in turn, and return the first answer that counts.

    The sources are asked one after the other, each within its own share of
    the time left until *deadline*. How a source is asked, and how it is asked
    again until it answers, is the vendor's business (``vendor.ask``). Returns
    the winning Reading; when nothing counted, the latest error-carrying
    reading with its value stripped (so diagnostics show why), else None. An
    ``UnsupportedSource`` reading comes back at once.
    """
    spec = vendor.spec(datum)
    legs = vendor.legs(spec, datum, mode)
    if isinstance(legs, Reading):
        return legs

    def answer_counts(reading):
        return reading is not None and counts(reading, succeeded, observed_after)

    failed = None
    began = time.monotonic()
    for turn, leg in enumerate(legs, start=1):
        until = began + (deadline - began) * turn / len(legs)
        reading = vendor.ask(
            leg,
            spec,
            client,
            until=until,
            answer_counts=answer_counts,
            about=about,
            source_kwargs=source_kwargs,
        )
        if answer_counts(reading):
            return reading
        if reading is not None and reading.error is not None:
            failed = reading._replace_value_none()
    return failed


def _success_for(vendor, datum, about, accept):
    spec = vendor.spec(datum)

    def succeeded(value):
        if not vendor.success(spec, value, about):
            return False
        return accept is None or bool(accept(value))

    return succeeded


def wait_for(
    vendor,
    datum,
    client,
    *,
    accept=None,
    about=None,
    observed_after=None,
    mode=None,
    deadline=None,
    source_kwargs=None,
    fallback=None,
):
    """Wait for a reading by the one rule; return the winning Reading, or a failure.

    Args:
        vendor: The microscope's own sources: the object that knows its table
            of readings and how to ask each source (for LAS X,
            ``operations/readings/sources.py``). The reader itself knows
            nothing of any one microscope.
        datum: A row of the vendor's table of readings.
        client: The connected microscope client.
        accept: Optional ``accept(value) -> bool`` that narrows the datum's
            own success rule for this caller (a target value, a field the
            caller needs). Applied on top of the rule, never instead of it.
        about: What a reading of one item is about (for LAS X, the name of
            a job), handed to the vendor's sources and its success rule.
        observed_after: Reject answers observed at or before this wall-clock
            time; a confirmation passes the moment its command was sent.
        mode: The source family the microscope's policy names; None takes the
            policy's own choice for this datum.
        deadline: A ``time.monotonic()`` time to watch until, for a caller
            that runs its own window (a command confirmation). None means
            the rule: ``WINDOWS`` windows of ``WINDOW_S``, requesting an
            unanswered read again at each new window.
        source_kwargs: Extra keyword arguments handed to the sources.
        fallback: The family that answers when the source tried first cannot;
            None lets the microscope's policy choose.

    Returns:
        The winning :class:`Reading`; else the latest error-carrying reading
        with its value stripped; else None.
    """
    spec = vendor.spec(datum)
    mode = mode if mode is not None else vendor.mode_for(datum)
    succeeded = _success_for(vendor, datum, about, accept)
    # Some microscopes have a source that is tried alone before the others
    # (for LAS X, the direct reader); the vendor says which, and which family
    # answers if that source cannot.
    first_stage, rest_mode = vendor.first_stage(spec, mode, fallback)
    if first_stage is not None:
        until = (
            deadline
            if deadline is not None
            else time.monotonic() + vendor.waiting.WINDOWS * vendor.waiting.WINDOW_S
        )
        reading = first_stage(
            succeeded=succeeded,
            deadline=until,
            observed_after=observed_after,
            kwargs=source_kwargs or {},
            waiting_for_change=accept is not None or observed_after is not None,
        )
        if reading is None or reading.error is None:
            return reading
        log.debug("%s: %s; the %s reader answers", datum, reading.error, rest_mode)
    mode = rest_mode
    kwargs = dict(
        mode=mode,
        succeeded=succeeded,
        about=about,
        observed_after=observed_after,
        source_kwargs=source_kwargs or {},
    )
    if deadline is not None:
        return _watch(vendor, datum, client, deadline=deadline, **kwargs)
    failed = None
    for window in range(1, vendor.waiting.WINDOWS + 1):
        reading = _watch(
            vendor,
            datum,
            client,
            deadline=time.monotonic() + vendor.waiting.WINDOW_S,
            **kwargs,
        )
        if reading is not None and reading.error is None:
            return reading
        if reading is not None:
            if isinstance(reading.error, vendor.UnsupportedSource):
                return reading
            failed = reading
        log.debug(
            "%s: no answer counted in window %d/%d",
            datum,
            window,
            vendor.waiting.WINDOWS,
        )
    log.warning(
        "%s%s: no answer counted as a success in %d windows of %ss",
        datum,
        f" for '{about}'" if about else "",
        vendor.waiting.WINDOWS,
        vendor.waiting.WINDOW_S,
    )
    return failed


def spec(datum):
    """The row of the table of readings for *datum*."""
    return capabilities.spec(datum)


#: Handed to the shared reader so that it can tell a missing leg from a failure.
UnsupportedSource = capabilities.UnsupportedSource


def profile():
    """The reading policy of this driver: which source each datum prefers."""
    return reading_settings.STATE_READERS


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


def watch_direct(spec, *, succeeded, deadline, observed_after, kwargs, waiting_for_change):
    """Ask the direct reader until an answer counts; a carried error at once when it fails.

    A caller waiting for a change (*waiting_for_change*: a confirmation's ``accept`` or
    ``observed_after``) is asked again every ``POLL_S`` until *deadline*,
    and gets None when nothing counted in time. Any other caller gets one
    answer: one that does not count is a failure, so the fallback answers
    at once instead of after the whole wait.
    """
    while True:
        reading = direct_read(spec, kwargs)
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
