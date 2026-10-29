"""Read, check and save this microscope's ``limits.json``.

The file is flat and meant to be read by a person::

    {
      "x_um": {"range": [-40000, 40000]},
      "y_um": {"range": [-25000, 25000]},
      "z_um": {"range": [1000, 6000]},
      "objective_position": [],
      "optical_configuration": {"allowed": ["DAPI", "GFP"]}
    }

The three stage axes are ranges in micrometres, in NIS's own stage
coordinates. ``objective_position`` and ``optical_configuration`` are either
``[]`` (any is allowed) or ``{"allowed": [...]}``. Every key must be there,
and no other: a file with a typing mistake is refused whole, rather than
read in part. Whatever the file says, the driver never allows more stage
travel than NIS itself reports (``limit_rules.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/limits/config.py``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ...primitives.nis_python import devices as device_table
from .. import store

KEYS = ("x_um", "y_um", "z_um", "objective_position", "optical_configuration")
#: Keys a limits file may leave out.
OPTIONAL_KEYS = ("devices",)


def _devices(value: Any, *, path: Path) -> dict:
    """The hardware settings' limits: ``{"<key>" or "<key>/<device>": {"allowed": [...]} or {"range": [low, high]}}``."""
    if not isinstance(value, dict):
        raise ValueError(  # noqa: TRY004 -- a wrong file is a ValueError, as every limits problem is
            f"{path}: devices must map each hardware setting to its limits, got {value!r}"
        )
    checked = {}
    for name, entry in value.items():
        key = str(name).split("/", 1)[0]
        if key not in device_table.SETTINGS:
            raise ValueError(
                f"{path}: devices.{name}: no hardware setting {key!r}; the settings are {sorted(device_table.SETTINGS)}"
            )
        if (
            not isinstance(entry, dict)
            or not entry
            or set(entry) - {"allowed", "range"}
        ):
            raise ValueError(
                f"{path}: devices.{name} must be {{'allowed': [values]}} or {{'range': [low, high]}}, got {entry!r}"
            )
        out = {}
        if "allowed" in entry:
            if not isinstance(entry["allowed"], list) or not entry["allowed"]:
                raise ValueError(
                    f"{path}: devices.{name}.allowed must be a list of values, got {entry['allowed']!r}"
                )
            out["allowed"] = list(entry["allowed"])
        if "range" in entry:
            out["range"] = _range(
                {"range": entry["range"]}, path=path, name=f"devices.{name}"
            )
        checked[str(name)] = out
    return checked


def _range(value: Any, *, path: Path, name: str) -> list[float]:
    if not isinstance(value, dict) or set(value) != {"range"}:
        raise ValueError(
            f"{path}: {name} must be {{'range': [low, high]}}, got {value!r}"
        )
    pair = value["range"]
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in pair)
    ):
        raise ValueError(
            f"{path}: {name}.range must be two numbers [low, high], got {pair!r}"
        )
    low, high = float(pair[0]), float(pair[1])
    if not (math.isfinite(low) and math.isfinite(high)) or low > high:
        raise ValueError(
            f"{path}: {name}.range must be finite with low <= high, got {pair!r}"
        )
    return [low, high]


def _allowed(value: Any, *, path: Path, name: str, kind: type) -> Any:
    if value == []:
        return []
    if (
        not isinstance(value, dict)
        or set(value) != {"allowed"}
        or not isinstance(value["allowed"], list)
        or not value["allowed"]
    ):
        raise ValueError(
            f"{path}: {name} must be [] (any) or {{'allowed': [...]}} with at least one value, got {value!r}"
        )
    items = value["allowed"]
    if any(isinstance(v, bool) or not isinstance(v, kind) for v in items):
        raise ValueError(
            f"{path}: {name}.allowed must hold only {kind.__name__} values, got {items!r}"
        )
    if len(set(items)) != len(items):
        raise ValueError(f"{path}: {name}.allowed lists a value twice: {items!r}")
    return {"allowed": list(items)}


def validate_payload(payload: Any, *, path: Path = Path("limits.json")) -> dict:
    """The checked limits document; ValueError naming what is wrong."""
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: the limits must be a JSON object")  # noqa: TRY004 -- a wrong file is a ValueError, as every limits problem is
    missing = sorted(set(KEYS) - set(payload))
    unknown = sorted(set(payload) - set(KEYS) - set(OPTIONAL_KEYS))
    if missing or unknown:
        raise ValueError(
            f"{path}: missing {missing}, unknown {unknown}; the keys are {list(KEYS)}, and optionally {list(OPTIONAL_KEYS)}"
        )
    checked = {
        axis: {"range": _range(payload[axis], path=path, name=axis)}
        for axis in ("x_um", "y_um", "z_um")
    }
    checked["objective_position"] = _allowed(
        payload["objective_position"], path=path, name="objective_position", kind=int
    )
    checked["optical_configuration"] = _allowed(
        payload["optical_configuration"],
        path=path,
        name="optical_configuration",
        kind=str,
    )
    if "devices" in payload:
        checked["devices"] = _devices(payload["devices"], path=path)
    return checked


def load(path: str | Path) -> dict:
    """Read and check a ``limits.json``."""
    path = Path(path)
    return validate_payload(json.loads(path.read_text(encoding="utf-8")), path=path)


def build_payload(
    stage_um: dict, *, objective_position: Any = (), optical_configuration: Any = ()
) -> dict:
    """A complete limits document from stage ranges ``{"x_um": [low, high], ...}``; the others ``[]`` unless given."""
    payload = {
        axis: {"range": list(stage_um[axis])} for axis in ("x_um", "y_um", "z_um")
    }
    payload["objective_position"] = (
        {"allowed": list(objective_position)} if objective_position else []
    )
    payload["optical_configuration"] = (
        {"allowed": list(optical_configuration)} if optical_configuration else []
    )
    return validate_payload(payload)


def adopt_limits(
    limits: dict,
    *,
    machine: store.MachineProfile | None = None,
    notebook_paths: Any = (),
) -> dict:
    """Check *limits* and save them as this microscope's limits; the saved file's path.

    *notebook_paths*, when given, are copied beside the file, so the
    measurement's own record (the four corners) is kept with it. The new
    limits govern from the next connection.
    """
    from ..notebook_support import archive_notebook

    payload = validate_payload(limits)
    machine = machine or store.machine()
    path = machine.write("limits", payload)
    notebooks = [
        str(archive_notebook(Path(nb), path.parent, directory="notebook"))
        for nb in notebook_paths
    ]
    return {"limits_path": str(path), "notebook_paths": notebooks}
