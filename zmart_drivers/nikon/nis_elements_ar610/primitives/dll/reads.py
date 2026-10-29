"""The readings the ``dll`` class can answer, one function each.

Each function sends one read-only request to the bridge and hands back the
answer in the driver's own words: micrometres, nosepiece and filter
positions as whole numbers counted from 1, and each device by its name, as
NIS-Elements names it. They read and change
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
    """The Perfect Focus System: ``present``, ``on``, NIS's ``status`` code and its ``meaning``.

    With a PFS there are two more: ``offset``, the plane the PFS keeps, in
    NIS's own units (not micrometres), and ``dish``, ``glass`` or ``plastic``,
    or None when this PFS does not let the dish type be changed.
    """
    return dict(client.request("get_pfs"))


def get_lights(client) -> dict:
    """Each lamp or LED, by name: ``{"on", "voltage", "iris"}``.

    A value NIS-Elements does not control is None: many LEDs have no
    voltage, and many lamps no iris.
    """
    return {
        light["name"]: {key: light.get(key) for key in ("on", "voltage", "iris")}
        for light in client.request("get_lights")
    }


def get_filters(client) -> dict:
    """Each filter changer (a turret or a wheel), by name: ``{"position", "filters"}``.

    Positions count from 1, as NIS-Elements' own panels show them, and
    ``filters`` names the filter in each position (an empty name for an
    empty one).
    """
    return {
        changer["name"]: {
            "position": int(changer["position"]),
            "filters": {
                int(position): str(name or "")
                for position, name in changer["filters"].items()
            },
        }
        for changer in client.request("get_filters")
    }


def get_shutters(client) -> dict:
    """Each shutter, by name: True when it is open."""
    return {
        shutter["name"]: bool(shutter["open"])
        for shutter in client.request("get_shutters")
    }


def get_light_path(client) -> dict:
    """The transmitted light path, the light port and the spinning disk, as NIS reports them.

    ``condenser`` (``position`` of ``positions``), ``apertures`` (by name:
    ``field``, ``epi_field``, ``dia``, each with its ``size`` and
    ``steps``), ``zoom`` (``zoom`` and ``manual``, True when it is turned by
    hand), ``analyzer`` (``inserted``), ``port`` (``position`` of
    ``positions``), ``disk`` (``shutter_open``, ``speed_rpm``) and
    ``piezo_z`` (whether there is one). A part NIS-Elements does not have is
    None.
    """
    return dict(client.request("get_light_path"))


def get_environment(client) -> dict:
    """The heating and cooling stages: each with its ``temperature`` (degrees C) and ``status``. Reads only."""
    return {
        stage["name"]: {
            "temperature": stage["temperature"],
            "status": stage["status"],
        }
        for stage in client.request("get_environment")["heat_stages"]
    }


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
    "lights": get_lights,
    "filters": get_filters,
    "shutters": get_shutters,
    "light_path": get_light_path,
    "environment": get_environment,
    "version": get_version,
}
