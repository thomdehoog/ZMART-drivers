"""Taking a picture: the ``acquire`` and ``tile_scan`` steps, and the note each keeps for saving (:class:`PendingCapture`).

``acquire`` takes one picture with the current settings (the window's
Capture Image) and ``tile_scan`` one picture of several fields (the window's
Preview over a list of positions). Both leave the picture in LAS X's cache,
write no files and clear nothing. Instead they note what only the moment of
the capture can tell (where the stage stood, the settings, the state of the
microscope) and keep the note with the session, as a :class:`PendingCapture`.
Saving the files is the ``save_data`` procedure, and letting LAS X go of the
picture is the ``clear_cache`` procedure.

A change that was sent but could not be confirmed is recorded and the capture
carries on, so the workflow is told rather than stopped.

The operator may move the stage or change the objective in LAS X between two
calls of the workflow, and the driver cannot see that as it happens. So
``acquire`` reads the stage once, just before the capture, and places every
plane from that reading. If it differs from what the workflow last saw, a note
is added to the result, and the capture goes ahead.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # the driver's version, for the capture record; never fail a capture over it
    from .. import __version__ as _DRIVER_VERSION
except Exception:  # noqa: BLE001 -- best-effort provenance
    _DRIVER_VERSION = None

from . import changes
from . import readings as _readers
from .absolute_coordinate_system import get_xyz, objective_delta_um, stage_target
from .save_data.naming import Naming, free_stem
from .state import get_state

#: This driver, as the capture record names it.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

log = logging.getLogger(__name__)

#: Between two fields of a row of tiles: a field and a tenth, so the fields do not overlap.
TILE_SPACING = 1.1


def try_(fn):
    """Call ``fn()``; degrade any failure to ``None`` (state must not fail a save)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- provenance capture is best-effort
        log.debug("export-state field unavailable: %s", exc)
        return None


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``full``, one picture with the current settings.

    That is the only way the controller's ``acquire`` captures, so it is the
    only option and the default. A tile scan is its own call
    (``Microscope.tile_scan``). How the pictures are saved comes from this
    microscope's profile (``IMAGE_SAVE``), and where they go from its data
    location, set up once.
    """
    handle.require_open()
    return {"trigger": {"options": ["full"], "default": "full", "active": "full"}}


def _with_defaults(handle, settings: dict | None) -> dict:
    """Validate settings against the menu, filling omissions from the active values."""
    menu = get_acquisition_settings(handle)
    resolved = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (settings or {}).items():
        if name not in menu:
            raise ValueError(f"unknown acquisition setting {name!r}")
        if value not in menu[name]["options"]:
            raise ValueError(
                f"invalid value {value!r} for acquisition setting {name!r} (available: {menu[name]['options']!r})"
            )
        resolved[name] = value
    return resolved


def _next_position_label(handle) -> str:
    """The next per-session position label ("000000", "000001", ...); consumes one counter value."""
    label = f"{handle.position_counter:06d}"
    handle.position_counter += 1
    return label


def current_settings(handle) -> dict:
    """What LAS X is set to at the moment of a capture, each reading best-effort."""
    client = handle.client
    return {
        name: try_(lambda read=read: read(client))
        for name, read in (
            ("zoom", _readers.get_zoom),
            ("scan_speed", _readers.get_scan_speed),
            ("scan_field_rotation", _readers.get_scan_field_rotation),
            ("averaging", _readers.get_averaging),
            ("pinhole", _readers.get_pinhole),
            ("format", _readers.get_format),
            ("scan_mode", _readers.get_scan_mode),
            ("objective", _readers.get_objective),
            ("z_stack", _readers.get_z_stack),
            ("fov_m", _readers.get_fov),
            ("bit_depth", _readers.get_bit_depth),
        )
    }


def _export_state(handle, *, stem: str, settings: dict) -> dict:
    """What only the moment of the capture can tell, for the store it will be saved as.

    In the controller's words, for the ZMART metadata convention
    (``zmart_drivers.metadata_convention``): ``controller_state`` is ``get_state()`` whole,
    ``xyz`` is ``get_xyz()`` as the contract defines it, and ``driver`` names
    this driver. ``vendor`` is this driver's and LAS X's own: how a position
    is made from the readings, the hardware report, the settings. Every
    field is read best-effort: a missing reader leaves ``None`` rather than
    failing the capture.
    """
    xyz = try_(lambda: get_xyz(handle)) or {}
    return {
        "controller_state": try_(lambda: get_state(handle)),
        "xyz": {axis: xyz[axis] for axis in ("x", "y", "z") if axis in xyz} or None,
        "driver": {"name": DRIVER_PACKAGE, "version": _DRIVER_VERSION},
        "vendor": {
            "position": {
                "made as": "reading - origin - objective translation; z is z-wide + z-galvo",
                "origin": dict(handle.origin or {}),
                "objective translation (um)": xyz.get("objective_translation_um"),
            },
            "software": {"client": handle.connection.get("client"), "api": "navigator"},
            "hardware": try_(lambda: _readers.get_hardware_info(handle.client)),
            "settings": settings,
            "stem": stem,
            "session": handle.hash6,
            "noted at": datetime.now(UTC).isoformat(),
        },
    }


def _where_it_goes(handle, output_folder) -> Path:
    """The folder a capture will be saved in: the one given, else this session's staging folder."""
    if output_folder is not None:
        return Path(output_folder)
    # Staging lives under the folder saved to, so moving a capture into
    # its run is a rename on the same disk, never a copy. Where that is
    # was set up once on this microscope; without it, this says how.
    return Path(handle.output_root()) / ".staging" / handle.hash6


def _stage_now(handle) -> dict:
    """Where the stage stands, read once before a picture; a note when it moved since the last look."""
    last_seen = handle.driven_to
    fresh = try_(lambda: get_xyz(handle))
    at = handle.driven_to if fresh is not None else last_seen
    if at is None:
        raise RuntimeError(
            "the driver does not know where the stage is and could not read it: "
            "check that LAS X answers, then capture again"
        )
    if last_seen is not None and fresh is not None and _moved(last_seen, at):
        handle.note_unconfirmed(
            "acquire: the stage or the objective changed",
            "the stage position or the objective is not what position() or move_to() "
            "last reported (changed in LAS X?); the capture is placed from the fresh reading",
        )
    return at


def _free_label(handle, position_label) -> str:
    label = position_label if position_label is not None else _next_position_label(handle)
    if any(waiting.label == label for waiting in handle.unsaved):
        raise ValueError(
            f"a capture labelled {label!r} is still waiting to be saved; run save first, "
            "or choose another label, so that no capture overwrites another"
        )
    return label


def acquire(
    handle,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
    acquisition_type: str,
    output_folder: str | None,
) -> dict:
    """Take one picture with the current settings and keep a note of it, for ``save_data``.

    Args:
        position_label: What the workflow calls this place. Every file name
            is the acquisition type, then the label, then the time point,
            channel and plane. When omitted, the next per-session counter
            value ("000000", "000001", ...) is used. A name already used in
            the folder saved to gets ``_001``, ``_002``, ... so no capture is
            ever overwritten.
        acquisition_settings: Values from :func:`get_acquisition_settings`:
            the trigger, which is always ``full``. Unknown keys or values raise.
        acquisition_type: The kind of acquisition in the workflow's run,
            such as ``"overview"``: always a non-empty text.
        output_folder: Where the capture will be saved, as text. When None,
            it is this session's staging folder under the microscope's data
            location. Nothing is written until ``save_data`` runs.

    Returns ``{"position_label", "acquisition_type", "picture"}``. Raises
    when the stage is unknown, the label is still waiting to be saved, or
    the capture itself fails (its scan never started).
    """
    handle.require_open()
    resolved = _with_defaults(handle, acquisition_settings)
    output_root = _where_it_goes(handle, output_folder)
    noted_before = len(handle.unconfirmed)
    at = _stage_now(handle)
    settings = current_settings(handle)
    acq = changes.take_picture(handle.client)
    label = _free_label(handle, position_label)
    naming = Naming(stem=free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem))
    handle.unsaved.append(
        PendingCapture(
            acq=acq,
            label=label,
            acquisition_type=acquisition_type,
            namings=[naming],
            fields=[dict(at)],
            state=_export_state(handle, stem=naming.stem, settings=settings),
            settings_at_capture=settings,
            output_root=output_root,
            session_hash6=handle.hash6,
            asked=dict(acquisition_settings or {}),
            settings=resolved,
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {"position_label": label, "acquisition_type": acquisition_type, "picture": acq.picture}


def tile_scan(
    handle,
    positions: list[tuple[float, float]],
    *,
    position_label: str | None = None,
    acquisition_type: str = "tile_scan",
    output_folder: str | None = None,
) -> dict:
    """Take one picture of several fields, at *positions*, and keep a note of it, for ``save_data``.

    *positions* are ``(x, y)`` pairs in micrometres from the origin, in the
    sample's frame, as ``move_to`` takes them; the fields are taken at the
    current focus. LAS X scans them in one acquisition (its Preview), and
    ``save_data`` writes one store per field, each placed where its field
    was taken and named ``<acquisition_type>_<label>_field<n>``. Afterwards
    the stage is put back at the first field. Every position is shown to
    the limits first; one outside them refuses the whole scan, and nothing
    moves.

    Returns ``{"position_label", "acquisition_type", "picture", "fields"}``.
    """
    handle.require_open()
    if not positions:
        raise ValueError("a tile scan needs at least one position")
    output_root = _where_it_goes(handle, output_folder)
    noted_before = len(handle.unconfirmed)
    at = _stage_now(handle)
    snapshot = {"objective": try_(lambda: _readers.get_objective(handle.client))}
    delta = objective_delta_um(handle, snapshot["objective"])  # raises when the objective is unknown to the calibration
    stage_positions, frame_positions = [], []
    for position in positions:
        try:
            x, y = (float(v) for v in position)
        except (TypeError, ValueError) as why:
            raise ValueError(f"a tile scan position is (x, y) in micrometres, not {position!r}: {why}") from why
        abs_x, abs_y, _focus = stage_target(x, y, at["z"], handle.origin, delta)
        stage_positions.append((abs_x, abs_y, at["z_galvo_um"]))
        frame_positions.append({"x": x, "y": y, "z": at["z"], "z_wide_um": at["z_wide_um"], "z_galvo_um": at["z_galvo_um"]})
    settings = current_settings(handle)
    acq = changes.take_tile_scan(handle.client, stage_positions)
    put_back = acq.command_result.get("put_back") or {}
    if not put_back.get("confirmed"):
        handle.note_unconfirmed("tile_scan: the stage back at the first field", put_back or "not sent")
    label = _free_label(handle, position_label)
    stem = free_stem(output_root, Naming(stem=f"{acquisition_type}_{label}").stem)
    namings = [Naming(stem=f"{stem}_field{number}") for number in range(len(positions))]
    handle.unsaved.append(
        PendingCapture(
            acq=acq,
            label=label,
            acquisition_type=acquisition_type,
            namings=namings,
            fields=frame_positions,
            state=_export_state(handle, stem=stem, settings=settings),
            settings_at_capture=settings,
            output_root=output_root,
            session_hash6=handle.hash6,
            asked={"positions": [list(p) for p in positions]},
            settings={"trigger": "tile_scan"},
            unconfirmed=list(handle.unconfirmed[noted_before:]),
        )
    )
    return {
        "position_label": label,
        "acquisition_type": acquisition_type,
        "picture": acq.picture,
        "fields": len(positions),
    }


def row_of_fields(handle, count: int, *, spacing: float = TILE_SPACING) -> list[tuple[float, float]]:
    """*count* positions in a row along x from where the stage stands, a field and a tenth apart.

    In the sample's frame, as :func:`tile_scan` takes them. The row runs the
    other way when it would leave the stage's canvas (its travel limits) in
    the usual direction. For the hardware check and for a first tile scan.
    """
    handle.require_open()
    here = get_xyz(handle)
    fov = _readers.get_fov(handle.client)
    if not fov:
        raise RuntimeError("could not read the field of view, so the row cannot be laid out")
    step = fov[0] * 1e6 * spacing
    x, y = here["x"]["position"], here["y"]["position"]
    low, high = here["x"]["canvas"]
    forward = [(x + i * step, y) for i in range(count)]
    if all(low <= p[0] <= high for p in forward):
        return forward
    return [(x - i * step, y) for i in range(count)]


#: How far the stage may differ from the last reported position before the
#: difference is worth a note. Below this it is just the stage settling.
STAGE_CHANGE_NOTE_UM = 1.0


def _moved(before: dict, after: dict) -> bool:
    """Whether the stage position (in the driver's coordinates) differs by more than a settling."""
    return any(abs(after[axis] - before[axis]) > STAGE_CHANGE_NOTE_UM for axis in ("x", "y", "z"))


@dataclass
class PendingCapture:
    """One picture taken, waiting to be saved, with the notes taken as it was captured.

    ``namings`` and ``fields`` have one entry per field (one for a capture,
    one per position for a tile scan): the name its store gets, and where
    on the sample it was taken, in the frame ``move_to`` takes (``x``,
    ``y``, ``z`` and the two z drives).
    """

    acq: Any
    label: str
    acquisition_type: str
    namings: list[Naming]
    fields: list[dict]
    state: dict
    settings_at_capture: dict
    output_root: Path
    session_hash6: str
    asked: dict
    settings: dict
    unconfirmed: list = field(default_factory=list)
