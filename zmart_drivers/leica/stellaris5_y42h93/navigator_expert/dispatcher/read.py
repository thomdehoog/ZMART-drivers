"""Routed LAS X state readers: one way to wait for an answer.

Every reading follows the one rule in :mod:`tuning`: four windows of three
seconds. Within a window both sources the profile allows are watched, the
CAM API and the LAS X log, and the first answer that counts as a success
wins, from whichever source gives it; no source is preferred. What counts as
a success is declared once per datum in :mod:`capabilities` (``success``),
and a caller can narrow it with ``accept``. A read the API did not answer in
a window is requested again at the next one. After four windows the reading
is unknown.

The source family (``api``, ``log``, ``hybrid``) is profile policy and says
which sources take part, never how long to wait. Asking a family for a leg
the datum does not have fails closed at once with ``UnsupportedSource``.

Public functions keep the plain return shapes by default; pass
``diagnostics=True`` to receive a :class:`Reading` with its source and age.
A caller that already runs its own window, such as a command confirmation,
passes ``deadline=`` to watch only until then.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from ..actions import derived
from ..actions import get as capabilities
from ..actions.derived import (
    stack_z_wide_um,  # noqa: F401  re-exported, as the readers package did
)
from ..vendor_interface import api_reader, log_reader
from . import tuning

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


def _profile():
    from ..actions import profiles

    return profiles.STATE_READERS


def _plain_or_diagnostic(reading, diagnostics):
    if diagnostics:
        return reading
    return None if reading is None else reading.value


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


# =============================================================================
# The two sources
# =============================================================================


def _api_read(fn) -> Reading:
    try:
        value = fn()
        # API has no independent freshness timestamp. observed_at/age_s mark
        # call completion, not proof that LAS X returned newly-produced state.
        return Reading(value=value, source="api", observed_at=time.time(), age_s=0.0)
    except Exception as exc:
        log.debug("api reader failed", exc_info=True)
        return Reading(value=None, source="api", observed_at=time.time(), age_s=None, error=exc)


def _snapshot_read(spec, *, max_age_s, job_name=None) -> Reading:
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
        return Reading(value=None, source="log", observed_at=time.time(), age_s=None, error=exc)


def _unsupported(mode, datum) -> Reading:
    return Reading(
        value=None,
        source=mode,
        observed_at=None,
        age_s=None,
        error=capabilities.UnsupportedSource(f"datum {datum!r} has no {mode} leg"),
    )


def _legs(spec, datum, mode):
    """Which sources take part for *mode*; an ``UnsupportedSource`` reading if none can."""
    if mode == "api":
        return ("api",) if spec.api_fn is not None else _unsupported("api", datum)
    if mode == "log":
        return ("log",) if spec.log_fn is not None else _unsupported("log", datum)
    if mode == "hybrid":
        legs = tuple(
            leg
            for leg, fn in (("api", spec.api_fn), ("log", spec.log_fn))
            if fn is not None
        )
        return legs or _unsupported("hybrid", datum)
    raise ValueError(f"unknown state-reader mode {mode!r}")


def _api_worker(spec, client, api_kwargs, *, deadline, stop, answers):
    """Request the read, and request it again every ``POLL_S`` until stopped or the window ends.

    One request at a time per connection: a busy connection is waited for,
    never skipped. The request's own check for its answer ends at the window's
    end or as soon as the watch stops, so an abandoned request frees the
    connection promptly.
    """
    key = _client_api_key(client)
    while not stop.is_set() and time.monotonic() < deadline:
        if not _API_TURNS.take(key, until=deadline, stop=stop):
            return
        try:
            reading = _api_read(
                lambda: spec.api_fn(
                    client, deadline=deadline, should_stop=stop.is_set, **api_kwargs
                )
            )
        finally:
            _API_TURNS.give_back(key)
        answers.put(reading)
        if stop.wait(tuning.POLL_S):
            return


def _log_worker(spec, job_name, max_age_s, *, deadline, stop, answers):
    """Read the LAS X log, and again every ``POLL_S`` until stopped or the window ends."""
    while True:
        answers.put(_snapshot_read(spec, max_age_s=max_age_s, job_name=job_name))
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


def _watch(datum, client, *, mode, succeeded, deadline, job_name, observed_after, api_kwargs):
    """Watch one window: the first answer that counts, from either source, or no answer.

    Returns the winning Reading; when nothing counted by *deadline*, the
    latest error-carrying reading with its value stripped (so diagnostics
    show why), else None. An ``UnsupportedSource`` reading comes back at
    once. Workers post every answer to one queue; this function blocks on
    the queue, so it needs no interval of its own.
    """
    spec = capabilities.spec(datum)
    legs = _legs(spec, datum, mode)
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
        max_age_s = (
            None if spec.log_max_age_attr is None else getattr(_profile(), spec.log_max_age_attr)
        )
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
        mode: ``api`` / ``log`` / ``hybrid``; None takes the profile's.
        deadline: A ``time.monotonic()`` time to watch until, for a caller
            that runs its own window (a command confirmation). None means
            the rule: ``WINDOWS`` windows of ``WINDOW_S``, requesting an
            unanswered read again at each new window.
        api_kwargs: Extra keyword arguments for the API leg (``job_name``).

    Returns:
        The winning :class:`Reading`; else the latest error-carrying reading
        with its value stripped; else None.
    """
    spec = capabilities.spec(datum)
    mode = mode if mode is not None else getattr(_profile(), spec.mode_attr)
    succeeded = _success_for(datum, job_name, accept)
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
        reading = _watch(datum, client, deadline=time.monotonic() + tuning.WINDOW_S, **kwargs)
        if reading is not None and reading.error is None:
            return reading
        if reading is not None:
            if isinstance(reading.error, capabilities.UnsupportedSource):
                return reading
            failed = reading
        log.debug("%s: no answer counted in window %d/%d", datum, window, tuning.WINDOWS)
    log.warning(
        "%s%s: no answer counted as a success in %d windows of %ss",
        datum,
        f" for '{job_name}'" if job_name else "",
        tuning.WINDOWS,
        tuning.WINDOW_S,
    )
    return failed


def _routed(datum, client, *, mode, diagnostics, deadline=None, accept=None, job_name=None):
    api_kwargs = {} if job_name is None else {"job_name": job_name}
    reading = wait_for(
        datum,
        client,
        accept=accept,
        job_name=job_name,
        mode=mode,
        deadline=deadline,
        api_kwargs=api_kwargs,
    )
    return _plain_or_diagnostic(reading, diagnostics)


def _derive(reading, value):
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
# The readers
# =============================================================================


def get_scan_status(client, *, mode=None, diagnostics=False, deadline=None, accept=None):
    return _routed(
        "scan_status", client, mode=mode, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def ping(client):
    return api_reader.ping(client)


def get_job_settings(
    client, job_name, *, mode=None, diagnostics=False, deadline=None, accept=None
):
    return _routed(
        "job_settings",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
        job_name=job_name,
    )


def get_hardware_info(client, *, mode=None, diagnostics=False, deadline=None):
    return _routed("hardware_info", client, mode=mode, diagnostics=diagnostics, deadline=deadline)


def get_xy(client, *, mode=None, diagnostics=False, deadline=None, accept=None):
    return _routed(
        "xy", client, mode=mode, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def read_zwide_um(client, job_name, *, mode=None):
    """Z-wide position (um) from the job settings, or None when unreadable.

    Waits for settings that carry ``zPosition``. Returns None only when no
    such settings arrive within the rule's four windows. Readable but
    incomplete settings raise: ``derived.zwide_um_from_settings`` raises
    ``RuntimeError`` when z-wide is missing, and its settings normalization
    can raise ``ValueError`` on a schema mismatch — unlike the routed
    readers, which never raise.
    """
    settings = get_job_settings(
        client, job_name, mode=mode, accept=lambda s: s.get("zPosition") is not None
    )
    if not settings:
        log.warning("read_zwide_um: could not read job settings for '%s'", job_name)
        return None
    return derived.zwide_um_from_settings(settings, client=client, job_name=job_name)


def get_jobs(client, *, mode=None, diagnostics=False, deadline=None, accept=None):
    return _routed(
        "jobs", client, mode=mode, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_job_by_name(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    jobs_reading = get_jobs(client, mode=mode, diagnostics=True, deadline=deadline)
    value = None if jobs_reading is None else derived.job_by_name(jobs_reading.value, job_name)
    reading = _derive(jobs_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_selected_job(client, *, mode=None, diagnostics=False, deadline=None, accept=None):
    return _routed(
        "selected_job",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
    )


def get_fov(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    settings_reading = get_job_settings(
        client, job_name, mode=mode, diagnostics=True, deadline=deadline
    )
    value = None if settings_reading is None else derived.fov_from_settings(settings_reading.value)
    reading = _derive(settings_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_base_fov(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    settings_reading = get_job_settings(
        client, job_name, mode=mode, diagnostics=True, deadline=deadline
    )
    value = (
        None if settings_reading is None else derived.base_fov_from_settings(settings_reading.value)
    )
    reading = _derive(settings_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_lasx_settings(settings_path=None):
    return api_reader.get_lasx_settings(settings_path=settings_path)


def get_pending_dialog(*, diagnostics=False):
    snapshot = log_reader.parse_msgbox_log()
    value = log_reader.get_pending_dialog(snapshot)
    age_s = capabilities.age_for_snapshot(snapshot, age_key="dialog")
    observed_at = None if age_s is None else snapshot.now - age_s
    reading = Reading(
        value=value,
        source="log",
        observed_at=observed_at,
        age_s=age_s,
        error=None,
    )
    return _plain_or_diagnostic(reading, diagnostics)
