"""The shared reader: one way to wait for an answer, whichever source gives it.

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
- ``ask(leg, spec, client, until=, answer_counts=, job_name=, api_kwargs=)``:
  ask one source, and again until an answer counts or *until*, and return a
  Reading;
- ``first_stage(spec, mode, fallback)``: the source tried alone first, if any,
  and the mode for the rest;
- ``UnsupportedSource``: the error class for a missing source.

For LAS X these are in ``vendor_specific/primitive_handling/sources.py``, and the
named readings the rest of the driver asks for (``get_xy`` and the others) are
in ``vendor_specific/primitive_handling/state.py``, which call :func:`wait_for`.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time

from dataclasses import dataclass



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
    job_name,
    observed_after,
    api_kwargs,
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
            job_name=job_name,
            api_kwargs=api_kwargs,
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
        vendor: The microscope's own sources: the object that knows its table
            of readings and how to ask each source (for LAS X,
            ``primitive_handling/sources.py``). The reader itself knows
            nothing of any one microscope.
        datum: A row of the vendor's table of readings.
        client: The connected microscope client.
        accept: Optional ``accept(value) -> bool`` that narrows the datum's
            own success rule for this caller (a target value, a field the
            caller needs). Applied on top of the rule, never instead of it.
        job_name: The job a per-job datum is about.
        observed_after: Reject answers observed at or before this wall-clock
            time; a confirmation passes the moment its command was sent.
        mode: The source family the microscope's policy names; None takes the
            policy's own choice for this datum.
        deadline: A ``time.monotonic()`` time to watch until, for a caller
            that runs its own window (a command confirmation). None means
            the rule: ``WINDOWS`` windows of ``WINDOW_S``, requesting an
            unanswered read again at each new window.
        api_kwargs: Extra keyword arguments handed to the sources.
        fallback: The family that answers when the source tried first cannot.

    Returns:
        The winning :class:`Reading`; else the latest error-carrying reading
        with its value stripped; else None.
    """
    spec = vendor.spec(datum)
    mode = mode if mode is not None else vendor.mode_for(datum)
    succeeded = _success_for(vendor, datum, job_name, accept)
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
            kwargs=api_kwargs or {},
            waiting=accept is not None or observed_after is not None,
        )
        if reading is None or reading.error is None:
            return reading
        log.debug("%s: %s; the %s reader answers", datum, reading.error, rest_mode)
    mode = rest_mode
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
            "%s: no answer counted in window %d/%d", datum, window, vendor.waiting.WINDOWS
        )
    log.warning(
        "%s%s: no answer counted as a success in %d windows of %ss",
        datum,
        f" for '{job_name}'" if job_name else "",
        vendor.waiting.WINDOWS,
        vendor.waiting.WINDOW_S,
    )
    return failed
