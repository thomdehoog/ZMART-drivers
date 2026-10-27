"""Load, check and publish this microscope's flat ``limits.json``.

The file is written to be read by a person. Three stage ranges, then one
entry per setting automation may choose::

    {
      "x_um": {"range": [-50000, 50000]},
      "y_um": {"range": [-30000, 30000]},
      "z_um": {"range": [0, 9000]},
      "objective_slot": [],
      "optical_configuration": [],
      "exposure_ms": {"range": [0.01, 10000]},
      "autofocus_range_um": {"range": [1, 500]}
    }

A range includes both ends. ``[]`` means reviewed and unrestricted: which
slots and configurations exist is checked live against NIS-Elements anyway,
so an allow-list there is only for fencing automation to some of them. A
file with a missing, extra or malformed entry is refused whole, never
guessed at.

``adopt_limits`` publishes a new record of the file from the ``set_limits``
notebook. Until the first record exists, the defaults that ship with the
driver govern, and the driver says so loudly at every connect.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .limit_rules import AXES, AXIS_FILE_KEYS, SETTING_LIMIT_KEYS

_REQUIRED_FILE_KEYS = (*AXIS_FILE_KEYS.values(), *SETTING_LIMIT_KEYS)
#: The kind of constraint each setting entry may carry.
_SETTING_KIND = {
    "objective_slot": "allowed",
    "optical_configuration": "allowed",
    "exposure_ms": "range",
    "autofocus_range_um": "range",
}


def defaults_path() -> Path:
    """The limits that ship with the driver."""
    from ..store import MACHINE

    return MACHINE.bundled_default_path("limits")


def _normalize_range(values: Any, *, path: Path, name: str) -> list[float]:
    """One strict numeric ``[min, max]``, as floats."""
    if not isinstance(values, list) or len(values) != 2:
        raise ValueError(f"{path}: {name} must be [min, max], got {values!r}")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        raise ValueError(f"{path}: {name} must hold two numbers, got {values!r}")
    low, high = float(values[0]), float(values[1])
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"{path}: {name} is not finite: {values!r}")
    if low > high:
        raise ValueError(
            f"{path}: {name} has its minimum above its maximum: {values!r}"
        )
    return [low, high]


def _normalize_typed(name: str, entry: Any, *, path: Path, kind: str) -> dict:
    if not isinstance(entry, dict) or len(entry) != 1 or next(iter(entry)) != kind:
        example = "{'range': [min, max]}" if kind == "range" else "{'allowed': [...]}"
        raise ValueError(f"{path}: {name} must be [] or {example}, got {entry!r}")
    values = entry[kind]
    if not isinstance(values, list):
        raise ValueError(f"{path}: {name}.{kind} must be a list, got {values!r}")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    if kind == "range":
        return {"range": _normalize_range(values, path=path, name=f"{name}.range")}
    if not values:
        raise ValueError(
            f"{path}: {name}.allowed must not be empty; use [] for unrestricted"
        )
    if name == "objective_slot" and any(
        isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in values
    ):
        raise ValueError(
            f"{path}: objective_slot.allowed must hold nosepiece slots counted from 1, got {values!r}"
        )
    if name == "optical_configuration" and any(
        not isinstance(v, str) or not v for v in values
    ):
        raise ValueError(
            f"{path}: optical_configuration.allowed must hold configuration names, got {values!r}"
        )
    if len({json.dumps(v, sort_keys=True) for v in values}) != len(values):
        raise ValueError(f"{path}: {name}.allowed repeats a value: {values!r}")
    return {"allowed": list(values)}


def validate_payload(
    payload: Any, *, path: Path = Path("limits.json")
) -> dict[str, Any]:
    """Check the whole flat document and hand it back normalised; raise ``ValueError`` saying what is wrong."""
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: the limits must be an object, got {payload!r}")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    missing = sorted(set(_REQUIRED_FILE_KEYS) - set(payload))
    unknown = sorted(set(payload) - set(_REQUIRED_FILE_KEYS))
    if missing:
        raise ValueError(f"{path}: missing entries {missing}")
    if unknown:
        raise ValueError(
            f"{path}: unknown entries {unknown}; the entries are {list(_REQUIRED_FILE_KEYS)}"
        )
    normalized: dict[str, Any] = {
        key: _normalize_typed(key, payload[key], path=path, kind="range")
        for key in AXIS_FILE_KEYS.values()
    }
    for name in SETTING_LIMIT_KEYS:
        entry = payload[name]
        normalized[name] = (
            []
            if entry == []
            else _normalize_typed(name, entry, path=path, kind=_SETTING_KIND[name])
        )
    return normalized


def validate_limits(limits: Any) -> dict[str, Any]:
    """Check the notebook's document and print the three stage ranges; the normalised document comes back."""
    normalized = validate_payload(limits)
    print("The limits are valid:")
    for axis in AXES:
        print(f"  {AXIS_FILE_KEYS[axis]}: {normalized[AXIS_FILE_KEYS[axis]]['range']}")
    return normalized


def load(limits_path: str | Path | None = None) -> dict[str, Any]:
    """Read and check a limits file; the bundled defaults when none is given.

    Answers ``{"policy": the whole normalised document, "stage_um": {"x":
    [min, max], ...}}``, the shapes the rulebook and the handshake use.
    """
    selected = Path(limits_path) if limits_path is not None else defaults_path()
    payload = validate_payload(
        json.loads(selected.read_text(encoding="utf-8")), path=selected
    )
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
    """Check the notebook's document and save it as this microscope's limits.

    *notebook_paths* are saved notebooks archived with the record, each with
    the record's date in its name, so the measurement travels with the file
    it produced. Answers the record's folder, the file's path and the
    archived notebooks' paths.
    """
    payload = validate_payload(limits)
    if machine is None:
        from ..store import MACHINE

        machine = MACHINE
    notebooks = tuple(Path(p) for p in notebook_paths)
    if not notebooks:
        path = machine.write_record("limits", payload, moment=moment)
        return {
            "record": str(path.parent),
            "limits_path": str(path),
            "notebook_paths": [],
        }
    from ..notebook_support import archive_notebook
    from ..store import record_name

    stamp = (
        record_name(moment)
        if moment is not None
        else datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    with TemporaryDirectory(prefix="zmart_limits_") as temp:
        for notebook in notebooks:
            archive_notebook(
                notebook,
                temp,
                filename=f"{notebook.stem}_{stamp}{notebook.suffix}",
                directory="notebook",
            )
        path = machine.write_record(
            "limits", payload, moment=moment, archive_paths=[Path(temp) / "notebook"]
        )
    return {
        "record": str(path.parent),
        "limits_path": str(path),
        "notebook_paths": [
            str(p) for p in sorted((path.parent / "notebook").glob("*.ipynb"))
        ],
    }
