"""Per-datum source capabilities - the one table behind the reader families.

Every datum the state readers serve is declared here once, with the
passive legs each source can provide (``api_fn`` / ``log_fn``): they
answer "what is the state now?" for the routed readers in :mod:`router`.
Either leg may be absent; a family asked for a leg the datum does not
have fails closed with ``UnsupportedSource``, and ``hybrid`` degrades to
the legs that exist.

The table holds *capabilities* (facts about what a source can prove) and
what counts as an answer, not preferences. Policy - which family is the
default - lives in ``profiles.StateReaderProfile``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from ..vendor_interface import api_reader, log_reader
from . import derived


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
    return isinstance(value, dict) and _finite(value.get("x_um")) and _finite(value.get("y_um"))


def jobs_listed(value, *, job_name=None) -> bool:
    """A job list whose every entry names its job; an empty list is a real answer."""
    return isinstance(value, list) and all(isinstance(j, dict) and j.get("Name") for j in value)


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
    return derived.settings_geometry_ready(value)


@dataclass(frozen=True)
class DatumSpec:
    """Source capabilities for one datum, and what counts as its answer.

    Passive legs are optional callables. ``mode_attr`` and
    ``log_max_age_attr`` name ``StateReaderProfile`` attributes, so every
    choice stays in the profile. ``success(value, *, job_name)`` says when an
    answer from either source counts; the first answer that does, wins. How
    long to wait is not per datum: it is the one rule in ``dispatcher.tuning``.
    """

    mode_attr: str
    success: Callable
    api_fn: Callable | None = None  # (client, *, deadline, should_stop) -> raw value
    log_fn: Callable | None = None  # (snapshot, *, max_age_s[, job_name])
    log_max_age_attr: str | None = None
    age_key: str | None = None  # key for age_for_snapshot()


def age_for_snapshot(snapshot, *, age_key=None, job_name=None, max_age_s=None):
    """Age of a datum's log value within *snapshot*, in seconds.

    Mirrors the log readers' value derivation, so the reported age belongs
    to the datum that actually produced the value -- not a min/max over
    tangential timestamps (which made fresh job lists report ancient ages
    and stale selections report fresh intent-echo ages):

    - ``jobs``: :func:`log_reader.get_jobs` derives the list from the matrix
      summary when it is present and fresh under *max_age_s*; otherwise from
      the ATL cluster, whose staleness is bounded by its oldest per-job line.
      Selection markers only tag ``IsSelected`` and are not the value's age.
    - ``selected_job``: :func:`log_reader.get_selected_job` prefers the
      ``CurrentBlock`` applied state; the element-index intent echo is only
      the fallback.
    """
    ages = log_reader.ages(snapshot)
    if job_name is not None:
        return ages.get("jobs", {}).get(job_name)

    def fresh(age):
        return age is not None and (max_age_s is None or age <= max_age_s)

    if age_key == "jobs":
        job_list_age = ages.get("job_list")
        if fresh(job_list_age):
            return job_list_age
        cluster = [age for age in (ages.get("jobs") or {}).values() if age is not None]
        return max(cluster) if cluster else job_list_age
    if age_key == "selected_job":
        current_block_age = ages.get("current_block")
        if fresh(current_block_age):
            return current_block_age
        selected_age = ages.get("selected")
        return selected_age if selected_age is not None else current_block_age
    return ages.get(age_key)


DATUMS = {
    "scan_status": DatumSpec(
        mode_attr="scan_status_mode",
        success=scan_status_known,
        api_fn=lambda client, **kw: api_reader.get_scan_status(client),
        log_fn=lambda snapshot, *, max_age_s: log_reader.get_scan_status(
            snapshot, max_age_s=max_age_s
        ),
        log_max_age_attr="scan_status_log_max_age_s",
        age_key="scan_status",
    ),
    "job_settings": DatumSpec(
        mode_attr="job_settings_mode",
        success=job_settings_complete,
        api_fn=lambda client, job_name, **kw: api_reader.get_job_settings(client, job_name, **kw),
        log_fn=lambda snapshot, *, max_age_s, job_name: log_reader.get_job_settings(
            job_name, snapshot, max_age_s=max_age_s
        ),
        log_max_age_attr="job_settings_log_max_age_s",
    ),
    "hardware_info": DatumSpec(
        mode_attr="hardware_info_mode",
        success=hardware_info_present,
        api_fn=lambda client, **kw: api_reader.get_hardware_info(client, **kw),
        log_fn=lambda snapshot, *, max_age_s: log_reader.get_hardware_info(
            snapshot, max_age_s=max_age_s
        ),
        log_max_age_attr="hardware_info_log_max_age_s",
        age_key="hardware_info",
    ),
    "xy": DatumSpec(
        mode_attr="xy_mode",
        success=xy_present,
        api_fn=lambda client, **kw: api_reader.get_xy(client, **kw),
        log_fn=lambda snapshot, *, max_age_s: log_reader.get_xy(snapshot, max_age_s=max_age_s),
        log_max_age_attr="xy_log_max_age_s",
        age_key="xy",
    ),
    "jobs": DatumSpec(
        mode_attr="jobs_mode",
        success=jobs_listed,
        # API only: there is no log source for the full job LIST. The log
        # stream reports only the ACTIVE job (that is the `selected_job`
        # datum, which keeps its log/hybrid legs); its job-list derivation is
        # a passive cluster that omits jobs not re-dumped this session, so it
        # is incomplete. Confirmed on the bench (2026-07-06). No log_fn.
        api_fn=lambda client, **kw: api_reader.get_jobs(client, **kw),
    ),
    "selected_job": DatumSpec(
        mode_attr="selected_job_mode",
        success=selected_job_named,
        api_fn=lambda client, **kw: api_reader.get_selected_job(client, **kw),
        log_fn=lambda snapshot, *, max_age_s: log_reader.get_selected_job(
            snapshot, max_age_s=max_age_s
        ),
        log_max_age_attr="selected_job_log_max_age_s",
        age_key="selected_job",
    ),
}


def spec(datum) -> DatumSpec:
    found = DATUMS.get(datum)
    if found is None:
        raise ValueError(f"unknown datum {datum!r}; known: {sorted(DATUMS)}")
    return found
