"""The devices beyond the stage: lights, lasers, shutters, filters, the light path, the port and the camera.

Each device is read through its named reading and changed through its named
change (``readings.py``, ``changes.py``), so every change passes the limits
and is read back. This file adds two things on top:

- :func:`get_devices`, every device's settings in one dictionary, in the
  shape ``set_state`` takes, so a state read once can be applied again later;
- :func:`apply_devices`, which applies such a dictionary, device by device,
  and changes only what differs from what NIS-Elements reports now. A name
  NIS-Elements does not have (a light, a laser line, a filter changer) is
  refused with the names it has, before anything is changed.

The shapes, all optional when applying::

    "lights":     {"DIA": {"on": True, "voltage": 3.0, "iris": 0.5}}
    "lasers":     {"SpectraIII": {"475": {"on": True, "power": 20}}}   by wavelength in nm
    "filters":    {"Turret-Up": 2}                                    position, from 1
    "shutters":   {"EPI": True}                                       True is open
    "light_path": {"condenser": 1, "apertures": {"epi_field": 80.0}, "zoom": 1.5, "analyzer": False}
    "port":       {"port": 1, "disk_shutter": False, "disk_speed_rpm": 4000}
    "camera":     {"format": "FMT 2x2 16", "roi": {"left": 0, "top": 0, "right": 1024, "bottom": 1024},
                   "averaging": 1, "integration": 1}

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from . import changes
from . import readings as _readers

log = logging.getLogger(__name__)

#: The devices, in the order :func:`apply_devices` changes them: the light
#: path and the port first, since they decide where the light goes; then the
#: filters and the camera; the light sources last, so nothing shines onto
#: the sample before its path is set.
DEVICES = ("light_path", "port", "filters", "camera", "lights", "lasers", "shutters")


# =============================================================================
# Reading
# =============================================================================


def lights_state(lights: dict | None) -> dict:
    """The lights as ``set_state`` takes them: a value NIS does not control is left out."""
    return {
        name: {k: v for k, v in light.items() if v is not None}
        for name, light in (lights or {}).items()
    }


def lasers_state(lasers: dict | None) -> dict:
    """The laser lines as ``set_state`` takes them: by wavelength, written as text, with ``on`` and ``power``."""
    return {
        laser: {
            str(nm): {"on": line["on"], "power": line["power"]}
            for nm, line in lines.items()
        }
        for laser, lines in (lasers or {}).items()
    }


def light_path_state(path: dict | None) -> dict:
    """The parts of the light path ``set_state`` can change; a zoom turned by hand is left out, since the driver cannot turn it."""
    if not path:
        return {}
    state: dict[str, Any] = {}
    if path.get("condenser"):
        state["condenser"] = path["condenser"]["position"]
    if path.get("apertures"):
        state["apertures"] = {
            name: aperture["size"] for name, aperture in path["apertures"].items()
        }
    if path.get("zoom") and not path["zoom"].get("manual"):
        state["zoom"] = path["zoom"]["zoom"]
    if path.get("analyzer"):
        state["analyzer"] = path["analyzer"]["inserted"]
    return state


def port_state(path: dict | None) -> dict:
    """The camera port and the spinning disk, as ``set_state`` takes them."""
    if not path:
        return {}
    state: dict[str, Any] = {}
    if path.get("port"):
        state["port"] = path["port"]["position"]
    disk = path.get("disk") or {}
    if disk.get("shutter_open") is not None:
        state["disk_shutter"] = disk["shutter_open"]
    if disk.get("speed_rpm") is not None:
        state["disk_speed_rpm"] = disk["speed_rpm"]
    return state


def camera_state(camera: dict | None) -> dict:
    """The camera settings ``set_state`` can change: format, region of interest, averaging, integration."""
    if not camera:
        return {}
    return {
        key: camera[key]
        for key in ("format", "roi", "averaging", "integration")
        if key in camera
    }


def get_devices(client) -> dict:
    """Every device's settings, in the shape :func:`apply_devices` takes. A device that cannot be read is left out."""
    path = _readers.get_light_path(client)
    found = {
        "light_path": light_path_state(path),
        "port": port_state(path),
        "filters": {
            name: changer["position"]
            for name, changer in (_readers.get_filters(client) or {}).items()
        },
        "camera": camera_state(_readers.get_camera(client)),
        "lights": lights_state(_readers.get_lights(client)),
        "lasers": lasers_state(_readers.get_lasers(client)),
        "shutters": dict(_readers.get_shutters(client) or {}),
    }
    return {name: value for name, value in found.items() if value}


# =============================================================================
# Applying
# =============================================================================


def _unknown(what: str, asked, known) -> ValueError:
    return ValueError(
        f"no {what} {asked!r}; NIS-Elements has {sorted(known) or 'none'}"
    )


def _need(result: dict, what: str, record) -> dict:
    """Raise when a change failed; note it when it was sent but never seen; answer it."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if result.get("confirmed") is False:
        record(what, result)
    return result


def check_names(client, wanted: dict) -> None:
    """Refuse a device name NIS-Elements does not have, before anything changes."""
    unknown = sorted(set(wanted) - set(DEVICES))
    if unknown:
        raise ValueError(f"no device {unknown}; the devices are {list(DEVICES)}")
    if "lights" in wanted:
        known = _readers.get_lights(client) or {}
        for name in wanted["lights"]:
            if name not in known:
                raise _unknown("light", name, known)
    if "lasers" in wanted:
        known = _readers.get_lasers(client) or {}
        for laser, lines in wanted["lasers"].items():
            if laser not in known:
                raise _unknown("laser", laser, known)
            for nm in lines:
                if int(nm) not in known[laser]:
                    raise _unknown(
                        f"line of {laser}", nm, [str(n) for n in known[laser]]
                    )
    if "filters" in wanted:
        known = _readers.get_filters(client) or {}
        for changer, position in wanted["filters"].items():
            if changer not in known:
                raise _unknown("filter changer", changer, known)
            if position not in known[changer]["filters"]:
                raise ValueError(
                    f"{changer} has positions {sorted(known[changer]['filters'])}, not {position!r}"
                )
    if "shutters" in wanted:
        known = _readers.get_shutters(client) or {}
        for name in wanted["shutters"]:
            if name not in known:
                raise _unknown("shutter", name, known)


def apply_devices(handle, wanted: dict) -> dict:
    """Apply *wanted* (the shape of :func:`get_devices`), changing only what differs; answer what was applied.

    Every name is checked against NIS-Elements first, so a wrong name
    changes nothing. A change NIS refused raises ``RuntimeError``, naming
    it; the devices changed before it stay as they were set. A change sent
    but never seen is recorded in the session and the others go on.
    """
    client = handle.client
    check_names(client, wanted)
    now = get_devices(client)
    applied: dict[str, Any] = {}
    record = handle.note_unconfirmed

    for device in DEVICES:
        if device not in wanted:
            continue
        asked, current = wanted[device], now.get(device, {})
        if device == "light_path":
            changed = {}
            for key in ("condenser", "zoom", "analyzer"):
                if key in asked and asked[key] != current.get(key):
                    _need(
                        changes.set_light_path(client, **{key: asked[key]}),
                        f"light path {key}",
                        record,
                    )
                    changed[key] = asked[key]
            for name, size in (asked.get("apertures") or {}).items():
                if size != (current.get("apertures") or {}).get(name):
                    _need(
                        changes.set_light_path(client, aperture=name, size=size),
                        f"aperture {name}",
                        record,
                    )
                    changed.setdefault("apertures", {})[name] = size
        elif device == "port":
            changed = {
                key: value for key, value in asked.items() if value != current.get(key)
            }
            if changed:
                _need(changes.set_port(client, **changed), "port and disk", record)
        elif device == "filters":
            changed = {}
            for changer, position in asked.items():
                if position != current.get(changer):
                    _need(
                        changes.set_filter(client, changer, position),
                        f"filter changer {changer}",
                        record,
                    )
                    changed[changer] = position
        elif device == "camera":
            changed = {
                key: value for key, value in asked.items() if value != current.get(key)
            }
            if changed:
                _need(changes.set_camera(client, **changed), "camera", record)
        elif device == "lights":
            changed = {}
            for name, settings in asked.items():
                differs = {
                    k: v
                    for k, v in settings.items()
                    if v != (current.get(name) or {}).get(k)
                }
                if differs:
                    _need(
                        changes.set_light(client, name, **differs),
                        f"light {name}",
                        record,
                    )
                    changed[name] = differs
        elif device == "lasers":
            changed = {}
            for laser, lines in asked.items():
                for nm, settings in lines.items():
                    differs = {
                        k: v
                        for k, v in settings.items()
                        if v != ((current.get(laser) or {}).get(str(nm)) or {}).get(k)
                    }
                    if differs:
                        _need(
                            changes.set_laser(client, laser, int(nm), **differs),
                            f"laser {laser} {nm} nm",
                            record,
                        )
                        changed.setdefault(laser, {})[str(nm)] = differs
        else:  # shutters
            changed = {}
            for name, open_ in asked.items():
                if open_ != current.get(name):
                    _need(
                        changes.set_shutter(client, name, open_),
                        f"shutter {name}",
                        record,
                    )
                    changed[name] = open_
        if changed:
            applied[device] = changed
    return applied


def resolve_laser(client, wavelength_nm: int, laser: str | None = None) -> str:
    """The light source that has the line *wavelength_nm*: *laser* when given, else the one there is."""
    lasers = _readers.get_lasers(client) or {}
    if laser is None:
        having = [name for name, lines in lasers.items() if int(wavelength_nm) in lines]
        if len(having) != 1:
            raise ValueError(
                f"name the laser: {len(having) or 'no'} light sources have a {wavelength_nm} nm line "
                f"({', '.join(f'{n}: {sorted(lines)}' for n, lines in lasers.items()) or 'none'})"
            )
        return having[0]
    if laser not in lasers:
        raise _unknown("laser", laser, lasers)
    if int(wavelength_nm) not in lasers[laser]:
        raise _unknown(
            f"line of {laser}", wavelength_nm, [str(n) for n in lasers[laser]]
        )
    return laser


def resolve_filter(
    client, changer: str, position: int | None = None, filter_name: str | None = None
) -> int:
    """The position of *changer* to turn to: *position* when given, else the one holding *filter_name*."""
    filters = _readers.get_filters(client) or {}
    if changer not in filters:
        raise _unknown("filter changer", changer, filters)
    held = filters[changer]["filters"]
    if position is not None:
        if position not in held:
            raise ValueError(
                f"{changer} has positions {sorted(held)}, not {position!r}"
            )
        return position
    named = [p for p, name in held.items() if name and name == filter_name]
    if len(named) != 1:
        raise ValueError(
            f"give a position, or one filter of {changer}: "
            + ", ".join(f"{p}: {n or '(empty)'}" for p, n in held.items())
        )
    return named[0]
