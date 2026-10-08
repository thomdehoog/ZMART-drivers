"""
ZMART controller adapter for ZEISS ZEN.
=======================================
The seam that plugs this driver into the vendor-agnostic **ZMART controller**
(``zmart_controller``). The controller drives every microscope through one
small set of functions -- ``connect`` plus one function per command. This
module implements them for ZEN (through the ZEN API gateway, see
``connection/``); ``zmart_controller_plugin.py`` beside it hands them to the controller.

As in the other drivers, the driver owns the frame **origin**: the controller
works in micrometres from an origin the driver subtracts, so the controller
never does coordinate maths. Every move is checked against the stage limits
of this microscope before ZEN is asked to move, and ``acquire`` runs the
loaded ZEN experiment and brings the CZI into the output folder.

What the neutral surface covers for ZEN today:

* **x/y/z** -- the XY stage and the focus drive (one "motoric" actuator each).
* **changeable state** -- the objective (position on the changer) and the
  loaded ZEN experiment (the imaging settings: channels, exposure, Z-stack).
* **procedures** -- ``software_autofocus`` (ZEN's focus search as set up in
  the loaded experiment), ``find_surface`` / ``store_focus`` / ``recall_focus``
  (Definite Focus), ``live`` and ``stop``.
* **acquire** -- a snap, or the whole experiment (tiles, Z-stack, time
  series) when asked, written by ZEN as one CZI and copied into
  ``<output_root>/data/``.

Plug the driver in by handing its ``driver`` module to the controller::

    import zmart_controller
    import zmart_drivers.zeiss.zenapi.zmart_controller_plugin as zeiss

    zmart_controller.set_instrument(zeiss, {"config": "C:/ZEN/config.ini"})

Author: Thom de Hoog (ZMB, University of Zurich)
        thom.dehoog@zmb.uzh.ch . thomdehoog@gmail.com
License: MIT
"""

from __future__ import annotations

import functools
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acquisition.save import _wait_stable, zen_image_path
from .calibration import machine as _machine
from .commands import commands as _cmd
from .connection.session import close as _close
from .connection.session import connect as _connect
from .limits import checks as _limits
from .limits import stage_config as _stage_config
from .readers import api_reader as _readers

log = logging.getLogger(__name__)

# Every axis is driven by one motor under ZEN: the XY stage and the focus drive.
_ACTUATORS: dict[str, list[str]] = {"x": ["motoric"], "y": ["motoric"], "z": ["motoric"]}

# The unit of every number in a get_xyz or set_xyz answer. The controller fixes
# this word, so a workflow can trust that every microscope speaks micrometres.
UNIT = "micrometer"

# The connection settings used for any key the caller leaves out.
# ``microscope`` names this instrument (it picks the folder its origin and
# stage limits are saved in). ``config`` is the ZEN API ``config.ini`` (host,
# port, gateway certificate, control token); left out, a ``config.ini`` in the
# folder Python was started from is used. ``host`` / ``port`` / ``cert_file``
# / ``control_token`` may be given directly instead.
CONNECTION = {"microscope": "zen-lm"}


@dataclass
class ZenHandle:
    """Live session handle the controller passes back into every op."""

    client: Any
    connection: dict
    output_root: Path
    machine: Any
    limits: dict = field(default_factory=dict)
    limits_are_defaults: bool = False
    origin: dict = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    immutable: dict = field(default_factory=dict)
    initial_position: dict = field(default_factory=dict)
    experiment: Any = None  # the loaded zenapi.Experiment, or None
    closed: bool = False


# =============================================================================
# lifecycle
# =============================================================================


def _open_client(connection: dict):
    """Connect to the gateway from the connection dict (kept separate so tests can swap it)."""
    return _connect(
        connection.get("config"),
        host=connection.get("host"),
        port=connection.get("port"),
        cert_file=connection.get("cert_file"),
        control_token=connection.get("control_token"),
        connect_timeout=connection.get("connect_timeout"),
    )


def connect(connection: dict | None = None) -> ZenHandle:
    """Open a session with ZEN through its API gateway and read what it offers.

    Every key is optional; a key left out takes its value from
    :data:`CONNECTION`. Honours ``microscope`` (the name of this instrument),
    ``config`` (path to the ZEN API ``config.ini``) or the explicit
    ``host`` / ``port`` / ``cert_file`` / ``control_token`` keys,
    ``output_root`` (where ``acquire`` copies images; a temp folder when
    omitted), ``machine_root`` (override for the ProgramData root) and
    ``experiment`` (a ZEN experiment to load right away). The stage limits
    come from this microscope's ``stage_limits.json`` (generic defaults are
    copied there on the first connect, with a warning) and govern every move
    of the session; the frame origin a previous session persisted is restored.
    """
    connection = {**CONNECTION, **(connection or {})}
    client = _open_client(connection)
    try:
        output_root = Path(connection.get("output_root") or tempfile.mkdtemp(prefix="zeiss_run_"))
        output_root.mkdir(parents=True, exist_ok=True)
        machine = _machine.MachineProfile(
            microscope_id=connection["microscope"],
            programdata_root=connection.get("machine_root"),
        )
        limits_path, copied = machine.ensure_limits_file()
        stage_cfg = _stage_config.load(limits_path)
        _limits.apply_stage_limits_from_config(stage_cfg)
        if copied:
            log.warning(
                "no stage limits were set for this microscope; generic defaults were "
                "copied to %s. Replace them with the real travel range of the stage.",
                limits_path,
            )
        limits = {
            axis: {"min": float(lo), "max": float(hi)}
            for axis, (lo, hi) in stage_cfg["stage_um"].items()
        }
        objectives = _readers.get_objectives(client)
        handle = ZenHandle(
            client=client,
            connection=dict(connection),
            output_root=output_root,
            machine=machine,
            limits=limits,
            limits_are_defaults=copied,
            immutable={
                "app": "ZEN",
                "api": "ZEN API (gRPC)",
                "microscope": connection.get("microscope"),
                "runtime": dict(getattr(client, "runtime", {})),
                "objectives": objectives,
                "image_output_path": _safe(_readers.get_image_output_path, client, default=""),
            },
            initial_position=_raw_xyz(client),
        )
        _restore_persisted_origin(handle)
        if connection.get("experiment"):
            _use_experiment(handle, str(connection["experiment"]))
    except Exception:
        _close(client)
        raise
    log.info("ZEN controller session ready (output_root=%s)", output_root)
    return handle


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - optional information must not block connect
        log.debug("%s unavailable: %s", getattr(fn, "__name__", fn), exc)
        return default


def _restore_persisted_origin(handle: ZenHandle) -> None:
    try:
        payload = handle.machine.read_origin()
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not block connect
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


def disconnect(handle: ZenHandle) -> None:
    """Close the connection to the gateway (ZEN itself keeps running)."""
    handle.closed = True
    _close(handle.client)


def _require_open(handle: ZenHandle) -> None:
    if handle.closed:
        raise RuntimeError("session is disconnected")


# =============================================================================
# frame origin
# =============================================================================


def _raw_xyz(client) -> dict[str, float]:
    """The position ZEN reports, in micrometres, before the origin is subtracted.

    ZEN sends metres over the wire; the readers turn them into micrometres,
    so these are the stage's own numbers in the unit the rest of the driver
    speaks. Nothing else is changed: no origin, no correction.
    """
    xy = _readers.get_xy(client)
    return {"x": float(xy["x_um"]), "y": float(xy["y_um"]), "z": float(_readers.get_z(client))}


def set_origin(handle: ZenHandle) -> dict:
    """Mark the current position as the origin, so that it reads (0, 0, 0) from now on.

    This is a one-time setup step that you run with the driver directly, not
    through the ``zmart_controller`` Session. The controller does not offer
    ``set_origin``, because the origin belongs to the microscope's configuration
    rather than to any single experiment. Move the stage to the point you want
    as zero, then run::

        handle = zen_zmart_adapter.connect(connection)
        zen_zmart_adapter.set_origin(handle)
        zen_zmart_adapter.disconnect(handle)

    The origin is saved to ``origin.json`` in this microscope's configuration
    folder, and :func:`connect` loads it every time it opens a session. It stays
    in use until you set it again. The return value holds the new origin (in raw
    stage micrometres) and the path of the file it was saved to. If the file
    cannot be written, a ``RuntimeError`` is raised so that you know the origin
    will not survive the next connect.
    """
    _require_open(handle)
    handle.origin = _raw_xyz(handle.client)
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


def _user_xyz(handle: ZenHandle, raw: dict) -> dict[str, float]:
    """The same position measured from the origin: what the controller calls ``position``."""
    return {axis: float(raw[axis]) - handle.origin[axis] for axis in ("x", "y", "z")}


# =============================================================================
# movement
# =============================================================================


def get_actuators(handle: ZenHandle) -> dict:
    """The actuator options each axis offers: one motor per axis under ZEN."""
    _require_open(handle)
    return {axis: list(opts) for axis, opts in _ACTUATORS.items()}


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    """The motor to use per axis for one call: the one named, or else the only one there is.

    ZEN offers one motor per axis, so this mostly checks that the names a
    workflow passes exist; an unknown axis or motor raises ``ValueError``.
    """
    chosen = {axis: opts[0] for axis, opts in _ACTUATORS.items()}
    if not with_actuators:
        return chosen
    for axis, actuator in with_actuators.items():
        if axis not in _ACTUATORS:
            raise ValueError(f"unknown axis {axis!r}")
        name = actuator[0] if isinstance(actuator, (list, tuple)) else actuator
        if name not in _ACTUATORS[axis]:
            raise ValueError(f"unknown actuator {actuator!r} for axis {axis!r}")
        chosen[axis] = name
    return chosen


def get_xyz(handle: ZenHandle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``actuators`` and ``canvas``.

    Every number is in micrometres. ``position`` is measured from the origin
    saved with :func:`set_origin`. ``actuators`` holds every motor of the
    axis with its own reading exactly as ZEN reports it, in the stage's own
    coordinates, so the origin is *not* subtracted there; under ZEN each
    axis has the one motor ``motoric``, so the two numbers differ by the
    origin alone. ``with_actuators`` only checks that the motors named exist;
    the answer always reports all of them.

    The ``canvas`` is everywhere a picture can show on that axis, ``[min,
    max]`` from the origin. For this driver it is the travel itself: this
    microscope's stage limits, the ones every move is checked against. A
    picture taken at the edge of the travel does show half a field beyond
    it, and a z-stack may reach past it, but the field size and the stack
    depth are set inside the ZEN experiment, which the ZEN API does not
    report, so the driver cannot widen the canvas by them.
    """
    _require_open(handle)
    _resolve_actuators(with_actuators)
    raw = _raw_xyz(handle.client)
    user = _user_xyz(handle, raw)
    return {
        axis: {
            "position": user[axis],
            "unit": UNIT,
            "actuators": {motor: raw[axis] for motor in _ACTUATORS[axis]},
            "canvas": [
                handle.limits[axis]["min"] - handle.origin[axis],
                handle.limits[axis]["max"] - handle.origin[axis],
            ],
        }
        for axis in ("x", "y", "z")
    }


def _raise_if_failed(result: dict, what: str) -> None:
    """Turn a failed command result into the controller's error contract."""
    if not result.get("success"):
        raise RuntimeError(f"{what} refused: {result.get('message')}")


def set_xyz(
    handle: ZenHandle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to a position in micrometres from the origin, then answer like :func:`get_xyz`.

    The target is mapped to ZEN's stage coordinates through the origin and
    checked against the stage limits before ZEN is asked to move; XY moves
    first, then the focus drive. Once both moves are done, the position is
    read back from ZEN, so the answer shows where the stage really is rather
    than the numbers that were asked for. A target outside the limits, or a
    move ZEN refused, raises ``RuntimeError`` and nothing is answered.
    """
    _require_open(handle)
    _resolve_actuators(with_actuators)
    targets = {
        axis: handle.origin[axis] + float(value) for axis, value in (("x", x), ("y", y), ("z", z))
    }
    # All three axes are checked before anything moves, so a bad Z target
    # never leaves the stage half-way to its XY target.
    try:
        _limits._check_xy_limits(targets["x"], targets["y"])
        _limits._check_z_limits(targets["z"])
    except RuntimeError as exc:
        raise RuntimeError(f"set_xyz refused: {exc}") from exc
    _raise_if_failed(_cmd.move_xy(handle.client, targets["x"], targets["y"]), "set_xyz")
    _raise_if_failed(_cmd.move_z(handle.client, targets["z"]), "set_xyz")
    return get_xyz(handle, with_actuators=with_actuators)


# =============================================================================
# state
# =============================================================================


def get_state(handle: ZenHandle) -> dict:
    """Changeable settings first, then the observed report.

    Changeable: ``objective_position`` (position index on the objective
    changer) and ``experiment`` (the loaded ZEN experiment, which carries the
    imaging settings: channels, exposure, Z-stack). Observed: identity, the
    objectives, the experiments ZEN can load, where ZEN writes images, the
    stage limits.
    """
    _require_open(handle)
    current = _readers.get_objective(handle.client)
    observed = dict(handle.immutable)
    observed["objective"] = current
    observed["available_experiments"] = _safe(
        _readers.get_available_experiments, handle.client, default=[]
    )
    observed["limits"] = dict(handle.limits)
    observed["limits_are_defaults"] = handle.limits_are_defaults
    observed["busy"] = _readers.get_status(handle.client)["is_experiment_running"]
    return {
        "changeable": {
            "objective_position": current["index"],
            "experiment": handle.experiment.name if handle.experiment else None,
        },
        "observed": observed,
    }


def set_state(handle: ZenHandle, state: dict) -> dict:
    """Apply the changeable settings; report what stuck.

    Accepts ``objective_position`` (a position index from the observed
    objectives) and ``experiment`` (a name from ``available_experiments``).
    ``observed`` is a report, never an instruction, and is not read here.
    """
    _require_open(handle)
    changeable = state.get("changeable") or {}
    applied: dict[str, Any] = {}
    if "objective_position" in changeable:
        result = _cmd.set_objective(handle.client, index=int(changeable["objective_position"]))
        _raise_if_failed(result, "set_state(objective_position)")
        applied["objective_position"] = result["index"]
    if changeable.get("experiment"):
        _use_experiment(handle, str(changeable["experiment"]))
        applied["experiment"] = handle.experiment.name
    return {"applied": applied}


def _use_experiment(handle: ZenHandle, name: str) -> None:
    """Load ``name`` in ZEN unless it is already the loaded experiment.

    Loading again would give ZEN a second copy of the same experiment, so a
    workflow that names the experiment on every position costs nothing extra.
    """
    if handle.experiment is not None and handle.experiment.name == name:
        return
    handle.experiment = _cmd.load_experiment(handle.client, name)


# =============================================================================
# procedures
# =============================================================================


def get_procedures(handle: ZenHandle) -> dict:
    """The named procedures this driver offers."""
    _require_open(handle)
    return {
        "software_autofocus": {
            "description": "ZEN's software autofocus with the settings of the loaded "
            "experiment; leaves the focus drive at the sharpest plane",
            "args": ["timeout_s"],
        },
        "find_surface": {
            "description": "Definite Focus: find the coverslip surface and move the "
            "focus drive there (needs Definite Focus hardware)"
        },
        "store_focus": {"description": "Definite Focus: remember the current focus"},
        "recall_focus": {"description": "Definite Focus: return to the stored focus"},
        "live": {"description": "start ZEN's live view with the loaded experiment"},
        "stop": {"description": "stop whatever ZEN is acquiring (live, snap or experiment)"},
    }


def _focus_record(handle: ZenHandle, name: str, result: dict) -> dict:
    _raise_if_failed(result, name)
    raw_z = result.get("z_um")
    if raw_z is None:
        raw_z = float(_readers.get_z(handle.client))
    return {
        "ran": name,
        "focus_um": raw_z,
        "frame_z_um": raw_z - handle.origin["z"],
        "duration_s": result.get("timing", {}).get("total_s"),
    }


def run_procedure(handle: ZenHandle, procedure: dict) -> dict:
    """Run a procedure. ``procedure`` is ``{"name": ..., ...args}``.

    The focus procedures report the focus as ``frame_z_um`` (relative to the
    origin) and ``focus_um`` (ZEN's raw position), like the other drivers.
    """
    _require_open(handle)
    name = procedure.get("name")
    if name == "software_autofocus":
        _require_experiment(handle, "software_autofocus")
        timeout = procedure.get("timeout_s")
        result = _cmd.find_autofocus(
            handle.client, handle.experiment, timeout_s=float(timeout) if timeout else None
        )
        return _focus_record(handle, name, result)
    if name == "find_surface":
        return _focus_record(handle, name, _cmd.find_surface(handle.client))
    if name == "store_focus":
        _raise_if_failed(_cmd.store_focus(handle.client), name)
        return {"ran": name}
    if name == "recall_focus":
        return _focus_record(handle, name, _cmd.recall_focus(handle.client))
    if name == "live":
        _require_experiment(handle, "live")
        _cmd.start_live(handle.client, handle.experiment)
        return {"ran": name, "experiment": handle.experiment.name}
    if name == "stop":
        stopped = _cmd.stop(handle.client)
        return {"ran": name, **stopped}
    raise ValueError(f"unknown procedure {name!r}; known: {sorted(get_procedures(handle))}")


def _require_experiment(handle: ZenHandle, what: str) -> None:
    if handle.experiment is None:
        raise ValueError(
            f"{what} needs a loaded ZEN experiment: set_state({{'changeable': "
            f"{{'experiment': <name>}}}}) first, or pass 'experiment' in the connection"
        )


# =============================================================================
# acquire (captures and saves)
# =============================================================================

def get_acquisition_settings(handle: ZenHandle) -> dict:
    """The acquisition and saving settings this instrument offers, with the active value of each.

    ``folder`` groups the CZI in a folder of that name inside
    ``<output_root>/data/`` (empty saves it straight into ``data``),
    ``experiment`` chooses the ZEN experiment (the imaging settings), ``mode``
    a single snap or the whole experiment, ``copy_to_output_root`` whether the
    CZI is copied from ZEN's image folder into ``<output_root>/data/``.
    """
    _require_open(handle)
    return {
        "folder": {"options": "any text; empty saves straight into data", "active": ""},
        "experiment": {
            "options": _safe(_readers.get_available_experiments, handle.client, default=[]),
            "active": handle.experiment.name if handle.experiment else None,
        },
        "mode": {"options": ["snap", "experiment"], "active": "snap"},
        "format": {"options": ["czi"], "active": "czi"},
        "copy_to_output_root": {"options": [True, False], "active": True},
        "timeout_s": {"options": "float > 0 (wait for the CZI to be complete)", "active": 60.0},
    }


def canonical_stem(folder: str, position_label: str) -> str:
    """A file-name stem safe on Windows: ``<folder>_<label>`` with odd characters replaced.

    With no folder, the stem is the label alone.
    """
    raw = f"{folder}_{position_label}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_") or "acquisition"


def acquire(
    handle: ZenHandle, *, position_label: str, acquisition_settings: dict | None = None
) -> dict:
    """Acquire with the loaded ZEN experiment and bring the CZI into the output folder.

    A snap by default; the whole experiment (Z-stack, tiles, time series) when
    ``mode`` is "experiment". ZEN writes ``<folder>_<label>.czi`` (or
    ``<label>.czi`` with no folder) into its image folder; that file is then
    copied to ``<output_root>/data/<folder>/`` (when the ZEN folder is
    reachable from here). The record names both locations.
    """
    _require_open(handle)
    options = dict(acquisition_settings or {})
    folder = options.get("folder") or ""
    if not isinstance(folder, str):
        # A ValueError, like every other unusable acquisition setting.
        raise ValueError(  # noqa: TRY004
            f"acquisition setting 'folder' must be text, not {folder!r}"
        )
    if options.get("experiment"):
        _use_experiment(handle, str(options["experiment"]))
    _require_experiment(handle, "acquire")
    fmt = str(options.get("format", "czi"))
    if fmt != "czi":
        raise ValueError(f"unknown format {fmt!r}; ZEN writes 'czi'")
    mode = options.get("mode", "snap")
    if mode not in ("snap", "experiment"):
        raise ValueError(f"unknown mode {mode!r}; choose 'snap' or 'experiment'")
    timeout = float(options.get("timeout_s", 60.0))

    started = time.perf_counter()
    output_name = canonical_stem(folder, position_label)
    run = _cmd.run_snap if mode == "snap" else _cmd.run_experiment
    result = run(handle.client, handle.experiment, output_name=output_name)
    _raise_if_failed(result, "acquire")
    status = result.get("status") or {}

    zen_path = zen_image_path(handle.client, result["output_name"])
    copied = False
    files = [str(zen_path)]
    if options.get("copy_to_output_root", True):
        data_dir = handle.output_root / "data"
        if folder:
            data_dir = data_dir / canonical_stem(folder, "")
        data_dir.mkdir(parents=True, exist_ok=True)
        dst = data_dir / f"{result['output_name']}.czi"
        if not zen_path.parent.is_dir():
            # The ZEN image folder is not visible from this computer (no share
            # mounted under that path): say so at once instead of waiting.
            log.warning(
                "ZEN's image folder %s is not reachable from this computer; the CZI "
                "stays there (%s)",
                zen_path.parent,
                zen_path,
            )
        else:
            try:
                _wait_stable(zen_path, timeout_s=timeout, poll_s=0.2)
                shutil.copy2(zen_path, dst)
                copied = True
                files = [str(dst)]
            except (TimeoutError, OSError) as exc:
                log.warning("CZI left on the ZEN computer (%s): %s", zen_path, exc)

    count = status.get("images_count")
    position = _user_xyz(handle, _raw_xyz(handle.client))
    return {
        "position_label": position_label,
        "folder": folder,
        "format": "czi",
        "mode": mode,
        "experiment": handle.experiment.name,
        "output_name": result["output_name"],
        "image_count": count if count and count > 0 else None,
        "planes": _planes(files[0], mode, count, position),
        # Every file saved, under the name the ZMART Controller's contract fixes,
        # so a workflow finds the pictures on any microscope.
        "files": files,
        "zen_image_path": str(zen_path),
        "copied": copied,
        "metadata_file": None,
        "status": status,
        "position": position,
        "duration_s": round(time.perf_counter() - started, 3),
    }


def _planes(path: str, mode: str, count: int | None, position: dict) -> list[dict]:
    """One entry per saved image plane: which channel it is, and where it was taken.

    A snap is taken once, at one depth, with the stage standing still, so
    each image ZEN reports is one channel (``c``) at the stage position. A
    whole experiment may hold tiles, depths and moments as well, and how its
    images are laid out is only written inside the CZI, which this driver
    does not read yet. So for an experiment the planes are left empty rather
    than guessed; ``image_count`` still says how many images ZEN took.
    """
    if mode != "snap":
        return []
    return [
        {
            "path": path,
            "c": channel,
            "z": 0,
            "t": 0,
            "x_um": position["x"],
            "y_um": position["y"],
            "z_um": position["z"],
        }
        for channel in range(max(int(count or 1), 1))
    ]


# =============================================================================
# info
# =============================================================================


# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from this.
# It says what the other answers cannot. The parts in braces are filled in by
# _described() from this session's limits and from what ZEN reports.
DESCRIPTION = """A ZEISS microscope driven through ZEN and the ZEN API, by way of the ZEN API Gateway.

Stage: x and y move the motorised stage, and z moves the focus drive; each axis has one motor ("motoric"). All positions are in micrometres from the origin saved with this driver's set_origin step (plain stage coordinates until one is saved). Every move is checked against this microscope's stage limits before ZEN is asked to move.{defaults}

Settings (the changeable part of the state): objective_position is the position of the objective on the objective changer, as listed below; experiment is the name of a ZEN experiment, which carries the imaging settings: channels, exposure and z-stack.

Objectives on the changer, by position: {objectives}.

Experiments ZEN can load: {experiments}.

Acquiring needs a loaded experiment. It takes a snap with that experiment, or runs the whole experiment (tiles, z-stack, time series) when the mode is "experiment" or the acquisition type mentions a stack, tiles or a time lapse. ZEN writes one CZI file, which is copied into the data folder under the output folder when ZEN's image folder can be reached from this computer."""


def _objective_text(objective: dict) -> str:
    """One objective as text: its position, name, and what ZEN knows of it."""
    details = []
    if objective.get("magnification"):
        details.append(f"{objective['magnification']:g}x")
    if objective.get("na"):
        details.append(f"NA {objective['na']:g}")
    if objective.get("immersion"):
        details.append(str(objective["immersion"]).lower())
    text = f"{objective.get('index')}: {objective.get('name')}"
    return f"{text} ({', '.join(details)})" if details else text


def _described(handle: ZenHandle, experiments: list) -> str:
    """The description, filled in from this session's limits and what ZEN reports."""
    defaults = (
        " The stage limits in use are still the generic defaults copied on the first "
        "connect; replace them with the real travel range of this stage."
        if handle.limits_are_defaults
        else ""
    )
    objectives = ", ".join(
        _objective_text(o) for o in handle.immutable.get("objectives", [])
    )
    return DESCRIPTION.format(
        defaults=defaults,
        objectives=objectives or "none reported",
        experiments=", ".join(str(name) for name in experiments) or "none reported",
    )


def get_info(handle: ZenHandle) -> dict:
    """Where images go, a description of the microscope, and read-only extras.

    The extras: where the session started, the limits, the objectives, the
    loaded experiment and the gateway.
    """
    _require_open(handle)
    experiments = _safe(_readers.get_available_experiments, handle.client, default=[])
    return {
        "output_root": str(handle.output_root),
        "description": _described(handle, experiments),
        "initial_position": dict(handle.initial_position),
        "limits": dict(handle.limits),
        "limits_file": str(handle.machine.limits_path()),
        "limits_are_defaults": handle.limits_are_defaults,
        "objectives": list(handle.immutable.get("objectives", [])),
        "image_output_path": handle.immutable.get("image_output_path"),
        "experiment": handle.experiment.name if handle.experiment else None,
        "server": dict(handle.immutable.get("runtime", {})),
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
    module return the content alone, and raise when something goes wrong
    (a move outside the limits, a call ZEN refused), so an answer that
    comes back is always a success. The controller catches what is raised
    and turns it into ``{"success": False, "content": <the error text>}``.
    """

    @functools.wraps(function)
    def command(*args, **kwargs):
        return {"success": True, "content": function(*args, **kwargs)}

    return command


def ops_table() -> dict[str, Any]:
    """The functions this driver hands to the controller, one per command.

    ``zmart_controller_plugin.py`` exposes these by name.
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
