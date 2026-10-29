"""The limits rulebook: every "is this allowed?" answer of the driver.

The dispatcher's gate (``dispatcher/limits.py``) asks one question before
anything is sent: ``check_command(command, values)``. This file answers it,
from the limits file the operator published (``config.py`` reads it) and
from the stage limits set in NIS-Elements itself. Nothing else in the driver
checks a limit.

A stage move is held to two layers, both here:

1. **The envelope**: the ranges in the limits file, cut down to what
   NIS-Elements allows (the handshake does the cutting). A move outside it
   is refused.
2. **NIS-Elements' own limits**, read from NIS when the driver connects.
   They are checked after the envelope, on their own, so that even a
   hand-widened file can never command a move NIS would refuse. There is
   nothing to type in here: the backstop comes from the instrument.

The other entries of the file fence what automation may choose: the
nosepiece slots, the optical configurations (selected, edited, copied or
deleted), the exposure, the range of the image-based focus sweep, the laser
lines and their power, a lamp's voltage, the PFS offset, and how far one
step of the piezo Z drive may go. Each is either ``[]`` (reviewed; no limit) or
one constraint, ``{"range": [min, max]}`` or ``{"allowed": [...]}``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...dispatcher.limits import LimitsError, LimitsStatus, LimitViolation

#: The three axes of the file, and their keys in it.
AXES = ("x", "y", "z")
AXIS_FILE_KEYS = {"x": "x_um", "y": "y_um", "z": "z_um"}

#: The entries that fence a setting, each ``[]`` or one typed constraint.
SETTING_LIMIT_KEYS = (
    "objective_slot",
    "optical_configuration",
    "exposure_ms",
    "autofocus_range_um",
    "laser_wavelength_nm",
    "laser_power",
    "light_voltage",
    "pfs_offset",
    "piezo_step_um",
)

#: Every change the dispatcher can be handed, and what of it the file checks:
#: ``"stage"`` for a move, else the pairs ``(entry of the file, the value it
#: checks)``. A value shown as None was not asked for and is not checked; a
#: value the change does not show at all is a mistake in the driver. A change
#: with no pairs is checked by nothing in the file, only by the gate being
#: there. A change missing here is a mistake too, and ``check_command``
#: raises for it.
MUTATING_COMMANDS = {
    "move_xyz": "stage",
    "set_objective": (("objective_slot", "objective_slot"),),
    "select_optical_configuration": (("optical_configuration", "name"),),
    "set_exposure": (("exposure_ms", "exposure_ms"),),
    "autofocus": (("autofocus_range_um", "range_um"),),
    "set_pfs": (),
    "set_pfs_offset": (("pfs_offset", "offset"),),
    "snap": (),
    "set_light": (("light_voltage", "voltage"),),
    "set_laser": (("laser_wavelength_nm", "wavelength_nm"), ("laser_power", "power")),
    "set_shutter": (),
    "set_filter": (),
    "set_light_path": (),
    "set_port": (),
    "set_camera": (),
    "move_piezo_z": (("piezo_step_um", "distance_um"),),
    "center_piezo_z": (),
    "escape": (),
    "live": (),
    "auto_exposure": (),
    "safe_state": (),
    "edit_optical_configuration": (
        ("optical_configuration", "name"),
        ("exposure_ms", "exposure_ms"),
    ),
    "copy_optical_configuration": (
        ("optical_configuration", "name"),
        ("exposure_ms", "exposure_ms"),
    ),
    "delete_optical_configuration": (("optical_configuration", "name"),),
}


def require_finite(value, label: str) -> float:
    """A move target must be a finite number; NaN, None, text and booleans all refuse.

    NaN compares False against every bound, so without this check a NaN
    target would pass straight through the range checks.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitViolation(f"{label}={value!r} is not a number; refusing the move")
    number = float(value)
    if not math.isfinite(number):
        raise LimitViolation(f"{label}={value!r} is not finite; refusing the move")
    return number


@dataclass(frozen=True, slots=True, init=False)
class NikonLimits:
    """The compiled limits document: the envelope, NIS's own limits, and the setting fences.

    ``envelope_um`` is per axis ``[min, max]``, already cut to NIS's limits;
    ``nis_um`` is what NIS-Elements allows, read live. ``source`` says where
    the file came from (``machine`` or ``defaults``) and ``is_fallback``
    whether the defaults stand in for a file that was missing or damaged.
    """

    _policy: Mapping[str, tuple[str, tuple[Any, ...]] | None]
    envelope_um: Mapping[str, tuple[float, float]]
    nis_um: Mapping[str, tuple[float, float]]
    source: str
    path: Any
    is_fallback: bool

    def __init__(
        self,
        policy: Mapping[str, Any],
        *,
        envelope_um: Mapping[str, Any],
        nis_um: Mapping[str, Any],
        source: str,
        path: Any,
        is_fallback: bool,
    ):
        compiled = {}
        for name in SETTING_LIMIT_KEYS:
            entry = policy[name]
            if entry == []:
                compiled[name] = None
                continue
            kind, values = next(iter(entry.items()))
            compiled[name] = (kind, tuple(values))
        object.__setattr__(self, "_policy", MappingProxyType(compiled))
        object.__setattr__(
            self,
            "envelope_um",
            MappingProxyType(
                {axis: (float(lo), float(hi)) for axis, (lo, hi) in envelope_um.items()}
            ),
        )
        object.__setattr__(
            self,
            "nis_um",
            MappingProxyType(
                {axis: (float(lo), float(hi)) for axis, (lo, hi) in nis_um.items()}
            ),
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "is_fallback", is_fallback)

    def _origin(self) -> str:
        return f"limits: {self.path}, source={self.source}"

    def check_stage(self, values: Mapping[str, Any]) -> None:
        """Every axis given must be finite, within the envelope, and within NIS's own limits."""
        for axis in AXES:
            key = AXIS_FILE_KEYS[axis]
            if key not in values:
                continue
            number = require_finite(values[key], key)
            lo, hi = self.envelope_um[axis]
            if number < lo or number > hi:
                raise LimitViolation(
                    f"{key}={number:g} is outside the limits [{lo:g}, {hi:g}] um ({self._origin()})"
                )
            # NIS's own limits, after the envelope: even a hand-widened file
            # cannot pass what the instrument refuses.
            nis_lo, nis_hi = self.nis_um[axis]
            if number < nis_lo or number > nis_hi:
                raise LimitViolation(
                    f"{key}={number:g} is outside what NIS-Elements allows, [{nis_lo:g}, {nis_hi:g}] um; "
                    "refused whatever the limits file says"
                )

    def check_setting(self, name: str, value: Any) -> None:
        """*value* must fit the entry *name*: within its range, or one of its allowed values."""
        spec = self._policy[name]
        if spec is None:
            return
        kind, configured = spec
        if kind == "range":
            low, high = configured
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LimitViolation(
                    f"{name}={value!r} is not a number (range [{low}, {high}]; {self._origin()})"
                )
            number = float(value)
            if not math.isfinite(number) or number < low or number > high:
                raise LimitViolation(
                    f"{name}={value!r} is outside the range [{low}, {high}] ({self._origin()})"
                )
            return
        if not any(
            type(value) is type(candidate) and value == candidate
            for candidate in configured
        ):
            raise LimitViolation(
                f"{name}={value!r} is not allowed; the limits allow {list(configured)!r} ({self._origin()})"
            )

    def check_command(self, command: str, values: Mapping[str, Any]) -> None:
        """What the gate asks: raise when *values* are not allowed for *command*."""
        if command not in MUTATING_COMMANDS:
            raise KeyError(
                f"{command!r} is not a change the limits know; add it to MUTATING_COMMANDS"
            )
        checks = MUTATING_COMMANDS[command]
        if checks == "stage":
            self.check_stage(values)
            return
        for entry, key in checks:
            if key not in values:
                raise LimitsError(
                    f"{command} was not shown its {key!r}, which the limits check ({self._origin()})"
                )
            if values[key] is not None:
                self.check_setting(entry, values[key])

    def status(self) -> LimitsStatus:
        return LimitsStatus(
            source=self.source,
            path=str(self.path) if self.path is not None else None,
            is_fallback=self.is_fallback,
        )

    def describe(self) -> dict:
        """The provenance and the envelope, in plain numbers, for ``get_state`` and ``get_info``."""
        return {
            **self.status().describe(),
            "envelope_um": {
                axis: list(bounds) for axis, bounds in self.envelope_um.items()
            },
            "nis_um": {axis: list(bounds) for axis, bounds in self.nis_um.items()},
        }


def cut_to_nis(
    stage_um: Mapping[str, Any], nis_um: Mapping[str, Any]
) -> tuple[dict, list[str]]:
    """The file's ranges cut down to what NIS-Elements allows; and a note per axis that was cut.

    The driver narrows, never widens: an axis of the file that reaches past
    NIS's limit is trimmed to it, and the note says so, so the operator can
    correct the file. An axis that lies wholly outside NIS's limits is a
    wrong-machine file and raises.
    """
    envelope, notes = {}, []
    for axis in AXES:
        lo, hi = float(stage_um[axis][0]), float(stage_um[axis][1])
        nis_lo, nis_hi = float(nis_um[axis][0]), float(nis_um[axis][1])
        cut_lo, cut_hi = max(lo, nis_lo), min(hi, nis_hi)
        if cut_lo > cut_hi:
            raise LimitsError(
                f"the {axis} limits [{lo:g}, {hi:g}] um lie wholly outside what NIS-Elements allows, "
                f"[{nis_lo:g}, {nis_hi:g}] um; this file was not written for this microscope"
            )
        if (cut_lo, cut_hi) != (lo, hi):
            notes.append(
                f"{axis}: the file's [{lo:g}, {hi:g}] um reaches past NIS-Elements' "
                f"[{nis_lo:g}, {nis_hi:g}] um and is cut to [{cut_lo:g}, {cut_hi:g}] um"
            )
        envelope[axis] = [cut_lo, cut_hi]
    return envelope, notes


def build_limits(
    stage_cfg: dict,
    nis_um: Mapping[str, Any],
    *,
    source: str,
    path: Any,
    is_fallback: bool,
):
    """Compile a loaded limits file (``config.load``) against NIS's live limits.

    Answers ``(limits, notes)``: the :class:`NikonLimits` the gate asks, and
    the notes about axes that had to be cut. Raises when the file cannot be
    used, so the caller can fall back to the bundled defaults.
    """
    envelope, notes = cut_to_nis(stage_cfg["stage_um"], nis_um)
    limits = NikonLimits(
        stage_cfg["policy"],
        envelope_um=envelope,
        nis_um={axis: nis_um[axis] for axis in AXES},
        source=source,
        path=path,
        is_fallback=is_fallback,
    )
    return limits, notes
