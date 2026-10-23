"""The named readings of a LAS X session: where the stage is, which job is selected, and so on.

Each function hands the name of one row of the table of readings in
``primitives/direct/profile.py`` to the dispatcher's ``run()``, which runs the
read loop (``dispatcher/read.py``), and hands back a plain value, or, with
``diagnostics=True``, a :class:`~...dispatcher.read.Reading` that also says
which source answered and how old the answer was. A reading that cannot be
had comes back as None; the readers here do not raise, except where a
function's own note says so.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch as _door
from ..dispatcher.read import (
    Reading,
    derive as _derive,
    plain_or_diagnostic as _plain_or_diagnostic,
)  # noqa: F401
from ..primitives.cam_api import reads as api_reader
from ..primitives.log import reads as log_reader
from ..primitives.cam_api import derived
from ..primitives.direct import profile as direct_profile
from ..primitives.cam_api.derived import stack_drive, stack_slices_um  # noqa: F401  re-exported, as the readers package did

log = logging.getLogger(__name__)


def wait_for(datum, client, **keywords):
    """Wait for a reading of *datum*, asking LAS X's sources: the dispatcher's ``run()`` with the reading's name.

    Every named reading below comes through here.
    """
    return _door.run(direct_profile, client, datum, **keywords)


def _routed(
    datum,
    client,
    *,
    mode,
    diagnostics,
    deadline=None,
    accept=None,
    job_name=None,
    fallback=None,
):
    fallback = direct_profile.STATE_READERS.reading_fallback if fallback is None else fallback
    reading = wait_for(
        datum,
        client,
        accept=accept,
        about=job_name,
        mode=mode,
        deadline=deadline,
        fallback=fallback,
    )
    return _plain_or_diagnostic(reading, diagnostics)


# =============================================================================
# The readers
# =============================================================================


def get_scan_status(
    client,
    *,
    mode=None,
    diagnostics=False,
    deadline=None,
    accept=None,
    fallback=None,
):
    return _routed(
        "scan_status",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
        fallback=fallback,
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
    return _routed(
        "hardware_info", client, mode=mode, diagnostics=diagnostics, deadline=deadline
    )


def get_xy(
    client,
    *,
    mode=None,
    diagnostics=False,
    deadline=None,
    accept=None,
    fallback=None,
):
    return _routed(
        "xy",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
        fallback=fallback,
    )


def z_um_from(settings, drive, *, client, job_name, mode=None):
    """Where *drive* is (um), from *job_name*'s *settings*; see ``derived.z_um_from_settings``.

    The drive carrying the job's z-stack is read from the hardware server
    when the direct reader is used (the profile's ``z_mode``, unless *mode*
    says), else from the experiment LAS X saves.
    """
    mode = mode if mode is not None else direct_profile.STATE_READERS.z_mode
    return derived.z_um_from_settings(settings, drive, client=client, job_name=job_name, direct=mode == "direct")


def read_z_um(client, job_name, drive, *, mode=None):
    """Where one z drive is (um), *drive* ``"z-galvo"`` or ``"z-wide"``; None when unreadable.

    From the direct reader (``direct_profile.z_um_direct``); otherwise from
    *job_name*'s settings: it waits for settings that carry ``zPosition``
    and returns None only when none arrive within the rule's four windows.
    Readable but incomplete settings raise: ``derived.z_um_from_settings``
    raises ``RuntimeError`` when the drive is missing, and its settings
    normalization can raise ``ValueError`` on a schema mismatch — unlike the
    routed readers, which never raise.
    """
    direct = direct_profile.z_um_direct(drive, mode=mode)
    if direct is not None:
        return direct
    settings = get_job_settings(
        client,
        job_name,
        mode=None if mode == "direct" else mode,
        accept=lambda s: s.get("zPosition") is not None,
    )
    if not settings:
        log.warning("read_z_um: could not read job settings for '%s'", job_name)
        return None
    return z_um_from(settings, drive, client=client, job_name=job_name, mode=mode)


def read_zwide_um(client, job_name, *, mode=None):
    """Z-wide position (um), or None when unreadable; see :func:`read_z_um`."""
    return read_z_um(client, job_name, "z-wide", mode=mode)


def get_jobs(client, *, mode=None, diagnostics=False, deadline=None, accept=None):
    return _routed(
        "jobs",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
    )


def get_job_by_name(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    jobs_reading = get_jobs(client, mode=mode, diagnostics=True, deadline=deadline)
    value = (
        None
        if jobs_reading is None
        else derived.job_by_name(jobs_reading.value, job_name)
    )
    reading = _derive(jobs_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_selected_job(
    client,
    *,
    mode=None,
    diagnostics=False,
    deadline=None,
    accept=None,
    fallback=None,
):
    return _routed(
        "selected_job",
        client,
        mode=mode,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
        fallback=fallback,
    )


def get_fov(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    settings_reading = get_job_settings(
        client, job_name, mode=mode, diagnostics=True, deadline=deadline
    )
    value = (
        None
        if settings_reading is None
        else derived.fov_from_settings(settings_reading.value)
    )
    reading = _derive(settings_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_base_fov(client, job_name, *, mode=None, diagnostics=False, deadline=None):
    settings_reading = get_job_settings(
        client, job_name, mode=mode, diagnostics=True, deadline=deadline
    )
    value = (
        None
        if settings_reading is None
        else derived.base_fov_from_settings(settings_reading.value)
    )
    reading = _derive(settings_reading, value)
    return _plain_or_diagnostic(reading, diagnostics)


def get_lasx_settings(settings_path=None):
    return api_reader.get_lasx_settings(settings_path=settings_path)


def get_pending_dialog(*, diagnostics=False):
    snapshot = log_reader.parse_msgbox_log()
    value = log_reader.get_pending_dialog(snapshot)
    age_s = log_reader.age_for_snapshot(snapshot, age_key="dialog")
    observed_at = None if age_s is None else snapshot.now - age_s
    reading = Reading(
        value=value,
        source="log",
        observed_at=observed_at,
        age_s=age_s,
        error=None,
    )
    return _plain_or_diagnostic(reading, diagnostics)
