"""The limits rulebook: every "is this change allowed?" answer of the driver comes from here.

The dispatcher's limits gate (``dispatcher/limits.py``) asks this rulebook,
through :class:`NikonLimits`, once for every change, before anything is sent
to NIS. Nothing else in the driver checks limits.

What it allows:

- **The stage** (``move_xy``, ``move_z``): x, y and z must be numbers within
  the ranges in force. Those are the operator's ``limits.json``, cut to the
  travel NIS itself reports: the driver never allows more than NIS does, and
  never invents an outer bound of its own. Until the operator has published a
  ``limits.json``, z may also move only a little either way from where it
  stood when the driver connected (``UNPUBLISHED_Z_WINDOW_UM``), because z is
  where an objective can run into the sample.
- **The objective** (``set_objective``) and **the optical configuration**
  (``select_optical_configuration``): any, or only those the file lists.
- **The capture** (``capture``): always allowed; it moves nothing.

A command this rulebook does not know is refused: a new change must be added
here before it can be sent.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/limits/limit_rules.py``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ...dispatcher.limits import LimitsError, LimitsStatus, LimitViolation
from ...primitives.nis_python import devices

#: How far z may move either way from where it stood at connect, until limits are published.
UNPUBLISHED_Z_WINDOW_UM = 100.0

#: The axes of the stage, as the limits file names them.
AXES = ("x_um", "y_um", "z_um")

#: Every change the driver can send, and the limits entry it is checked against.
COMMANDS = {
    "move_xy": ("x_um", "y_um"),
    "move_z": ("z_um",),
    "set_objective": ("objective_position",),
    "select_optical_configuration": ("optical_configuration",),
    "capture": (),
    "set_device": ("devices",),
}


def number(value: Any, label: str) -> float:
    """*value* as a finite number; LimitViolation for anything else (text, True, NaN, infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitViolation(f"{label}={value!r} is not a number; refusing the move")
    result = float(value)
    if not math.isfinite(result):
        raise LimitViolation(f"{label}={value!r} is not finite; refusing the move")
    return result


def cut_to(file_range: list, travel: list | None, axis: str) -> list[float]:
    """The part of the file's range that NIS's travel also allows; LimitsError when they do not meet."""
    low, high = float(file_range[0]), float(file_range[1])
    if travel is not None:
        low, high = max(low, float(travel[0])), min(high, float(travel[1]))
    if low > high:
        raise LimitsError(
            f"the limits file's {axis} range {file_range} lies outside NIS's own travel {travel}"
        )
    return [low, high]


class NikonLimits:
    """The limits in force for one connection: what the gate asks with ``check_command``.

    *policy* is the validated ``limits.json`` (see ``config.py``). *travel*
    is NIS's own travel per axis, or None when it could not be read.
    *z_window* is ``[low, high]`` around z at connect, or None once limits
    are published. *source*, *path* and *is_fallback* say where the limits
    came from, for ``get_info`` and the log.
    """

    def __init__(
        self,
        policy: Mapping[str, Any],
        *,
        travel: Mapping[str, list] | None,
        z_window: list | None,
        source: str,
        path: Any,
        is_fallback: bool,
    ) -> None:
        self.ranges = {
            axis: cut_to(policy[axis]["range"], (travel or {}).get(axis), axis)
            for axis in AXES
        }
        if z_window is not None:
            z = self.ranges["z_um"]
            self.ranges["z_um"] = [max(z[0], z_window[0]), min(z[1], z_window[1])]
            if self.ranges["z_um"][0] > self.ranges["z_um"][1]:
                raise LimitsError(f"z at connect lies outside the z range {z}")
        #: The hardware settings' own limits: ``{key: {"allowed": [...]} or {"range": [low, high]}}``.
        self.devices = dict(policy.get("devices") or {})
        self.allowed = {
            name: (None if policy[name] == [] else tuple(policy[name]["allowed"]))
            for name in ("objective_position", "optical_configuration")
        }
        self.travel = dict(travel) if travel else None
        self.z_window = list(z_window) if z_window else None
        self.source = source
        self.path = path
        self.is_fallback = is_fallback

    def _origin(self) -> str:
        return f"limits: {self.path or 'the driver default'}, source={self.source}"

    def check_xy(self, x: Any, y: Any) -> None:
        """Refuse a stage position outside the x and y ranges in force."""
        for axis, value in (("x_um", x), ("y_um", y)):
            low, high = self.ranges[axis]
            if not low <= number(value, axis) <= high:
                raise LimitViolation(
                    f"{axis}={value} is outside the limits [{low:g}, {high:g}] ({self._origin()})"
                )

    def check_z(self, z: Any) -> None:
        """Refuse a focus position outside the z range in force."""
        low, high = self.ranges["z_um"]
        if not low <= number(z, "z_um") <= high:
            why = (
                " (limits are not published yet, so z stays near where it was at connect)"
                if self.z_window
                else ""
            )
            raise LimitViolation(
                f"z_um={z} is outside the limits [{low:g}, {high:g}]{why} ({self._origin()})"
            )

    def check_command(self, command: str, values: Mapping[str, Any]) -> None:
        """Raise LimitViolation when *values* are not allowed for *command*; LimitsError for an unknown command."""
        if command not in COMMANDS:
            raise LimitsError(
                f"{command!r} is not a change the limits know; it is refused"
            )
        if command == "move_xy":
            self.check_xy(values.get("x_um"), values.get("y_um"))
        elif command == "move_z":
            self.check_z(values.get("z_um"))
        elif command in ("set_objective", "select_optical_configuration"):
            (name,) = COMMANDS[command]
            allowed = self.allowed[name]
            value = values.get(name)
            if allowed is not None and not any(
                type(value) is type(a) and value == a for a in allowed
            ):
                raise LimitViolation(
                    f"{name}={value!r} is not allowed; the limits allow {list(allowed)} ({self._origin()})"
                )
        elif command == "set_device":
            self.check_device(
                values.get("device"), values.get("instance"), values.get("value")
            )

    def check_device(self, key: Any, instance: Any, value: Any) -> None:
        """Refuse a hardware setting the limits do not allow.

        The limits file may hold an entry for a setting (``"lamp_voltage"``)
        or for one device of it (``"lamp_voltage/DIA"``, which then wins):
        ``{"allowed": [values]}`` or ``{"range": [low, high]}``. A setting
        without an entry is allowed, unless the table marks it as guarded
        (lasers, heating, gas): those are refused until this microscope's
        limits allow them, because a wrong value could harm the sample, the
        microscope or a person.
        """
        if key not in devices.SETTINGS:
            raise LimitsError(
                f"{key!r} is not a hardware setting the limits know; it is refused"
            )
        row = devices.SETTINGS[key]
        entry = self.devices.get(f"{key}/{instance}") if instance else None
        entry = entry if entry is not None else self.devices.get(key)
        if entry is None:
            if row.guarded:
                raise LimitViolation(
                    f"{key} is guarded ({row.says}); it is refused until this microscope's limits allow it: "
                    f'add "{key}" under "devices" in limits.json ({self._origin()})'
                )
            return
        if "allowed" in entry:
            if row.kind == devices.NUMBER:
                fits = any(
                    number(value, key) == float(a)
                    for a in entry["allowed"]
                    if not isinstance(a, bool)
                )
            else:
                fits = any(
                    value == a and type(value) is type(a) for a in entry["allowed"]
                )
            if not fits:
                raise LimitViolation(
                    f"{key}={value!r} is not allowed; the limits allow {list(entry['allowed'])} ({self._origin()})"
                )
        if "range" in entry:
            low, high = entry["range"]
            if not low <= number(value, key) <= high:
                raise LimitViolation(
                    f"{key}={value} is outside the limits [{low:g}, {high:g}] ({self._origin()})"
                )

    def stage_um(self) -> dict:
        """The stage ranges in force, as the canvas and ``get_info`` read them."""
        return {axis: list(bounds) for axis, bounds in self.ranges.items()}

    def status(self) -> LimitsStatus:
        return LimitsStatus(
            source=self.source,
            path=str(self.path) if self.path is not None else None,
            is_fallback=self.is_fallback,
        )

    def describe(self) -> dict:
        return {
            **self.status().describe(),
            "stage_um": self.stage_um(),
            "z_window": self.z_window,
        }
