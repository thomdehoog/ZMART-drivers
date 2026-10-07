"""
ZMART controller adapter.
=========================
The seam that plugs this driver into the vendor-agnostic **ZMART controller**
(``zmart_controller``). The controller drives every microscope through one small
set of functions -- ``connect`` plus one function per command. This module
implements them for mesoSPIM; the plug-in folder ``zmart_controller/`` hands them
to the controller, and its ``zmart.json`` names the instrument.

Like the reference ``mock_driver``, the driver owns the frame **origin**: the
controller works in micrometers from an origin the driver subtracts, so the
controller never does coordinate math. It also owns the changeable/observed
state boundary and the capture+save step.

The controller surface is deliberately x/y/z centric. mesoSPIM's extra axes
(focus, rotation) and light-path settings are exposed too: focus/rotation as
**procedures**, and laser/filter/zoom/intensity/shutter/ETL as the **changeable
state**. The full driver API (``import mesospim``) remains available for anything
the neutral surface does not cover.

Plug the driver in once on the microscope computer, by its folder or module
name; importing this module registers nothing::

    import zmart_controller

    zmart_controller.register_driver("zmart_drivers.mesospim")

To give the instrument another name, host or port, edit the plug-in folder's
``zmart.json``.

Author: Thom de Hoog (ZMB, University of Zurich)
        thom.dehoog@zmb.uzh.ch . thomdehoog@gmail.com
License: MIT
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import acquisition as _acq
from . import commands as _cmd
from .calibration import machine as _machine
from .config.profiles import ACQUISITION, HARDWARE
from .connection.session import close as _close
from .connection.session import connect as _connect
from .limits import checks as _limits
from .readers import readers as _readers

log = logging.getLogger(__name__)

# The ops that change something about the microscope. Each MUST have an entry
# in function_limits.json (null = reviewed-and-unlimited); the loader rejects
# a file that misses one, so a new mutating op cannot ship silently unlimited.
_MUTATING_OPS = ("set_origin", "set_xyz", "set_state", "run_procedure", "acquire")

# Per-axis actuator options this instrument exposes to the controller. mesoSPIM
# linear axes are single-motoric; focus/rotation are separate axes reached via
# procedures, not actuators of x/y/z.
_ACTUATORS: dict[str, list[str]] = {
    "x": ["motoric"],
    "y": ["motoric"],
    "z": ["motoric"],
}

# State keys the driver treats as mutable (capturable + reapplyable).
_MUTABLE_KEYS = (
    "laser",
    "intensity",
    "filter",
    "zoom",
    "shutterconfig",
    "etl_l_amplitude",
    "etl_l_offset",
    "etl_r_amplitude",
    "etl_r_offset",
)


@dataclass
class MesospimHandle:
    """Live session handle the controller passes back into every op."""

    client: Any
    connection: dict
    output_root: Path
    origin: dict = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    initial_positions: list = field(default_factory=list)
    immutable: dict = field(default_factory=dict)
    # Machine profile: where this instrument's machine-local config lives
    # (stage/function limits, persisted origin). Set at connect.
    machine: Any = None
    # Function-keyed limits for this session (mesospim.limits.FunctionLimits),
    # loaded at connect; None when the file could not be loaded — every
    # mutating op then refuses (fail-closed), read-only use still works.
    function_limits: Any | None = None
    # The stage envelope loaded at connect, ``{axis: [min, max]}`` in raw stage
    # coordinates (um, theta in degrees): what get_xyz reports as each axis's range.
    stage_limits: dict = field(default_factory=dict)
    # Monotonic counter to give each acquisition a unique image-writer staging
    # dir (so repeated/same-label captures never collide).
    _acq_seq: int = 0


# =============================================================================
# lifecycle
# =============================================================================


def connect(connection: dict) -> MesospimHandle:
    """Open a mesoSPIM session and capture the initial positions.

    Honours ``connection`` keys ``host`` / ``port`` / ``timeout`` (forwarded to
    the driver ``connect``), ``output_root`` (where ``acquire`` saves),
    ``machine_root`` (override for the ProgramData root -- see
    ``calibration.machine``), and ``stage_limits`` (an explicit path to a
    stage-limits config; default resolves the machine copy, else the bundled
    envelope). If no ``output_root`` is given, a per-session temp directory is
    created.

    Stage safety limits are loaded here so the controller path is never left
    fail-open: ``check_axis`` rejects an unconfigured axis, so a move can only
    run once limits exist. The function-keyed limits (``function_limits.json``,
    machine copy else bundled) load here too, with the stage envelope overlaid
    onto their ``stage.*`` constraints. The frame origin that was saved earlier
    with the driver's :func:`set_origin` setup step is loaded as well, so the
    zero point survives reconnects.
    """
    client = _connect(connection)
    output_root = Path(connection.get("output_root") or tempfile.mkdtemp(prefix="mesospim_run_"))
    output_root.mkdir(parents=True, exist_ok=True)
    machine = _machine.MachineProfile(
        microscope_id=connection.get("microscope") or CONNECTION["microscope"],
        programdata_root=connection.get("machine_root"),
    )

    # Configure hard stage limits before any move is possible. Without this the
    # fail-closed ``check_axis`` would reject every move; with it the machine's
    # (or caller-supplied) envelope is active for the whole session. Cleared
    # first so a reconnect fully replaces the limits rather than merging into a
    # prior session's. NOTE: limits are process-global, so this driver assumes a
    # single instrument per process; connecting a second instrument in the same
    # process would share (and overwrite) these limits.
    explicit = connection.get("stage_limits")
    if explicit is not None:
        stage_path = Path(explicit)
    else:
        stage_path, stage_fallback = machine.resolve(_machine.STAGE_LIMITS_FILENAME)
        if stage_fallback:
            log.info(
                "no machine stage envelope under %s; using bundled default", machine.machine_dir()
            )
    stage_cfg = _limits.load_stage_config(stage_path)
    _limits.clear_stage_limits()
    _limits.apply_stage_limits_from_config(stage_cfg)

    positions = _readers.get_positions(client)
    info = dict(client.server_info)
    handle = MesospimHandle(
        client=client,
        connection=dict(connection),
        output_root=output_root,
        machine=machine,
        immutable={
            "app": info.get("app", "mesoSPIM-control"),
            "microscope": connection.get("microscope"),
            "version": info.get("version"),
            "host": client.host,
            "port": client.port,
        },
        initial_positions=[{k: positions.get(k) for k in ("x", "y", "z", "f", "theta")}],
        stage_limits={axis: list(bounds) for axis, bounds in stage_cfg["axes"].items()},
    )
    handle.function_limits = _load_function_limits(machine, stage_cfg)
    _restore_persisted_origin(handle)
    log.info("mesoSPIM controller session ready (output_root=%s)", output_root)
    return handle


def _load_function_limits(machine: Any, stage_cfg: dict | None) -> Any | None:
    """Load the session's function-keyed limits (:mod:`mesospim.limits.function_limits`), fail-closed.

    Resolves ``function_limits.json`` through the machine profile -- the machine
    copy, else the driver-bundled default -- and overlays the machine's physical
    stage envelope onto the file's ``stage.*`` constraints, so the numbers that
    govern moves are the machine's, never a stale bundled copy. The loader also
    enforces completeness: every op in :data:`_MUTATING_OPS` must have an entry.

    Returns None when the file cannot be loaded or fails validation; every
    mutating op then refuses via :func:`_check_limits` while read-only
    controller use still works.
    """
    try:
        from .limits import function_limits as _shared_limits

        path, is_fallback = machine.resolve(_machine.FUNCTION_LIMITS_FILENAME)
        overrides = None
        if stage_cfg is not None:
            overrides = {
                f"stage.{axis}": {"min": bounds[0], "max": bounds[1]}
                for axis, bounds in stage_cfg["axes"].items()
            }
        return _shared_limits.load(
            path,
            functions=_MUTATING_OPS,
            constraint_overrides=overrides,
            is_fallback=is_fallback,
        )
    except Exception as exc:  # noqa: BLE001 -- config IO / schema; degrade, don't crash connect
        log.warning(
            "function limits unavailable (%s); every mutating op will refuse until %s loads",
            exc,
            _machine.FUNCTION_LIMITS_FILENAME,
        )
        return None


def _check_limits(handle: MesospimHandle, function: str, values: dict) -> None:
    """Gate one mutating op on the session's function-keyed limits.

    Fail-closed: with no limits loaded the op refuses outright. An
    out-of-bounds value raises ``mesospim.limits.LimitViolation`` (a
    RuntimeError) naming the value, the constraint, and the governing file.
    """
    if handle.function_limits is None:
        raise RuntimeError(
            f"{function} refused: function limits are not configured — connect() "
            f"could not load {_machine.FUNCTION_LIMITS_FILENAME} (see the connect warning)"
        )
    handle.function_limits.check(function, values)


def _restore_persisted_origin(handle: MesospimHandle) -> None:
    """Restore the frame origin a previous session persisted (if any)."""
    try:
        payload = handle.machine.read_origin()
    except Exception as exc:  # noqa: BLE001 -- corrupt file must not block connect
        log.warning("could not read persisted origin (%s); frame is raw stage coordinates", exc)
        return
    if not payload:
        return
    origin = payload.get("origin") or {}
    try:
        handle.origin = {axis: float(origin[axis]) for axis in ("x", "y", "z")}
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("persisted origin is malformed (%s); frame is raw stage coordinates", exc)
        return
    log.info(
        "restored frame origin from %s", handle.machine.machine_dir() / _machine.ORIGIN_FILENAME
    )


def disconnect(handle: MesospimHandle) -> None:
    """Close the underlying client session."""
    _close(handle.client)


# =============================================================================
# frame origin
# =============================================================================


def set_origin(handle: MesospimHandle) -> dict:
    """Mark the current position as the origin, so that it reads (0, 0, 0) from now on.

    This is a one-time setup step that you run with the driver directly, not
    through the ``zmart_controller`` Session. The controller does not offer
    ``set_origin``, because the origin belongs to the microscope's configuration
    rather than to any single experiment. Move the stage to the point you want
    as zero, then run::

        handle = mesospim_zmart_adapter.connect(connection)
        mesospim_zmart_adapter.set_origin(handle)
        mesospim_zmart_adapter.disconnect(handle)

    The origin is saved to ``origin.json`` in this microscope's configuration
    folder, and :func:`connect` loads it every time it opens a session. It stays
    in use until you set it again. Like every command that changes the
    microscope, this one is refused if ``connect`` could not load the safety
    limits file (``function_limits.json``); the warning printed at connect
    tells you why.

    The return value holds the new origin (in raw stage micrometres) and the
    path of the file it was saved to. If the file cannot be written, a
    ``RuntimeError`` is raised. The new origin is already in use for this
    session at that point, but it would be lost at the next connect, so it is
    better to fix the cause and run ``set_origin`` again than to carry on.
    """
    _check_limits(handle, "set_origin", {})
    pos = _readers.get_positions(handle.client)
    handle.origin = {axis: float(pos.get(axis) or 0.0) for axis in ("x", "y", "z")}
    try:
        path = handle.machine.write_origin(
            {
                "origin": dict(handle.origin),
                "microscope": handle.immutable.get("microscope"),
                "captured_at": time.time(),
            }
        )
    except OSError as exc:
        raise RuntimeError(f"could not persist origin reference: {exc}") from exc
    return {"origin": dict(handle.origin), "origin_file": str(path)}


def _user_xyz(handle: MesospimHandle, pos: dict) -> dict[str, float]:
    return {axis: float(pos.get(axis) or 0.0) - handle.origin[axis] for axis in ("x", "y", "z")}


# =============================================================================
# movement
# =============================================================================


def get_actuators(handle: MesospimHandle) -> dict:
    """The actuator options each axis offers (driver-defined)."""
    return {axis: list(opts) for axis, opts in _ACTUATORS.items()}


def _validate_actuators(with_actuators: dict | None) -> None:
    if not with_actuators:
        return
    for axis, actuator in with_actuators.items():
        if axis not in _ACTUATORS:
            raise ValueError(f"unknown axis {axis!r}")
        # A single value or a one-element list both name the actuator.
        name = actuator[0] if isinstance(actuator, (list, tuple)) else actuator
        if name not in _ACTUATORS[axis]:
            raise ValueError(f"unknown actuator {actuator!r} for axis {axis!r}")


def get_xyz(handle: MesospimHandle, *, with_actuators: dict | None = None) -> dict:
    """Report the linear position per axis (um, relative to origin).

    ``range`` is how far each axis may travel, ``[min, max]`` in um from the
    origin: the stage envelope loaded at connect, the one every move is
    checked against. It is None for an axis the envelope does not cover.
    """
    _validate_actuators(with_actuators)
    pos = _readers.get_positions(handle.client)
    user = _user_xyz(handle, pos)
    return {
        axis: {
            "value": user[axis],
            "actuator": _ACTUATORS[axis][0],
            "unit": "um",
            "range": _range(handle, axis),
        }
        for axis in ("x", "y", "z")
    }


def _range(handle: MesospimHandle, axis: str) -> list[float] | None:
    """The stage envelope of one axis, shifted into the frame."""
    bounds = handle.stage_limits.get(axis)
    if bounds is None:
        return None
    return [float(bounds[0]) - handle.origin[axis], float(bounds[1]) - handle.origin[axis]]


def set_xyz(
    handle: MesospimHandle,
    x: float,
    y: float,
    z: float,
    *,
    with_actuators: dict | None = None,
) -> dict:
    """Move to an absolute target (um, relative to origin); return a move record.

    The driver maps user coordinates to raw stage coordinates via the origin and
    issues one absolute move, then reports the confirmed position.
    """
    _validate_actuators(with_actuators)
    targets = {
        "x": handle.origin["x"] + float(x),
        "y": handle.origin["y"] + float(y),
        "z": handle.origin["z"] + float(z),
    }
    # Two layers on purpose: the function limits carry provenance (which file,
    # which constraint) and gate the ABSOLUTE targets; the driver's own
    # check_move in move_absolute stays as the safety net underneath.
    _check_limits(handle, "set_xyz", targets)
    result = _cmd.move_absolute(handle.client, targets)
    if not result.get("success"):
        raise RuntimeError(f"set_xyz failed: {result.get('message')}")
    return {
        "position": {"x": float(x), "y": float(y), "z": float(z)},
        "confirmed": result.get("confirmed"),
        "actuators": {axis: _ACTUATORS[axis][0] for axis in ("x", "y", "z")},
    }


# =============================================================================
# state
# =============================================================================


def get_state(handle: MesospimHandle) -> dict:
    """Capture instrument state: changeable settings first, then the observed report.

    ``observed`` carries the identity fingerprint plus the provenance of the
    function limits governing this session (evidence, not an instruction) --
    None when limits failed to load (every mutating op is then refusing).
    """
    state = _readers.get_state(handle.client)
    changeable = {key: state.get(key) for key in _MUTABLE_KEYS if state.get(key) is not None}
    observed = dict(handle.immutable)
    observed["limits"] = (
        None if handle.function_limits is None else handle.function_limits.describe()
    )
    return {"changeable": changeable, "observed": observed}


def set_state(handle: MesospimHandle, state: dict) -> dict:
    """Apply the changeable settings; report what stuck.

    ``observed`` is a report, never an instruction — it is not read here
    (operator decision: the identity gate returns only if the changeable
    part ever grows beyond low-risk settings).
    """
    changeable = {k: v for k, v in (state.get("changeable") or {}).items() if k in _MUTABLE_KEYS}
    _check_limits(handle, "set_state", changeable)
    if not changeable:
        return {"applied": {}}
    result = _cmd.set_state(handle.client, changeable)
    if not result.get("success"):
        raise RuntimeError(f"set_state failed: {result.get('message')}")
    return {"applied": changeable, "confirmed": result.get("confirmed")}


# =============================================================================
# procedures
# =============================================================================


def get_procedures(handle: MesospimHandle) -> dict:
    """The named procedures the driver offers."""
    procs = {name: {"description": desc} for name, desc in ACQUISITION.procedures}
    procs["move_focus"] = {"description": "move the focus (detection) axis (um)", "args": ["value"]}
    procs["move_rotation"] = {"description": "rotate the sample (degrees)", "args": ["value"]}
    return procs


def run_procedure(handle: MesospimHandle, procedure: dict) -> dict:
    """Run a procedure. ``procedure`` is ``{"name": ..., ...args}``.

    The focus / rotation moves are gated by the function-keyed limits under
    the ``f`` / ``theta`` constraints — these axes live outside the xyz frame,
    so this is where their envelope is enforced at the controller layer.
    """
    name = procedure.get("name")
    if name == "move_focus":
        _check_limits(handle, "run_procedure", {"f": float(procedure["value"])})
        result = _cmd.move_focus(handle.client, float(procedure["value"]))
    elif name == "move_rotation":
        _check_limits(handle, "run_procedure", {"theta": float(procedure["value"])})
        result = _cmd.move_rotation(handle.client, float(procedure["value"]))
    elif name == "zero_stage":
        _check_limits(handle, "run_procedure", {})
        result = _cmd.zero_axes(handle.client, ["x", "y", "z"])
    elif name in ("autofocus", "find_sample"):
        _check_limits(handle, "run_procedure", {})
        # Server-side named procedures: forwarded verbatim to the command server.
        reply = handle.client.request("procedure", name=name, args=procedure.get("args", {}))
        return {"ran": name, "data": dict(reply.data)}
    else:
        raise ValueError(f"unknown procedure {name!r}")
    if not result.get("success"):
        raise RuntimeError(f"procedure {name!r} failed: {result.get('message')}")
    return {"ran": name, "confirmed": result.get("confirmed")}


# =============================================================================
# acquire (captures and saves)
# =============================================================================


def get_acquisition_settings(handle: MesospimHandle) -> dict:
    """The acquisition and saving settings this instrument offers, with the active value of each."""
    zooms = [name for name, _px in HARDWARE.zoom_pixel_size_um]
    return {
        "folder": {"options": "any text; empty saves straight into data", "active": ""},
        "format": {"options": list(ACQUISITION.formats), "active": ACQUISITION.save_format},
        "backlash_correction": {"options": [True, False], "active": True},
        "shutterconfig": {
            "options": list(HARDWARE.shutter_configs),
            "active": ACQUISITION.default_shutterconfig,
        },
        "zoom": {"options": zooms, "active": ACQUISITION.default_zoom},
        "planes": {"options": "int >= 1", "active": 1},
        "z_step": {"options": "float um", "active": 1.0},
    }


_ACQUIRE_STATE_KEYS = ("laser", "intensity", "filter", "zoom", "shutterconfig")
_ACQUIRE_CAPTURE_KEYS = ("planes", "z_step", "z_start", "z_end")


def acquire(
    handle: MesospimHandle,
    *,
    position_label: str,
    acquisition_settings: dict | None = None,
) -> dict:
    """Capture one dataset and save it, returning the record.

    Applies any light-path settings as state first, optionally settles the stage
    (backlash correction), captures via the mesoSPIM image writer, then relocates
    the frames into ``<output_root>/data/``, named after ``position_label``.
    The ``folder`` setting puts them in a folder of that name inside ``data``.
    """
    options = dict(acquisition_settings or {})
    fmt = options.get("format", ACQUISITION.save_format)
    folder = options.get("folder") or ""
    if not isinstance(folder, str):
        # A ValueError, like every other unusable acquisition setting.
        raise ValueError(  # noqa: TRY004
            f"acquisition setting 'folder' must be text, not {folder!r}"
        )
    # Fail-closed gate BEFORE anything is applied; the absolute z bounds are
    # checked again below, once they are mapped through the frame origin.
    _check_limits(handle, "acquire", {})

    # 1) apply light-path settings that were passed as acquisition settings.
    state_updates = {k: options[k] for k in _ACQUIRE_STATE_KEYS if k in options}
    if state_updates:
        res = _cmd.set_state(handle.client, state_updates)
        if not res.get("success"):
            raise RuntimeError(f"acquire: applying settings failed: {res.get('message')}")

    # 2) optional backlash settle on the linear stage before capture.
    if options.get("backlash_correction", True):
        _settle(handle)

    # 3) capture (snap or stack, per the capture settings).
    capture_options = {k: options[k] for k in _ACQUIRE_CAPTURE_KEYS if k in options}
    # Stack Z bounds arrive in the controller's user frame (like set_xyz); map
    # them to raw stage coordinates via the origin so a non-zero origin does not
    # shift the stack. z_step is a delta, so it is left untouched.
    for zc in ("z_start", "z_end"):
        if zc in capture_options:
            capture_options[zc] = handle.origin["z"] + float(capture_options[zc])
    # Limit-check the swept Z range before firing: the capture path sweeps the
    # stage but does not go through move_absolute, so enforce the same hard
    # limits here (fail closed) rather than trusting the acquisition to be safe.
    # Two layers, as for set_xyz: the function-keyed limits (with provenance)
    # first, the driver's own check_axis as the safety net.
    _check_limits(
        handle,
        "acquire",
        {zc: capture_options[zc] for zc in ("z_start", "z_end") if zc in capture_options},
    )
    try:
        for zc in ("z_start", "z_end"):
            if zc in capture_options:
                _limits.check_axis("z", float(capture_options[zc]))
    except _limits.LimitError as exc:
        raise RuntimeError(f"acquire: stack Z range outside stage limits: {exc}") from exc

    # Give the image writer an explicit, per-acquisition output location so the
    # resident server can resolve the frame paths and repeated/same-label
    # captures never collide. Cleaned up after the frames are relocated.
    handle._acq_seq += 1
    stem = _acq.canonical_stem(position_label)
    staging = handle.output_root / "_staging" / f"{stem}_{handle._acq_seq:04d}"
    staging.mkdir(parents=True, exist_ok=True)
    capture_options.setdefault("folder", str(staging))
    capture_options.setdefault("filename", f"{stem}.tiff")
    result = _acq.acquire(handle.client, stem, options=capture_options)

    # 4) save into the canonical layout, then drop the staging copies (save()
    #    copies rather than moves, so remove the writer's originals to avoid
    #    doubling every dataset on disk).
    try:
        saved = _acq.save(
            result,
            handle.output_root,
            position_label=position_label,
            folder=folder,
            format=fmt,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "position_label": position_label,
        "folder": folder,
        "format": fmt,
        "planes": result.planes,
        # Every file saved, under the name the ZMART Controller's contract fixes,
        # so a workflow finds the pictures on any microscope: the images, then
        # the metadata written beside them.
        "files": [str(p) for p in [*saved.image_paths, saved.metadata_path] if p],
        "metadata_file": str(saved.metadata_path) if saved.metadata_path else None,
        "position": _user_xyz(handle, _readers.get_positions(handle.client)),
        "duration_s": result.duration_s,
    }


def _settle(handle: MesospimHandle, overshoot_um: float = 5.0) -> None:
    """Pin the linear stage backlash: nudge -overshoot on x/y/z, then return.

    Backlash correction is an optimisation, not a requirement: if the overshoot
    would violate a stage limit (or otherwise fails), it is skipped with a
    warning rather than driving into a hard stop. But once the overshoot has
    moved the stage, a failed return move leaves the stage displaced, so that is
    surfaced as an error rather than proceeding to capture at the wrong place.
    """
    pos = _readers.get_positions(handle.client)
    linear = {axis: float(pos.get(axis) or 0.0) for axis in ("x", "y", "z")}
    nudge = _cmd.move_absolute(handle.client, {a: v - overshoot_um for a, v in linear.items()})
    if not nudge.get("success"):
        log.warning("acquire: backlash settle skipped (%s)", nudge.get("message"))
        return
    back = _cmd.move_absolute(handle.client, linear)
    if not back.get("success"):
        raise RuntimeError(f"acquire: backlash settle left stage displaced: {back.get('message')}")


# =============================================================================
# info
# =============================================================================

# The instrument this driver serves, with its connect settings, exactly as the
# controller reads it from the plug-in folder's zmart.json. ``microscope``
# is a placeholder for a specific instrument; edit it (and host/port) per
# deployment.
_MANIFEST = Path(__file__).resolve().parent / "zmart_controller" / "zmart.json"
CONNECTION = json.loads(_MANIFEST.read_text(encoding="utf-8"))["instruments"][0]

# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from this.
# It says what the other answers cannot. The parts in braces are filled in by
# _described() from the stage envelope loaded at connect and from the hardware
# model mesoSPIM-control reports.
DESCRIPTION = """A mesoSPIM light-sheet microscope, driven through mesoSPIM-control and its Remote Scripting server.

Stage: x, y and z move the sample, one motor each ("motoric"), in micrometres from the origin saved with this driver's set_origin step (plain stage coordinates until one is saved). Every move is checked against this microscope's stage limits. Two more axes are moved with procedures: move_focus moves the detection focus (f, in micrometres{f_bounds}) and move_rotation turns the sample (theta, in degrees{theta_bounds}).

Settings (the changeable part of the state): laser is the laser line ({lasers}); intensity is the laser intensity in percent, 0 to 100; filter is the emission filter ({filters}); zoom is the detection zoom ({zooms}); shutterconfig chooses which light sheet is on ({shutters}); etl_l_amplitude, etl_l_offset, etl_r_amplitude and etl_r_offset are the amplitude and offset of the electrically tunable lens of the left and the right light sheet.{camera}

Acquiring captures one plane with the current settings, or a stack when z_start and z_end (micrometres from the origin) or planes are given, with z_step micrometres between planes. Laser, intensity, filter, zoom and shutterconfig may be given as acquisition settings too. The images are saved as ome-tiff, raw or h5 in the data folder under the output folder, named by the position label; the folder setting groups them in a folder of that name inside data."""


def _bounds(handle: MesospimHandle, axis: str) -> str:
    bounds = handle.stage_limits.get(axis)
    return f", from {bounds[0]:g} to {bounds[1]:g}" if bounds else ""


def _described(handle: MesospimHandle) -> str:
    """The description, filled in from the loaded limits and mesoSPIM-control's hardware model."""
    config = _readers.get_config(handle.client)
    lasers = ", ".join(str(laser.get("name")) for laser in config.get("lasers", []))
    filters = ", ".join(str(name) for name in config.get("filters", []))
    zooms = ", ".join(
        f"{z.get('name')} at {z.get('pixel_size_um'):g} um per pixel"
        if z.get("pixel_size_um")
        else str(z.get("name"))
        for z in config.get("zooms", [])
    )
    shutters = ", ".join(str(name) for name in config.get("shutter_configs", []))
    camera = config.get("camera") or {}
    camera_text = (
        f" The camera image is {camera['pixels_x']} x {camera['pixels_y']} pixels."
        if camera.get("pixels_x") and camera.get("pixels_y")
        else ""
    )
    return DESCRIPTION.format(
        f_bounds=_bounds(handle, "f"),
        theta_bounds=_bounds(handle, "theta"),
        lasers=lasers or "none reported",
        filters=filters or "none reported",
        zooms=zooms or "none reported",
        shutters=shutters or "none reported",
        camera=camera_text,
    )


def get_info(handle: MesospimHandle) -> dict:
    """Where images go, a description of the microscope, and read-only extras.

    The extras: the initial positions, and the focus and rotation now.
    """
    pos = _readers.get_positions(handle.client)
    return {
        "output_root": str(handle.output_root),
        "description": _described(handle),
        "initial_positions": [dict(p) for p in handle.initial_positions],
        "focus_um": pos.get("f"),
        "rotation_deg": pos.get("theta"),
        "server": dict(handle.immutable),
    }


# =============================================================================
# the controller's shape
# =============================================================================

# The commands that answer in the controller's shape. connect returns the
# handle and disconnect returns nothing, so those two are handed over as they are.
_ANSWERING_OPS = (
    "get_acquisition_settings",
    "get_actuators",
    "get_xyz",
    "set_xyz",
    "acquire",
    "get_state",
    "set_state",
    "get_procedures",
    "run_procedure",
    "get_info",
)


def _answered(function):
    """Wrap a command so it answers the way the controller documents.

    The controller promises every workflow the same answer from every
    microscope: ``{"success": ..., "content": ...}``. The functions in this
    module return the content alone, and raise when something goes wrong.
    ``success`` is therefore True, unless the content says that a change was
    sent but could not be confirmed (``"confirmed": False``): an outcome that
    is safe to carry on from, so it is reported rather than raised.
    """

    @functools.wraps(function)
    def command(*args, **kwargs):
        content = function(*args, **kwargs)
        unconfirmed = isinstance(content, dict) and content.get("confirmed") is False
        return {"success": not unconfirmed, "content": content}

    return command


def ops_table() -> dict[str, Any]:
    """The functions this driver hands to the controller, one per command.

    The plug-in folder ``zmart_controller/`` exposes these by name.
    ``connect`` and ``disconnect`` are handed over unchanged. Every other
    command is wrapped so that, called through the controller, it answers
    ``{"success": ..., "content": ...}``. Called directly from this module,
    the same functions return the content alone.
    """
    functions = {
        "connect": connect,
        "disconnect": disconnect,
        "get_acquisition_settings": get_acquisition_settings,
        "get_actuators": get_actuators,
        "get_xyz": get_xyz,
        "set_xyz": set_xyz,
        "acquire": acquire,
        "get_state": get_state,
        "set_state": set_state,
        "get_procedures": get_procedures,
        "run_procedure": run_procedure,
        "get_info": get_info,
    }
    return {
        name: _answered(function) if name in _ANSWERING_OPS else function
        for name, function in functions.items()
    }
