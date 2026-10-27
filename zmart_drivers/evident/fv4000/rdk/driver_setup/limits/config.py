"""Load, check and publish this microscope's flat ``limits.json``.

The file is written for an operator to read: four stage ranges (``x_um``,
``y_um``, ``z_um`` for the focus motor, ``z_piezo_um`` for the piezo),
``objective_slot``, and one entry for each setting the driver can change.
Every constraint says ``range`` or ``allowed``; an empty list means
"reviewed, no limit is enforced". ``objective_slot`` is ``[]`` (any slot
FLUOVIEW reports) or an allow-list of nosepiece slots, counted from 0.

:func:`adopt_limits` publishes a new dated record of the file from the
``set_limits`` notebook. Until the first record is published, the defaults
bundled with the driver govern, and the driver says so loudly: they are
placeholders around FLUOVIEW's zero, not this microscope's measured travel.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import limit_rules as checks
from .limit_rules import AXIS_FILE_KEYS, SETTER_LIMIT_KEYS

_REQUIRED_FILE_KEYS = (*AXIS_FILE_KEYS.values(), "objective_slot", *SETTER_LIMIT_KEYS)


def defaults_path() -> Path:
    """The ``limits.json`` bundled with the driver."""
    from ..store import LIMITS_FILENAME, MACHINE

    return MACHINE.bundled_default_path(LIMITS_FILENAME)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_range(values: Any, *, path: Path, name: str) -> list[float]:
    """One ``[min, max]`` of finite numbers, as floats; anything else is refused with the reason."""
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
) -> dict[str, list]:
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
            f"{path} {name}.allowed must not be empty; use [] for an unrestricted setting"
        )
    if any(type(value) not in (bool, int, float, str) for value in values):
        raise ValueError(
            f"{path} {name}.allowed values must be JSON booleans, numbers or strings, got {values!r}"
        )
    if any(isinstance(value, float) and not math.isfinite(value) for value in values):
        raise ValueError(
            f"{path} {name}.allowed contains a non-finite number: {values!r}"
        )
    if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
        raise ValueError(f"{path} {name}.allowed contains duplicates: {values!r}")
    return {"allowed": list(values)}


def validate_payload(
    payload: Any, *, path: Path = Path("limits.json")
) -> dict[str, Any]:
    """Check the whole flat limits document and answer it normalised; a wrong file raises with what is wrong."""
    if not isinstance(payload, dict):
        raise ValueError(f"{path} limits must be an object, got {payload!r}")
    missing = sorted(set(_REQUIRED_FILE_KEYS) - set(payload))
    unknown = sorted(set(payload) - set(_REQUIRED_FILE_KEYS))
    if missing:
        raise ValueError(f"{path} missing limits entries: {missing}")
    if unknown:
        raise ValueError(f"{path} has unknown limits entries: {unknown}")

    axes = {
        key: _normalize_typed_limit(key, payload[key], path=path, required_kind="range")
        for key in AXIS_FILE_KEYS.values()
    }
    objective_entry = payload["objective_slot"]
    if objective_entry == []:
        objective: Any = []
    else:
        objective = _normalize_typed_limit(
            "objective_slot", objective_entry, path=path, required_kind="allowed"
        )
        slots = objective["allowed"]
        if any(
            isinstance(slot, bool) or not isinstance(slot, int) or slot < 0
            for slot in slots
        ):
            raise ValueError(
                f"{path} objective_slot.allowed must contain whole numbers from 0 (nosepiece slots), got {slots!r}"
            )
    setters = {
        name: (
            []
            if payload[name] == []
            else _normalize_typed_limit(name, payload[name], path=path)
        )
        for name in SETTER_LIMIT_KEYS
    }
    return {**axes, "objective_slot": objective, **setters}


def validate_limits(limits: Any) -> dict[str, Any]:
    """Check the notebook's limits document and print the four stage ranges."""
    normalized = validate_payload(limits)
    print("Limits are valid:")
    for key in AXIS_FILE_KEYS.values():
        print(f"  {key}: {normalized[key]['range']}")
    return normalized


def build_limits_payload(stage_um: dict[str, Any]) -> dict[str, Any]:
    """The complete flat document for the stage ranges *stage_um* (``{"x": [lo, hi], ...}``), with every setting unrestricted."""
    unknown = sorted(set(stage_um) - set(AXIS_FILE_KEYS))
    missing = sorted(set(AXIS_FILE_KEYS) - set(stage_um))
    if unknown or missing:
        raise ValueError(
            f"stage limits must name exactly {list(AXIS_FILE_KEYS)}; unknown {unknown}, missing {missing}"
        )
    payload = {
        **{
            AXIS_FILE_KEYS[axis]: {"range": list(bounds)}
            for axis, bounds in stage_um.items()
        },
        "objective_slot": [],
        **{name: [] for name in SETTER_LIMIT_KEYS},
    }
    return validate_payload(payload)


def load(limits_path: str | Path | None = None) -> dict[str, Any]:
    """Load a ``limits.json`` (the bundled default when no path is given) as the gate and the stage checks take it.

    Answers ``{"policy": <the normalised file>, "stage_um": {"x": [lo, hi], ...}}``.
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
    """Check the notebook's flat document and publish it as this microscope's limits.

    *notebook_paths* are saved notebooks archived beside the record, each
    with the record's time in its name, as the written account of the
    measurement. Answers the record's folder, the file's path and the
    archived notebooks.
    """
    payload = validate_payload(limits)
    stage_um = {axis: payload[key]["range"] for axis, key in AXIS_FILE_KEYS.items()}
    checks.check_envelope_within_backstop(stage_um)
    if machine is None:
        from ..store import MACHINE

        machine = MACHINE
    if moment is None:
        moment = datetime.now(UTC)
    from tempfile import TemporaryDirectory

    from ..notebook_support import archive_notebook
    from ..store import format_snapshot_name

    stamp = format_snapshot_name(moment)
    archived: list[Path] = []
    with TemporaryDirectory(prefix="zmart_limits_") as temp_dir:
        folder = Path(temp_dir) / "notebook"
        for notebook in (Path(p) for p in notebook_paths):
            archived.append(
                archive_notebook(
                    notebook,
                    temp_dir,
                    filename=f"{notebook.stem}_{stamp}{notebook.suffix}",
                    directory="notebook",
                ).relative_to(Path(temp_dir))
            )
        snapshot = machine.publish_snapshot(
            moment, limits=payload, archive_paths=[folder] if archived else []
        )
    return {
        "snapshot": str(snapshot),
        "limits_path": str(snapshot / "limits.json"),
        "notebook_paths": [str(snapshot / relative) for relative in archived],
    }
