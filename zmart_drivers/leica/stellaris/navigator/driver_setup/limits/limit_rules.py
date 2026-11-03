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
    "set_frame_average",
    "set_line_accumulation",
    "set_frame_accumulation",
    "set_z_stack_step",
    "set_scan_mode",
    "set_autofocus",
    "set_laser_power",
    "set_laser_shutter",
    "set_format",
    "set_resonant_scanner",
    "set_filter_wheel",
    "set_detector_gain",
    "set_bidirectional",
    "set_phase_x",
    "set_pinhole_um",
    "set_pinhole_unit",
    "set_emission_wavelength",
    "set_bit_depth",
    "set_system_optimized_step",
    "set_collar",
    "set_smartcorr_range",
    "set_collar_reference",
    "set_sequential_mode",
    "set_laser_wavelength",
    "set_autofocus_method",
    "set_autofocus_precision",
    "set_autofocus_steps",
    "set_autofocus_range_um",
    "set_laser_output_power",
    "set_autofocus_drive",
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
        if function == "z_stack":
            self._check_z_stack(values)
            return
        if function == "setting_file":
            for percent in values.get("laser_power", []):
                self._check_one("set_laser_power", self._policy["set_laser_power"], percent)
            for gain in values.get("detector_gain", []):
                self._check_one("set_detector_gain", self._policy["set_detector_gain"], gain)
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
        for place in places:
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
        # Then how far each move goes: a second guard, against an unexpected jump.
        self._check_steps(values, places)

    def _check_z_stack(self, values: Mapping[str, Any]) -> None:
        """A z-stack's two ends, each like a z move from where the drive stands: the file's range, the backstop, the step."""
        axis = values.get("axis")
        if axis not in ("z_galvo_um", "z_wide_um"):
            raise LimitViolation(f"a z-stack names its drive's axis, z_galvo_um or z_wide_um, not {axis!r}")
        z_mode = "galvo" if axis == "z_galvo_um" else "zwide"
        for end in ("begin_um", "end_um"):
            z = values.get(end)
            self._check_one(axis, self._policy[axis], z)
            try:
                check_z(z, z_mode)
            except RuntimeError as exc:
                raise LimitViolation(f"the z-stack's {end[:-3]}: {exc}") from exc
            # Each end on its own from where z stands: the stack's length is a
            # gradual sweep, not a jump.
            self._check_steps(values, [{axis: z}])

    def _check_steps(self, values: Mapping[str, Any], places: list) -> None:
        """Each step against its largest: from where the drive stands (``from_<axis>``) to the first place, then place to place."""
        steps = {param: self._policy.get(f"step_{param}") for param in ("x_um", "y_um", "z_galvo_um", "z_wide_um")}
        here = {param: values[f"from_{param}"] for param in steps if f"from_{param}" in values}
        for place in places:
            for param, spec in steps.items():
                if spec is None or param not in place:
                    continue
                if param not in here:
                    raise LimitViolation(
                        f"step_{param}: where the drive stands (from_{param}) is not known, so the "
                        f"step cannot be checked ({self._origin()})"
                    )
                self._check_one(f"step_{param}", spec, abs(float(place[param]) - float(here[param])))
            here.update({param: place[param] for param in steps if param in place})

    def status(self) -> LimitsStatus:
        return LimitsStatus(
            source=self.source,
            path=str(self.path) if self.path is not None else None,
            is_fallback=self.is_fallback,
        )

    def describe(self) -> dict:
        return self.status().describe()


# The largest step of each axis, in micrometres: how far one move may go from
# where the drive stands, beside where it ends. Each key appears in limits.json
# as ``[]`` (no step limit) or ``{"range": [0, largest]}``. It guards against
# an unexpected jump only; it does not keep the objective off the sample, which
# is the z range's work (simulator, 2026-10-30: z-wide went from -7200 to
# +1000 um in one step because only the target was checked).
STEP_LIMIT_KEYS = ("step_x_um", "step_y_um", "step_z_galvo_um", "step_z_wide_um")


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
    # a scan through the collar's positions, within the range just set
    "smartcorr": "capture",
    # Live: a scan like a picture's, on until it is stopped
    "start_live": "capture",
    "stop_live": "capture",
    # Fast Live's Scan Settings: how it scans, not the light
    "set_fast_live": "set_fast_live",
    # the time series: how many frames and how often, not the light
    "set_time_series": "set_time_series",
    "set_line_scan": "set_line_scan",
    # ROI scan and the bleach mode: switches; the light in a region is set as any laser power
    "set_roi_scan": "set_roi_scan",
    # linear Z-compensation: its modes and points hold powers and gains already set; Move To is a z move
    "set_z_compensation": "set_z_compensation",
    # when the autofocus runs in a time series: its own search is checked when it runs
    "set_autofocus_in_time_series": "set_autofocus_in_time_series",
    # Auto Sync and its scan options: how the autofocus scans, its light set as any power
    "set_auto_sync": "set_auto_sync",
    # the λ scan's detection band: which light is collected, not the light
    "set_lambda_scan": "set_lambda_scan",
    "set_excitation_scan": "set_excitation_scan",
    "set_auto_sync_options": "set_auto_sync_options",
    "add_z_compensation_point": "add_z_compensation_point",
    "remove_z_compensation_point": "remove_z_compensation_point",
    "move_to_z_compensation_point": "set_xyz",
    "set_bleach_mode": "set_bleach_mode",
    # the z-stack: its ends are z positions the scan moves through
    "set_z_stack_drive": "set_z_stack_drive",
    "set_z_stack_range": "z_stack",
    "z_stack_picture": "z_stack",
    "set_z_stack_size": "z_stack",
    "set_z_stack_here": "z_stack",
    "recenter_z_stack": "z_stack",
    "clear_z_stack": "z_stack",
    # the autofocus's search: its two ends, as a z-stack's
    "autofocus_run": "z_stack",
    # the coverslip search: its two ends, as an autofocus's; the offset is a number the autofocus uses
    "find_coverslip": "z_stack",
    "set_coverslip_offset": "set_coverslip_offset",
    # the Fluorifier disc: which light reaches the detectors, not the light
    "set_notch_auto_select": "set_notch_auto_select",
    "set_mfp_auto_select": "set_mfp_auto_select",
    # how a tunable laser holds its power along the wavelengths; the power itself is limited as any
    "set_excitation_control": "set_excitation_control",
    "set_polarizer": "set_polarizer",
    # a whole setting as a file: saving changes nothing; a file's values are checked before it is loaded
    "save_setting": "save_setting",
    "load_setting": "setting_file",
    # how a channel is shown: not the light, not the detector
    "set_channel_colour": "set_channel_colour",
    # the setting switched on or off for scanning, and its name
    "set_setting_enabled": "set_setting_enabled",
    "set_setting_name": "set_setting_name",
    # how a tau channel is shown: not the light, not the detector
    "set_tau_channel_colour": "set_tau_channel_colour",
    # which photons a tau channel takes: not the light, not the detector's gain
    "set_tau_channel_lifetime": "set_tau_channel_lifetime",
    # TauGating's channels and gates: which photons a channel takes, not the light
    "set_tau_gate": "set_tau_gate",
    "set_tau_scan": "set_tau_scan",
    "set_detector_tau_contrast": "set_detector_tau_contrast",
    "set_detector_dye": "set_detector_dye",
    "set_excitation_line": "set_excitation_line",
    "show_spectra": "show_spectra",
    "set_tau_donor_lifetime": "set_tau_donor_lifetime",
    "add_tau_channel": "add_tau_channel",
    "remove_tau_channel": "remove_tau_channel",
    "add_tau_gate": "add_tau_gate",
    "remove_tau_gate": "remove_tau_gate",
    "add_tau_gate_channel": "add_tau_gate_channel",
    "remove_tau_gate_channel": "remove_tau_gate_channel",
    "find_tau_lifetimes": "find_tau_lifetimes",
    # the AOBS: which share of a line goes on, not the light's power
    "set_aobs_reflection": "set_aobs_reflection",
    "set_aobs_enhanced_dynamics": "set_aobs_enhanced_dynamics",
    "add_setting": "add_setting",
    "delete_setting": "delete_setting",
    "activate_setting": "activate_setting",
    # a detector and its line kept from LAS X's Dye Assistant
    "lock_detector": "lock_detector",
    "lock_all_settings": "lock_all_settings",
    # a white-light line switched on, at LCS's starting power; its power is set and checked after
    "set_laser_lines": "set_laser_lines",
    # a detector on or off, its band and its tau mode, in the current setting (the gain has its own key)
    "set_detector_active": "set_detector_active",
    "set_detector_band": "set_detector_band",
    "set_detector_tau": "set_detector_tau",
    "set_detector_operating_mode": "set_detector_operating_mode",
    # the Best Focus Setting: its scan, on or off, and its optics (its power checked as a laser power)
    "set_autofocus_format": "set_autofocus_format",
    "set_autofocus_speed": "set_autofocus_speed",
    "set_autofocus_bidirectional": "set_autofocus_bidirectional",
    "set_autofocus_zoom": "set_autofocus_zoom",
    "set_autofocus_optics": "setting_file",
    # a setting's dyes, as the window's dye drop: its laser powers checked as any
    "set_dyes": "setting_file",
    "set_autofocus_laser_power": "setting_file",
    # a laser line taken away: less light, never more
    "remove_laser_line": "remove_laser_line",
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
