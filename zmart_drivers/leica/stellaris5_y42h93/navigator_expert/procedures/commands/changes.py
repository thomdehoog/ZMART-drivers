"""
The changes the driver can make to LAS X, as named functions.
==============================================================
Public ``set_*``, ``move_*``, ``acquire``, and ``select_job`` functions.

The settings of a job (zoom, scan speed, laser intensity, and the rest) are
all changed by the one runner, ``_change_setting``, from their entry in
``vendor_interface/primitive_handling/settings.py``: that table says which LAS X
request to fill in, in which order, how the result is checked, and which values
the limits gate is shown. Each ``set_*`` function here only gives the setting
its name, its arguments and its documentation.

The functions that are more than a table entry stay here in full, because they
are recipes: ``set_objective`` and ``select_job`` (which keep the sample point in
view after the lens changes), ``move_xy``, ``move_z`` and ``acquire``. Every one
follows three phases:

    **Phase A** - Pre-checks: input validation, limit checks, enum
        resolution, early-exit optimizations.
    **Phase B** - Backbone: calls ``confirm_and_fire`` with the command's
        profile.
    **Phase C** - Post-processing: attach extra data to the result dict
        (e.g. ``move_xy`` attaches the target position).

Numeric command tuning comes from the profiles in
``vendor_interface/primitive_handling/profiles.py``. Keyword arguments such as
``max_retries`` are explicit overrides for tests and unusual hardware runs;
``None`` means "use the profile".

Result contract: with the profiles' ``success_on_unconfirmed=True``
(deliberate - see ``CommandProfile``), ``result["success"]`` means the
command was *accepted*, not that it took effect. A caller that needs
proof the setting/move landed must check ``result["confirmed"]``.

Low-level limits gate: every mutating command declares its policy key in
``gate.MUTATING_COMMANDS`` and calls ``_limits_refusal`` in Phase A - closed
BEFORE the native call can fire (no limits handshake / invalid machine-local
limits / constraint violation all refuse with a result dict). See
``dispatcher/gate.py``.
"""

import logging
import time
from dataclasses import replace
from functools import partial

from ...dispatcher import gate as _gate
from ...dispatcher.writer import confirm_and_fire
from ...vendor_interface.primitive_handling.limit_rules import check_xy, check_z
from ...dispatcher.writer import _make_log_entry, _make_timing
from ...vendor_interface.parsing import _hw_get
from ...vendor_interface.primitive_handling import settings
from . import objective_shift as _objective_shift
from ...vendor_interface.primitive_handling.confirm_select_job import (
    prepare_select_job,
    select_job_confirm_legs,
)
from ...vendor_interface.primitive_handling.confirmations import (
    confirm_acquire,
    confirm_move_xy,
    confirm_move_z,
    confirm_objective,
    race_confirmations,
)
from .objectives import objective_by_slot
from ...vendor_interface.primitive_handling.profiles import (
    ACQUIRE,
    MOVE_XY,
    MOVE_Z,
    OBJECTIVE,
    SELECT_JOB,
)

log = logging.getLogger(__name__)


def _profile_value(profile, name, override=None):
    """Return an explicit override or the command profile value."""
    return override if override is not None else getattr(profile, name)


def _limits_refusal(client, command, values, **extra):
    """Fail-closed limits gate for one wrapper (Phase A, before fire).

    Returns None when the command may fire, else the wrapper's fail-closed
    result dict (``success=False``, the native call must NOT fire). ``extra``
    lets a wrapper add its contract-specific keys (e.g. ``position=None``).
    """
    message = _gate.check_refusal(client, command, values)
    if message is None:
        return None
    log.error(message)
    return {
        "success": False,
        "confirmed": None,
        "message": message,
        "timing": _make_timing(total_s=0.0, attempts=0),
        "logs": [_make_log_entry("error", message)],
        **extra,
    }


def _has_bound_keyword(fn, name):
    """Return True when a partial already owns a keyword value."""
    return isinstance(fn, partial) and name in (fn.keywords or {})


# =============================================================================
# Internal helper - uniform backbone call
# =============================================================================


def _dispatch(
    client,
    api_obj,
    description,
    profile,
    *,
    setup_fn,
    confirm_fn=None,
    log_confirm_fn=None,
    confirm_race_budget_s=None,
    max_retries=None,
    retry_backoff=None,
    retry_escalate=None,
    max_confirm_attempts=None,
    error_check_fn=None,
):
    """Call confirm_and_fire with a profile's settings.

    This is the single internal helper that all command wrappers call.
    It binds ``client`` into each profile callable via lambda, producing
    the zero-arg callables that the backbone expects. The binding
    pattern is identical for every command. Every confirmation routes
    through ``confirmations.race_confirmations``: with only the api leg
    (the normal case) that is an identity pass-through; commands that
    also have log evidence pass ``log_confirm_fn`` and the confirmation
    becomes a target-gated race with ``confirm_race_budget_s`` as its
    wall-clock bound (required, must fit inside one confirm attempt).

    Args:
        client: LAS X API client.
        api_obj: Resolved API object.
        description: Human-readable label for logging.
        profile: CommandProfile instance with all settings.
        setup_fn: Callable(model) that writes parameters to api_obj.Model.
        confirm_fn: Override for the profile's confirm_fn. Use when
            the confirm callable needs command-specific parameters
            (e.g. target value, tolerance) that are not in the profile.
            When None, uses profile.confirm_fn (which may also be None,
            meaning no confirmation).
        log_confirm_fn: Optional log-evidence confirm leg. When provided,
            the confirmation becomes a target-gated race between the api and
            log legs instead of an identity pass-through.
        confirm_race_budget_s: Wall-clock bound for that race (seconds).
            Required when ``log_confirm_fn`` is given; must fit inside one
            confirm attempt.
        max_retries: Override for the profile's max_retries. None uses
            the profile default.
        retry_backoff: Override for the profile's retry_backoff. None
            uses the profile default.
        retry_escalate: Override for the profile's retry_escalate. None
            uses the profile default.
        max_confirm_attempts: Override for the profile's max_confirm_attempts.
            None uses the profile default.
        error_check_fn: Override for the profile's API echo error check.
            Use only for command-specific LAS X echo semantics.

    Returns:
        Result dict from confirm_and_fire.
    """
    # Resolve confirm_fn: explicit override > profile default > None
    effective_confirm = confirm_fn if confirm_fn is not None else profile.confirm_fn

    # Inject the confirm poll window (confirm_poll_s) from the profile into simple
    # readback confirmations. Long-poll confirms (acquire, select_job) bind their
    # own `timeout` and are left untouched by the guard below.
    if (
        effective_confirm is not None
        and profile.confirm_poll_s is not None
        and not _has_bound_keyword(effective_confirm, "timeout")
        and not _has_bound_keyword(effective_confirm, "poll_window")
    ):
        _inner = effective_confirm
        _poll_window = profile.confirm_poll_s

        def effective_confirm(c, _f=_inner, _t=_poll_window):
            return _f(c, poll_window=_t)

    if profile.pre_check_fn is not None:

        def pre_check_fn():
            return profile.pre_check_fn(client)
    else:
        pre_check_fn = None

    effective_error_check = (
        error_check_fn if error_check_fn is not None else profile.error_check_fn
    )
    api_confirm_leg = (lambda: effective_confirm(client)) if effective_confirm else None
    final_confirm_fn = race_confirmations(
        api_leg=api_confirm_leg,
        log_leg=log_confirm_fn,
        label=description,
        budget_s=confirm_race_budget_s,
    )

    overrides = {}
    if max_retries is not None:
        overrides["max_retries"] = max_retries
    if max_confirm_attempts is not None:
        overrides["max_confirm_attempts"] = max_confirm_attempts
    if retry_backoff is not None:
        overrides["retry_backoff"] = retry_backoff
    if retry_escalate is not None:
        overrides["retry_escalate"] = retry_escalate
    effective_profile = replace(profile, **overrides) if overrides else profile

    return confirm_and_fire(
        client,
        api_obj,
        description,
        profile=effective_profile,
        setup_fn=setup_fn,
        pre_check_fn=pre_check_fn,
        error_check_fn=(lambda: effective_error_check(client))
        if effective_error_check
        else None,
        confirm_fn=final_confirm_fn,
    )


def _change_setting(setting, client, arguments, *, max_retries=None, tolerance=None):
    """Change one LAS X setting, following its entry in ``primitive_handling/settings.py``.

    The steps are the same for every setting: tidy the arguments, show the new
    value to the limits gate (nothing is sent if the gate refuses), check that
    the value can be sent at all, then send it through the dispatcher and read
    the state back. What differs from one setting to the next is in its entry.
    """
    if setting.normalize is not None:
        arguments = setting.normalize(arguments)

    refused = _limits_refusal(client, setting.command, setting.gate(arguments))
    if refused:
        return refused

    if setting.validate is not None:
        problem = setting.validate(arguments)
        if problem is not None:
            return {
                "success": False,
                "confirmed": None,
                "message": problem,
                "timing": _make_timing(total_s=0.0, attempts=0),
                "logs": [],
            }

    return _dispatch(
        client,
        getattr(client, setting.request),
        setting.describe.format(**arguments),
        setting.profile,
        setup_fn=lambda model: setting.write(model, arguments),
        confirm_fn=setting.confirm(arguments, tolerance),
        error_check_fn=setting.error_check(arguments) if setting.error_check else None,
        max_retries=max_retries,
    )


# =============================================================================
# Settings of a job - one named function each, all through the runner above
# =============================================================================


def set_zoom(client, job_name, value, *, max_retries=None, tolerance=None):
    """Set zoom level for the specified job.

    Args:
        client: LAS X API client.
        job_name: Target job name.
        value: Desired zoom level.
        max_retries: Transient error retry ceiling.
        tolerance: Readback confirmation tolerance.
    """
    return _change_setting(
        settings.ZOOM,
        client,
        {
            "job_name": job_name,
            "value": value,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_scan_speed(client, job_name, value, *, max_retries=None):
    """Set scan speed for the specified job."""
    return _change_setting(
        settings.SCAN_SPEED,
        client,
        {
            "job_name": job_name,
            "value": value,
        },
        max_retries=max_retries,
    )


def set_scan_resonant(client, job_name, enable, *, max_retries=None):
    """Enable or disable resonant scanning for the specified job."""
    return _change_setting(
        settings.SCAN_RESONANT,
        client,
        {
            "job_name": job_name,
            "enable": enable,
        },
        max_retries=max_retries,
    )


def set_scan_mode(client, job_name, mode, *, max_retries=None):
    """Set scan mode (e.g. 'xyz', 'xyzt') for the specified job."""
    return _change_setting(
        settings.SCAN_MODE,
        client,
        {
            "job_name": job_name,
            "mode": mode,
        },
        max_retries=max_retries,
    )


def set_sequential_mode(client, job_name, mode, *, max_retries=None):
    """Set sequential mode ('Line', 'Frame', or 'Stack') for the specified job."""
    return _change_setting(
        settings.SEQUENTIAL_MODE,
        client,
        {
            "job_name": job_name,
            "mode": mode,
        },
        max_retries=max_retries,
    )


def set_scan_field_rotation(
    client, job_name, angle, *, max_retries=None, tolerance=None
):
    """Set scan field rotation angle (degrees) for the specified job."""
    return _change_setting(
        settings.SCAN_FIELD_ROTATION,
        client,
        {
            "job_name": job_name,
            "angle": angle,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_image_format(client, job_name, format_str, *, max_retries=None):
    """Set image dimensions for the specified job.

    Args:
        format_str: Either a string like '512 x 512' or a tuple (w, h).
    """
    return _change_setting(
        settings.IMAGE_FORMAT,
        client,
        {
            "job_name": job_name,
            "format_str": format_str,
        },
        max_retries=max_retries,
    )


def set_z_stack_definition(
    client,
    job_name,
    begin_um=None,
    end_um=None,
    old_begin_um=None,
    old_end_um=None,
    *,
    max_retries=None,
    tolerance=None,
):
    """Set z-stack begin/end positions (micrometers).

    Args:
        begin_um, end_um: New positions; None leaves the field untouched
            (unless the matching old_* flag requests a reset).
        old_begin_um, old_end_um: Reset *flags*, not values — passing any
            non-None value asks LAS X to reset that field to its default
            (SetBegin/SetEnd = 0). The numeric value itself is never sent.

    Note: confirm_fn is provided but LAS X may recalculate z-stack
    geometry (size, end) after setting begin/end. Confirmation may
    report unconfirmed even though the command was accepted; reset
    outcomes are not confirmed at all (only non-None targets are).
    """
    return _change_setting(
        settings.Z_STACK_DEFINITION,
        client,
        {
            "job_name": job_name,
            "begin_um": begin_um,
            "end_um": end_um,
            "old_begin_um": old_begin_um,
            "old_end_um": old_end_um,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_z_stack_step_size(
    client, job_name, step_size_um, *, max_retries=None, tolerance=None
):
    """Set z-stack step size (micrometers)."""
    return _change_setting(
        settings.Z_STACK_STEP_SIZE,
        client,
        {
            "job_name": job_name,
            "step_size_um": step_size_um,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_z_stack_size(client, job_name, size_um, *, max_retries=None, tolerance=None):
    """Set z-stack total size (micrometers).

    Note: LAS X may recalculate z-stack geometry when size is changed.
    Confirmation may report unconfirmed if "System Optimized" is active.
    """
    return _change_setting(
        settings.Z_STACK_SIZE,
        client,
        {
            "job_name": job_name,
            "size_um": size_um,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_frame_accumulation(client, job_name, setting_index, value, *, max_retries=None):
    """Set frame accumulation count for a specific setting index."""
    return _change_setting(
        settings.FRAME_ACCUMULATION,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "value": value,
        },
        max_retries=max_retries,
    )


def set_frame_average(client, job_name, setting_index, value, *, max_retries=None):
    """Set frame average count for a specific setting index."""
    return _change_setting(
        settings.FRAME_AVERAGE,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "value": value,
        },
        max_retries=max_retries,
    )


def set_line_accumulation(client, job_name, setting_index, value, *, max_retries=None):
    """Set line accumulation count for a specific setting index."""
    return _change_setting(
        settings.LINE_ACCUMULATION,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "value": value,
        },
        max_retries=max_retries,
    )


def set_line_average(client, job_name, setting_index, value, *, max_retries=None):
    """Set line average count for a specific setting index."""
    return _change_setting(
        settings.LINE_AVERAGE,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "value": value,
        },
        max_retries=max_retries,
    )


def set_pinhole_airy(
    client,
    job_name,
    setting_index,
    value,
    *,
    max_retries=None,
    tolerance=None,
):
    """Set pinhole size in Airy units for a specific setting index."""
    return _change_setting(
        settings.PINHOLE_AIRY,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "value": value,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_detector_gain(
    client,
    job_name,
    setting_index,
    beam_route,
    value,
    *,
    max_retries=None,
    tolerance=None,
):
    """Set detector gain for a specific detector identified by beam route."""
    return _change_setting(
        settings.DETECTOR_GAIN,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "beam_route": beam_route,
            "value": value,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_laser_intensity(
    client,
    job_name,
    setting_index,
    beam_route,
    line_index,
    value,
    *,
    max_retries=None,
    tolerance=None,
):
    """Set laser intensity (0.0-1.0) for a specific laser line."""
    return _change_setting(
        settings.LASER_INTENSITY,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "beam_route": beam_route,
            "line_index": line_index,
            "value": value,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


def set_laser_shutter(
    client,
    job_name,
    setting_index,
    beam_route,
    activate,
    *,
    max_retries=None,
):
    """Open or close laser shutter for a specific beam route."""
    return _change_setting(
        settings.LASER_SHUTTER,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "beam_route": beam_route,
            "activate": activate,
        },
        max_retries=max_retries,
    )


def set_filter_wheel_slot(
    client,
    job_name,
    setting_index,
    beam_route,
    filter_wheel_type,
    slot_index,
    *,
    max_retries=None,
):
    """Set filter wheel to a specific slot."""
    return _change_setting(
        settings.FILTER_WHEEL_SLOT,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "beam_route": beam_route,
            "filter_wheel_type": filter_wheel_type,
            "slot_index": slot_index,
        },
        max_retries=max_retries,
    )


def set_filter_wheel_spectrum(
    client,
    job_name,
    setting_index,
    beam_route,
    filter_wheel_type,
    position,
    *,
    max_retries=None,
    tolerance=None,
):
    """Set filter wheel spectrum position (nm)."""
    return _change_setting(
        settings.FILTER_WHEEL_SPECTRUM,
        client,
        {
            "job_name": job_name,
            "setting_index": setting_index,
            "beam_route": beam_route,
            "filter_wheel_type": filter_wheel_type,
            "position": position,
        },
        max_retries=max_retries,
        tolerance=tolerance,
    )


# =============================================================================
# Changing the objective
# =============================================================================


def _resolve_objective(hw_info, slot_index=None, name=None, magnification=None):
    """Resolve an objective to (slot, display_name) from hardware info.

    Any one of *slot_index*, *name*, or *magnification* identifies the
    objective.  Returns ``(None, None)`` if not found.
    """
    # Empty turret slots (objectiveNumber 0) are excluded by objective_by_slot;
    # sending those to LAS X can trigger modal error dialogs that block the app.
    for obj in objective_by_slot(hw_info).values():
        s = _hw_get(obj, "slotIndex")
        n = _hw_get(obj, "name", "").strip()
        m = _hw_get(obj, "magnification")

        if slot_index is not None and s == slot_index:
            return s, n
        if name is not None and n == name.strip():
            return s, n
        if magnification is not None and m == magnification:
            return s, n

    return None, None


def set_objective(
    client,
    job_name,
    hw_info,
    slot_index=None,
    name=None,
    magnification=None,
    *,
    compensate=None,
    max_retries=None,
):
    """Set objective by slot index, name, or magnification.

    Resolves the input to a slot index, then fires the command.
    Exactly one of slot_index, name, or magnification must be provided.

    ``compensate`` controls whether the calibrated stage move that keeps
    the sample point runs after the swap: ``None`` (default) follows the
    session's connect-time calibration policy, ``True`` requires it, and
    ``False`` asks for a bare swap. See
    :func:`objective_shift.record_before_change`.
    """
    # The limits gate is consulted once, below, after the selector resolves
    # to a physical slot — that one check covers both the fail-closed
    # session state and the objective allow-list. (Malformed selectors get
    # a usage error first; they fire nothing, so nothing needs gating.)
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "exactly one of slot_index, name, or magnification must be "
                f"provided (got {len(selectors)})"
            ),
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }
    slot, target_name = _resolve_objective(
        hw_info, slot_index=slot_index, name=name, magnification=magnification
    )

    if slot is None:
        available = [
            (_hw_get(o, "slotIndex"), _hw_get(o, "name", "").strip())
            for o in objective_by_slot(hw_info).values()
        ]
        return {
            "success": False,
            "confirmed": None,
            "message": f"Could not find objective: slot_index={slot_index}, "
            f"name={name}, mag={magnification}. "
            f"Available: {available}",
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    # The flat limits file may fence automation to specific slots
    # (objective_slot; [] — the default — is unrestricted). The resolved
    # physical slot is checked regardless of whether the caller selected it
    # by slot, name, or magnification.
    refused = _limits_refusal(
        client,
        "set_objective",
        {"job_name": job_name, "objective_slot": slot},
    )
    if refused:
        return refused

    # Record the motoric XY and z-wide position BEFORE the objective change —
    # while they still mean "where the current objective is looking" — so the
    # calibrated translation can keep the sample point afterwards. Armed only
    # for clients the driver connected; a failed pre-read refuses the change
    # while nothing has happened yet.
    try:
        before = _objective_shift.record_before_change(
            client, job_name, compensate=compensate
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "refusing the objective change: could not record the "
                f"pre-change position for objective compensation: {exc}"
            ),
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    api_obj = client.PyApiSetObjectiveSlotByJobName

    def setup(m):
        m.JobName = job_name
        m.ObjectiveSlotIndex = slot

    result = _dispatch(
        client,
        api_obj,
        f"Objective -> {target_name} (slot {slot})",
        OBJECTIVE,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_objective,
            job_name=job_name,
            target_slot=slot,
            target_name=target_name,
        ),
        max_retries=max_retries,
    )
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(
                client, job_name, before, new_slot=slot
            ),
        )
    return result


# =============================================================================
# Stage movement
# =============================================================================


def move_xy(client, x, y, unit="um", *, max_retries=None, tolerance=None):
    """Move XY stage to absolute position.

    Args:
        client: LAS X API client.
        x, y: Target coordinates in the specified unit.
        unit: 'um' (micrometers), 'mm' (millimeters), or 'm' (meters).
        tolerance: Position confirmation tolerance in micrometers.

    Returns:
        Result dict; 'position' is the requested *target* (meters), not a
        readback — check 'confirmed' for evidence the stage arrived.
    """
    if unit not in ("um", "mm", "m"):
        return {
            "success": False,
            "confirmed": None,
            "message": f"unknown unit {unit!r} (expected 'um', 'mm', or 'm')",
            "position": None,
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }
    # Phase A: convert to um, gate on the session limits, then the
    # stage envelope + backstop — all before the native call can fire.
    try:
        if unit == "mm":
            x_um, y_um = x * 1000, y * 1000
        elif unit == "m":
            x_um, y_um = x * 1e6, y * 1e6
        else:
            x_um, y_um = x, y
        refused = _limits_refusal(
            client, "move_xy", {"x_um": x_um, "y_um": y_um}, position=None
        )
        if refused:
            return refused
        check_xy(x_um, y_um)
    except (RuntimeError, TypeError) as e:
        return {
            "success": False,
            "confirmed": None,
            "message": str(e),
            "position": None,
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    api_obj = client.PyApiMoveHardwareXY

    # Resolve .NET enums with integer fallback
    UNIT_ENUM_MAP = {"um": "eMicrons", "mm": "eMillimeter", "m": "eMeter"}
    UNIT_INT_MAP = {"um": 4, "mm": 3, "m": 1}
    try:
        unit_val = getattr(type(api_obj.Model.Units), UNIT_ENUM_MAP[unit])
    except Exception:
        unit_val = UNIT_INT_MAP[unit]
        log.warning("Units enum resolution failed, using int fallback: %d", unit_val)

    try:
        mode_val = type(api_obj.Model.MoveXyMode).eMoveXY
    except Exception:
        mode_val = 2  # eMoveXY=2 (NOT 0 which is eDontMove!)
        log.warning(
            "MoveXyMode enum resolution failed, using int fallback: %d", mode_val
        )

    def setup(m):
        m.RelativePosition = False
        m.XPosition = x
        m.YPosition = y
        m.MoveXyMode = mode_val
        m.Units = unit_val

    # Phase B: backbone
    r = _dispatch(
        client,
        api_obj,
        f"MoveXY -> ({x}, {y}) {unit}",
        MOVE_XY,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_move_xy,
            target_x_um=x_um,
            target_y_um=y_um,
            tolerance=_profile_value(MOVE_XY, "confirm_tolerance", tolerance),
        ),
        max_retries=max_retries,
    )

    # Target position (not a readback - check r["confirmed"] for verification status)
    r["position"] = {"x": x_um * 1e-6, "y": y_um * 1e-6, "x_um": x_um, "y_um": y_um}
    return r


def move_z(
    client,
    job_name,
    z,
    unit="um",
    z_mode="galvo",
    *,
    max_retries=None,
    tolerance=None,
):
    """Move Z drive to an absolute position (galvo or zwide).

    Args:
        client: LAS X API client.
        job_name: Target job.
        z: Target position (can be negative).
        unit: 'um', 'mm', or 'm'.
        z_mode: Drive type - 'galvo' or 'zwide'.
        tolerance: Position confirmation tolerance in micrometers.
    """
    ZMODE_MAP = {"galvo": "eUseGalvo", "zwide": "eUseZWide"}
    UNIT_MAP = {"um": "eMicrons", "mm": "eMillimeter", "m": "eMeter"}
    UNIT_INT_MAP = {"um": 4, "mm": 3, "m": 1}

    z_member = ZMODE_MAP.get(z_mode)
    if z_member is None:
        return {
            "success": False,
            "confirmed": None,
            "message": f"Unknown z_mode '{z_mode}'. Use: {list(ZMODE_MAP.keys())}",
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }
    if unit not in UNIT_MAP:
        return {
            "success": False,
            "confirmed": None,
            "message": f"unknown unit {unit!r}. Use: {list(UNIT_MAP.keys())}",
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    # Convert to um for limit check and confirmation
    try:
        if unit == "mm":
            z_um = z * 1000
        elif unit == "m":
            z_um = z * 1e6
        else:
            z_um = z

        # Function-keyed gate, then the stage envelope + backstop — all
        # before the native call can fire.
        refused = _limits_refusal(
            client, "move_z", {f"z_{'galvo' if z_mode == 'galvo' else 'wide'}_um": z_um}
        )
        if refused:
            return refused
        check_z(z_um, z_mode)
    except (RuntimeError, ValueError, TypeError) as e:
        return {
            "success": False,
            "confirmed": None,
            "message": str(e),
            "timing": _make_timing(total_s=0.0, attempts=0),
            "logs": [],
        }

    # Resolve .NET enums from the live model
    api_obj = client.PyApiMoveZByJobName
    try:
        z_use_val = getattr(type(api_obj.Model.ZUseMode), z_member)
    except Exception:
        z_use_val = z_member
        log.warning("ZUseMode enum resolution failed for '%s'", z_member)

    try:
        unit_val = getattr(type(api_obj.Model.Units), UNIT_MAP[unit])
    except Exception:
        unit_val = UNIT_INT_MAP[unit]
        log.warning("Units enum resolution failed, using int fallback: %d", unit_val)

    def setup(m):
        m.JobName = job_name
        m.RelativePosition = False
        m.ZPosition = z
        m.ZUseMode = z_use_val
        m.Units = unit_val

    command_started_at = time.time()
    return _dispatch(
        client,
        api_obj,
        f"Z -> {z} {unit} ({z_mode})",
        MOVE_Z,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_move_z,
            job_name=job_name,
            z_mode=z_mode,
            target_um=z_um,
            tolerance=_profile_value(MOVE_Z, "confirm_tolerance", tolerance),
            observed_after=command_started_at,
        ),
        max_retries=max_retries,
    )


# =============================================================================
# Acquisition
# =============================================================================


def acquire(
    client,
    job_name,
    poll_interval=None,
    poll_timeout=None,
    heartbeat_interval=None,
    start_timeout=None,
):
    """Trigger acquisition and block until scan completes.

    Routes through the backbone for consistent idle-wait and timing
    instrumentation. Acquisition commands are deliberately fired once.
    If LAS X never reports scanning before ``start_timeout``, the result
    is a failure; the driver does not send a second acquire command.

    Args:
        client: LAS X API client.
        job_name: Job to acquire.
        poll_interval: Seconds between scan status polls during completion.
        poll_timeout: Hard ceiling for scan completion (seconds). None
            for no timeout (wait indefinitely).
        heartbeat_interval: Log interval during long scans (seconds).
        start_timeout: Seconds to wait for scan to start before
            reporting failure.

    Returns:
        Result dict with timing in timing['total_s'].
    """
    refused = _limits_refusal(client, "acquire", {"job_name": job_name})
    if refused:
        return refused

    api_obj = client.PyApiAcquireJob

    def setup(m):
        m.JobName = job_name

    return _dispatch(
        client,
        api_obj,
        f"Acquire '{job_name}'",
        ACQUIRE,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_acquire,
            start_timeout=_profile_value(ACQUIRE, "start_timeout", start_timeout),
            heartbeat_interval=_profile_value(
                ACQUIRE, "heartbeat_interval", heartbeat_interval
            ),
            timeout=_profile_value(ACQUIRE, "poll_timeout", poll_timeout),
            poll_interval=_profile_value(ACQUIRE, "poll_interval", poll_interval),
        ),
    )


# =============================================================================
# Job selection
# =============================================================================


def select_job(client, job_name, *, compensate=None):
    """Select a job by name.

    Routes through the backbone. The SELECT_JOB profile pre-checks scanner
    idle before firing (``check_idle``), and confirms like every command:
    four windows of ``WINDOW_S``, fired again between. Source policy
    (api / log / hybrid) lives entirely
    in the confirmation layer: ``prepare_select_job`` owns the
    "already selected" decision and the api baseline, and
    ``select_job_confirm_legs`` builds the confirmation legs the dispatch
    race runs.

    Args:
        client: LAS X API client.
        job_name: Name of job to select.
        compensate: A job change can swap the objective with it; this
            controls the calibrated stage move that keeps the sample point
            afterwards. ``None`` (default) follows the session's
            connect-time calibration policy, ``True`` requires it,
            ``False`` asks for a bare change. See
            :func:`objective_shift.record_before_change`.
    """
    refused = _limits_refusal(client, "select_job", {"job_name": job_name})
    if refused:
        return refused

    t0 = time.perf_counter()
    noop, context = prepare_select_job(client, job_name)
    if noop is not None:
        noop["timing"] = _make_timing(total_s=time.perf_counter() - t0, attempts=0)
        return noop

    # A job change can carry an objective change with it. Record the motoric
    # XY and z-wide position NOW — before the switch, while they still mean
    # "where the current objective is looking" — so the compensation after
    # the switch can keep the sample point. Armed only for clients the driver
    # connected (per-connection config installed); a failed pre-read refuses
    # the change while nothing has happened yet.
    try:
        before = _objective_shift.record_before_change(
            client, context.get("api_baseline_name"), compensate=compensate
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed before the change fires
        return {
            "success": False,
            "confirmed": None,
            "message": (
                "refusing the job change: could not record the pre-change "
                f"position for objective compensation: {exc}"
            ),
            "timing": _make_timing(total_s=time.perf_counter() - t0, attempts=0),
            "logs": [],
        }

    api_obj = client.PyApiSelectJobByName

    def setup(m):
        m.JobName = job_name

    command_started_at = time.time()
    api_confirm, log_leg, budget_s = select_job_confirm_legs(
        job_name,
        command_started_at=command_started_at,
        api_baseline_name=context["api_baseline_name"],
        timeout=SELECT_JOB.confirm_poll_s,
    )
    result = _dispatch(
        client,
        api_obj,
        f"SelectJob '{job_name}'",
        SELECT_JOB,
        setup_fn=setup,
        confirm_fn=api_confirm,
        log_confirm_fn=log_leg,
        confirm_race_budget_s=budget_s,
    )
    if context["api_said_selected"]:
        result.setdefault("logs", []).append(
            _make_log_entry(
                "info",
                "API reported target already selected before SelectJob, "
                "but the log-participating confirmation still fired the "
                "command",
            )
        )
    if before is not None and result.get("success"):
        result = _objective_shift.merge_into_result(
            result,
            _objective_shift.compensate_after_change(client, job_name, before),
        )
    return result
