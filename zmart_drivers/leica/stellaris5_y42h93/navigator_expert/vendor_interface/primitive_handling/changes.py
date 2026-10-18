"""The changes the driver can make to LAS X, one command at a time, as named functions.

Public ``set_*``, ``move_xy``, ``move_z`` and ``acquire`` functions. Each one
is a single command to LAS X, with its limits check, its handling notes and its
confirmation; none of them is more than one command. (The two that change the
lens and then correct the stage, ``set_objective`` and ``select_job``, are
recipes and live in ``procedures/commands/compensated_changes.py``.)

The settings of a job (zoom, scan speed, laser intensity, and the rest) are
all changed by the one runner, ``_change_setting``, from their entry in
``settings.py`` beside this file: that table says which LAS X request to fill
in, in which order, how the result is checked, and which values the limits gate
is shown. Each ``set_*`` function only gives the setting its name, its
arguments and its documentation.

Every command follows three phases:

    **Phase A** - Pre-checks: input validation, limit checks, enum
        resolution, early-exit optimizations.
    **Phase B** - Backbone: calls the dispatcher's ``write`` with the command's
        profile.
    **Phase C** - Post-processing: attach extra data to the result dict
        (e.g. ``move_xy`` attaches the target position).

Numeric command tuning comes from the profiles in ``profiles.py``. Keyword
arguments such as ``max_retries`` are explicit overrides for tests and unusual
hardware runs; ``None`` means "use the profile".

Result contract: with the profiles' ``success_on_unconfirmed=True``
(deliberate - see ``CommandProfile``), ``result["success"]`` means the command
was *accepted*, not that it took effect. A caller that needs proof the
setting/move landed must check ``result["confirmed"]``.

Low-level limits gate: every mutating command declares its policy key in
``limit_rules.MUTATING_COMMANDS`` and calls ``limits_refusal`` in Phase A,
closed BEFORE the native call can fire (no limits handshake / invalid
machine-local limits / constraint violation all refuse with a result dict).
See ``dispatcher/gate.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import time
from dataclasses import replace
from functools import partial

from ...dispatcher import dispatcher as _dispatcher
from ...dispatcher import gate as _gate
from ...dispatcher.dispatcher import (
    make_log_entry as _make_log_entry,
    make_timing as _make_timing,
)
from . import settings
from .confirmations import (
    confirm_acquire,
    confirm_move_xy,
    confirm_move_z,
    race_confirmations,
)
from .limit_rules import check_xy, check_z
from .profiles import ACQUIRE, MOVE_XY, MOVE_Z

log = logging.getLogger(__name__)


def profile_value(profile, name, override=None):
    """Return an explicit override or the command profile value."""
    return override if override is not None else getattr(profile, name)


def limits_refusal(client, command, values, **extra):
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


def dispatch_change(
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
    """Call the dispatcher's write with a profile's settings.

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
        Result dict from the dispatcher's write.
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

    return _dispatcher.write(
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

    refused = limits_refusal(client, setting.command, setting.gate(arguments))
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

    return dispatch_change(
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
        refused = limits_refusal(
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
    r = dispatch_change(
        client,
        api_obj,
        f"MoveXY -> ({x}, {y}) {unit}",
        MOVE_XY,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_move_xy,
            target_x_um=x_um,
            target_y_um=y_um,
            tolerance=profile_value(MOVE_XY, "confirm_tolerance", tolerance),
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
        refused = limits_refusal(
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
    return dispatch_change(
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
            tolerance=profile_value(MOVE_Z, "confirm_tolerance", tolerance),
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
    refused = limits_refusal(client, "acquire", {"job_name": job_name})
    if refused:
        return refused

    api_obj = client.PyApiAcquireJob

    def setup(m):
        m.JobName = job_name

    return dispatch_change(
        client,
        api_obj,
        f"Acquire '{job_name}'",
        ACQUIRE,
        setup_fn=setup,
        confirm_fn=partial(
            confirm_acquire,
            start_timeout=profile_value(ACQUIRE, "start_timeout", start_timeout),
            heartbeat_interval=profile_value(
                ACQUIRE, "heartbeat_interval", heartbeat_interval
            ),
            timeout=profile_value(ACQUIRE, "poll_timeout", poll_timeout),
            poll_interval=profile_value(ACQUIRE, "poll_interval", poll_interval),
        ),
    )
