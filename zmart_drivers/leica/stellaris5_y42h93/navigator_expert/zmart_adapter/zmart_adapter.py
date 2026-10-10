r"""ZMART Controller adapter for the Navigator Expert driver.

The functions that plug this driver into ``zmart_controller``: one function
per controller command, each taking the opaque handle as its first argument.
The controller stays vendor-free — this module (not the controller) knows
both contracts. The controller finds these functions through the driver
module, ``zmart_controller_plugin.py`` in the driver folder::

    import zmart_controller
    import zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_controller_plugin as stellaris

    zmart_controller.set_instrument(stellaris)

Answers: through the controller, every command answers
``{"success": True, "content": ...}``, the shape the controller documents for
every driver. The functions in this module return the content alone, and
:func:`ops_table` wraps them for the controller. Failures are always raised,
never reported as ``success: False``.

Frame math lives here: the driver speaks absolute stage micrometres, the
controller speaks micrometres relative to the saved frame origin. The
controller's single ``z`` axis is the *focus*
displacement — the sum of the two physical drives (z-wide + z-galvo)
relative to the origin's focus sum — so it reads the same regardless of
which drive realized a move. An objective change is compensated with the
calibration's per-objective translation totals (ΔT relative to the
origin's objective): x/y apply to the motoric stage and translation-z is
realized through z-wide. Ordinary requested z motion remains independently
actuator-selectable. The calibration schema is unchanged (operator decision,
2026-07-02). Cross-objective moves REFUSE when translations are
unavailable; reads warn and return uncompensated. The driver package
itself is untouched. Full design: ``docs/design/objective-aware-frame.md``.

Scope of v1 (grow as needed):
    - The frame origin is part of this microscope's configuration, like the
      limits, the orientation and the calibration. It is set once, in a
      separate setup step where the operator works with the driver directly:
      :func:`set_origin` captures stage XY, both z drives and the current
      objective as the zero point and saves it to the machine-local
      ``origin/`` folder. Every :func:`connect` then loads the newest saved
      origin, so each session starts in the same frame. The controller does
      not set the origin; it only forwards commands. When no origin has been
      saved yet, the frame is plain absolute stage coordinates.
    - ``get_xyz`` returns both frames: controller-relative values plus
      the raw hardware readings under ``"hardware"``.
    - ``get_state``/``set_state`` round-trip the selected job (the job
      is LAS X's unit of configuration, so reapplying the selection
      restores the whole setup).
    - ``get_procedures`` offers backlash takeup, zero-z-galvo (park the
      galvo at 0 with the focus kept), and autofocus (with job
      discovery); ``run_procedure`` runs them.
    - ``acquire`` selects the job, captures, and saves in one step;
      the ``folder`` acquisition setting and ``position_label`` map onto
      the driver's Naming slots and travel verbatim in the save lineage.

Live-validation note: the z model assumes the two drives combine
*additively with the same sign*. The arithmetic, readback keys/units,
and sign convention are validated against a live CAM by
``testing/hardware/validate_zmart_adapter.py`` (galvo leg). The *physical*
additivity of the two drives on a real objective still wants one hardware
pass (park the galvo at a known offset, move z-wide, check the focus sum)
before trusting large z moves.

Dependency direction:
    - Imports: driver internals only; nothing from ``zmart_controller``.
    - Imported by: the driver module ``zmart_controller_plugin.py`` — nothing else in the
      driver.
"""

from __future__ import annotations

import functools
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # driver version for embedded export state; never fail acquire over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001 -- best-effort provenance
    _DRIVER_VERSION = None

from .. import connect as _session
from .. import scanfields as _scanfields
from ..actions import acquire as _capture
from ..actions import set as _commands
from ..actions.derived import z_um_from_settings as _z_um_from_settings
from ..configuration import image_stage_registration as _orientation
from ..configuration import session_state as _session_state
from ..configuration import store as _machine
from ..dispatcher import gate as _gate
from ..dispatcher import read as _readers
from ..output import save as _save
from ..output.naming import Naming, run_hash
from ..procedures import backlash as _motion
from ..vendor_interface.parsing import parse_tile_geometry as _parse_tile_geometry
from . import info as _info

log = logging.getLogger(__name__)

# The connection settings used for any key the caller leaves out; the
# docstring of zmart_controller_plugin.py explains each one. ``microscope`` names this
# instrument. Image orientation is enabled by IMAGE_SAVE in config/profiles.py;
# only the orientation measurement explicitly saves raw pixels.
CONNECTION = {
    "microscope": "stellaris5-y42h93",
    "client": "PythonClient",
    "api_delay_ms": None,
    "output_root": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}

# The motors that can move each axis, under the names the controller uses.
# These are the names ``get_actuators`` lists and ``with_actuators`` accepts.
_ACTUATORS = {"x": ("motoric",), "y": ("motoric",), "z": ("z-wide", "z-galvo")}

# Where each motor's own reading sits in a hardware snapshot (see
# ``_hardware_snapshot``). ``get_xyz`` reports these readings under
# ``actuators``, so every motor here must also be listed in ``_ACTUATORS``.
_MOTOR_READINGS = {
    "x": {"motoric": "x_um"},
    "y": {"motoric": "y_um"},
    "z": {"z-wide": "z_wide_um", "z-galvo": "z_galvo_um"},
}

# Fixed defaults for axes omitted from ``with_actuators`` — never sticky: a
# previous call's choice is not state.
_DEFAULT_ACTUATORS = {"x": "motoric", "y": "motoric", "z": "z-wide"}

# Every number in a get_xyz or set_xyz answer is in micrometres, and each
# axis says so under this key. The same spelling the controller uses.
UNIT = "micrometer"

# controller actuator name -> driver move_z z_mode
_Z_MODES = {"z-wide": "zwide", "z-galvo": "galvo"}

# How long get_info waits (seconds) for LAS X to flush the live experiment
# to disk before parsing its scan fields. A read that needs a save first is
# quirky but unavoidable: the on-disk template is the only complete source.
EXPERIMENT_FLUSH_TIMEOUT_S = 60

# Routine backlash take-up remains explicitly available, but ordinary
# controller acquisitions do not add in-place correction cycles unless the
# caller opts in with a positive ``backlash_rounds`` value.
ACQUISITION_BACKLASH_DEFAULT_ROUNDS = 0

# Limits are enforced BELOW this adapter, in the command wrappers
# themselves (commands/gate.py + limits/checks.py; maintainer decision §7).
# The adapter does NO limit checking of its own — it composes commands and
# translates failures into actionable errors. connect() runs the limits
# handshake; a failed handshake leaves the session read-only and every
# mutating command underneath refuses with the recorded reason.


@dataclass
class ZmartHandle:
    """Opaque controller handle: the CAM client plus all adapter state.

    Attributes:
        client: Connected LAS X CAM client (process-lifetime, no close).
        connection: The connection dict this session was opened with
            (carries ``output_root`` and the connect params).
        hash6: SESSION hash, minted at connect. Travels in lineage /
            ``session_hash6`` provenance only — each :func:`acquire` mints
            its own acquired-position hash for the output Naming.
        origin: The frame zero point: stage XY, both z drives, their focus
            sum, and the objective it was captured under. :func:`connect`
            fills it from the newest origin saved by :func:`set_origin`.
            When no origin has been saved, it stays all-zero, which means
            the frame is simply the absolute stage coordinates.
        position_counter: Next per-session position index handed to an
            unlabeled :func:`acquire` (formatted 6-digit zero-padded). An
            explicit ``position_label`` does not consume a counter value.
        translations: Per-objective-slot translation triples (µm) from the
            active calibration, loaded at connect; ``None`` when it could not
            be read (cross-objective moves are then refused, reads warn).
        closed: Set by :func:`disconnect`; every op refuses a closed
            handle.
        unconfirmed: Every change this session sent that was accepted but
            could not be confirmed by a readback, newest last: what it was,
            when, and the driver's message. The driver does its best to
            confirm every change; when it cannot, it says so here (and in a
            warning) and carries on, so a workflow is told rather than
            stopped. ``get_state`` reports the list under ``observed``.
    """

    client: Any
    connection: dict[str, Any]
    hash6: str
    origin: dict[str, Any] = field(
        default_factory=lambda: {
            "x_um": 0.0,
            "y_um": 0.0,
            "z_wide_um": 0.0,
            "z_galvo_um": 0.0,
            "z_focus_um": 0.0,
            "objective": None,
        }
    )
    #: Where the last :func:`set_xyz` sent the stage: the frame position
    #: asked for, and the absolute z-wide it was realized at. Remembered
    #: rather than re-read, so that the capture path holds no extra call that
    #: can hang. The move is absolute, and a leg that was not confirmed is
    #: recorded under ``unconfirmed``. The z-wide rides along because a job
    #: states its stack in absolute z-wide, and without the anchor those
    #: slices cannot be put in the frame.
    driven_to: dict[str, float] | None = None
    position_counter: int = 0
    acquisition_hashes: set[str] = field(default_factory=set)
    translations: dict[int, tuple[float, float, float]] | None = None
    closed: bool = False
    unconfirmed: list[dict[str, Any]] = field(default_factory=list)


def _note_unconfirmed(handle: ZmartHandle, what: str, result: Any) -> None:
    """Record a change that was sent and accepted but never confirmed, and carry on.

    The driver confirms what it can. When a readback never matched in
    time, the change is not known to have failed, only not known to have
    happened, so it is written down here, said in a warning, and the
    command goes on. The workflow sees the list in ``get_state``.
    """
    message = result.get("message") if isinstance(result, dict) else str(result)
    note = {
        "what": what,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": message or "readback did not confirm the change",
    }
    handle.unconfirmed.append(note)
    del handle.unconfirmed[:-50]  # keep the newest fifty
    log.warning("unconfirmed, carrying on: %s: %s", what, note["message"])


def _require_open(handle: ZmartHandle) -> None:
    """Refuse to drive a disconnected handle."""
    if handle.closed:
        raise RuntimeError("session is disconnected")


# =============================================================================
# Lifecycle
# =============================================================================


def connect(connection: dict | None = None) -> ZmartHandle:
    """Open the CAM client and return the controller handle.

    Args:
        connection: The connection settings, all optional; a key left out
            takes its value from :data:`CONNECTION`, so an empty dictionary
            connects with the defaults. ``client`` and ``api_delay_ms`` feed
            the CAM connection; ``output_root`` is where :func:`acquire`
            saves; ``load_origin`` (default True) decides whether the saved
            frame origin is loaded.

    Delegates to the driver's own connection entry point
    (:func:`navigator_expert.connect_microscope`), which loads this microscope's
    three machine-local configs and installs each so the whole session works
    from one consistent picture: the **instrument limits** (into the commands gate),
    the **camera-to-stage orientation**, and the **per-objective calibration**
    (both into the per-connection session registry). Image orientation follows
    ``IMAGE_SAVE`` in ``config/profiles.py``. The connection dict may override
    limits/calibration loading and pick a named calibration set.

    The limits load is fail-soft: an invalid or opted-out ``limits.json``
    falls back to the bundled default envelope (loudly warned) rather than
    leaving the session unable to move; the hardcoded physical backstop bounds
    every move regardless.

    The frame origin is loaded here as well, from the newest ``origin.json``
    that :func:`set_origin` saved on this computer. Every session therefore
    starts in the same frame, and the origin keeps the objective it was
    captured under, so objective changes are handled exactly as they would
    be right after ``set_origin``. When no origin has been saved yet, the
    frame is the plain absolute stage coordinates and a warning is logged.
    Setting ``load_origin`` to False in the connection dict skips the saved
    origin on purpose and starts in stage coordinates.

    Raises:
        RuntimeError: when a saved origin exists but cannot be read or does
            not contain the expected values. The frame would otherwise
            silently fall back to stage coordinates and every move would
            land somewhere other than intended, so connect stops instead.
            The origin is checked before LAS X is contacted, so nothing is
            left half-connected.
        Whatever :func:`connect_microscope` raises when LAS X is
        unreachable; the controller passes that to the caller unchanged.
    """
    connection = {**CONNECTION, **(connection or {})}
    saved_origin = _load_saved_origin() if connection.get("load_origin", True) else None
    client = _session.connect_microscope(
        client_name=connection.get("client", "PythonClient"),
        api_delay_ms=connection.get("api_delay_ms"),
        load_limits=connection.get("load_limits", True),
        load_calibration=connection.get("load_calibration", True),
        calibration_name=connection.get("calibration_name"),
    )
    handle = ZmartHandle(client=client, connection=dict(connection), hash6=run_hash())
    loaded = _session_state.get(client)
    handle.translations = loaded.translations if loaded is not None else None
    if saved_origin is not None:
        handle.origin = saved_origin
    return handle


# The values a frame origin must carry. They match what set_origin captures,
# so an origin loaded at connect behaves exactly like a freshly captured one.
_ORIGIN_NUMBER_KEYS = ("x_um", "y_um", "z_wide_um", "z_galvo_um", "z_focus_um")


def _load_saved_origin() -> dict | None:
    """Read the newest saved frame origin, or return None when none exists.

    The origin lives in this microscope's ``origin/<datetime>/origin.json``
    (see ``config/machine.py``); the newest folder wins, just as for the
    limits, orientation and calibration.

    Returns:
        The origin with the same fields :func:`set_origin` captures
        (``x_um``, ``y_um``, ``z_wide_um``, ``z_galvo_um``, ``z_focus_um``
        and ``objective``), or None when no origin has ever been saved. In
        that case a warning explains that the frame is the plain stage
        coordinates.

    Raises:
        RuntimeError: when the file exists but cannot be read, is not valid
            JSON, or is missing one of the values above. Guessing a zero
            origin here would quietly shift every position in the
            experiment, so the connection stops and says how to fix it.
    """
    path = _machine.MACHINE.origin_path()
    fix = (
        "Fix or remove that file, or connect with load_origin=False and capture "
        "the origin again with the driver's set_origin step."
    )
    try:
        payload = _machine.MACHINE.read_origin()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"the saved frame origin at {path} could not be read ({exc}). {fix}"
        ) from exc
    if payload is None:
        log.warning(
            "no frame origin has been saved on this microscope yet, so positions "
            "are plain stage coordinates. Run the driver's set_origin step once "
            "to choose a zero point; it will then be loaded at every connect."
        )
        return None
    stored = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        raise RuntimeError(f"the saved frame origin at {path} has no 'origin' section. {fix}")
    origin: dict[str, Any] = {}
    for key in _ORIGIN_NUMBER_KEYS:
        value = stored.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(
                f"the saved frame origin at {path} has no usable value for {key!r} "
                f"(found {value!r}). {fix}"
            )
        origin[key] = float(value)
    # The objective is what lets the driver refuse a move under a different
    # objective when no calibration can translate between the two, so a
    # file that does not say which objective it was captured under is
    # refused rather than treated as "no objective recorded".
    if "objective" not in stored:
        raise RuntimeError(
            f"the saved frame origin at {path} does not record which objective it "
            f"was captured under. {fix}"
        )
    objective = stored["objective"]
    if objective is not None and not isinstance(objective, dict):
        raise RuntimeError(
            f"the saved frame origin at {path} records its objective in an unexpected "
            f"form ({objective!r}). {fix}"
        )
    origin["objective"] = dict(objective) if objective is not None else None
    log.info("loaded the saved frame origin from %s", path)
    return origin


def _loaded_orientation(handle: ZmartHandle):
    """The camera-to-stage turn loaded for this connection (identity if none).

    Read from the per-connection session registry the driver populated at
    connect, so every saved image in a session uses the same orientation the
    connection loaded — rather than each save re-reading the file.
    """
    loaded = _session_state.get(handle.client)
    if loaded is not None:
        return loaded.orientation
    # No per-connection state for this handle (built without connect_microscope,
    # or its registry entry was uninstalled). Fall back to reading the file
    # fresh — loudly, because this re-read can differ from what the connection
    # loaded and can differ from what the connection loaded.
    log.warning(
        "no per-connection orientation is installed for this handle; reading "
        "orientation.json fresh for this save"
    )
    return _orientation.rig_orientation()


def _objective_delta_um(handle: ZmartHandle, current_objective: dict | None) -> tuple:
    """ΔT = T[current objective] − T[origin's objective], in µm (x, y, z-focus).

    Zero when the origin has no objective anchor or the objective is
    unchanged. Raises RuntimeError when the objective HAS changed but the
    translations are unavailable — the caller decides whether that refuses
    (moves) or warns (reads).
    """
    origin_slot = (handle.origin.get("objective") or {}).get("slotIndex")
    current_slot = (current_objective or {}).get("slotIndex")
    if origin_slot is None or current_slot == origin_slot:
        return (0.0, 0.0, 0.0)
    if current_slot is None:
        raise RuntimeError(
            f"objective changed since the origin was captured (slot {origin_slot} -> "
            f"unidentified) so the frame cannot be mapped; capture the origin "
            f"again under the current objective"
        )
    # The delta math and the missing-pair policy live in ONE place —
    # calibration/core/model.py — shared with the driver's swap-time
    # compensation, so the two layers can never drift apart. Imported
    # lazily, matching the connection layer's calibration imports.
    from ..configuration.optical_calibration import model as _cal_model

    try:
        return _cal_model.translation_delta_um(handle.translations, origin_slot, current_slot)
    except RuntimeError as exc:
        raise RuntimeError(
            f"objective changed since the origin was captured but {exc} — or "
            f"capture the origin again under the current objective"
        ) from exc


def disconnect(handle: ZmartHandle) -> None:
    """Mark the handle closed and drop this connection's loaded driver state.

    The CAM client itself has no teardown, and none exists to call: verified
    by reflection that ``LasxApiClientPyModel`` (the client `connect_python_client`
    returns) exposes only ``Connect``/``ConnectAsync`` (plus a COM-style
    ``Release``, not a session close) -- there is no ``Disconnect``/``Close``/
    ``Dispose``. Reconnecting without disconnecting the previous handle was
    also verified live (both connections independently ping and read state
    correctly; the first stays usable after the second connects) -- a
    resource leak on the LAS X side, not a corruption/dead-end risk. Without
    this function's ``uninstall`` call, a disconnected client's
    ``FunctionLimits`` stayed installed in the gate's module-level registry
    indefinitely; a reconnect on a new handle re-runs the handshake and
    installs its own state regardless, but a stale entry left behind after
    disconnect is real teardown debt, not just cosmetic.
    """
    _gate.uninstall(handle.client)
    _session_state.uninstall(handle.client)
    handle.closed = True


# =============================================================================
# Frame and movement
# =============================================================================


def _hardware_snapshot(handle: ZmartHandle) -> dict:
    """One consistent read of everything the frame math needs.

    Stage XY, both z drives, and the current objective use the configured
    reader policy; raises when any of them is unreadable.
    """
    xy = _readers.get_xy(handle.client)
    if not xy:
        raise RuntimeError("could not read stage XY position")
    job = _selected_job_name(handle)
    if job is None:
        raise RuntimeError(
            "could not determine the selected LAS X job, which this command needs "
            "to read the focus drives; nothing was moved"
        )
    settings = _readers.get_job_settings(handle.client, job)
    if not settings:
        raise RuntimeError(f"could not read job settings for '{job}'")
    return {
        "job": job,
        "x_um": float(xy["x_um"]),
        "y_um": float(xy["y_um"]),
        "z_wide_um": _z_um_from_settings(settings, "z-wide", client=handle.client, job_name=job),
        "z_galvo_um": _z_um_from_settings(settings, "z-galvo", client=handle.client, job_name=job),
        "objective": settings.get("objective"),
    }


def _delta_or_warn(handle: ZmartHandle, snapshot: dict) -> tuple:
    """ΔT for a READ: uncompensated-but-loud when translations are missing.

    Reads must stay available (a read cannot land the stage anywhere wrong),
    so a missing translation degrades to ΔT = 0 with a warning instead of
    raising — unlike moves, which refuse.
    """
    try:
        return _objective_delta_um(handle, snapshot.get("objective"))
    except RuntimeError as exc:
        log.warning("%s; frame values are UNCOMPENSATED for the objective change", exc)
        return (0.0, 0.0, 0.0)


def _selected_job_name(handle: ZmartHandle) -> str | None:
    """Name of the currently selected LAS X job, or ``None`` when it cannot be read.

    A reading that fails is unknown, not a stop. The commands that only
    use this to decide whether a selection is needed go ahead and select;
    the ones that need the name to compute a move say so and refuse.
    """
    selected = _readers.get_selected_job(handle.client)
    name = selected.get("Name") if selected else None
    if not name:
        log.warning("could not determine the selected LAS X job")
        return None
    return name


def _setup_readiness(
    handle: ZmartHandle,
    active_objective: dict | None,
    limits: dict | None,
) -> dict:
    """Driver-owned verdict for limits, calibration, and orientation readiness.

    Workflows consume only ``ready`` and ``issues``. The Leica driver owns the
    meaning of the evidence and all machine-configuration application.
    """
    loaded = _session_state.get(handle.client)
    orientation = dict((loaded.orientation_info if loaded else None) or {})
    calibration = dict((loaded.calibration_info if loaded else None) or {})
    slot = (active_objective or {}).get("slotIndex")
    slot = None if slot is None else int(slot)
    origin_slot = (handle.origin.get("objective") or {}).get("slotIndex")
    origin_slot = None if origin_slot is None else int(origin_slot)
    # A slot only counts as calibrated when its entry records the session that
    # measured it. A missing calibration file is seeded from the repository's
    # bundled placeholder, which has no such provenance — reporting those
    # values as "ready" would let a never-calibrated microscope compensate
    # objective changes with numbers nobody measured on it.
    known_slots = calibration.get("slots", [])
    measured_slots = calibration.get("measured_slots", [])
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            f"machine-specific limits are not active (got {limits}); publish this "
            "machine's measured envelope with configuration/limits/notebooks/set_limits.ipynb first"
        )
    if not orientation.get("measured"):
        issues.append(
            "camera-to-stage orientation is not a measured machine value; "
            "run and adopt set_orientation"
        )
    if not calibration.get("loaded"):
        issues.append("objective calibration is not loaded")
    elif slot is None:
        issues.append("active objective slot is unreadable")
    elif slot not in known_slots:
        issues.append(
            f"active objective slot {slot} is absent from the loaded objective calibration"
        )
    elif slot not in measured_slots:
        issues.append(
            f"active objective slot {slot} carries only shipped placeholder values, not a "
            "calibration measured on this microscope; run and adopt the "
            "calibrate_objective_pair notebook"
        )
    if calibration.get("loaded"):
        if origin_slot is None:
            issues.append(
                "the saved frame origin does not record an objective; capture the "
                "origin again with the driver's set_origin step under the current objective"
            )
        elif origin_slot not in known_slots:
            issues.append(
                f"run-origin objective slot {origin_slot} is absent from the loaded "
                "objective calibration"
            )
        elif origin_slot not in measured_slots:
            issues.append(
                f"run-origin objective slot {origin_slot} carries only shipped placeholder "
                "values, not a calibration measured on this microscope; run and adopt the "
                "calibrate_objective_pair notebook"
            )
    return {
        "ready": not issues,
        "issues": issues,
        "active_objective_slot": slot,
        "origin_objective_slot": origin_slot,
        "orientation": orientation,
        "calibration": calibration,
    }


def _job_catalog(handle: ZmartHandle) -> tuple[list[dict], list[dict]]:
    """The live job catalog split into (normal, autofocus) jobs.

    Autofocus jobs are a separate category: they never appear as acquisition
    or state options and run only through the ``autofocus`` procedure.
    """
    jobs = _readers.get_jobs(handle.client) or []
    normal = [dict(j) for j in jobs if not j.get("IsAutofocus")]
    autofocus = [dict(j) for j in jobs if j.get("IsAutofocus")]
    return normal, autofocus


def set_origin(handle: ZmartHandle) -> dict:
    """Make the current stage position the (0, 0, 0) of the frame and save it.

    This is a setup step for the driver, not a controller command. The
    operator runs it once, working with the driver directly, for example::

        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import zmart_adapter as adapter
        handle = adapter.connect(adapter.CONNECTION)
        adapter.set_origin(handle)

    It reads stage XY, both z drives (z-wide and z-galvo), their sum (the
    focus) and the current objective, and records them as the zero point.
    The record is saved to this microscope's configuration as a new
    ``origin/<datetime>/origin.json``, and every later :func:`connect` loads
    the newest one, so all sessions share the same frame until the origin
    is captured again. The objective is saved with it so that a later
    objective change can be compensated with the calibration, or refused
    when no calibration can translate between the two objectives.

    The handle passed in starts using the new origin straight away.

    Returns:
        ``{"origin": {"x": 0, "y": 0, "z": 0}, "reference": <the captured
        stage values>, "origin_file": <path of the saved origin.json>}``.

    Raises:
        RuntimeError: when the position cannot be read, or when the file
            cannot be saved. In the second case this handle already uses the
            new origin, but the next connect would not, so fix the cause and
            run ``set_origin`` again.

    It is not checked against the limits, because it moves nothing: it only
    reads the current position and writes a small file. (``set_origin``
    stays in the ``limits.json`` ``functions`` list so machine files remain
    explicit about it.)
    """
    _require_open(handle)
    snap = _hardware_snapshot(handle)
    handle.origin = {
        "x_um": snap["x_um"],
        "y_um": snap["y_um"],
        "z_wide_um": snap["z_wide_um"],
        "z_galvo_um": snap["z_galvo_um"],
        "z_focus_um": snap["z_wide_um"] + snap["z_galvo_um"],
        "objective": snap["objective"],
    }
    payload = {
        "origin": handle.origin,
        "job": snap["job"],
        "session_hash6": handle.hash6,
        "captured_at": time.time(),
    }
    try:
        path = _machine.MACHINE.write_origin(payload)
    except OSError as exc:
        # This handle already uses the new origin, but the next connect will
        # load the previous saved one. Say so clearly, because otherwise the
        # two would disagree without anyone noticing.
        raise RuntimeError(
            f"could not save the origin: {exc}. This connection already uses the "
            f"new zero point, but the next connect will load the previously saved "
            f"origin. Fix the cause and run set_origin again."
        ) from exc
    return {
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "reference": dict(handle.origin),
        "origin_file": str(path),
    }


def get_actuators(handle: ZmartHandle) -> dict:
    """The motors that can move each axis, by name: exactly the names ``with_actuators`` accepts.

    ``{"x": ["motoric"], "y": ["motoric"], "z": ["z-wide", "z-galvo"]}``.
    These are also the names under which :func:`get_xyz` reports each
    motor's own reading. An axis left out of ``with_actuators`` uses the
    first motor listed here (x and y ``motoric``, z ``z-wide``); a previous
    call's choice is never remembered.
    """
    _require_open(handle)
    return {axis: list(opts) for axis, opts in _ACTUATORS.items()}


def _resolve_actuators(with_actuators: dict | None) -> dict[str, str]:
    """Merge a per-call actuator selection over the fixed defaults."""
    chosen = dict(_DEFAULT_ACTUATORS)
    for axis, actuator in (with_actuators or {}).items():
        if actuator not in _ACTUATORS.get(axis, ()):
            raise ValueError(f"unknown actuator {actuator!r} for axis {axis!r}")
        chosen[axis] = actuator
    return chosen


def get_xyz(handle: ZmartHandle, *, with_actuators: dict | None = None) -> dict:
    """Where the stage is, per axis: ``position``, ``unit``, ``actuators`` and ``canvas``.

    The answer has the keys ``x``, ``y`` and ``z``. Each axis carries the
    same four entries, and every number in them is in micrometres:

    - ``position``: where the axis is, measured from the saved origin. For
      ``z`` this is the *focus*: the sum of the two z drives (z-wide plus
      z-galvo) minus the origin's focus sum, so it reads the same whichever
      drive made the move. When the objective in place is not the one the
      origin was saved under, the calibrated objective translation ΔT is
      subtracted as well, so the position names the same point of the
      sample under either objective. (In short: position = fresh reading −
      origin − ΔT.) When no translation is available, the position is
      reported uncompensated and a warning is logged, because a read can
      never land the stage anywhere wrong.
    - ``unit``: always ``"micrometer"``.
    - ``actuators``: every motor of the axis with its own reading, exactly
      as LAS X reports it: ``{"motoric": ...}`` for x and y, ``{"z-wide":
      ..., "z-galvo": ...}`` for z. These are the stage's own numbers, with
      nothing subtracted, so they show how the two z drives share the focus.
    - ``canvas``: ``[min, max]``, everywhere a picture can show along the
      axis, in the same frame as ``position`` (see :func:`_canvas`). When
      no limits govern the session, so that every move is refused, the
      canvas is the current position alone.

    ``with_actuators`` only checks that the motors it names exist; the
    answer always reports all of them. The driver adds one extra on top,
    ``objective_translation_um``: the ΔT that was subtracted, as
    ``[x, y, z]``, so an operator can see when a compensation is in play.
    """
    _require_open(handle)
    _resolve_actuators(with_actuators)  # only to refuse an unknown motor name
    snap = _hardware_snapshot(handle)
    dt = _delta_or_warn(handle, snap)
    z_focus = snap["z_wide_um"] + snap["z_galvo_um"]
    frame = {
        "x": snap["x_um"] - handle.origin["x_um"] - dt[0],
        "y": snap["y_um"] - handle.origin["y_um"] - dt[1],
        "z": z_focus - handle.origin["z_focus_um"] - dt[2],
    }
    canvas = _canvas(handle) or {}
    result = {
        axis: {
            "position": frame[axis],
            "unit": UNIT,
            "actuators": {
                motor: snap[reading] for motor, reading in _MOTOR_READINGS[axis].items()
            },
            "canvas": canvas.get(f"{axis}_um") or [frame[axis], frame[axis]],
        }
        for axis in ("x", "y", "z")
    }
    result["objective_translation_um"] = list(dt)
    return result


def set_xyz(
    handle: ZmartHandle, x: float, y: float, z: float, *, with_actuators: dict | None = None
) -> dict:
    """Move to (x, y, z), in micrometres from the origin, then answer like :func:`get_xyz`.

    Once every leg of the move is confirmed, the position is read back from
    the microscope and returned in exactly the shape :func:`get_xyz` gives,
    so the answer shows where the stage really is, not the numbers that
    were asked for. A move that is refused or cannot be confirmed raises
    instead, because carrying on at an unknown position is never safe.

    Destination = origin + F + ΔT, commanded absolutely. Ordinary frame-Z
    movement uses the selected actuator, but objective-calibration ΔT.z always
    belongs to z-wide. With z-wide selected, its one target contains both the
    requested frame Z and ΔT.z while the galvo stays parked. With z-galvo
    selected across objectives, z-wide first receives the absolute calibrated
    objective offset and z-galvo then realizes only the requested frame Z::

        zwide_for_objective = origin_zwide + ΔT.z
        zgalvo_for_frame = origin_zgalvo + z

    ΔT compensates an objective change relative to the origin's objective
    (from the calibration's translation totals); a cross-objective move with
    no translations available REFUSES. The adapter does no limit checking of
    its own: every leg is checked inside the command that fires it, in the
    actions. A refused leg raises with an actionable alternative; legs that
    already ran stay where they arrived (each was checked too). A leg that
    was sent and accepted but could not be confirmed does not raise: it is
    recorded under the handle's ``unconfirmed`` list, and the answer is read
    back from the microscope anyway, so the workflow sees where the stage
    really is and decides for itself.
    """
    _require_open(handle)
    chosen = _resolve_actuators(with_actuators)
    snap = _hardware_snapshot(handle)
    dt = _objective_delta_um(handle, snap.get("objective"))  # raises if unavailable
    abs_x = handle.origin["x_um"] + x + dt[0]
    abs_y = handle.origin["y_um"] + y + dt[1]
    target_focus = handle.origin["z_focus_um"] + z + dt[2]
    z_targets: list[tuple[str, float]]
    if chosen["z"] == "z-wide":
        z_target = target_focus - snap["z_galvo_um"]
        z_targets = [("zwide", z_target)]
    elif dt[2] != 0.0:
        # Translation is an objective property, not a general focus move. Pin
        # its absolute contribution on z-wide so repeated set_xyz calls cannot
        # accumulate it, then let z-galvo realize only the requested frame Z.
        zwide_target = handle.origin["z_wide_um"] + dt[2]
        z_target = handle.origin["z_galvo_um"] + z
        z_targets = [("zwide", zwide_target), ("galvo", z_target)]
    else:
        z_target = target_focus - snap["z_wide_um"]
        z_targets = [("galvo", z_target)]

    # Every leg below is limit-checked inside the command it fires — the
    # adapter carries no checks of its own. A refused leg raises here with
    # an actionable message; legs that already ran stay where they arrived.
    arrived = _motion.arrive_xy(handle.client, abs_x, abs_y)
    if not arrived.get("confirmed"):
        _note_unconfirmed(handle, f"set_xyz: xy to ({x}, {y})", arrived)

    for z_mode, target in z_targets:
        z_result = _commands.move_z(
            handle.client,
            snap["job"],
            target,
            unit="um",
            z_mode=z_mode,
        )
        if not z_result.get("success"):
            # With z-galvo selected across objectives, the extra z-wide leg
            # is the calibrated objective offset — it can only ever be
            # realized by z-wide, so suggesting the other actuator would
            # send the operator in a circle. The hint is only useful for
            # the leg that carries the requested frame-Z motion.
            if len(z_targets) == 2 and z_mode == "zwide":
                raise RuntimeError(
                    f"move_z ({z_mode}) failed: {z_result} "
                    f"(this z-wide target is the calibrated objective offset, which "
                    f"cannot be moved to the other z drive — re-set the origin under "
                    f"the current objective, or re-adopt the objective calibration)"
                )
            alternative = "z-wide" if chosen["z"] == "z-galvo" else "z-galvo"
            raise RuntimeError(
                f"move_z ({z_mode}) failed: {z_result} "
                f"(try with_actuators={{'z': '{alternative}'}})"
            )
        if not z_result.get("confirmed"):
            _note_unconfirmed(handle, f"set_xyz: z ({z_mode}) to {target}", z_result)

    # Remembered for acquire, which labels every saved plane with the frame
    # position it was asked to go to (see _where_the_planes_are).
    handle.driven_to = {
        "x": x,
        "y": y,
        "z": z,
        "z_wide_um": next(
            (target for mode, target in z_targets if mode == "zwide"), snap["z_wide_um"]
        ),
    }
    # Read back from the microscope, so the answer is where the stage is now.
    return get_xyz(handle, with_actuators=with_actuators)


# =============================================================================
# Acquisition (captures and saves)
# =============================================================================


def get_acquisition_settings(handle: ZmartHandle) -> dict:
    """The acquisition and saving settings this instrument offers, with the active value of each.

    ``folder`` names the folder the pictures are saved in, and every file
    name starts with it. The Leica file names always carry one, so it cannot
    be empty; it defaults to ``"scan"`` and must be kebab-case lowercase (for
    example ``"overview"`` or ``"high-res"``), at most 25 characters.

    Discovered live on every call: ``job`` lists the LAS X jobs with the
    selected one active; ``cleanup_source`` is forwarded
    to the driver's ``save()``; ``backlash_correction`` runs an XY slack
    takeup before capture and optional ``backlash_rounds`` controls its pass
    count (default ``0``, which skips it).

    The scanning template is never touched: positions are never made in the
    Navigator Expert, so LAS X acquires the single current position.
    """
    _require_open(handle)
    normal, _ = _job_catalog(handle)
    names = [j["Name"] for j in normal if j.get("Name")]
    try:
        selected = _selected_job_name(handle)
    except RuntimeError:
        selected = None
    if selected not in names:
        selected = next((j["Name"] for j in normal if j.get("IsSelected")), None)
    return {
        "folder": {
            "options": "kebab-case lowercase text, at most 25 characters",
            "active": "scan",
        },
        "job": {"options": names, "active": selected},
        "backlash_correction": {"options": [True, False], "active": True},
        "backlash_rounds": {
            "options": "int >= 0",
            "active": ACQUISITION_BACKLASH_DEFAULT_ROUNDS,
        },
        "format": {"options": ["ome-tiff"], "active": "ome-tiff"},
        "cleanup_source": {"options": [True, False], "active": False},
    }


def _with_defaults(handle: ZmartHandle, settings: dict | None) -> dict:
    """Validate settings against the live menu, filling omissions from actives."""
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(f"unknown acquisition setting {name!r}")
        if name == "folder":
            # Checked here, before the scan fires, so a bad name never wastes a
            # capture. The rules themselves live in Naming, in one place.
            if not isinstance(value, str):
                raise ValueError(
                    f"acquisition setting 'folder' must be text, not {value!r}"
                )
            Naming(folder=value, hash6="000000", position_label="check")
            resolved[name] = value
            continue
        if name == "backlash_rounds":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    "invalid value for acquisition setting 'backlash_rounds': "
                    f"expected a non-negative integer, got {value!r}"
                )
            resolved[name] = value
            continue
        if value not in menu[name]["options"]:
            raise ValueError(
                f"invalid value {value!r} for acquisition setting {name!r} "
                f"(available: {menu[name]['options']!r})"
            )
        resolved[name] = value
    return resolved


def _next_acquisition_hash(handle: ZmartHandle) -> str:
    """Mint a unique driver-owned hash for one acquired position."""

    now = time.time()
    for offset in range(100):
        value = run_hash(now + offset)
        if value not in handle.acquisition_hashes:
            handle.acquisition_hashes.add(value)
            return value
    raise RuntimeError("could not mint a unique acquisition-position hash")


def _next_position_label(handle: ZmartHandle) -> str:
    """The next per-session position label ("000000", "000001", ...).

    Consumes one counter value. Only used when :func:`acquire` gets no
    explicit ``position_label``.
    """
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def _try(fn):
    """Call ``fn()``; degrade any failure to ``None`` (state must not fail save)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- provenance capture is best-effort
        log.debug("export-state field unavailable: %s", exc)
        return None


def _export_state(
    handle: ZmartHandle,
    *,
    folder: str,
    position_label: str,
    acquisition_hash: str,
    job: str,
) -> dict:
    """A JSON-serialisable snapshot of machine/software state at export time.

    Every field is captured best-effort: a missing reader degrades to
    ``None`` rather than failing the save. Embedded per-plane by ``save``.
    """
    machine_state = _try(lambda: get_state(handle))
    return {
        "software": {
            "driver_version": _DRIVER_VERSION,
            "client": handle.connection.get("client"),
            "api": "navigator-expert",
        },
        "hardware": _try(lambda: _readers.get_hardware_info(handle.client)),
        "job_settings": _try(lambda: _readers.get_job_settings(handle.client, job)),
        "job_state": (machine_state or {}).get("changeable") if machine_state else None,
        "position": _try(lambda: get_xyz(handle)),
        "provenance": {
            "folder": folder,
            "position_label": position_label,
            "job": job,
            "session_hash6": handle.hash6,
            "acquisition_hash": acquisition_hash,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def acquire(
    handle: ZmartHandle,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
) -> dict:
    """Run the job, wait for the export, and persist the OME-TIFF product.

    Args:
        position_label: Free text naming this position; sanitized into the
            filename by ``Naming``. When omitted, the next per-session
            counter value ("000000", "000001", ...) is used (an explicit
            label does NOT consume a counter value). The label travels
            verbatim in the lineage record ``save()`` writes to
            ``summary.json`` and in the embedded per-plane state.
        acquisition_settings: Values from :func:`get_acquisition_settings`;
            settings left out use the active defaults, unknown keys/values
            raise. ``folder`` names the output folder and starts every file
            name, so it must be kebab-case lowercase (``Naming`` raises a
            clear ``ValueError`` otherwise). It defaults to ``"scan"``.

    The driver's helper mints a fresh acquisition-position hash for this
    capture. The folder itself has no hash. The session hash
    (``handle.hash6``) rides along only in lineage/provenance. The machine/software state is captured
    and embedded in each saved plane's OME-XML (no sidecar).

    Returns a record with the resolved job and settings, ``files`` (every file
    saved) and ``planes`` (which file holds which channel, z and t, and where
    on the sample it was taken).
    Raises on any unrecoverable step — job selection, capture, export
    detection, or persistence.
    """
    _require_open(handle)
    workflow_root = _info.output_root(handle, _save.save_source_root)
    output_root = workflow_root / ".staging" / handle.hash6
    resolved = _with_defaults(handle, acquisition_settings)
    folder = resolved["folder"]
    job = resolved["job"]
    if not job:
        raise RuntimeError(
            "no LAS X job selected and none passed via acquisition_settings['job']"
        )

    noted_before = len(handle.unconfirmed)
    # When the selected job cannot be read, select the one asked for anyway:
    # selecting is harmless to repeat, and a reading that fails is unknown,
    # not a reason to stop.
    if job != _selected_job_name(handle):
        select = _commands.select_job(handle.client, job)
        if not select.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {select}")
        if not select.get("confirmed"):
            _note_unconfirmed(handle, f"acquire: select_job('{job}')", select)

    backlash_rounds = resolved["backlash_rounds"]
    apply_backlash = resolved["backlash_correction"] and backlash_rounds > 0
    if apply_backlash:
        settled = _motion.correct_backlash(handle.client, passes=backlash_rounds)
        if not settled.get("confirmed"):
            _note_unconfirmed(handle, "acquire: backlash takeup", settled)

    acq = _capture.acquire(handle.client, job)

    label = position_label if position_label is not None else _next_position_label(handle)
    acquisition_hash = _next_acquisition_hash(handle)
    naming = Naming(
        folder=folder,
        hash6=acquisition_hash,
        position_label=label,
    )
    state = _export_state(
        handle,
        folder=folder,
        position_label=label,
        acquisition_hash=acquisition_hash,
        job=job,
    )
    saved = _save.save(
        handle.client,
        acq,
        output_root,
        naming,
        lineage={
            "folder": folder,
            "position_label": label,
            "job": job,
            "session_hash6": handle.hash6,
            "acquisition_hash": acquisition_hash,
        },
        state=state,
        # Rig image->stage orientation, applied to the saved planes behind the
        # scenes so the workflow only ever sees stage-aligned images. Loaded once
        # at connect (from the set_orientation setup notebook's file); a separate
        # concern from pixel-scale calibration and limits.
        orientation=_loaded_orientation(handle),
        cleanup_source=resolved["cleanup_source"],
    )

    written = sorted(saved.image_paths.items())
    taken_at = _where_the_planes_are(handle, job, len(written))
    planes = [
        {
            "t": int(getattr(index, "t", 0)),
            "z": int(getattr(index, "z", 0)),
            "c": int(getattr(index, "c", ordinal)),
            # Where the pixels are, and where inside them. A flat OME-TIFF
            # holds exactly the one plane t/c/z name; were this a store, the
            # same three would index into it and only the path would change.
            "path": str(path),
            **taken_at(ordinal),
        }
        for ordinal, (index, path) in enumerate(written)
    ]
    vendor_metadata = [str(path) for path in getattr(saved, "vendor_metadata_paths", ())]
    printed = [str(path) for path in getattr(saved, "state_paths", ())]
    return {
        "position_label": label,
        "folder": folder,
        "job": job,
        "format": resolved["format"],
        "acquisition_hash": acquisition_hash,
        "settle": "backlash-corrected" if apply_backlash else "direct",
        "backlash_rounds": backlash_rounds if apply_backlash else 0,
        # Every file this acquisition saved, under the name the ZMART
        # Controller's contract fixes, so a workflow finds them on any
        # microscope: the images, then the vendor's metadata, then the state.
        "files": list(dict.fromkeys([*(plane["path"] for plane in planes), *vendor_metadata, *printed])),
        # The manifest a workflow needs to tell channels from z and t, and
        # where on the sample each plane was taken.
        "planes": planes,
        "vendor_metadata": vendor_metadata,
        # What the driver printed about this capture: the state, beside the
        # images in ``data/metadata``. A client moving a record moves this too.
        "metadata": printed,
        # The changes made for this capture that were sent but never
        # confirmed by a readback (the job selection, the backlash takeup).
        # Empty when everything confirmed. The capture itself went ahead.
        "unconfirmed": list(handle.unconfirmed[noted_before:]),
    }


# =============================================================================
# State and procedures
# =============================================================================


def _where_the_planes_are(handle: ZmartHandle, job: str, count: int):
    """Answer, per plane, where on the sample it was captured.

    In the frame :func:`set_xyz` takes, so a position handed back can be
    handed straight back in. A saved file cannot say this and neither can the
    drive alone: the job states its stack in absolute z-wide, so each height
    is the frame the drive was sent to, displaced by how far that slice sits
    from the z-wide the drive was realized at. Nothing is assumed about where
    in its stack the drive stands. No stack, or one that does not describe the
    planes that came back, leaves every plane at the drive's height -- a
    guessed spread is worse than none.
    """
    at = handle.driven_to
    slices = _readers.stack_z_wide_um(
        _readers.get_job_settings(handle.client, job) or {}, count
    )
    def where(ordinal: int) -> dict:
        if at is None:
            return {"x_um": None, "y_um": None, "z_um": None}
        height = at["z"]
        if slices is not None:
            height += slices[ordinal] - at["z_wide_um"]
        return {"x_um": at["x"], "y_um": at["y"], "z_um": height}

    return where


def get_state(handle: ZmartHandle) -> dict:
    """The instrument state: the changeable part first, then the observed report.

    ``changeable`` is the promise — exactly what :func:`set_state` reapplies.
    It is deliberately just the selected job: the job *is* the unit of
    configuration in LAS X, so reapplying the selection round-trips the whole
    setup. ``observed`` is a read-only report of identity and condition — the
    connection identity, the hardware-reported serial number and system type
    (simulator vs. real), the stand, the turret configuration (slot →
    objectiveNumber), the full selected-job record, its current XY pixel size,
    the job catalog, and the provenance of the function limits governing this
    session. All LAS X-derived values are fresh reads.
    """
    _require_open(handle)
    hw = _readers.get_hardware_info(handle.client) or {}
    microscope = hw.get("Microscope") or {}
    selected = _readers.get_selected_job(handle.client) or {}
    if not selected.get("Name"):
        # A reading that fails is unknown, not a stop: the job is reported
        # as None, and set_state with a job name will select it afresh.
        log.warning("could not determine the selected LAS X job")
        selected = {}
    settings = (
        _readers.get_job_settings(handle.client, selected["Name"]) if selected.get("Name") else {}
    ) or {}
    try:
        geometry = _parse_tile_geometry(settings)
        pixel_x = float(geometry["pixel_w_um"])
        pixel_y = float(geometry["pixel_h_um"])
        if not all(math.isfinite(value) and value > 0 for value in (pixel_x, pixel_y)):
            raise ValueError("pixel size must be finite and positive")
        pixel_size = {
            "x": pixel_x,
            "y": pixel_y,
            "unit": "um",
        }
        # The field of view, from the job's imageSize: parsed here anyway to
        # get the pixel size, and the only measured answer to how much sample
        # one frame covers.
        frame_size = {
            "x": float(geometry["tile_w_um"]),
            "y": float(geometry["tile_h_um"]),
            "unit": "um",
        }
    except (KeyError, TypeError, ValueError):
        pixel_size = None
        frame_size = None
    active_objective = settings.get("objective")
    normal, autofocus = _job_catalog(handle)
    limits = _gate.describe(handle.client)
    return {
        "changeable": {"job": selected.get("Name")},
        "observed": {
            "vendor": "leica",
            "microscope": handle.connection.get("microscope"),
            "serial_number": hw.get("SerialNumber"),
            "system_type": hw.get("SystemType"),
            "stand": microscope.get("name"),
            "objectives": [
                [o.get("slotIndex"), o.get("objectiveNumber")]
                for o in (microscope.get("objectives") or [])
            ],
            "job": dict(selected),
            "active_objective": dict(active_objective or {}),
            "pixel_size": pixel_size,
            "frame_size": frame_size,
            "jobs": normal,
            "autofocus_jobs": autofocus,
            # Which function-limits file governs this session (evidence, not
            # an instruction): path, source tag, is_fallback — reported by
            # the commands-layer gate. None when the limits handshake failed
            # (every mutating command underneath is then refusing).
            "limits": limits,
            "setup": _setup_readiness(handle, active_objective, limits),
            # Changes this session sent that were accepted but never
            # confirmed by a readback, newest last. The driver carried on;
            # a workflow that cares can look here.
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def set_state(handle: ZmartHandle, state: dict) -> dict:
    """Apply the changeable part; report what stuck.

    ``observed`` is a report, never an instruction — it is not read here
    (operator decision, 2026-07-02; an identity gate returns only if the
    changeable part ever grows beyond the low-risk job selection). The job
    must still exist on this instrument before it is reapplied: the catalog
    changes legitimately, so only the REFERENT is guarded — with an error
    that lists what is available.
    """
    _require_open(handle)
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)
    job = (state.get("changeable") or {}).get("job")
    # When the selected job cannot be read, the one asked for is selected
    # anyway: selecting is harmless to repeat.
    if job and job != _selected_job_name(handle):
        normal, autofocus = _job_catalog(handle)
        names = [j.get("Name") for j in normal if j.get("Name")]
        if job in (j.get("Name") for j in autofocus):
            raise ValueError(
                f"{job!r} is an autofocus job — run it via the 'autofocus' "
                "procedure (run_procedure), not as state"
            )
        if job not in names:
            raise ValueError(
                f"job {job!r} no longer exists on this instrument (available: {names})"
            )
        result = _commands.select_job(handle.client, job)
        if not result.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {result}")
        if not result.get("confirmed"):
            _note_unconfirmed(handle, f"set_state: select_job('{job}')", result)
        applied["job"] = job
    # What was applied, and what was sent but could not be confirmed by a
    # readback (the selection went ahead; the workflow is told, not stopped).
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


def get_procedures(handle: ZmartHandle) -> dict:
    """The named procedures this instrument offers (discover-then-apply)."""
    _require_open(handle)
    _, autofocus = _job_catalog(handle)
    return {
        "backlash_takeup": {
            "description": "pin the XY leadscrew slack at the current position (+X +Y approach)"
        },
        "zero_z_galvo": {
            "description": "park the z-galvo at 0 without changing the focus "
            "(its offset is transferred onto z-wide), freeing the galvo's "
            "full travel for a following z-stack"
        },
        "autofocus": {
            "description": "run a LAS X autofocus job (capture only, nothing saved); "
            "restores the previously selected job and returns the focus readback",
            "args": ["job"],
            "jobs": [j["Name"] for j in autofocus if j.get("Name")],
        },
    }


def run_procedure(handle: ZmartHandle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; report what ran."""
    _require_open(handle)
    name = procedure.get("name")
    if name == "backlash_takeup":
        _motion.correct_backlash(handle.client)
        return {"ran": dict(procedure)}
    if name == "zero_z_galvo":
        return {"ran": dict(procedure), **_zero_z_galvo(handle)}
    if name == "autofocus":
        return _run_autofocus(handle, procedure)
    raise ValueError(f"unknown procedure {name!r}")


def _zero_z_galvo(handle: ZmartHandle) -> dict:
    """Park the z-galvo at 0 while keeping the focus.

    A pure re-split of the focus sum between the two drives: z-wide
    absorbs the galvo's current offset FIRST — so a refused z-wide leg
    (limits) aborts with nothing moved — and only then does the galvo
    drive to 0. The frame z is unchanged by construction, and the galvo's
    full travel becomes available for whatever comes next. Both legs go
    through the checked ``move_z`` door; a refused or failed leg raises, a
    leg that could not be confirmed is recorded and the procedure carries on.
    """
    snap = _hardware_snapshot(handle)
    offset = snap["z_galvo_um"]
    if offset == 0.0:
        return {"transferred_um": 0.0}
    for z_mode, target in (("zwide", snap["z_wide_um"] + offset), ("galvo", 0.0)):
        result = _commands.move_z(handle.client, snap["job"], target, unit="um", z_mode=z_mode)
        if not result.get("success"):
            raise RuntimeError(f"zero_z_galvo: move_z ({z_mode}) failed: {result}")
        if not result.get("confirmed"):
            _note_unconfirmed(handle, f"zero_z_galvo: move_z ({z_mode}) to {target}", result)
    return {"transferred_um": offset}


def _run_autofocus(handle: ZmartHandle, procedure: dict) -> dict:
    """Select the autofocus job, run it capture-only, restore the selection.

    ``job`` names the autofocus job; it may be omitted when the instrument
    has exactly one. Nothing is saved — the result is the focus readback
    right after the run (before the selection is restored), in both
    hardware and frame terms. Like every capture it never touches the
    scanning template: positions are never made in the Navigator Expert.
    """
    _, autofocus = _job_catalog(handle)
    names = [j["Name"] for j in autofocus if j.get("Name")]
    if not names:
        raise RuntimeError("no autofocus job exists on this instrument")
    job = procedure.get("job")
    if job is None:
        if len(names) > 1:
            raise ValueError(f"multiple autofocus jobs; pass 'job' (available: {names})")
        job = names[0]
    elif job not in names:
        raise ValueError(f"{job!r} is not an autofocus job (available: {names})")

    original = _selected_job_name(handle)
    if job != original:
        selected = _commands.select_job(handle.client, job)
        if not selected.get("success"):
            raise RuntimeError(f"select_job('{job}') failed: {selected}")
        if not selected.get("confirmed"):
            _note_unconfirmed(handle, f"autofocus: select_job('{job}')", selected)
    try:
        acq = _capture.acquire(handle.client, job)
        # Read the focus result BEFORE restoring the selection: restoring
        # could reposition (jobs own objective state).
        snap = _hardware_snapshot(handle)
    finally:
        if original is None:
            log.warning("the job selected before autofocus was unknown, so none is restored")
        elif job != original:
            restored = _commands.select_job(handle.client, original)
            if not restored.get("success"):
                log.warning("could not restore job %r after autofocus: %s", original, restored)
            elif not restored.get("confirmed"):
                _note_unconfirmed(handle, f"autofocus: restore select_job('{original}')", restored)
    focus = snap["z_wide_um"] + snap["z_galvo_um"]
    dt = _delta_or_warn(handle, snap)
    return {
        "ran": "autofocus",
        "job": job,
        "focus_um": focus,
        "frame_z_um": focus - handle.origin["z_focus_um"] - dt[2],
        "duration_s": acq.finished_at - acq.started_at,
    }


# =============================================================================
# Live setup information and registration
# =============================================================================


def _scan_field(handle: ZmartHandle, *, default_job_name: str) -> dict | None:
    """Positions and focus points the operator stored in the scanning template.

    Saves the experiment first (the parsers read the on-disk template; a
    load alone does not flush it), then reports every stored position as a
    typed entry in BOTH coordinate spaces — grid positions name the region
    group they belong to. Template z values are treated as focus positions
    (the same convention as the frame's z axis). None when this machine has
    no LAS X scanning-templates profile.

    Read this BEFORE acquiring: the default ``strip_scan_fields``
    acquisition setting empties the template.
    """
    templates_dir = _scanfields.find_scanning_templates_dir()
    if templates_dir is None:
        return None
    saved = _scanfields.save_experiment(
        handle.client,
        _scanfields.TEMPLATE_XML,
        templates_dir,
        timeout=EXPERIMENT_FLUSH_TIMEOUT_S,
        confirm_path=Path(templates_dir) / _scanfields.TEMPLATE_RGN,
    )
    if not saved:
        # The template on disk may be stale, so its positions are not
        # reported rather than reported wrong; get_info still answers.
        _note_unconfirmed(handle, "get_info: save_experiment (scan-field positions)", saved)
        return None
    parsed = _scanfields.parse_scan_positions(
        templates_dir,
        _scanfields.TEMPLATE_BASE,
        client=handle.client,
        default_job_name=default_job_name,
    )
    dt = _delta_or_warn(handle, _hardware_snapshot(handle))
    origin = handle.origin

    def entry(kind: str, x_um: float, y_um: float, z_um: float | None, **meta: Any) -> dict:
        return {
            "kind": kind,
            "frame": {
                "x_um": x_um - origin["x_um"] - dt[0],
                "y_um": y_um - origin["y_um"] - dt[1],
                "z_um": None if z_um is None else z_um - origin["z_focus_um"] - dt[2],
            },
            "stage": {"x_um": x_um, "y_um": y_um, "z_um": z_um},
            **meta,
        }

    positions = []
    for region_key, region in (parsed.get("acquisition_positions") or {}).items():
        geometry_id = region.get("geometry_id")
        geometry = (parsed.get("geometries") or {}).get(geometry_id, {})
        # Unassigned LAS X point shapes are operator markers.  They are
        # exposed below as ``marker`` entries, never duplicated as tiles.
        if region.get("source") == "geometry_plan" and geometry.get("type") == "Point":
            continue
        for tile in region.get("positions") or []:
            positions.append(
                entry(
                    "grid",
                    tile["x_um"],
                    tile["y_um"],
                    tile.get("z_um"),
                    group={"region": region_key, "row": tile.get("row"), "col": tile.get("col")},
                    job=region.get("job_name"),
                    tile_size={
                        "x": region.get("tile_size_um"),
                        "y": region.get("tile_size_um"),
                    },
                )
            )
    for kind, points in (
        ("focus-point", parsed.get("focus_points") or []),
        ("autofocus-point", parsed.get("autofocus_points") or []),
    ):
        for point in points:
            positions.append(
                entry(
                    kind,
                    point["x_um"],
                    point["y_um"],
                    point.get("z_um"),
                    id=point.get("identifier"),
                    enabled=point.get("enabled", True),
                )
            )
    for geometry in (parsed.get("geometries") or {}).values():
        center = geometry.get("center_um") or {}
        if geometry.get("type") == "Point" and center.get("x_um") is not None:
            positions.append(
                entry("marker", center["x_um"], center["y_um"], None, label=geometry.get("label"))
            )
    return {
        "coordinate_spaces": {
            "frame": "um from the origin, objective-compensated (what set_xyz accepts)",
            "stage": "absolute stage um (what LAS X stores)",
        },
        "template_state": _scanfields.get_template_state(templates_dir),
        "positions": positions,
    }


def get_info(handle: ZmartHandle) -> dict:
    """Read the operator-authored setup and resolved output root live.

    ``tile_positions`` are the tiles currently placed in the LAS X scanning
    template, not the microscope's physical position (read that with
    :func:`get_xyz`). Reading the template flushes the live experiment to
    disk before parsing it, so this truthful snapshot can block briefly and
    raises when a configured template cannot be read safely.
    """
    _require_open(handle)
    selected = _selected_job_name(handle)
    scan_field = _scan_field(handle, default_job_name=selected)
    root = _info.output_root(handle, _save.save_source_root)
    return {
        "output_root": str(root),
        "description": _described(handle),
        "selected_job": selected,
        "tile_positions": _info.tile_positions(scan_field),
        "focus_positions": _info.focus_positions(scan_field),
        "client": handle.connection.get("client"),
        "session_hash6": handle.hash6,
        "canvas": _canvas(handle),
        "connection_status": _connection_status(handle, root),
    }


# The microscope in plain words, for whoever drives it: a person, a notebook,
# or the ZMART AI agent, which builds its picture of the instrument from this.
# It says what the other answers cannot. The parts in braces are filled in by
# _described() from what this session has loaded and what LAS X reports, so
# the description never promises more than the driver allows.
DESCRIPTION = """A Leica STELLARIS 5 confocal microscope, driven through LAS X and its Navigator Expert (CAM) interface.

Stage: x and y move the motorised stage; z is the focus. The focus has two drives: "z-wide", the focus drive for long moves, and "z-galvo", a fast drive for small steps. The z position reported is their sum, so it reads the same whichever drive made the move. All positions are in micrometres from the origin saved in this microscope's setup (plain stage coordinates until one is saved). When the objective changes, the driver applies the objective calibration, so the origin stays on the same spot of the sample. {galvo}

Settings (the changeable part of the state): job is the name of a LAS X job. A job holds the whole imaging setup: objective, lasers, detectors, zoom and z-stack. Choosing a job applies all of it at once; set_state refuses a job that does not exist. {jobs}

Objectives on the turret, as slot: LAS X objective number: {objectives}.

Acquiring runs a LAS X job and saves what LAS X captured under the output folder, in the folder named by the folder setting ("scan" unless given), with files named by that folder and the position label."""


def _described(handle: ZmartHandle) -> str:
    """The description, filled in from the loaded limits and what LAS X reports now."""
    state = _gate.state_for(handle.client)
    galvo_range = None
    if state is not None and state.stage_cfg is not None:
        galvo_range = state.stage_cfg["stage_um"].get("z_galvo")
    galvo = (
        f"This microscope's limits let the z-galvo travel from {galvo_range[0]:g} to "
        f"{galvo_range[1]:g} um around its own zero."
        if galvo_range
        else "No limits are loaded in this session, so every move is refused."
    )
    normal, autofocus = _job_catalog(handle)
    names = [j["Name"] for j in normal if j.get("Name")]
    af_names = [j["Name"] for j in autofocus if j.get("Name")]
    jobs = f"The jobs on this microscope now: {', '.join(names) or 'none'}."
    if af_names:
        jobs += (
            f" The autofocus jobs ({', '.join(af_names)}) are not settings; run them "
            "with the autofocus procedure."
        )
    hardware = _readers.get_hardware_info(handle.client) or {}
    turret = (hardware.get("Microscope") or {}).get("objectives") or []
    objectives = (
        ", ".join(f"{o.get('slotIndex')}: {o.get('objectiveNumber')}" for o in turret)
        or "not reported by LAS X"
    )
    return DESCRIPTION.format(galvo=galvo, jobs=jobs, objectives=objectives)


def _canvas(handle: ZmartHandle) -> dict | None:
    """Everywhere a picture can show, per axis, in the frame. None when no limits govern the session.

    On z the frame follows the focus, the sum of the two z drives, so the
    canvas is the z-wide travel widened by the z-galvo travel: a z-stack on
    the galvo reaches no further than the galvo can go. On x and y the
    canvas is the stage travel itself. A picture taken at the edge of the
    travel does show half a field beyond it, but the field depends on the
    objective and the zoom, and LAS X only reports it for the objective in
    place now, so the driver cannot know the widest field of every allowed
    objective ahead of time and does not widen x and y.
    """
    state = _gate.state_for(handle.client)
    if state is None or state.stage_cfg is None:
        return None
    stage = state.stage_cfg["stage_um"]
    galvo = stage.get("z_galvo") or [0.0, 0.0]
    origin = handle.origin
    return {
        "x_um": [stage["x"][0] - origin["x_um"], stage["x"][1] - origin["x_um"]],
        "y_um": [stage["y"][0] - origin["y_um"], stage["y"][1] - origin["y_um"]],
        "z_um": [
            stage["z_wide"][0] + galvo[0] - origin["z_focus_um"],
            stage["z_wide"][1] + galvo[1] - origin["z_focus_um"],
        ],
    }


def _connection_status(handle: ZmartHandle, root: Path) -> dict:
    """What a session stands on, from what the driver already holds; a
    failure names itself."""
    limits = _gate.describe(handle.client)
    loaded = _session_state.get(handle.client)
    calibration = loaded.calibration_info if loaded is not None else None

    def stage() -> str:
        at = get_xyz(handle)
        return (
            f"x {at['x']['position']:.0f} · y {at['y']['position']:.0f} · "
            f"z {at['z']['position']:.1f} um"
        )

    return {
        "api": "answering" if _try(lambda: _readers.ping(handle.client)) else "failed — no answer",
        "limits": (
            f"{limits['source']}{' (fallback)' if limits.get('is_fallback') else ''}"
            if limits else "failed — none loaded, every move refused"
        ),
        "calibration": (
            calibration.get("name") or "found" if calibration else "failed — none found"
        ),
        "stage": _try(stage) or "failed — no reading",
        "autosave": (
            "enabled" if _try(_save.native_autosave_enabled)
            else "failed — LAS X will not save what is captured"
        ),
        "output root": str(root),
    }


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
    module return only the content. Each of them raises when something goes
    wrong, so an answer that comes back at all is a success, and the content
    is exactly what the function returned.
    """

    @functools.wraps(function)
    def command(*args, **kwargs):
        return {"success": True, "content": function(*args, **kwargs)}

    return command


def ops_table() -> dict[str, Any]:
    """The functions this driver hands to the controller, one per command.

    ``connect`` and ``disconnect`` are handed over unchanged. Every other
    command is wrapped so that, called through the controller, it answers
    ``{"success": True, "content": ...}``. Called directly from this module,
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
