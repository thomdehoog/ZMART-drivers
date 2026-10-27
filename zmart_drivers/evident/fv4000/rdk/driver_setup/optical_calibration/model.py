"""Load and apply the objective calibration: one translation triple per nosepiece slot.

The calibration file, ``calibration.json``, holds for each slot the
objective's name and its ``translation_um``: how far, in x, y and focus, the
stage must move from the reference objective's position to see the same
point of the sample. The reference objective is the one whose translation is
``[0, 0, 0]``; the others are measured against it. Translations are
relative, so the difference between any two objectives follows from them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
After the Leica driver's calibration model.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 13


def default_path() -> Path:
    """The newest saved ``calibration.json``, seeded from the bundled default when none was saved."""
    from ..store import MACHINE

    return MACHINE.calibration_path()


def validate_calibration(config: dict[str, Any]) -> None:
    """Refuse a calibration that is not in the shape this driver reads, with what is wrong."""
    if not isinstance(config, dict):
        raise ValueError("calibration.json must hold an object")
    extra = set(config) - {"schema_version", "objectives"}
    if extra:
        raise ValueError(f"calibration.json has unsupported fields: {sorted(extra)}")
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"calibration.json schema_version must be {SCHEMA_VERSION}, got {config.get('schema_version')!r}"
        )
    objectives = config.get("objectives")
    if not isinstance(objectives, dict):
        raise ValueError("calibration.json is missing its 'objectives' object")
    for slot, entry in objectives.items():
        if not str(slot).isdigit():
            raise ValueError(
                f"calibration objective {slot!r}: slots are whole numbers from 0"
            )
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError(
                f"calibration objective {slot!r} must be an object with a 'name'"
            )
        extra = set(entry) - {"name", "translation_um"}
        if extra:
            raise ValueError(
                f"calibration objective {slot!r} has unsupported fields: {sorted(extra)}"
            )
        if entry.get("translation_um") is not None:
            get_translation_um(config, int(slot))
    if any(entry.get("translation_um") is not None for entry in objectives.values()):
        get_reference_slot(config)


def load_calibration(path: str | Path | None = None) -> dict[str, Any]:
    """Read and check a calibration file (the newest saved one when no path is given)."""
    current = Path(path) if path is not None else default_path()
    if not current.exists():
        raise FileNotFoundError(f"calibration config not found: {current}")
    config = json.loads(current.read_text(encoding="utf-8"))
    validate_calibration(config)
    return config


def update_objective(
    config: dict[str, Any], slot: int, *, name: str | None = None, translation_um=None
) -> None:
    """Add or change the entry of *slot* in *config*: its name and, when given, its translation (x, y, z in µm)."""
    objectives = config.setdefault("objectives", {})
    key = str(int(slot))
    entry = objectives.get(key)
    if entry is None:
        if name is None:
            raise ValueError(f"cannot create objective slot {slot} without a name")
        entry = objectives[key] = {}
    if name is not None:
        entry["name"] = name
    if translation_um is not None:
        if len(translation_um) != 3:
            raise ValueError(
                f"translation_um must have 3 values, got {translation_um!r}"
            )
        entry["translation_um"] = [float(v) for v in translation_um]


def get_translation_um(config: dict[str, Any], slot: int) -> tuple[float, float, float]:
    """The translation of *slot* in micrometres; raises when the slot is not calibrated."""
    entry = (config.get("objectives") or {}).get(str(int(slot)))
    if entry is None:
        available = sorted(int(s) for s in config.get("objectives", {}))
        raise ValueError(
            f"no calibration entry for slot {slot}; calibrated: {available}"
        )
    value = entry.get("translation_um")
    if value is None:
        raise ValueError(
            f"slot {slot} has no translation_um; run the calibration notebook for it"
        )
    if len(value) != 3:
        raise ValueError(
            f"slot {slot} translation_um must have 3 values, got {value!r}"
        )
    return float(value[0]), float(value[1]), float(value[2])


def get_reference_slot(config: dict[str, Any]) -> int:
    """The reference objective: the one slot whose translation is ``[0, 0, 0]``."""
    refs = [
        int(slot)
        for slot, entry in (config.get("objectives") or {}).items()
        if entry.get("translation_um") is not None
        and all(float(v) == 0.0 for v in entry["translation_um"])
    ]
    if not refs:
        raise ValueError(
            "the calibration has no reference objective: no entry has translation_um == [0, 0, 0]"
        )
    if len(refs) > 1:
        raise ValueError(
            f"the calibration has more than one zero-translation reference: {refs}"
        )
    return refs[0]


def translation_delta_um(
    translations: dict | None, from_slot: int, to_slot: int
) -> tuple[float, float, float]:
    """The stage move (x, y, z in µm) that keeps the sample point when the objective changes from *from_slot* to *to_slot*.

    This is the one place the sign convention lives: ``delta = T[to] - T[from]``.
    Both the objective change (``procedures/keep_in_view.py``) and the frame
    arithmetic (``procedures/absolute_coordinate_system.py``) call it, so the
    two can never drift apart. Raises ``RuntimeError`` when either slot is
    not covered: an uncovered pair is never silently treated as zero.
    """
    table = translations or {}
    if from_slot not in table or to_slot not in table:
        raise RuntimeError(
            f"no calibration translation covers objective slots {from_slot!r} -> {to_slot!r}; measure the pair "
            "with driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
        )
    a, b = table[from_slot], table[to_slot]
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def load_objective_calibration(*, enabled: bool = True) -> tuple[dict | None, dict]:
    """The translations by slot, and a record of what was loaded, from one read of the saved file.

    With *enabled* False nothing is loaded and the first answer is None: the
    session declined the calibration. When the file cannot be used the first
    answer is ``{}``: the session wanted a calibration and has none, so
    objective changes and moves across objectives refuse.
    """
    info = {
        "enabled": enabled,
        "loaded": False,
        "path": None,
        "slots": [],
        "measured_slots": [],
    }
    if not enabled:
        return None, info
    path = None
    try:
        path = default_path().absolute()
        config = load_calibration(path)
        translations = {
            int(slot): get_translation_um(config, int(slot))
            for slot, entry in (config.get("objectives") or {}).items()
            if entry.get("translation_um") is not None
        }
    except Exception as exc:  # noqa: BLE001 -- a file problem degrades the session, never stops the connect
        _log.warning(
            "objective translations unavailable (%s); objective changes and moves across objectives will be refused",
            exc,
        )
        info["path"] = None if path is None else str(path)
        return {}, info
    return translations, {
        **info,
        "loaded": True,
        "path": str(path),
        "slots": sorted(translations),
        "measured_slots": sorted(translations),
    }
