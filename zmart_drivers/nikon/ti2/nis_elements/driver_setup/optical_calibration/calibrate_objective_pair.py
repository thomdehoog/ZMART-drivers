"""Measure the offset between two objectives, with the operator centring the same feature under each.

The measurement is a conversation between the operator and the driver:

1. Under the first objective, centre a small feature in the picture and
   bring it into focus (joystick, or NIS-Elements). Record.
2. Change to the second objective. The driver turns the nosepiece and moves
   nothing else. Centre and focus the same feature again. Record.
3. Adopt: the difference between the two recorded stage positions is the
   offset of the second objective relative to the first.

The first objective recorded becomes the reference (offset zero) unless the
saved calibration already has one; then every new slot is measured against
it, so all offsets share one reference.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ...procedures import absolute_coordinate_system as _frame
from ...procedures import changes as _changes
from . import model


class ObjectivePairRecorder:
    """Records where the stage stands with the same feature centred under each objective."""

    def __init__(self, handle) -> None:
        self.handle = handle
        self.recorded: dict[int, dict] = {}

    def record(self) -> dict:
        """Read the stage and the objective in use, and keep them for that slot (recording again replaces it)."""
        self.handle.require_open()
        snap = _frame.hardware_snapshot(self.handle)
        objective = snap["objective"]
        if objective is None:
            raise RuntimeError(
                "NIS-Elements does not report which objective is in use; the nosepiece may be absent"
            )
        self.recorded[objective["slot"]] = {
            "name": objective["name"],
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_um"],
        }
        print(
            f"slot {objective['slot']} ({objective['name']}): x {snap['x_um']:.1f}, y {snap['y_um']:.1f}, "
            f"z {snap['z_um']:.1f} um"
        )
        return dict(self.recorded[objective["slot"]])

    def change_objective(self, slot: int) -> dict:
        """Turn the nosepiece to *slot* and nothing else; the operator then centres the feature again."""
        self.handle.require_open()
        result = _changes.set_objective(self.handle.client, slot)
        if not result.get("success"):
            raise RuntimeError(
                f"the objective change to slot {slot} failed: {result.get('message')}"
            )
        return result

    def translations(self) -> dict[int, dict]:
        """The offsets of the recorded slots, relative to the reference objective.

        The reference is the one the saved calibration already has, when a
        recorded slot is it; else the first slot recorded, which gets offset
        zero. Every other recorded slot's offset is its position minus the
        reference's.
        """
        if len(self.recorded) < 2:
            raise RuntimeError("record the feature under at least two objectives first")
        saved, _ = model.load_calibration()
        saved_translations = model.translations_of(saved)
        reference = next(
            (
                slot
                for slot, t in saved_translations.items()
                if t == (0.0, 0.0, 0.0) and slot in self.recorded
            ),
            None,
        )
        if reference is None:
            reference = next(iter(self.recorded))
        base = self.recorded[reference]
        return {
            slot: {
                "name": entry["name"],
                "translation_um": [
                    entry["x_um"] - base["x_um"],
                    entry["y_um"] - base["y_um"],
                    entry["z_um"] - base["z_um"],
                ],
            }
            for slot, entry in self.recorded.items()
        }

    def adopt(self, notebook_paths=()) -> dict:
        """Save the measured offsets into this microscope's calibration, and use them in this session."""
        saved = model.adopt_calibration(
            self.translations(), archive_paths=notebook_paths
        )
        translations, info = model.load_objective_calibration()
        self.handle.translations = translations
        self.handle.calibration_info = info
        return saved
