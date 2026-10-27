"""The readings the ``dll`` class can answer, one function each.

Each function sends one read-only request to the bridge and hands back the
answer in the driver's own words: micrometres, nosepiece slots as whole
numbers, and the PFS state as the bridge reports it. They read and change
nothing. The rest of the driver never calls them directly: it asks the
dispatcher's ``run()`` for a reading by its name, and the profile
(``profile.py``) looks the function up in ``READS``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations


def get_xyz(client) -> dict:
    """Where the stage and the focus drive are, in micrometres, as NIS-Elements reports them."""
    position = client.request("get_position")
    return {
        "x_um": float(position["x"]),
        "y_um": float(position["y"]),
        "z_um": float(position["z"]),
    }


def get_limits(client) -> dict:
    """The stage limits set in NIS-Elements itself: per axis ``[min, max]`` in micrometres."""
    limits = client.request("get_limits")
    return {
        axis: [float(limits[axis]["min"]), float(limits[axis]["max"])]
        for axis in ("x", "y", "z")
    }


def get_optical_configurations(client) -> list[str]:
    """The names of the optical configurations NIS-Elements has (each one is a channel's settings)."""
    return [str(name) for name in client.request("get_optical_configurations")]


def get_objectives(client) -> dict:
    """The nosepiece: ``{"current": slot or None, "objectives": {slot: name}}``, slots counted from 1.

    The bridge sends the slots as JSON keys, which are text; here they become
    whole numbers again, so a slot compares with what ``set_objective`` takes.
    """
    answer = client.request("get_objectives")
    current = answer.get("current")
    return {
        "current": None if current is None else int(current),
        "objectives": {
            int(slot): str(name) for slot, name in answer.get("objectives", {}).items()
        },
    }


def get_pfs(client) -> dict:
    """The Perfect Focus System: ``present``, ``on``, NIS's ``status`` code and its ``meaning``."""
    return dict(client.request("get_pfs"))


def get_version(client) -> str:
    """The NIS-Elements version, as NIS reports it."""
    return str(client.request("get_version"))


def ping(client) -> dict:
    """Whether the bridge answers: its version, the protocol version and NIS's version."""
    return dict(client.request("ping"))


#: The readings of this class, by the name the dispatcher is asked for.
READS = {
    "xyz": get_xyz,
    "limits": get_limits,
    "optical_configurations": get_optical_configurations,
    "objectives": get_objectives,
    "pfs": get_pfs,
    "version": get_version,
}
