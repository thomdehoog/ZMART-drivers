"""The limits rulebook: every "is this allowed?" answer in the driver comes from here.

Stage and focus positions, the objective positions, the experiments that
may be loaded: the rules for each are in this file. Enforcing them, the
moment of refusing, is the dispatcher's limits gate (``dispatcher/limits.py``),
which asks this rulebook through :class:`ZeissLimits` once for every change,
before anything is sent. Nothing else checks limits itself.

Stage moves are guarded by two layers, both asked by the gate:

1. **The session envelope**: the limits file measured for this microscope
   (``limits.json``), installed at connect by ``handshake.py``. Moves refuse
   until it is configured.
2. **The backstop** (:data:`STAGE_BACKSTOP_UM`): checked after the envelope,
   independently of any file, so that even a hand-widened limits file can
   never command a move far beyond any ZEISS stage. ZEN drives many stands
   with different stages, so this is a sanity bound, not the measured travel
   of one stage: the measured limits are the envelope that matters.

The envelope is set once at connect and shared by every change of the
session: one instrument per process, as the dispatcher assumes.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on the Leica driver's rulebook.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...dispatcher.limits import LimitsError, LimitsStatus, LimitViolation

# =============================================================================
# The backstop
# =============================================================================

#: The furthest any ZEISS stage ZEN drives can reach, in ZEN's own coordinates
#: (micrometres, [min, max] per axis): 200 mm of travel either way, and 50 mm
#: of focus. No stand comes near; a limits file that reaches past this is a
#: hand-edited or wrong-machine file and is refused. Narrowing is always safe.
STAGE_BACKSTOP_UM = {
    "x": (-200000.0, 200000.0),
    "y": (-200000.0, 200000.0),
    "z": (-50000.0, 50000.0),
}


def check_envelope_within_backstop(stage_um: Mapping[str, Any]) -> None:
    """Refuse a limits envelope that reaches outside the backstop; such a file cannot be trusted."""
    for axis, (low, high) in STAGE_BACKSTOP_UM.items():
        lo, hi = float(stage_um[axis][0]), float(stage_um[axis][1])
        if lo < low or hi > high:
            raise RuntimeError(
                f"the stage limits for {axis} = [{lo}, {hi}] reach outside the backstop [{low}, {high}] "
                "(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM); a file wider than any ZEISS "
                "stage's travel cannot be trusted"
            )


# =============================================================================
# The session envelope
# =============================================================================

_stage_limits: dict[str, float | None] = {
    "x_min": None,
    "x_max": None,
    "y_min": None,
    "y_max": None,
    "z_min": None,
    "z_max": None,
}


def set_stage_limits(*, x_min, x_max, y_min, y_max, z_min, z_max) -> None:
    """Set the session's envelope for the stage and the focus, in micrometres."""
    _stage_limits.update(
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, z_min=z_min, z_max=z_max
    )


def get_stage_limits() -> dict:
    """The session's envelope, as a copy."""
    return dict(_stage_limits)


def apply_stage_limits_from_config(stage_cfg: Mapping[str, Any]) -> None:
    """Set the envelope from a loaded limits configuration (``config.load``)."""
    stage = stage_cfg["stage_um"]
    set_stage_limits(
        x_min=stage["x"][0],
        x_max=stage["x"][1],
        y_min=stage["y"][0],
        y_max=stage["y"][1],
        z_min=stage["z"][0],
        z_max=stage["z"][1],
    )


def _require_finite(value, label: str) -> float:
    """A move target must be a finite number: NaN, infinity, None and text all refuse."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label}={value!r} is not a number; refusing the move")
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{label}={value!r} is not finite; refusing the move")
    return number


def check_xy(x, y) -> None:
    """Refuse an XY target outside the envelope or the backstop, with ``RuntimeError``."""
    if _stage_limits["x_min"] is None:
        raise RuntimeError(
            "the stage limits are not configured; the driver sets them when it connects"
        )
    x = _require_finite(x, "X")
    y = _require_finite(y, "Y")
    for axis, value in (("x", x), ("y", y)):
        lo, hi = _stage_limits[f"{axis}_min"], _stage_limits[f"{axis}_max"]
        if value < lo or value > hi:
            raise RuntimeError(
                f"{axis.upper()}={value} is outside the limits [{lo}, {hi}]"
            )
    for axis, value in (("x", x), ("y", y)):
        lo, hi = STAGE_BACKSTOP_UM[axis]
        if value < lo or value > hi:
            raise RuntimeError(
                f"{axis.upper()}={value} is outside the backstop [{lo}, {hi}] "
                "(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM), refused whatever the envelope says"
            )


def check_z(z) -> None:
    """Refuse a focus target outside the envelope or the backstop, with ``RuntimeError``."""
    if _stage_limits["z_min"] is None:
        raise RuntimeError(
            "the stage limits are not configured; the driver sets them when it connects"
        )
    z = _require_finite(z, "Z")
    lo, hi = _stage_limits["z_min"], _stage_limits["z_max"]
    if z < lo or z > hi:
        raise RuntimeError(f"Z={z} is outside the focus limits [{lo}, {hi}]")
    lo, hi = STAGE_BACKSTOP_UM["z"]
    if z < lo or z > hi:
        raise RuntimeError(
            f"Z={z} is outside the backstop [{lo}, {hi}] "
            "(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM), refused whatever the envelope says"
        )


# =============================================================================
# The compiled limits document
# =============================================================================

#: The entries of limits.json besides the three axes: each is ``[]``
#: (reviewed, unrestricted) or one typed constraint, ``{"allowed": [...]}``
#: or ``{"range": [min, max]}``.
POLICY_KEYS = ("objective_position", "experiment", "set_z_stack")

#: The axes of limits.json and the key each is written under.
AXIS_FILE_KEYS = {"x": "x_um", "y": "y_um", "z": "z_um"}


@dataclass(frozen=True, slots=True, init=False)
class ZeissLimits:
    """The limits document compiled into a checker the gate asks."""

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
                f"{name}={value!r} is not allowed; the allowed values are {list(configured)!r} ({self._origin()})"
            )

    def check_command(self, command: str, values: Mapping[str, Any]) -> None:
        """Check *values* for *command* by the policy entry it maps to; an unknown command is a driver mistake."""
        self.check(MUTATING_COMMANDS[command], values)

    def check(self, function: str, values: Mapping[str, Any]) -> None:
        if function == "set_xyz":
            for param in ("x_um", "y_um", "z_um"):
                if param in values:
                    self._check_one(param, self._policy[param], values[param])
            try:
                if "x_um" in values and "y_um" in values:
                    check_xy(values["x_um"], values["y_um"])
                if "z_um" in values:
                    check_z(values["z_um"])
            except RuntimeError as why:
                raise LimitViolation(str(why)) from why
            return
        if function == "set_objective":
            self._check_one(
                "objective_position",
                self._policy["objective_position"],
                values["objective_position"],
            )
            return
        if function == "experiment":
            self._check_one(
                "experiment", self._policy["experiment"], values["experiment"]
            )
            return
        if function == "set_z_stack":
            self._check_one(
                "experiment", self._policy["experiment"], values["experiment"]
            )
            ends = list(values.get("values") or [])
            # A stack's ends are focus positions: they stay within the focus travel.
            try:
                for end in ends:
                    check_z(end)
            except RuntimeError as why:
                raise LimitViolation(f"a z-stack end: {why}") from why
            spec = self._policy["set_z_stack"]
            if spec is None:
                return
            if not ends:
                raise LimitsError(
                    f"set_z_stack has a configured limit but no end was given ({self._origin()})"
                )
            for value in ends:
                self._check_one("set_z_stack", spec, value)
            return
        # Everything else (stopping, focusing, remembering a focus) has no limit to check.

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

#: Every change -> the policy entry that governs it. ``testing/unit/test_limits.py``
#: checks that every write the driver builds is here.
MUTATING_COMMANDS = {
    "move_xy": "set_xyz",
    "move_z": "set_xyz",
    "set_objective": "set_objective",
    "load_experiment": "experiment",
    "acquire": "experiment",
    "set_z_stack": "set_z_stack",
    "stop": "stop",
    "autofocus": "experiment",
    "find_surface": "focus",
    "store_focus": "focus",
    "recall_focus": "focus",
}


def build_limits(stage_cfg, *, source, path, is_fallback) -> ZeissLimits:
    """Check a loaded limits file against the backstop, apply its envelope, and compile it.

    Raises when the file is not acceptable, so the caller can fall back to
    the bundled defaults.
    """
    check_envelope_within_backstop(stage_cfg["stage_um"])
    limits = ZeissLimits(
        stage_cfg["policy"], source=source, path=path, is_fallback=is_fallback
    )
    apply_stage_limits_from_config(stage_cfg)
    return limits
