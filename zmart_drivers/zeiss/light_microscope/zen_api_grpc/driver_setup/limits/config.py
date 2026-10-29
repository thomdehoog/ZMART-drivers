"""Load, check and publish this microscope's ``limits.json``.

The file is flat and meant to be read by a person: three ranges, for the
stage's x and y and for the focus, in micrometres of ZEN's own coordinates;
then the objective positions and the experiments the driver may choose, and
the z-stack ends it may set. Each of those is ``[]`` (reviewed, no limit) or
one typed constraint::

    {
      "x_um": {"range": [0, 100000]},
      "y_um": {"range": [0, 75000]},
      "z_um": {"range": [0, 10000]},
      "objective_position": [],
      "experiment": {"allowed": ["ZMART_Snap", "ZMART_ZStack"]},
      "set_z_stack": []
    }

A file under the microscope's settings folder exists only because an
operator published it, so its presence is its provenance. Until the first
publication the bundled defaults govern, as a clearly reported fallback.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import limit_rules as checks
from .limit_rules import AXIS_FILE_KEYS, POLICY_KEYS

REQUIRED_AXES = tuple(AXIS_FILE_KEYS)
REQUIRED_FILE_KEYS = (*AXIS_FILE_KEYS.values(), *POLICY_KEYS)


def defaults_path() -> Path:
    """The bundled fallback ``limits.json``."""
    from ..store import LIMITS_FILENAME, MACHINE

    return MACHINE.bundled_default_path(LIMITS_FILENAME)


def _read_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_range(values: Any, *, path: Path, name: str) -> list[float]:
    """One ``[min, max]`` of finite numbers, as floats; anything else raises ``ValueError``."""
    if not isinstance(values, list) or len(values) != 2:
        raise ValueError(f"{path} {name} must be [min, max], got {values!r}")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        raise ValueError(f"{path} {name} must contain numbers, got {values!r}")
    low, high = float(values[0]), float(values[1])
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"{path} {name} is not finite: {values!r}")
    if low > high:
        raise ValueError(f"{path} {name} has min > max: {values!r}")
    return [low, high]


def _normalize_typed_limit(
    name: str, entry: Any, *, path: Path, required_kind: str | None = None
) -> dict:
    if not isinstance(entry, dict) or len(entry) != 1:
        raise ValueError(
            f"{path} {name} must be {{'range': [min, max]}} or {{'allowed': [...]}}, got {entry!r}"
        )
    kind, values = next(iter(entry.items()))
    if kind not in {"range", "allowed"}:
        raise ValueError(f"{path} {name} has unknown limit type {kind!r}")
    if required_kind is not None and kind != required_kind:
        raise ValueError(f"{path} {name} must use {required_kind!r}, got {kind!r}")
    if not isinstance(values, list):
        raise ValueError(f"{path} {name}.{kind} must be a list, got {values!r}")
    if kind == "range":
        return {"range": _normalize_range(values, path=path, name=f"{name}.range")}
    if not values:
        raise ValueError(
            f"{path} {name}.allowed must not be empty; use [] for no limit"
        )
    if any(type(value) not in (bool, int, float, str) for value in values):
        raise ValueError(
            f"{path} {name}.allowed values must be booleans, numbers or texts, got {values!r}"
        )
    if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
        raise ValueError(f"{path} {name}.allowed contains duplicates: {values!r}")
    return {"allowed": list(values)}


def validate_payload(
    payload: Any, *, path: Path = Path("limits.json")
) -> dict[str, Any]:
    """The complete limits document, checked and normalised; ``ValueError`` says what is wrong."""
    if not isinstance(payload, dict):
        raise ValueError(f"{path} limits must be an object, got {payload!r}")
    missing = sorted(set(REQUIRED_FILE_KEYS) - set(payload))
    unknown = sorted(set(payload) - set(REQUIRED_FILE_KEYS))
    if missing:
        raise ValueError(f"{path} is missing the limits entries {missing}")
    if unknown:
        raise ValueError(f"{path} has unknown limits entries {unknown}")
    axes = {
        key: _normalize_typed_limit(key, payload[key], path=path, required_kind="range")
        for key in AXIS_FILE_KEYS.values()
    }
    normalized: dict[str, Any] = dict(axes)
    for name in POLICY_KEYS:
        entry = payload[name]
        normalized[name] = (
            [] if entry == [] else _normalize_typed_limit(name, entry, path=path)
        )
    positions = normalized["objective_position"]
    if positions and any(
        isinstance(v, bool) or not isinstance(v, int) or v < 1
        for v in positions.get("allowed", [1])
    ):
        raise ValueError(
            f"{path} objective_position.allowed must contain changer positions, whole numbers from 1"
        )
    return normalized


def validate_limits(limits: Any) -> dict[str, Any]:
    """Check a limits document in a notebook and print the three ranges."""
    normalized = validate_payload(limits)
    print("The limits are valid:")
    for key in AXIS_FILE_KEYS.values():
        print(f"  {key}: {normalized[key]['range']}")
    return normalized


def build_limits_payload(stage_um: dict[str, Any]) -> dict[str, Any]:
    """The flat document for three ranges (``{"x": [..], "y": [..], "z": [..]}``) and no other limit."""
    payload = {
        **{
            AXIS_FILE_KEYS[axis]: {"range": list(stage_um[axis])}
            for axis in REQUIRED_AXES
        },
        **{name: [] for name in POLICY_KEYS},
    }
    return validate_payload(payload)


def load(limits_path: str | Path | None = None) -> dict[str, Any]:
    """Load ``limits.json`` (the bundled fallback when no path is given), as the gate and the rules take it.

    Returns ``{"policy": the normalised document, "stage_um": {"x": [..], "y": [..], "z": [..]}}``.
    """
    selected = Path(limits_path) if limits_path is not None else defaults_path()
    payload = validate_payload(_read_json(selected), path=selected)
    return {
        "policy": payload,
        "stage_um": {
            axis: payload[key]["range"] for axis, key in AXIS_FILE_KEYS.items()
        },
    }


def adopt_limits(
    limits: dict[str, Any],
    *,
    machine: Any = None,
    moment: datetime | None = None,
    notebook_paths: Any = (),
) -> dict:
    """Check and publish the notebook's limits document as a new snapshot of this microscope's limits.

    *limits* is the complete flat document, or just the three axes
    (``{"x": [..], "y": [..], "z": [..]}``), which gets no other limit. The
    notebooks given are archived with the snapshot, as the measurement's own
    record. Returns the snapshot's path, the file's path and the archived
    notebooks' paths.
    """
    if set(limits) == set(REQUIRED_AXES):
        payload = build_limits_payload(limits)
    else:
        payload = validate_payload(limits)
    checks.check_envelope_within_backstop(
        {axis: payload[key]["range"] for axis, key in AXIS_FILE_KEYS.items()}
    )
    if machine is None:
        from ..store import MACHINE

        machine = MACHINE
    if moment is None:
        moment = datetime.now(UTC)
    notebook_paths = tuple(Path(path) for path in notebook_paths)
    archived: list[Path] = []
    if notebook_paths:
        from ..notebook_support import archive_notebook
        from ..store import format_snapshot_name

        stamp = format_snapshot_name(moment)
        with TemporaryDirectory(prefix="zmart_limits_") as temp_dir:
            for notebook in notebook_paths:
                destination = archive_notebook(
                    notebook,
                    temp_dir,
                    filename=f"{notebook.stem}_{stamp}{notebook.suffix}",
                    directory="notebook",
                )
                archived.append(destination.relative_to(Path(temp_dir)))
            snapshot = machine.publish_snapshot(
                moment, limits=payload, archive_paths=[Path(temp_dir) / "notebook"]
            )
    else:
        snapshot = machine.publish_snapshot(moment, limits=payload)
    return {
        "snapshot": str(snapshot),
        "limits_path": str(snapshot / "limits.json"),
        "notebook_paths": [str(snapshot / relative) for relative in archived],
    }
