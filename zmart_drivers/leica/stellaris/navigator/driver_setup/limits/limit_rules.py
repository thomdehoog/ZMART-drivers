"""The limits rulebook: every "is this allowed?" answer in the driver.

Stage positions (the envelope and the physical backstop), objective slots,
and the five settings the plain Navigator route can change are checked here.
Enforcement, the moment of refusing, is the dispatcher's limits gate
(``dispatcher/limits.py``), which asks this rulebook through
:class:`LeicaLimits` once for every change, before anything is sent. Nothing
else checks limits itself.

Stage moves are guarded by two independent layers, both asked by the gate
before anything is sent:

1. **The session envelope**: the machine-local limits file, applied through
   ``apply_stage_limits_from_config`` by the connect-time handshake. Moves
   refuse until it is configured (fail closed).
2. **The physical backstop** (:data:`STAGE_BACKSTOP_UM`): checked after the
   envelope, independently of any file, so even a hand-widened limits file
   can never command a move outside the physical travel.

A tile scan is a list of positions; every one of them is checked the same
way, and one outside refuses the whole scan.

The module-level envelope is intentional: limits are set once at session
start and shared across all changes. This assumes one instrument per
process, as the dispatcher does; a second connect in the same process
rebinds the envelope for everyone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...dispatcher.limits import LimitsError, LimitsStatus, LimitViolation

# =============================================================================
# The physical backstop
# =============================================================================

# Absolute coordinate backstop for the motorised stage and both z drives
# (micrometres, [min, max] per axis), the ZMB STELLARIS (serial y42h93)
# envelope as the Navigator Expert driver carries it. The bundled X/Y
# defaults start at 1000 um as a conservative margin; operator-measured
# limits may extend below it, down to coordinate zero. The z-wide maximum of
# 8000 um is the measured travel of this stage (operator-confirmed at the
# rig, 2026-07-19). NEVER widen these without measured rig data; narrowing
# is always safe.
STAGE_BACKSTOP_UM = {
    "x": (0.0, 130000.0),
    "y": (0.0, 100000.0),
    "z_galvo": (-250.0, 250.0),
    "z_wide": (0.0, 8000.0),
}


def check_envelope_within_backstop(stage_um):
    """Validate that a limits-file envelope sits WITHIN the physical backstop.

    ``stage_um`` is the ``{"x": [min, max], ...}`` mapping from a validated
    limits file. Raises ``RuntimeError`` when any axis reaches outside
    :data:`STAGE_BACKSTOP_UM`: a file wider than the physical travel is a
    hand-edited (or wrong-machine) file and must not be trusted.
    """
    for axis, (backstop_lo, backstop_hi) in STAGE_BACKSTOP_UM.items():
        lo, hi = float(stage_um[axis][0]), float(stage_um[axis][1])
        if lo < backstop_lo or hi > backstop_hi:
            raise RuntimeError(
                f"stage limits for axis {axis!r} = [{lo}, {hi}] reach outside the "
                f"physical backstop [{backstop_lo}, {backstop_hi}] "
                f"(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM); refusing the envelope: "
                f"a file wider than the machine's physical travel cannot be trusted"
            )


# =============================================================================
# The stage envelope of this session
# =============================================================================

_stage_limits = {
    "x_min": None,
    "x_max": None,
    "y_min": None,
    "y_max": None,
    "z_galvo_min": None,
    "z_galvo_max": None,
    "z_wide_min": None,
    "z_wide_max": None,
}


def set_stage_limits(*, x_min, x_max, y_min, y_max, z_galvo_min, z_galvo_max, z_wide_min, z_wide_max):
    """Configure the session's stage envelope (micrometres).

    An explicit operator action; the hardcoded :data:`STAGE_BACKSTOP_UM`
    still bounds every move independently of what is set here.
    """
    _stage_limits.update(
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
        z_galvo_min=z_galvo_min, z_galvo_max=z_galvo_max, z_wide_min=z_wide_min, z_wide_max=z_wide_max,
    )


def get_stage_limits():
    """The current stage envelope, as a copy."""
    return dict(_stage_limits)


def apply_stage_limits_from_config(stage_cfg: dict) -> None:
    """Configure the stage envelope from a loaded limits configuration (``config.load``'s shape)."""
    lim = stage_cfg["stage_um"]
    set_stage_limits(
        x_min=lim["x"][0], x_max=lim["x"][1], y_min=lim["y"][0], y_max=lim["y"][1],
        z_galvo_min=lim["z_galvo"][0], z_galvo_max=lim["z_galvo"][1],
        z_wide_min=lim["z_wide"][0], z_wide_max=lim["z_wide"][1],
    )


def _require_finite(value, label):
    """A move target must be a finite number; NaN, infinity, None and text all refuse.

    NaN compares False against every bound, so without this check a NaN
    target would pass straight through the range checks. Numeric text is
    refused too, not converted: the checked value must be the commanded value.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label}={value!r} is not a number; refusing the move")
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{label}={value!r} is not finite; refusing the move")
    return number


def check_xy(x, y):
    """Validate a stage position against the envelope, then the physical backstop.

    Raises RuntimeError on any violation (an unconfigured envelope, a target
    that is not a finite number, outside the session envelope, or outside
    the backstop).
    """
    if _stage_limits["x_min"] is None:
        raise RuntimeError("Stage limits not configured. Call set_stage_limits() first.")
    x = _require_finite(x, "X")
    y = _require_finite(y, "Y")
    if x < _stage_limits["x_min"] or x > _stage_limits["x_max"]:
        raise RuntimeError(f"X={x} outside limits [{_stage_limits['x_min']}, {_stage_limits['x_max']}]")
    if y < _stage_limits["y_min"] or y > _stage_limits["y_max"]:
        raise RuntimeError(f"Y={y} outside limits [{_stage_limits['y_min']}, {_stage_limits['y_max']}]")
    # The backstop AFTER the envelope: even a hand-widened envelope cannot
    # authorise a move outside the physical travel.
    for axis, value in (("x", x), ("y", y)):
        lo, hi = STAGE_BACKSTOP_UM[axis]
        if value < lo or value > hi:
            raise RuntimeError(
                f"{axis.upper()}={value} outside the physical backstop [{lo}, {hi}] "
                f"(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM); refused regardless of the "
                f"configured envelope"
            )


def check_z(z, z_mode):
    """Validate a z position against the envelope, then the physical backstop.

    *z_mode* is ``"galvo"`` or ``"zwide"``. Raises RuntimeError when the
    limits are not configured, z is not finite, or z is out of range;
    ValueError for an unknown z_mode.
    """
    if _stage_limits["z_galvo_min"] is None:
        raise RuntimeError("Stage limits not configured. Call set_stage_limits() first.")
    if z_mode == "galvo":
        axis = "z_galvo"
    elif z_mode == "zwide":
        axis = "z_wide"
    else:
        raise ValueError(f"Unknown z_mode '{z_mode}'. Use: 'galvo' or 'zwide'")
    z = _require_finite(z, "Z")
    lo, hi = _stage_limits[f"{axis}_min"], _stage_limits[f"{axis}_max"]
    if z < lo or z > hi:
        raise RuntimeError(f"Z={z} outside {z_mode} limits [{lo}, {hi}]")
    backstop_lo, backstop_hi = STAGE_BACKSTOP_UM[axis]
    if z < backstop_lo or z > backstop_hi:
        raise RuntimeError(
            f"Z={z} ({z_mode}) outside the physical backstop [{backstop_lo}, {backstop_hi}] "
            f"(driver_setup/limits/limit_rules.py STAGE_BACKSTOP_UM); refused regardless of the "
            f"configured envelope"
        )


# =============================================================================
# The compiled limits document (objective slots and the settings)
# =============================================================================

# The five settings the plain Navigator route can change. Each key appears in
# limits.json with either ``[]`` (unrestricted) or one typed constraint, and
# the change of the same name is checked against it before it is sent.
SETTER_LIMIT_KEYS = (
    "set_zoom",
    "set_scan_speed",
    "set_scan_field_rotation",
    "set_line_average",
    "set_pinhole_airy",
)


@dataclass(frozen=True, slots=True, init=False)
class LeicaLimits:
    """The limits document compiled into a checker the gate asks; it never changes once made."""

    _policy: Mapping[str, tuple[str, tuple[Any, ...]] | None]
    source: str
    path: Any
    is_fallback: bool

    def __init__(self, payload: Mapping[str, Any], *, source: str, path: Any, is_fallback: bool):
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
                raise LimitViolation(f"{name}={value!r} is not numeric (range [{low}, {high}]; {self._origin()})")
            number = float(value)
            if not math.isfinite(number) or number < low or number > high:
                raise LimitViolation(f"{name}={value!r} outside range [{low}, {high}] ({self._origin()})")
            return
        allowed = configured
        if not any(type(value) is type(candidate) and value == candidate for candidate in allowed):
            raise LimitViolation(
                f"{name}={value!r} not allowed; expected one of {list(allowed)!r} ({self._origin()})"
            )

    def check_command(self, command: str, values: Mapping[str, Any]) -> None:
        """Check *values* for the change *command* by the policy key it maps to.

        A command missing from ``MUTATING_COMMANDS`` is a mistake in the
        driver, not in the values, so it raises ``KeyError``.
        """
        self.check(MUTATING_COMMANDS[command], values)

    def check(self, function: str, values: Mapping[str, Any]) -> None:
        if function == "set_xyz":
            self._check_positions(values)
            return
        if function == "set_objective":
            if "objective_slot" in values:
                self._check_one("objective_slot", self._policy["objective_slot"], values["objective_slot"])
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
            raise LimitsError(f"{function} has a configured limit but the change supplied no value ({self._origin()})")
        for value in candidates:
            self._check_one(function, spec, value)

    def _check_positions(self, values: Mapping[str, Any]) -> None:
        """A stage move's axes, or every position of a tile scan, against the file's limits, the envelope and the backstop."""
        places = [dict(values)]
        if "positions_um" in values:
            positions = values["positions_um"]
            if not isinstance(positions, (list, tuple)) or not positions:
                raise LimitViolation(f"positions_um must be a list of (x, y, z) positions, got {positions!r}")
            places = []
            for position in positions:
                try:
                    x, y, z = position
                except (TypeError, ValueError):
                    raise LimitViolation(f"a tile position is (x, y, z) in micrometres, not {position!r}") from None
                places.append({"x_um": x, "y_um": y, "z_galvo_um": z})
        for place in places:
            for param in ("x_um", "y_um", "z_galvo_um", "z_wide_um"):
                if param in place:
                    self._check_one(param, self._policy[param], place[param])
            # The stage envelope and the physical backstop, after the file's
            # limits: even a hand-widened envelope cannot pass the travel.
            try:
                if "x_um" in place and "y_um" in place:
                    check_xy(place["x_um"], place["y_um"])
                for param, z_mode in (("z_galvo_um", "galvo"), ("z_wide_um", "zwide")):
                    if param in place:
                        check_z(place[param], z_mode)
            except RuntimeError as exc:
                raise LimitViolation(str(exc)) from exc

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

# Every change -> its policy key. The completeness test
# (testing/unit/test_limits.py) checks that every Write the driver builds is here.
MUTATING_COMMANDS = {
    **{name: name for name in SETTER_LIMIT_KEYS},
    "set_objective": "set_objective",
    # stage motion, a tile scan's positions included
    "move_xy": "set_xyz",
    "move_z": "set_xyz",
    "tile_scan": "set_xyz",
    "after_tile_scan": "set_xyz",
    # a picture
    "capture": "capture",
}


def build_limits(stage_cfg, *, source, path, is_fallback):
    """Check a loaded limits file against the physical backstop, apply it, and compile it.

    The stage envelope must sit within the backstop. On success the
    envelope becomes the one the stage checks use, and the compiled
    :class:`LeicaLimits` is returned. Raises when the file is not
    acceptable, so the caller can fall back to the bundled defaults.
    """
    check_envelope_within_backstop(stage_cfg["stage_um"])
    limits = LeicaLimits(stage_cfg["policy"], source=source, path=path, is_fallback=is_fallback)
    apply_stage_limits_from_config(stage_cfg)
    return limits
