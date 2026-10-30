"""What the microscope is doing now, and what can be changed: its state.

The *changeable* part is what ``set_state`` applies: the objective (its
position on the nosepiece, counted from 1) and the optical configuration (a
named setup in NIS-Elements that carries the light path, the filters, the
camera's exposure and, often, the objective), then every hardware setting of
the table in ``primitives/nis_python/devices.py`` whose device NIS reports:
a shutter is ``"shutter/EPI"``, the light path is ``"light_path"``, a laser
line's power is ``"laser_power/LU-N4.2"``. Applying them all again restores the
imaging setup. ``set_state`` applies the objective, then the optical
configuration, then the hardware settings, the light last (see
:data:`GROUP_ORDER`). The *observed* part can only be read: the NIS version,
the stage's travel, the objectives and optical configurations there are,
Perfect Focus, the limits in force, which objective the origin was saved
under, how far this microscope is set up, the changes that were sent but
never confirmed, every hardware setting there is with what it means and
which values it takes (``devices``), and the size of a picture and of its
pixels.

The size of a picture (``image_size_px``) and of its pixels
(``pixel_size_um``) are the two keys the ZMART Controller asks every driver
for. NIS tells them only for an image that is open, so they are answered,
in this order, from:

1. what this microscope recorded at its last capture with the same objective
   and optical configuration (``driver_setup/pixel_size``);
2. the image open in NIS now, which may have been taken with other settings;
3. nothing: both are None, and ``image_size_source`` says to capture one picture.

``image_size_source`` always says which it was. ``get_state`` never fails
for want of an image.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/state.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..dispatcher import limits as gate
from ..driver_setup import pixel_size
from ..primitives.nis_python import devices as device_table
from . import changes, readings

log = logging.getLogger(__name__)

#: What ``set_state`` can change, in the order it applies them.
CHANGEABLE = ("objective_position", "optical_configuration")


def _try(what: str, read):
    """*read()*, or None with a warning when NIS cannot give it: one missing reading does not hide the rest."""
    try:
        return read()
    except RuntimeError as why:
        log.warning("get_state: %s could not be read: %s", what, why)
        return None


def sizes(handle, objective: str | None, configuration: str | None) -> dict:
    """``image_size_px``, ``pixel_size_um`` and ``image_size_source``, as the picture is saved (turned when it is)."""
    entry = pixel_size.lookup(objective, configuration)
    if entry is not None:
        size, px = entry["image_size_px"], entry["pixel_size_um"]
        source = f"the last capture with {objective} and {configuration}, {entry.get('seen')}"
        if not px:
            source += "; that objective is not calibrated in NIS, so the pixel size is unknown"
    else:
        info = _try("the open image", lambda: readings.image_info(handle.client)) or {}
        calibration = (
            _try("the calibration", lambda: readings.calibration(handle.client)) or {}
        )
        if info.get("open"):
            size, px = [info["width"], info["height"]], calibration.get("pixel_size_um")
            source = "the image open in NIS now, which may have been taken with other settings"
            if not px:
                source += f"; its pixel size is unknown ({calibration.get('why') or 'no calibration'})"
        else:
            return {
                "image_size_px": None,
                "pixel_size_um": None,
                "image_size_source": "unknown: capture one picture",
            }
    pixels = [px, px] if px else None
    if handle.orientation is not None and handle.orientation.swaps_axes:
        size = size[::-1]
    return {
        "image_size_px": list(size),
        "pixel_size_um": pixels,
        "image_size_source": source,
    }


def setup_readiness(handle, limits: dict | None) -> dict:
    """Whether this microscope is set up for an experiment, and what is missing, in words."""
    issues = []
    if not limits or limits.get("is_fallback"):
        issues.append(
            "the limits are not published: the stage keeps to NIS's travel and z to near where it stood at connect"
        )
    if not (handle.orientation_info or {}).get("measured"):
        issues.append(
            "the orientation is not stated: pictures are saved as the camera gives them"
        )
    if handle.origin.get("objective") is None:
        issues.append("no origin is saved: positions are NIS's own stage coordinates")
    if handle.data_location is None and not handle.connection.get("output_root"):
        issues.append("no data location is set: acquire needs an output_folder")
    return {"ready": not issues, "issues": issues}


def get_state(handle) -> dict:
    """``{"changeable", "observed"}``: the objective and optical configuration, then what can only be read."""
    handle.require_open()
    client = handle.client
    turret = _try("the nosepiece", lambda: readings.nosepiece(client)) or {
        "present": False,
        "position": None,
        "objectives": {},
    }
    position = turret.get("position")
    objective = turret["objectives"].get(position) if position else None
    known = (
        _try(
            "the optical configurations",
            lambda: readings.optical_configurations(client),
        )
        or []
    )
    configuration = _try(
        "the selected optical configuration",
        lambda: readings.selected_optical_configuration(client),
    )
    limits = gate.describe(client)
    inventory = _try("the hardware settings", lambda: readings.devices(client)) or {}
    origin_objective = handle.origin.get("objective")
    observed: dict[str, Any] = {
        "vendor": "nikon",
        "microscope": handle.connection.get("microscope"),
        "nis_version": handle.nis_version,
        "nis_travel": _try("NIS's travel", lambda: readings.stage_limits(client)),
        "objectives": {str(k): v for k, v in turret["objectives"].items()},
        "objective": {"position": position, "name": objective},
        "optical_configurations": known,
        "pfs": _try("Perfect Focus", lambda: readings.pfs(client)),
        "limits": limits,
        "origin_objective": origin_objective,
        "objective_differs_from_origin": bool(origin_objective)
        and origin_objective.get("position") != position,
        **sizes(handle, objective, configuration),
        "orientation": dict(handle.orientation_info or {}),
        "setup": setup_readiness(handle, limits),
        "unconfirmed": list(handle.unconfirmed),
        "devices": {
            key: entry
            for key, entry in inventory.items()
            if entry["devices"] or entry["error"]
        },
    }
    return {
        "changeable": {
            "objective_position": position,
            "optical_configuration": configuration,
            **readings.device_names(inventory, settable_only=True),
        },
        "observed": observed,
    }


def set_state(handle, changeable: dict) -> dict:
    """Apply the objective, then the optical configuration (it may bring an objective of its own), then the hardware settings; what was applied.

    The hardware settings are named as ``get_state`` names them
    (``"shutter/EPI"``, ``"light_path"``) and applied in the order of
    :data:`GROUP_ORDER`, the light last. Each change is read back.

    Everything asked is checked before anything is sent: an unknown setting
    or a value it does not take raises ``ValueError``, and a value the limits
    refuse raises ``RuntimeError``, with nothing changed. A hardware setting
    is read again just before its turn, after the optical configuration has
    had its say, and is not sent when it already holds the asked value. A
    change that was sent but never seen is recorded, and reported with the
    answer.
    """
    handle.require_open()
    client = handle.client
    asked = {name: value for name, value in changeable.items() if value is not None}
    asked_devices = {
        name: value for name, value in asked.items() if name not in CHANGEABLE
    }
    checked = check_everything(handle, asked, asked_devices)
    noted_before = len(handle.unconfirmed)
    applied: dict[str, Any] = {}
    steps = (
        ("objective_position", changes.set_objective),
        ("optical_configuration", changes.select_optical_configuration),
    )
    for name, change in steps:
        if name not in asked:
            continue
        value = asked[name]
        result = change(client, value)
        if not result.get("success"):
            raise RuntimeError(
                f"{name} {value!r} was not applied: {result.get('message')}"
            )
        if not result.get("confirmed"):
            handle.note_unconfirmed(f"set_state: {name} {value!r}", result)
        applied[name] = value
    for name in device_order(asked_devices):
        value = checked[name]
        key, instance = changes.split_device_name(name)
        row = device_table.setting(key)
        if row.confirms:
            try:
                now = readings.device(client, key, instance)
            except RuntimeError:
                now = None
            if device_table.matches(row, value, now):
                applied[name] = (
                    value  # already so: nothing is sent, not even to a guarded setting
                )
                continue
        result = changes.set_device(
            client, name, value, inventory=checked["_inventory"]
        )
        if not result.get("success"):
            raise RuntimeError(
                f"{name} {value!r} was not applied: {result.get('message')}"
            )
        if not result.get("confirmed") and row.confirms:
            handle.note_unconfirmed(f"set_state: {name} {value!r}", result)
        applied[name] = value
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


def check_everything(handle, asked: dict, asked_devices: dict) -> dict:
    """Check every asked setting and value, and ask the limits, before anything is sent.

    Answers the hardware settings' values as they will be sent (a switch as
    1 or 0, a position as a whole number), and, under ``"_inventory"``, what
    NIS reported about its devices. Raises ``ValueError`` for an unknown
    setting or a value it does not take, and ``RuntimeError`` for a value
    the limits refuse.
    """
    client = handle.client
    checked: dict[str, Any] = {"_inventory": None}
    if "optical_configuration" in asked:
        known = readings.optical_configurations(client)
        if asked["optical_configuration"] not in known:
            raise ValueError(
                f"no optical configuration {asked['optical_configuration']!r}; NIS has {known}"
            )
    for name, command in (
        ("objective_position", "set_objective"),
        ("optical_configuration", "select_optical_configuration"),
    ):
        if name in asked:
            refusal = gate.check_refusal(client, command, {name: asked[name]})
            if refusal:
                raise RuntimeError(f"nothing was changed: {refusal}")
    if not asked_devices:
        return checked
    inventory = readings.devices(client)
    checked["_inventory"] = inventory
    offered = readings.device_names(inventory)
    unknown = sorted(set(asked_devices) - set(offered))
    if unknown:
        raise ValueError(
            f"no changeable setting {unknown}; the changeable settings are "
            f"{list(CHANGEABLE) + sorted(readings.device_names(inventory, settable_only=True))}"
        )
    for name, value in asked_devices.items():
        key, instance = changes.split_device_name(name)
        row = device_table.setting(key)
        positions = next(
            (
                d.get("positions")
                for d in inventory[key]["devices"]
                if d["instance"] == instance
            ),
            None,
        )
        value = device_table.checked_value(row, value, positions)
        checked[name] = value
        if row.confirms and device_table.matches(row, value, offered.get(name)):
            # Already so: nothing will be sent unless it changes before its turn,
            # and then the limits gate is asked as for every change.
            continue
        refusal = gate.check_refusal(
            client, "set_device", {"device": key, "instance": instance, "value": value}
        )
        if refusal:
            raise RuntimeError(f"nothing was changed: {refusal}")
        checked[name] = value
    return checked


#: The order in which ``set_state`` applies hardware settings: the light path,
#: optics and filters first, and the light itself last, so that a shutter
#: opens or a lamp comes on only once the light has its way to the sample.
GROUP_ORDER = (
    "optics",
    "filters",
    "focus",
    "spinning disk",
    "TIRF",
    "environment",
    "lasers",
    "light",
)


def device_order(names) -> list[str]:
    """The hardware settings in the order they are applied (see :data:`GROUP_ORDER`), then the table's order."""
    rows = {row.key: index for index, row in enumerate(device_table.TABLE)}

    def place(name: str) -> tuple:
        key = name.partition("/")[0]
        group = (
            device_table.SETTINGS[key].group if key in device_table.SETTINGS else None
        )
        return (
            GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER),
            rows.get(key, 0),
            name,
        )

    return sorted(names, key=place)
