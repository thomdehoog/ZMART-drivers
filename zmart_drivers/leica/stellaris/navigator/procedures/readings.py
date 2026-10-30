"""The named readings of a session: where the stage is, what LAS X is set to, what the instrument is.

Each function hands the name of one row of the table of readings in
``primitives/direct/profile.py`` to the dispatcher's ``run()``, which runs
the read loop (``dispatcher/read.py``) by the one waiting rule, and hands
back a plain value, or, with ``diagnostics=True``, a
:class:`~...dispatcher.read.Reading` that also says when the answer was
seen. A reading that cannot be had comes back as None; the readers here do
not raise.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch as _door
from ..dispatcher.read import Reading  # noqa: F401
from ..dispatcher.read import derive as _derive
from ..dispatcher.read import plain_or_diagnostic as _plain_or_diagnostic
from ..primitives.direct import profile as direct_profile
from ..primitives.direct import reads as direct_reader

log = logging.getLogger(__name__)


def wait_for(datum, client, **keywords):
    """Wait for a reading of *datum* by the one rule: the dispatcher's ``run()`` with the reading's name.

    Every named reading below comes through here.
    """
    return _door.run(direct_profile, client, datum, **keywords)


def _routed(datum, client, *, diagnostics=False, deadline=None, accept=None, about=None):
    reading = wait_for(datum, client, accept=accept, about=about, deadline=deadline)
    return _plain_or_diagnostic(reading, diagnostics)


def ping(client) -> bool:
    """Whether LAS X's hardware server answers now; never raises."""
    return direct_reader.ping()


def get_scan_status(client, *, diagnostics=False, deadline=None, accept=None):
    """The scan status as LAS X names it: ``eScanIdle``, ``eScanSingle``, ``eScanContinuous``..."""
    return _routed("scan_status", client, diagnostics=diagnostics, deadline=deadline, accept=accept)


def get_scan_mode(client, *, diagnostics=False, deadline=None):
    """The scan mode: ``xyz``, ``xyt``..."""
    return _routed("scan_mode", client, diagnostics=diagnostics, deadline=deadline)


def get_xy(client, *, diagnostics=False, deadline=None, accept=None):
    """The stage position: ``x_um`` and ``y_um`` (and ``x``, ``y`` in metres)."""
    return _routed("xy", client, diagnostics=diagnostics, deadline=deadline, accept=accept)


def read_z_um(client, drive, *, diagnostics=False, deadline=None, accept=None):
    """Where the z drive *drive* (``"z-galvo"`` or ``"z-wide"``) is, in micrometres."""
    return _routed("z", client, diagnostics=diagnostics, deadline=deadline, accept=accept, about=drive)


def read_zwide_um(client, **keywords):
    """Where z-wide is, in micrometres; see :func:`read_z_um`."""
    return read_z_um(client, "z-wide", **keywords)


def get_zoom(client, **keywords):
    """The zoom: ``current``, ``min``, ``max``."""
    return _routed("zoom", client, **keywords)


def get_scan_speed(client, **keywords):
    """The scan speed: ``value``, ``unit``, ``isResonant``."""
    return _routed("scan_speed", client, **keywords)


def get_scan_speed_range(client, **keywords):
    """The scan speeds the instrument offers: ``Min``, ``Max``, ``ResonantSpeed`` (Hz)."""
    return _routed("scan_speed_range", client, **keywords)


def get_scan_field_rotation(client, **keywords):
    """The scan field's rotation in degrees: ``value``, ``min``, ``max``."""
    return _routed("scan_field_rotation", client, **keywords)


def get_averaging(client, **keywords):
    """Line and frame averaging and accumulation: ``lineAverage``, ``frameAverage``..."""
    return _routed("averaging", client, **keywords)


def get_pinhole(client, **keywords):
    """The pinhole: ``airy`` (``value``, ``min``, ``max``) and ``metres``."""
    return _routed("pinhole", client, **keywords)


def get_format(client, **keywords):
    """The image format in pixels, as LAS X writes it: ``"512 x 512"``."""
    return _routed("format", client, **keywords)


def get_objective(client, **keywords):
    """The objective in the light path: ``slotIndex``, ``name``, ``magnification``, ``immersion``..."""
    return _routed("objective", client, **keywords)


def get_objectives(client, **keywords):
    """Every objective on the turret, by slot."""
    return _routed("objectives", client, **keywords)


def get_fov(client, **keywords):
    """The scan field's width and height, in metres."""
    return _routed("fov", client, **keywords)


def get_pixel_size(client, **keywords):
    """A pixel's width and height, in metres."""
    return _routed("pixel_size", client, **keywords)


def get_bit_depth(client, **keywords):
    """The bits per pixel a capture is digitised with (8, 12 or 16)."""
    return _routed("bit_depth", client, **keywords)


def get_z_stack(client, *, diagnostics=False, deadline=None):
    """The z-stack of the current settings, in micrometres; None when there is none (one section).

    The table's row answers ``{"stack": ...}`` (``z_stack_read`` in the
    profile), so that no stack counts as an answer; it is unwrapped here.
    """
    reading = wait_for("z_stack", client, deadline=deadline)
    if reading is not None and isinstance(reading.value, dict):
        reading = _derive(reading, reading.value["stack"])
    return _plain_or_diagnostic(reading, diagnostics)


def get_serial_number(client, **keywords):
    """The instrument's serial number."""
    return _routed("serial_number", client, **keywords)


def get_jobs(client, **keywords):
    """The Navigator Expert jobs LAS X lists: empty while LAS X shows its plain Navigator."""
    return _routed("jobs", client, **keywords)


def get_hardware_info(client, **keywords):
    """The instrument, whole, as LAS X describes it (``Microscope``, ``SerialNumber``, ``SystemType``...)."""
    return _routed("hardware_info", client, **keywords)


def shows_plain_navigator(client) -> bool | None:
    """Whether LAS X shows its plain Navigator: it lists no Navigator Expert jobs then. None when unknown.

    Navigator Expert's jobs are in the state tree only while Navigator
    Expert is shown; with the plain Navigator the list is empty
    (STELLARIS, 2026-10-15). A session that finds jobs is on the wrong
    Navigator for this driver and says so at connect.
    """
    jobs = get_jobs(client)
    return None if jobs is None else not jobs
