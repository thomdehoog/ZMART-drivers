"""The objective calibration: how far the view and the focus shift between objectives, loaded and saved.

Each objective looks at a slightly different spot of the sample and focuses
at a slightly different height. The calibration records that offset once per
objective, as ``translation_um`` = [x, y, z] in micrometres, measured against
one reference objective whose offset is [0, 0, 0]::

    {
      "schema_version": 1,
      "objectives": {
        "1": {"name": "Plan-Apochromat 10x/0.45", "translation_um": [0, 0, 0]},
        "2": {"name": "Plan-Apochromat 20x/0.8", "translation_um": [4.2, -1.5, 2.0]}
      }
    }

The keys are the objective changer's positions, counted from 1 as ZEN
counts them. The shift that keeps a sample point in view when the objective
changes from position a to position b is T[b] − T[a]
(:func:`translation_delta_um`). The calibration is measured with
``calibrate_objective_pair.py`` and its notebook.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def default_path(calibration_name: str | None = None) -> Path:
    """The calibration in force: the newest saved one, seeded from the bundled default on first use."""
    from ..store import MACHINE

    return MACHINE.calibration_path(calibration_name)


def validate_calibration(config: Any) -> dict:
    """The calibration document checked; ``ValueError`` says what is wrong.

    Every objective needs a ``name``; ``translation_um``, when given, is three
    finite numbers. As soon as one objective has a translation, exactly one
    must be the reference at [0, 0, 0].
    """
    if not isinstance(config, dict) or config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"the calibration must be an object with schema_version {SCHEMA_VERSION}, got {config!r}"
        )
    extra = set(config) - {"schema_version", "objectives"}
    if extra:
        raise ValueError(f"the calibration has unknown fields {sorted(extra)}")
    objectives = config.get("objectives")
    if not isinstance(objectives, dict):
        raise ValueError("the calibration has no 'objectives' object")
    references = []
    for key, entry in objectives.items():
        if not str(key).isdigit() or int(key) < 1:
            raise ValueError(
                f"objective {key!r}: the keys are changer positions, whole numbers from 1"
            )
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError(f"objective {key!r} must be an object with a 'name'")
        unknown = set(entry) - {"name", "translation_um"}
        if unknown:
            raise ValueError(f"objective {key!r} has unknown fields {sorted(unknown)}")
        translation = entry.get("translation_um")
        if translation is None:
            continue
        if (
            not isinstance(translation, list)
            or len(translation) != 3
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                for v in translation
            )
        ):
            raise ValueError(
                f"objective {key!r}: translation_um must be three finite numbers, got {translation!r}"
            )
        if all(v == 0 for v in translation):
            references.append(key)
    if (
        any(entry.get("translation_um") is not None for entry in objectives.values())
        and len(references) != 1
    ):
        raise ValueError(
            f"exactly one objective must be the reference at [0, 0, 0]; found {references}"
        )
    return config


def load_calibration(
    path: str | Path | None = None, *, calibration_name: str | None = None
) -> dict:
    """The calibration document from *path* (else the one in force), checked."""
    current = Path(path) if path is not None else default_path(calibration_name)
    if not current.exists():
        raise FileNotFoundError(f"the calibration {current} does not exist")
    return validate_calibration(json.loads(current.read_text(encoding="utf-8")))


def translations_of(config: dict) -> dict[int, tuple[float, float, float]]:
    """``{position: (x, y, z)}`` for every objective that has a translation."""
    return {
        int(key): tuple(float(v) for v in entry["translation_um"])
        for key, entry in config["objectives"].items()
        if entry.get("translation_um") is not None
    }


def translation_delta_um(
    translations: dict | None, from_position: int, to_position: int
) -> tuple[float, float, float]:
    """The shift (x, y, z in micrometres) that keeps a sample point in view from objective *from_position* to *to_position*.

    This is the one place the sign is decided: shift = T[to] − T[from]. Both
    the objective change (``keep_in_view.py``) and the frame arithmetic
    (``absolute_coordinate_system.py``) call it. Raises ``RuntimeError`` when
    the calibration does not cover both positions: an uncovered pair is never
    treated as zero.
    """
    table = translations or {}
    if from_position not in table or to_position not in table:
        raise RuntimeError(
            f"no calibration covers objective positions {from_position!r} -> {to_position!r}; calibrate the pair "
            "with driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
        )
    a, b = table[from_position], table[to_position]
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def load_objective_calibration(
    calibration_name: str | None = None, *, enabled: bool = True
) -> tuple[dict | None, dict]:
    """The translations and a record of what was loaded, from one read of one file.

    Returns ``(translations, info)``. With *enabled* False, ``(None, ...)``:
    the session runs without a calibration and objective changes are made
    without a correction. A file that cannot be used gives ``({}, ...)`` with
    a warning: the session wanted a calibration, so changes across objectives
    are refused rather than made uncorrected.
    """
    info = {
        "enabled": enabled,
        "loaded": False,
        "name": calibration_name,
        "path": None,
        "measured_positions": [],
    }
    if not enabled:
        return None, info
    path = None
    try:
        path = default_path(calibration_name).absolute()
        config = load_calibration(path)
    except Exception as why:  # noqa: BLE001 -- a file problem degrades the session, never crashes connect
        log.warning(
            "the objective calibration could not be loaded (%s); changes across objectives are refused",
            why,
        )
        info["path"] = None if path is None else str(path)
        return {}, info
    translations = translations_of(config)
    info.update(loaded=True, path=str(path), measured_positions=sorted(translations))
    return translations, info


def calibration_with(
    config: dict, position: int, name: str | None, translation_um
) -> dict:
    """A copy of *config* with the objective at *position* set to *translation_um*."""
    updated = json.loads(json.dumps(config))
    entry = updated["objectives"].setdefault(
        str(int(position)), {"name": name or f"position {position}"}
    )
    if name:
        entry["name"] = name
    entry["translation_um"] = [float(v) for v in translation_um]
    return validate_calibration(updated)
