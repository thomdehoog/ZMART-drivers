"""Load and apply the objective calibration: one offset per nosepiece slot.

The saved file is small::

    {
      "schema_version": 1,
      "objectives": {
        "1": {"name": "Plan Apo 10x", "translation_um": [0.0, 0.0, 0.0]},
        "2": {"name": "Apo 20x WI", "translation_um": [12.5, -3.0, 41.0]}
      }
    }

``translation_um`` is where the stage and the focus drive stand when a
feature is centred and in focus under that objective, minus where they stand
for the same feature under the reference objective (the one whose offset is
all zero). So the move that keeps a feature in view when the objective changes
from slot A to slot B is ``T[B] - T[A]`` (:func:`translation_delta_um`), the
one place this sign rule lives.

A slot without a measured offset is not covered: a change to or from it is
refused rather than guessed, because a wrong guess puts the objective on the
wrong spot, or into the sample.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any

from .. import store as _machine

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Where the operator measures the calibration, said in every refusal.
CALIBRATE = "driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"


def validate_calibration(
    config: Any, *, where: str = "calibration.json"
) -> dict[str, Any]:
    """Check a calibration document and hand it back; raise ``ValueError`` saying what is wrong."""
    if not isinstance(config, dict):
        raise ValueError(f"{where}: the calibration must be an object")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{where}: schema_version must be {SCHEMA_VERSION}, got {config.get('schema_version')!r}"
        )
    extra = sorted(set(config) - {"schema_version", "objectives"})
    if extra:
        raise ValueError(f"{where}: unknown entries {extra}")
    objectives = config.get("objectives")
    if not isinstance(objectives, dict):
        raise ValueError(  # noqa: TRY004 -- a malformed file is a ValueError to the caller
            f"{where}: objectives must map each nosepiece slot to its entry"
        )
    measured = {}
    for slot, entry in objectives.items():
        if not (isinstance(slot, str) and slot.isdigit() and int(slot) >= 1):
            raise ValueError(
                f"{where}: a nosepiece slot is a whole number from 1, written as text; got {slot!r}"
            )
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError(f"{where}: slot {slot} must have a name")  # noqa: TRY004 -- a malformed file is a ValueError to the caller
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
                f"{where}: slot {slot} translation_um must be three finite numbers [x, y, z]"
            )
        measured[int(slot)] = tuple(float(v) for v in translation)
    if measured and not any(t == (0.0, 0.0, 0.0) for t in measured.values()):
        raise ValueError(
            f"{where}: one measured objective must be the reference, with translation_um [0, 0, 0]"
        )
    return config


def translations_of(config: dict[str, Any]) -> dict[int, tuple[float, float, float]]:
    """``{slot: (x, y, z)}`` for every objective with a measured offset."""
    return {
        int(slot): tuple(float(v) for v in entry["translation_um"])
        for slot, entry in config["objectives"].items()
        if entry.get("translation_um") is not None
    }


def translation_delta_um(
    translations: dict | None, from_slot: int, to_slot: int
) -> tuple[float, float, float]:
    """The stage and focus move (x, y, z in um) that keeps the sample point when the objective changes from *from_slot* to *to_slot*.

    ``delta = T[to_slot] - T[from_slot]``. Raises ``RuntimeError`` when either
    slot has no measured offset: an uncovered pair is never taken as zero.
    """
    table = translations or {}
    if from_slot not in table or to_slot not in table:
        raise RuntimeError(
            f"no objective calibration covers nosepiece slots {from_slot} -> {to_slot}; "
            f"measure the pair with {CALIBRATE}"
        )
    a, b = table[from_slot], table[to_slot]
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def load_calibration(
    machine: _machine.MachineProfile | None = None,
) -> tuple[dict[str, Any], str | None]:
    """The newest saved calibration document (the bundled default when none was saved), and its path."""
    machine = machine if machine is not None else _machine.MACHINE
    path = machine.latest_path("calibration")
    if path is None:
        bundled = machine.bundled_default_path("calibration")
        return validate_calibration(
            json.loads(bundled.read_text(encoding="utf-8")), where=str(bundled)
        ), None
    return validate_calibration(
        machine.read_record("calibration"), where=str(path)
    ), str(path)


def load_objective_calibration(
    *, enabled: bool = True, machine: _machine.MachineProfile | None = None
):
    """What ``connect`` loads: ``(translations, info)``.

    ``translations`` is ``{slot: (x, y, z)}`` for the measured objectives; it
    is ``{}`` when the connection wanted the calibration but none can be
    used (objective changes are then refused), and None when the connection
    declined it (``load_calibration=False``: objective changes then move
    nothing to compensate). ``info`` reports what was loaded, for ``get_info``.
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
    try:
        config, path = load_calibration(machine)
    except Exception as why:  # noqa: BLE001 -- a bad file degrades the session; connect goes on
        log.warning(
            "the objective calibration cannot be used (%s); objective changes are refused",
            why,
        )
        return {}, info
    translations = translations_of(config)
    info.update(
        loaded=True,
        path=path,
        slots=sorted(int(slot) for slot in config["objectives"]),
        measured_slots=sorted(translations),
    )
    return translations, info


def adopt_calibration(
    objectives: dict[int, dict],
    *,
    machine: _machine.MachineProfile | None = None,
    moment: datetime | None = None,
    archive_paths=(),
) -> dict:
    """Save a calibration: ``{slot: {"name": ..., "translation_um": [x, y, z]}}`` over the saved one.

    Slots given here replace their entries; slots not given keep what was
    saved before, so pairs can be measured one at a time. Answers the
    record's folder and the file's path.
    """
    machine = machine if machine is not None else _machine.MACHINE
    config, _ = load_calibration(machine)
    merged = {
        **config["objectives"],
        **{str(int(slot)): dict(entry) for slot, entry in objectives.items()},
    }
    record = validate_calibration(
        {"schema_version": SCHEMA_VERSION, "objectives": merged}
    )
    path = machine.write_record(
        "calibration", record, moment=moment, archive_paths=archive_paths
    )
    return {
        "record": str(path.parent),
        "calibration_path": str(path),
        "objectives": merged,
    }
