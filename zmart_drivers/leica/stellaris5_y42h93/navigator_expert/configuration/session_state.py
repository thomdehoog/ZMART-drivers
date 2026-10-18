"""Per-connection driver configuration: the orientation and calibration loaded
when the driver connects to the microscope.

The limits gate keeps its own client-keyed registry
(:mod:`navigator_expert.commands.gate`). This module is its sibling for the
other two machine-local configs the driver loads at connect time: how the
camera is turned relative to the stage (``orientation``) and the per-objective
translation offsets that keep coordinates consistent when you switch lenses
(``translations``).

Both are read once, at connect, so a single connection carries one consistent
picture of the microscope for its whole life — rather than every saved image
re-reading files that might have changed underneath it. Re-measuring a config
takes effect on the next connection.

Single instrument per process, exactly like the gate: the registry is keyed by
``id(client)`` and holds a strong reference to the client, so a client's id can
never be recycled onto a different, never-loaded client.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .image_stage_registration import Orientation

_log = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    """The orientation and calibration a connection loaded at connect time.

    ``orientation`` is the measured camera turn applied to saved images (the
    identity "no turn" when orientation was not loaded). ``translations`` maps
    each objective slot to its ``(x, y, z)`` offset in micrometres, or is
    ``None`` when calibration was not loaded — in which case the frame math
    refuses cross-objective moves rather than computing uncompensated values.
    ``calibration_name`` records which named calibration set was selected.
    ``orientation_info`` and ``calibration_info`` are immutable provenance
    captured at connect; the adapter uses them to issue its driver-owned
    readiness verdict without making workflows interpret configuration files.
    """

    orientation: Orientation
    translations: dict | None = None
    calibration_name: str | None = None
    orientation_info: dict | None = None
    calibration_info: dict | None = None


# id(client) -> (client, SessionConfig). The client reference is deliberately
# strong so a garbage-collected client's id can never be recycled onto a new,
# never-loaded client that would then inherit stale config. CAM clients live
# for the process, so this is not a leak in practice.
_STATE: dict[int, tuple[Any, SessionConfig]] = {}


def install(client: Any, config: SessionConfig) -> None:
    """Record the config loaded for *client* (rebinds on a re-connect)."""
    _STATE[id(client)] = (client, config)


def get(client: Any) -> SessionConfig | None:
    """The config loaded for *client*, or None when the driver never loaded it."""
    entry = _STATE.get(id(client))
    return entry[1] if entry is not None else None


def uninstall(client: Any) -> None:
    """Drop a client's loaded config (e.g. on disconnect)."""
    _STATE.pop(id(client), None)


# =============================================================================
# Loading what a session needs at connect
# =============================================================================


def load_objective_calibration(
    calibration_name: str | None = None,
    *,
    enabled: bool = True,
) -> tuple[dict | None, dict]:
    """Load translations and readiness provenance from one exact document.

    Resolving/loading twice can straddle an atomic snapshot adoption: old
    translations could otherwise be paired with new ``measured_slots`` and be
    reported ready. This function deliberately resolves one path, parses it
    once, and derives both outputs from that immutable in-memory config.
    """
    # This import must stay inside the function: the package root imports this
    # module when the driver loads, and the calibration modules import the
    # package root — a module-level import here would be a circular import.
    effective_name = calibration_name
    empty_info = {
        "enabled": enabled,
        "loaded": False,
        "name": effective_name,
        "path": None,
        "slots": [],
        "measured_slots": [],
    }
    if not enabled:
        return None, empty_info

    from .optical_calibration import model as _cal_model

    path = None
    try:
        path = _cal_model.default_path(calibration_name).absolute()
        config = _cal_model.load_calibration(path)
        translations = {
            int(slot): _cal_model.get_translation_um(config, int(slot))
            for slot, entry in (config.get("objectives") or {}).items()
            if entry.get("translation_um") is not None
        }
    except Exception as exc:  # noqa: BLE001 -- config IO / schema; degrade, don't crash connect
        _log.warning(
            "objective translations unavailable (%s); objective changes and "
            "cross-objective moves will be refused",
            exc,
        )
        empty_info["path"] = None if path is None else str(path)
        # {} (not None): this session WANTED calibration but none is usable,
        # so lens swaps refuse fail-closed. None is reserved for the explicit
        # opt-out (enabled=False), where swaps proceed bare instead.
        return {}, empty_info

    info = {
        "enabled": True,
        "loaded": True,
        "name": effective_name,
        "path": str(path),
        "slots": sorted(translations),
        "measured_slots": sorted(
            int(slot)
            for slot, entry in (config.get("objectives") or {}).items()
            if entry.get("translation_um") is not None
        ),
    }
    return translations, info


def load_rig_orientation() -> tuple[Any, dict]:
    """Load orientation and readiness evidence from one exact document.

    A single resolve/read supplies both the orientation used for images and the
    provenance used by preflight, so snapshot adoption cannot mix old geometry
    with new readiness evidence. Invalid or disabled configuration returns the
    identity mapping with an explicit not-ready record.
    """
    from ..vendor_interface.primitive_handling.profiles import IMAGE_SAVE
    from . import image_stage_registration as _orientation
    from .store import MACHINE

    identity = _orientation.Orientation()
    if not IMAGE_SAVE.apply_orientation:
        return identity, {
            "enabled": False,
            "loaded": False,
            "measured": False,
            "path": None,
            "rotate_deg": int(identity.rotate_deg),
            "mirrored": identity.mirrored,
        }

    path = None
    try:
        path = MACHINE.orientation_path().absolute()
        data = json.loads(path.read_text(encoding="utf-8"))
        # Match orientation.load_orientation's validation without reading the
        # file a second time: readiness evidence and runtime geometry must come
        # from this same in-memory document.
        orientation = _orientation.orientation_from_config(data)
    except Exception as exc:  # noqa: BLE001 -- config IO / schema; degrade, don't crash connect
        _log.warning(
            "orientation unavailable (%s); saved images will NOT be corrected to the "
            "stage axes this session. Re-publish orientation.json with "
            "configuration/image_stage_registration/notebooks/set_orientation.ipynb and reconnect.",
            exc,
        )
        return identity, {
            "enabled": True,
            "loaded": False,
            "measured": False,
            "path": None if path is None else str(path),
            "rotate_deg": int(identity.rotate_deg),
            "mirrored": identity.mirrored,
            "error": str(exc),
        }
    return orientation, {
        "enabled": True,
        "loaded": True,
        # A real measured 0-degree orientation is valid. The explicit marker,
        # not the angle, distinguishes it from the shipped identity placeholder.
        "measured": data.get("measured") is True,
        "path": str(path),
        "rotate_deg": int(orientation.rotate_deg),
        "mirrored": orientation.mirrored,
        "axis_signs": orientation.axis_signs,
        "axis_mapping": orientation.axis_mapping,
        "image_to_stage": [list(row) for row in orientation.image_to_stage],
    }
