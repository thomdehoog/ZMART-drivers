"""The named readings of a FLUOVIEW session: where the stage is, what FLUOVIEW is doing, which objective is in.

Each function hands the name of one row of the table of readings
(``primitives/rdk/reads.py``) to the dispatcher's ``run()``, which runs the
read loop (``dispatcher/read.py``) by the one waiting rule, and hands back a
plain value, or, with ``diagnostics=True``, a ``Reading`` that also says when
the answer was seen. A reading that cannot be had comes back as None; the
readers here do not raise.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time

from ..dispatcher import dispatch as _door
from ..dispatcher.read import plain_or_diagnostic
from ..primitives.rdk import profile as rdk_profile
from ..primitives.rdk.reads import MANUAL_MAIN, READS

log = logging.getLogger(__name__)


def wait_for(datum, client, **keywords):
    """Wait for a reading of *datum* by the one rule: the dispatcher's ``run()`` with the reading's name."""
    return _door.run(rdk_profile, client, datum, **keywords)


def read(datum, client, *, about=None, diagnostics=False, deadline=None, accept=None):
    """The reading *datum* (a name in ``READS``), about *about* when it is about one item; None when there is no answer."""
    if datum not in READS:
        raise ValueError(f"unknown reading {datum!r}; the readings are {sorted(READS)}")
    reading = wait_for(datum, client, about=about, deadline=deadline, accept=accept)
    return plain_or_diagnostic(reading, diagnostics)


def read_now(datum, client, *, about=None):
    """One look at the reading *datum*, without waiting for a better answer; None when FLUOVIEW does not answer."""
    return read(
        datum, client, about=about, deadline=time.monotonic() + rdk_profile.POLL_S
    )


# --- the readings nearly every procedure asks ----------------------------------------------


def ping(client) -> bool:
    """Whether FLUOVIEW answers: its version read within one window."""
    return (
        read("version", client, deadline=time.monotonic() + rdk_profile.WINDOW_S)
        is not None
    )


def get_version(client, **keywords):
    return read("version", client, **keywords)


def get_scan_state(client, **keywords):
    """``IDLING``, ``SCANNING``, ``RESTING`` or ``WAITING``; None when unknown."""
    return read("scan_state", client, **keywords)


def get_progress(client, execution_type=MANUAL_MAIN, **keywords):
    """The progress record of the scan of *execution_type*; see ``reads.get_protocol_progress``."""
    return read("protocol_progress", client, about=execution_type, **keywords)


def get_xy(client, **keywords):
    """``{"x_um", "y_um"}`` in FLUOVIEW's own coordinates; None when unknown."""
    return read("xy", client, **keywords)


def get_z(client, **keywords):
    """The focus motor's position in micrometres, FLUOVIEW's absolute coordinates; None when unknown."""
    return read("z", client, **keywords)


def get_z_piezo(client, **keywords):
    """The piezo's position in micrometres from FLUOVIEW's Z origin; None when unknown."""
    return read("z_piezo", client, **keywords)


def get_z_origin(client, **keywords):
    return read("z_origin", client, **keywords)


def get_objective(client, **keywords):
    """``{"slot", "name"}`` of the objective in use; None when unknown."""
    return read("objective", client, **keywords)


def get_objectives(client, **keywords):
    """``[{"slot", "name"}, ...]`` for the nosepiece; None when unknown."""
    return read("objectives", client, **keywords)


def get_channels(client, **keywords):
    return read("channels", client, **keywords)


def get_lasers(client, **keywords):
    return read("lasers", client, **keywords)


def get_acquiring_files(client, **keywords):
    return read("acquiring_files", client, **keywords)
