"""The limits rulebook: every "is this allowed?" answer in the driver comes from here.

Stage positions (the measured travel and the physical backstop), the
objective slot, and the value of every setting are checked by the rules in
this file. Refusing is the dispatcher's limits gate (``dispatcher/limits.py``),
which asks this rulebook through :class:`EvidentLimits` once for every change,
before anything is sent. Nothing else checks limits itself.

A stage move is guarded by two layers, both asked before anything is sent:

1. **The measured travel**, from this microscope's ``limits.json``
   (``driver_setup/limits``), applied with :func:`set_stage_limits` when the
   driver connects. Moves refuse until it is configured.
2. **The physical backstop** (:data:`STAGE_BACKSTOP_UM`), fixed in this file
   and checked after the travel, so that even a hand-widened limits file can
   never command a move outside what the hardware can do.

The FV4000 reports positions in nanometres, counted from a zero FLUOVIEW
chooses; where that zero lies on the stage is not known until the limits are
measured on the microscope (``measure_limits.py``). The backstop below is
therefore wide and symmetric around zero: wider than any FV4000 stage, so a
file reaching outside it cannot be trusted, but no protection on its own.
The measured ``limits.json`` is what keeps the stage safe.

The stage travel kept here is shared by every call in the process: one
microscope per process, set at connect.

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

# =============================================================================
# The physical backstop
# =============================================================================

# !! NOT YET VERIFIED ON AN FV4000 !! -----------------------------------------
# Micrometres, [min, max] per axis, in FLUOVIEW's own coordinates. Chosen to
# be wider than the travel of any FV4000 frame (an inverted IX85 stage travels
# about 114 x 75 mm; a focus drive about 10 mm; a piezo a few hundred µm), on
# either side of FLUOVIEW's zero, because where that zero lies is only known
# once the limits are measured. NEVER widen these without measured data from
# the microscope; narrowing is always safe.
# -------------------------------------------------------------------------------
STAGE_BACKSTOP_UM = {
    "x": (-200000.0, 200000.0),
    "y": (-200000.0, 200000.0),
    "z": (-20000.0, 20000.0),
    "z_piezo": (-1000.0, 1000.0),
}


def check_envelope_within_backstop(stage_um: Mapping[str, Any]) -> None:
    """Refuse a limits file whose travel reaches outside the physical backstop.

    *stage_um* is ``{"x": [min, max], "y": ..., "z": ..., "z_piezo": ...}``
    from a validated limits file. A file wider than the hardware is a
    hand-edited or wrong-microscope file and must not govern a session.
    """
    for axis, (backstop_lo, backstop_hi) in STAGE_BACKSTOP_UM.items():
        lo, hi = float(stage_um[axis][0]), float(stage_um[axis][1])
        if lo < backstop_lo or hi > backstop_hi:
            raise RuntimeError(
                f"stage limits for axis {axis!r} = [{lo}, {hi}] reach outside the physical "
                f"backstop [{backstop_lo}, {backstop_hi}] (driver_setup/limits/limit_rules.py "
                f"STAGE_BACKSTOP_UM); refusing the file: one wider than the hardware cannot be trusted"
            )


# =============================================================================
# The stage travel in force
# =============================================================================

AXES = ("x", "y", "z", "z_piezo")
_stage_limits: dict[str, tuple[float, float] | None] = {axis: None for axis in AXES}


def set_stage_limits(**ranges: tuple[float, float]) -> None:
    """Set the travel of each axis (``x=(lo, hi), y=..., z=..., z_piezo=...``), in micrometres."""
    unknown = sorted(set(ranges) - set(AXES))
    if unknown:
        raise ValueError(f"unknown stage axes {unknown}; the axes are {list(AXES)}")
    for axis, (lo, hi) in ranges.items():
        _stage_limits[axis] = (float(lo), float(hi))


def get_stage_limits() -> dict:
    """The travel in force, per axis (a copy)."""
    return dict(_stage_limits)


def clear_stage_limits() -> None:
    """Forget the travel, so every move refuses until it is set again (the tests use this)."""
    for axis in AXES:
        _stage_limits[axis] = None


def apply_stage_limits_from_config(stage_cfg: dict) -> None:
    """Set the travel from a loaded limits file (the shape ``config.load`` gives)."""
    set_stage_limits(
        **{axis: tuple(bounds) for axis, bounds in stage_cfg["stage_um"].items()}
    )


def _require_finite(value, label):
    """A move target must be a plain finite number; a NaN would pass every comparison below."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label}={value!r} is not a number; refusing the move")
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{label}={value!r} is not finite; refusing the move")
    return number


def _check_axis(axis: str, value, label: str) -> None:
    travel = _stage_limits[axis]
    if travel is None:
        raise RuntimeError(
            "the stage limits are not configured; the driver sets them when it connects"
        )
    number = _require_finite(value, label)
    lo, hi = travel
    if number < lo or number > hi:
        raise RuntimeError(
            f"{label}={number} outside the measured travel [{lo}, {hi}] um"
        )
    # The backstop after the travel: a hand-widened file never passes the hardware.
    backstop_lo, backstop_hi = STAGE_BACKSTOP_UM[axis]
    if number < backstop_lo or number > backstop_hi:
        raise RuntimeError(
            f"{label}={number} outside the physical backstop [{backstop_lo}, {backstop_hi}] um "
            "(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM); refused whatever the file says"
        )


def check_xy(x, y) -> None:
    """Refuse an XY target outside the travel or the backstop (``RuntimeError`` with the reason)."""
    _check_axis("x", x, "X")
    _check_axis("y", y, "Y")


def check_z(z, axis: str = "z") -> None:
    """Refuse a focus target outside the travel or the backstop; *axis* is ``"z"`` (the motor) or ``"z_piezo"``."""
    if axis not in ("z", "z_piezo"):
        raise ValueError(f"unknown z axis {axis!r}; use 'z' or 'z_piezo'")
    _check_axis(axis, z, "Z" if axis == "z" else "piezo")


# =============================================================================
# The compiled limits document
# =============================================================================

#: The axis entries of ``limits.json``, each ``{"range": [min, max]}`` in micrometres.
AXIS_FILE_KEYS = {"x": "x_um", "y": "y_um", "z": "z_um", "z_piezo": "z_piezo_um"}

#: The settings a limits file names, ``set_<setting>`` for every setting of
#: ``primitives/rdk/writes.py``: ``[]`` (reviewed, unrestricted) or one typed
#: constraint, ``{"range": [min, max]}`` or ``{"allowed": [...]}``. Kept here
#: as a plain list, so this rulebook imports nothing of the driver but the gate.
SETTER_LIMIT_KEYS = (
    "set_channel_enable",
    "set_channel_hv",
    "set_channel_gain",
    "set_channel_offset",
    "set_channel_digital_gain",
    "set_channel_laser",
    "set_laser_enable",
    "set_laser_intensity",
    "set_z_stack_enable",
    "set_z_stack_slices",
    "set_z_stack_step",
    "set_z_stack_start",
    "set_z_stack_end",
    "set_z_stack_range",
    "set_z_origin",
    "set_z_type",
    "set_time_lapse_enable",
    "set_time_lapse_repeat",
    "set_time_lapse_interval",
    "set_scanner_type",
    "set_scanner_mode",
    "set_brightness_adjustment_create",
    "set_brightness_adjustment_enable",
    "set_brightness_adjustment_delete_all",
    "set_matl_create",
    "set_matl_well_duplicate",
    "set_matl_delete",
    "set_matl_enable",
    "set_matl_area_delete",
    "set_matl_area_enable",
    "set_matl_area_z_range",
    "set_load_parameter",
)


@dataclass(frozen=True, slots=True, init=False)
class EvidentLimits:
    """The limits of one session, compiled from its flat ``limits.json``, ready to be asked."""

    _policy: Mapping[str, tuple[str, tuple[Any, ...]] | None]
    source: str
    path: Any
    is_fallback: bool

    def __init__(
        self, payload: Mapping[str, Any], *, source: str, path: Any, is_fallback: bool
    ):
        compiled = {}
        for name, entry in payload.items():
            if entry == []:
                compiled[name] = None
                continue
            kind, values = next(iter(entry.items()))
            compiled[name] = (kind, tuple(values))
        object.__setattr__(self, "_policy", MappingProxyType(compiled))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "is_fallback", is_fallback)

    def _origin(self) -> str:
        return f"limits: {self.path}, source={self.source}"

    def _check_one(self, name: str, spec: Any, value: Any) -> None:
        if spec is None:
            return
        kind, configured = spec
        if kind == "range":
            low, high = configured
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LimitViolation(
                    f"{name}={value!r} is not numeric (range [{low}, {high}]; {self._origin()})"
                )
            number = float(value)
            if not math.isfinite(number) or number < low or number > high:
                raise LimitViolation(
                    f"{name}={value!r} outside range [{low}, {high}] ({self._origin()})"
                )
            return
        if not any(
            type(value) is type(candidate) and value == candidate
            for candidate in configured
        ):
            raise LimitViolation(
                f"{name}={value!r} not allowed; expected one of {list(configured)!r} ({self._origin()})"
            )

    def check_command(self, command: str, values: Mapping[str, Any]) -> None:
        """Check *values* for the change *command*, under the policy key ``MUTATING_COMMANDS`` maps it to.

        A command missing from that table is a mistake in the driver, not in
        the values, so it raises ``KeyError``.
        """
        self.check(MUTATING_COMMANDS[command], values)

    def check(self, function: str, values: Mapping[str, Any]) -> None:
        if function == "set_xyz":
            for key in AXIS_FILE_KEYS.values():
                if key in values:
                    self._check_one(key, self._policy[key], values[key])
            try:
                if "x_um" in values and "y_um" in values:
                    check_xy(values["x_um"], values["y_um"])
                if "z_um" in values:
                    check_z(values["z_um"], "z")
                if "z_piezo_um" in values:
                    check_z(values["z_piezo_um"], "z_piezo")
            except RuntimeError as exc:
                raise LimitViolation(str(exc)) from exc
            return
        if function == "set_objective":
            if "objective_slot" in values:
                self._check_one(
                    "objective_slot",
                    self._policy["objective_slot"],
                    values["objective_slot"],
                )
            return
        if function not in SETTER_LIMIT_KEYS:
            return
        spec = self._policy[function]
        if spec is None:
            return
        candidates = values.get("values")
        if candidates is None and "value" in values:
            candidates = [values["value"]]
        if not candidates:
            raise LimitsError(
                f"{function} has a configured limit but the change supplied no value ({self._origin()})"
            )
        for value in candidates:
            self._check_one(function, spec, value)

    def status(self) -> LimitsStatus:
        return LimitsStatus(
            source=self.source,
            path=str(self.path) if self.path is not None else None,
            is_fallback=self.is_fallback,
        )

    def describe(self) -> dict:
        return self.status().describe()


# =============================================================================
# What the shared gate asks of this microscope
# =============================================================================

#: Every change -> the policy key it is checked under.
MUTATING_COMMANDS = {
    **{name: name for name in SETTER_LIMIT_KEYS},
    # A MATL group's position is a place the scan will go: checked like a move.
    "set_matl_xy": "set_xyz",
    "set_matl_z": "set_xyz",
    "set_matl_z_origin": "set_xyz",
    "set_objective": "set_objective",
    # Loading a picture's settings recalls a whole setup at once; the file may name which pictures.
    "load_parameter": "set_load_parameter",
    "move_xy": "set_xyz",
    "move_z": "set_xyz",
    "move_piezo": "set_xyz",
    # Allowed as such; they move nothing and set no value the file could bound.
    "acquire": "acquire",
    "stop": "stop",
    "repeat_scan": "repeat_scan",
    "set_remote_mode": "set_remote_mode",
}


def build_limits(stage_cfg, *, source, path, is_fallback) -> EvidentLimits:
    """Check a loaded limits file against the backstop, apply its travel, and compile it.

    Raises when the file is not acceptable, so the caller can fall back to the
    bundled defaults.
    """
    check_envelope_within_backstop(stage_cfg["stage_um"])
    limits = EvidentLimits(
        stage_cfg["policy"], source=source, path=path, is_fallback=is_fallback
    )
    apply_stage_limits_from_config(stage_cfg)
    return limits
