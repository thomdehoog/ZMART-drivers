"""The named readings of a ZEN session: where the stage is, which objective is in, how an experiment is doing.

Each function hands the name of one row of the table of readings in
``primitives/gateway/profile.py`` to the dispatcher's ``run()``, which runs
the read loop (``dispatcher/read.py``), and hands back a plain value, or,
with ``diagnostics=True``, a ``Reading`` that also says when the answer was
seen and, when there was none, why. A reading that cannot be had comes back
as None; the readers here do not raise.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch as _door
from ..dispatcher.read import plain_or_diagnostic as _plain_or_diagnostic
from ..primitives.gateway import profile as gateway_profile

log = logging.getLogger(__name__)


def wait_for(datum, client, **keywords):
    """Wait for a reading of *datum* by the one rule: the dispatcher's ``run()`` with the reading's name.

    Every named reading below comes through here.
    """
    return _door.run(gateway_profile, client, datum, **keywords)


def _routed(
    datum, client, *, diagnostics=False, deadline=None, accept=None, about=None
):
    reading = wait_for(datum, client, accept=accept, about=about, deadline=deadline)
    return _plain_or_diagnostic(reading, diagnostics)


def get_xy(client, *, diagnostics=False, deadline=None, accept=None):
    """Where the XY stage is: ``{"x_um", "y_um"}`` in ZEN's own stage coordinates; None when unknown."""
    return _routed(
        "xy", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_z(client, *, diagnostics=False, deadline=None, accept=None):
    """Where the focus drive is: ``{"z_um"}`` in ZEN's own focus coordinates; None when unknown."""
    return _routed(
        "z", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_objective(client, *, diagnostics=False, deadline=None):
    """The objective in place: its changer ``position``, ``name``, ``magnification``, ``na``; None when unknown."""
    return _routed("objective", client, diagnostics=diagnostics, deadline=deadline)


def get_objectives(client, *, diagnostics=False, deadline=None):
    """Every objective on the changer, by its position; None when unknown."""
    return _routed("objectives", client, diagnostics=diagnostics, deadline=deadline)


def get_experiments(client, *, diagnostics=False, deadline=None):
    """The names of the experiments ZEN can load; None when unknown."""
    return _routed("experiments", client, diagnostics=diagnostics, deadline=deadline)


def get_image_output_path(client, *, diagnostics=False, deadline=None):
    """The folder where ZEN writes its CZI files, as ZEN names it; None when unknown."""
    return _routed(
        "image_output_path", client, diagnostics=diagnostics, deadline=deadline
    )


def get_experiment_status(
    client, experiment_id, *, diagnostics=False, deadline=None, accept=None
):
    """How the loaded experiment *experiment_id* is doing (see ``reads.status_as_dict``); None when unknown."""
    return _routed(
        "experiment_status",
        client,
        diagnostics=diagnostics,
        deadline=deadline,
        accept=accept,
        about=experiment_id,
    )


def get_activity(client, *, diagnostics=False, deadline=None, accept=None):
    """Whether ZEN is acquiring anything now: ``{"running": bool, "status": ...}``; None when unknown."""
    return _routed(
        "activity", client, diagnostics=diagnostics, deadline=deadline, accept=accept
    )


def get_zstack(client, experiment_id, *, diagnostics=False, deadline=None):
    """The experiment's z-stack: first and last slice, step and count, in micrometres; None when unknown."""
    return _routed(
        "zstack",
        client,
        diagnostics=diagnostics,
        deadline=deadline,
        about=experiment_id,
    )


def get_tracks(client, experiment_id, *, diagnostics=False, deadline=None):
    """The experiment's tracks and their channels; None when unknown."""
    return _routed(
        "tracks",
        client,
        diagnostics=diagnostics,
        deadline=deadline,
        about=experiment_id,
    )


def ping(client) -> bool:
    """Whether ZEN answers: the stage reading comes back."""
    return get_xy(client) is not None


def active_channels(tracks) -> list[str]:
    """The names of the channels an acquisition takes: those active in the active tracks, in order."""
    return [
        channel["name"]
        for track in tracks or []
        if track.get("active")
        for channel in track.get("channels", [])
        if channel.get("active")
    ]
