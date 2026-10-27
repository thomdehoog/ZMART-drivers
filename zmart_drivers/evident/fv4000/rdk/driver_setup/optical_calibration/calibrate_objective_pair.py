"""Measuring the offset between two objectives, the simple way: the same spot centred under each.

Put a small, recognisable feature (a bead, a cell, a mark on a slide) under
the reference objective, centre it in the picture and focus on it, and
record. Change to the other objective, centre and focus on the same feature
again, and record. The difference between the two recorded stage positions
is the translation that keeps the sample in view when the objective changes,
and that is what the calibration saves.

The measurement is done by eye in FLUOVIEW's live view, which every operator
knows; the notebook only records and saves. Several pairs that share the same
reference objective combine on their own: once the 10x-20x and 10x-40x pairs
are recorded, the 20x-40x translation follows from them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ...procedures.absolute_coordinate_system import hardware_snapshot
from .. import store as _machine
from . import model

log = logging.getLogger(__name__)


@dataclass
class PairSession:
    """One sitting at the microscope: the reference objective and every target recorded against it."""

    reference_slot: int
    session_id: str
    reference: dict | None = None
    targets: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)


def start_session(reference_slot: int, session_id: str | None = None) -> PairSession:
    """Begin a sitting: *reference_slot* is the nosepiece slot (from 0) every other objective is measured against."""
    if (
        isinstance(reference_slot, bool)
        or not isinstance(reference_slot, int)
        or reference_slot < 0
    ):
        raise ValueError(
            f"reference_slot must be a whole number from 0, got {reference_slot!r}"
        )
    return PairSession(
        reference_slot=reference_slot,
        session_id=session_id or datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S"),
    )


def _read(handle, *, expected_slot: int | None = None) -> dict:
    snap = hardware_snapshot(handle)
    slot = (snap.get("objective") or {}).get("slot")
    if slot is None:
        raise RuntimeError("FLUOVIEW does not say which objective is in use")
    if expected_slot is not None and slot != expected_slot:
        raise RuntimeError(
            f"objective slot {slot} is in use, but slot {expected_slot} was expected; change the objective and record again"
        )
    return {
        "slot": int(slot),
        "name": snap["objective"].get("name", ""),
        "x_um": snap["x_um"],
        "y_um": snap["y_um"],
        "z_um": snap["z_um"],
    }


def record_reference(session: PairSession, handle) -> dict:
    """Record the stage and focus with the feature centred under the reference objective; refuses another objective."""
    session.reference = _read(handle, expected_slot=session.reference_slot)
    session.names[session.reference_slot] = session.reference["name"]
    log.info(
        "reference objective slot %s recorded at %s",
        session.reference_slot,
        session.reference,
    )
    return dict(session.reference)


def record_target(session: PairSession, handle) -> dict:
    """Record the stage and focus with the same feature centred under the target objective now in use.

    The objective in use is the target; recording with the reference objective
    in place is refused. A target recorded again replaces its earlier record.
    """
    if session.reference is None:
        raise RuntimeError("record the reference objective first")
    reading = _read(handle)
    if reading["slot"] == session.reference_slot:
        raise RuntimeError(
            f"slot {reading['slot']} is the reference objective; change to the target objective and record again"
        )
    reading["translation_um"] = [
        reading["x_um"] - session.reference["x_um"],
        reading["y_um"] - session.reference["y_um"],
        reading["z_um"] - session.reference["z_um"],
    ]
    session.targets[reading["slot"]] = reading
    session.names[reading["slot"]] = reading["name"]
    log.info(
        "target objective slot %s recorded: translation %s um",
        reading["slot"],
        reading["translation_um"],
    )
    return dict(reading)


def calibration_from_session(
    session: PairSession, existing: dict | None = None
) -> dict:
    """The calibration document with this sitting's measurements written in (over *existing*, when given).

    An existing calibration with another reference objective is replaced
    whole, because translations only mean something against one reference.
    """
    if session.reference is None or not session.targets:
        raise RuntimeError(
            "record the reference objective and at least one target objective first"
        )
    config = {"schema_version": model.SCHEMA_VERSION, "objectives": {}}
    if existing is not None:
        try:
            if model.get_reference_slot(existing) == session.reference_slot:
                config = json.loads(json.dumps(existing))
        except ValueError:
            pass
    model.update_objective(
        config,
        session.reference_slot,
        name=session.names[session.reference_slot],
        translation_um=(0.0, 0.0, 0.0),
    )
    for slot, target in session.targets.items():
        model.update_objective(
            config, slot, name=target["name"], translation_um=target["translation_um"]
        )
    model.validate_calibration(config)
    return config


def adopt_calibration(session: PairSession, notebook_paths=(), *, machine=None) -> dict:
    """Publish this sitting's measurements as this microscope's calibration; answers the record and the file."""
    machine = machine if machine is not None else _machine.MACHINE
    try:
        existing = model.load_calibration(machine.calibration_path())
    except Exception as why:  # noqa: BLE001 -- an unusable earlier file is replaced, and said
        log.warning(
            "the earlier calibration could not be read (%s); starting afresh", why
        )
        existing = None
    config = calibration_from_session(session, existing)
    folder = Path(tempfile.mkdtemp(prefix="zmart_calibration_"))
    record = folder / "measurement.json"
    record.write_text(
        json.dumps(
            {
                "session_id": session.session_id,
                "measured_at": datetime.now(UTC).isoformat(),
                "reference": session.reference,
                "targets": session.targets,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = machine.publish_snapshot(
        calibration=config, archive_paths=[record, *map(Path, notebook_paths)]
    )
    return {
        "snapshot": str(snapshot),
        "calibration_path": str(snapshot / _machine.CALIBRATION_FILENAME),
        "calibration": config,
    }
