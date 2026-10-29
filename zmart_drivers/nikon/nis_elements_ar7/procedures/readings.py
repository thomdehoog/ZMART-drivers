"""The named readings of a NIS-Elements session: where the stage is, which objective is in, and the others.

Each function hands the name of one row of the table of readings
(``primitives/nis_python/profile.py``) to the dispatcher's ``run()``, which
asks the bridge by the waiting rule. :func:`reading` hands back the whole
:class:`~...dispatcher.read.Reading` (its value, or the reason there is none);
the short functions below hand back the value and raise ``RuntimeError`` with
the reason when there is none, because the steps that call them cannot go on
without it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import dispatch
from ..dispatcher.read import Reading
from ..primitives.nis_python import profile


def reading(client: Any, name: str, **asked: Any) -> Reading | None:
    """The reading *name*, through the dispatcher: a Reading, or None when nothing counted in time."""
    return dispatch.run(profile, client, name, **asked)


def value(client: Any, name: str) -> Any:
    """The value of the reading *name*; ``RuntimeError`` saying why when NIS gave none."""
    found = reading(client, name)
    if found is None:
        raise RuntimeError(f"NIS did not answer the reading {name!r} in time")
    if found.error is not None:
        raise RuntimeError(f"NIS could not give the reading {name!r}: {found.error}")
    return found.value


def xy(client) -> dict:
    """Where the stage is: ``{"x_um", "y_um"}``, NIS's own numbers."""
    return value(client, "xy")


def z(client) -> dict:
    """Where the focus drive is: ``{"z_um"}``, NIS's own number."""
    return value(client, "z")


def version(client) -> str:
    return value(client, "version")


def stage_limits(client) -> dict:
    return value(client, "stage_limits")


def optical_configurations(client) -> list[str]:
    return value(client, "optical_configurations")


def selected_optical_configuration(client) -> str | None:
    """The name of the selected optical configuration, or None when NIS does not say."""
    from ..primitives.nis_python.reads import selected_name

    return selected_name(
        value(client, "selected_optical_configuration"), optical_configurations(client)
    )


def nosepiece(client) -> dict:
    return value(client, "nosepiece")


def objective(client) -> dict:
    """The objective in place: ``{"position", "name"}``; both None without a motorised nosepiece."""
    turret = nosepiece(client)
    position = turret.get("position")
    return {
        "position": position,
        "name": turret["objectives"].get(position) if position else None,
    }


def pfs(client) -> dict:
    return value(client, "pfs")


def image_info(client) -> dict:
    return value(client, "image_info")


def calibration(client) -> dict:
    return value(client, "calibration")


def devices(client) -> dict:
    """Every hardware setting the driver knows, as NIS has it now: which devices are there, and their values.

    ``{key: {"group", "says", "kind", "unit", "guarded", "devices": [{"instance", "label",
    "positions", "value"}], "error"}}``. A setting whose device is not there has an
    empty ``devices`` list.
    """
    return value(client, "devices")


def device(client, key: str, instance: str | None = None):
    """One hardware setting's value now: ``device("shutter", "EPI")``; ``RuntimeError`` when NIS gives none."""
    return value(client, f"device/{key}" + (f"/{instance}" if instance else ""))


def device_names(inventory: dict, *, settable_only: bool = False) -> dict:
    """``{"shutter/EPI": value, "light_path": value, ...}``: each device that is there, by the name ``set_state`` takes.

    With *settable_only*, a setting whose reading is not what it sets (a
    heating stage reads its current temperature, but is set to a target) is
    left out: handing its reading back to ``set_state`` would make the
    current temperature the new target.
    """
    from ..primitives.nis_python import devices as device_table

    found = {}
    for key, entry in inventory.items():
        if (
            settable_only
            and key in device_table.SETTINGS
            and not device_table.SETTINGS[key].confirms
        ):
            continue
        for item in entry["devices"]:
            found[key + (f"/{item['instance']}" if item["instance"] else "")] = item[
                "value"
            ]
    return found
