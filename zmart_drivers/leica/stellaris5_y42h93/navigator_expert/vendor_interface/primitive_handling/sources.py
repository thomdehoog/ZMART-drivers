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
import time

from ...dispatcher.reader import Reading
from ..connection.hardware_server import HardwareServerUnavailable
from ..primitives.log import log_reader
from . import profiles
from . import readings as capabilities

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
