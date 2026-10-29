"""Measuring the offset between two objectives, with the operator's eye.

The operator picks a small, sharp feature of a sample (a bead, a corner of a
grid). Under the reference objective they centre it in ZEN's live view and
focus on it, and the driver records where the stage and the focus are. Then
the driver turns the changer to the other objective, without any correction,
and the operator centres and focuses the same feature again. The difference
between the two positions is the other objective's offset against the
reference. ZEN is used as it always is: the joystick, the focus knob and the
live view; the driver only reads the positions and turns the changer.

The reference objective is the one whose offset is [0, 0, 0]. The first pair
measured sets it; every later pair is measured against that same reference.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...procedures import changes
from ...procedures import readings as _readers
from ...procedures.absolute_coordinate_system import hardware_snapshot
from . import model


class PairCalibration:
    """One objective pair, measured on one feature: the reference position, then the other.

    ``record_reference()`` reads the stage and the focus under the reference
    objective; ``change_to(position)`` turns the changer to the other
    objective without a correction; ``record_other()`` reads them again once
    the operator has found the feature. ``result()`` is the other objective's
    offset, and :func:`adopt_calibration` saves it.
    """

    def __init__(self, handle: Any):
        self.handle = handle
        self.reference: dict | None = None
        self.other: dict | None = None

    def record_reference(self) -> dict:
        """Read where the stage and the focus stand under the reference objective; returns it."""
        self.reference = hardware_snapshot(self.handle)
        self.other = None
        return dict(self.reference)

    def change_to(self, position: int) -> dict:
        """Turn the changer to *position*, without moving the stage; the operator then finds the feature again."""
        if self.reference is None:
            raise RuntimeError("record the reference objective first")
        objectives = _readers.get_objectives(self.handle.client)
        result = changes.set_objective(
            self.handle.client, objectives, position=position
        )
        if not result.get("success"):
            raise RuntimeError(
                f"the objective could not be changed: {result.get('message')}"
            )
        return result

    def record_other(self) -> dict:
        """Read where the stage and the focus stand under the other objective; returns it."""
        if self.reference is None:
            raise RuntimeError("record the reference objective first")
        snap = hardware_snapshot(self.handle)
        if snap["objective"].get("position") == self.reference["objective"].get(
            "position"
        ):
            raise RuntimeError(
                "the objective is still the reference one; change to the other objective first"
            )
        self.other = snap
        return dict(snap)

    def result(self) -> dict:
        """The other objective's offset against the reference: ``{"reference", "other", "translation_um"}``."""
        if self.reference is None or self.other is None:
            raise RuntimeError("record both objectives first")
        translation = [
            self.other[k] - self.reference[k] for k in ("x_um", "y_um", "z_um")
        ]
        return {
            "reference": dict(self.reference["objective"]),
            "other": dict(self.other["objective"]),
            "translation_um": translation,
        }


def adopt_calibration(
    result: dict,
    *,
    calibration_name: str | None = None,
    machine: Any = None,
    moment=None,
) -> dict:
    """Save a measured pair into the calibration in force, as a new snapshot.

    The reference objective must be the calibration's reference already, or
    the calibration must have none yet (then this pair sets it). The other
    objective's offset is its translation against the reference. Returns the
    new calibration and where it was saved.
    """
    if machine is None:
        from ..store import MACHINE

        machine = MACHINE
    try:
        config = model.load_calibration(calibration_name=calibration_name)
    except FileNotFoundError:
        config = {"schema_version": model.SCHEMA_VERSION, "objectives": {}}
    reference = int(result["reference"]["position"])
    other = int(result["other"]["position"])
    translations = model.translations_of(config)
    if translations:
        if translations.get(reference) != (0.0, 0.0, 0.0):
            raise ValueError(
                f"objective position {reference} is not this calibration's reference; measure against the "
                f"reference, position {next(p for p, t in translations.items() if t == (0.0, 0.0, 0.0))}"
            )
    else:
        config = model.calibration_with(
            config, reference, result["reference"].get("name"), (0.0, 0.0, 0.0)
        )
    config = model.calibration_with(
        config, other, result["other"].get("name"), result["translation_um"]
    )
    snapshot = machine.publish_snapshot(
        moment or datetime.now(UTC),
        calibration=config,
        calibration_name=calibration_name,
    )
    return {"calibration": config, "snapshot": str(snapshot)}
