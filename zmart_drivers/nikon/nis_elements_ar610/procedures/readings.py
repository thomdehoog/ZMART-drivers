"""The named readings of a session: where the stage is, which objectives there are, the camera, and so on.

Each function hands the name of one row of the table of readings in
``primitives/dll/profile.py`` to the dispatcher's ``run()``, which runs the
read loop (``dispatcher/read.py``), and hands back a plain value, or, with
``diagnostics=True``, a :class:`~...dispatcher.read.Reading` that also says
which class answered and when. A reading that cannot be had comes back as
None; the readers here do not raise.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch as _door
from ..dispatcher.read import plain_or_diagnostic as _plain_or_diagnostic
from ..primitives.dll import profile as dll_profile
from ..primitives.dll import reads as dll_reads

log = logging.getLogger(__name__)


def wait_for(datum, client, **keywords):
    """Wait for a reading of *datum* by the one rule: the dispatcher's ``run()`` with the reading's name.

    Every named reading below comes through here.
    """
    return _door.run(dll_profile, client, datum, **keywords)


def _routed(datum, client, *, diagnostics=False, deadline=None, accept=None):
    reading = wait_for(datum, client, accept=accept, deadline=deadline)
    return _plain_or_diagnostic(reading, diagnostics)


def get_xyz(client, *, diagnostics=False, deadline=None, accept=None):
    """Where the stage and the focus drive are: ``{"x_um", "y_um", "z_um"}``, as NIS reports them."""
    return _routed(
        "xyz", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_limits(client, *, diagnostics=False, deadline=None):
    """The stage limits set in NIS-Elements itself: per axis ``[min, max]`` in micrometres."""
    return _routed("limits", client, diagnostics=diagnostics, deadline=deadline)


def get_optical_configurations(client, *, diagnostics=False, deadline=None):
    """The names of NIS's optical configurations: the channels one can select."""
    return _routed(
        "optical_configurations", client, diagnostics=diagnostics, deadline=deadline
    )


def get_objectives(client, *, diagnostics=False, deadline=None, accept=None):
    """The nosepiece: the current slot and the objective in each slot."""
    return _routed(
        "objectives", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_pfs(client, *, diagnostics=False, deadline=None, accept=None):
    """The Perfect Focus System: present, on, and NIS's status."""
    return _routed(
        "pfs", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_camera(client, *, diagnostics=False, deadline=None, accept=None):
    """The camera and its exposure (through the macro class: about a tenth of a second)."""
    return _routed(
        "camera", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_version(client, *, diagnostics=False, deadline=None):
    """The NIS-Elements version."""
    return _routed("version", client, diagnostics=diagnostics, deadline=deadline)


def ping(client):
    """Whether the bridge answers; its versions when it does, None when it does not."""
    try:
        return dll_reads.ping(client)
    except Exception as why:  # noqa: BLE001 -- a ping that fails is an answer: no
        log.warning("the bridge did not answer the ping: %s", why)
        return None
