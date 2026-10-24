"""The five Leica setup steps, written against the driver's own setup procedures.

Each step does what its notebook does, in the same order and with the same
safety checks; the wizard only replaces the notebook as the place the operator
presses the buttons. The driver is imported inside the functions, so this file
can be read, and the wizard's page opened, on a computer without LAS X.

Where a notebook is normally saved as the written record of a measurement, the
wizard saves a small ``.json`` record of what it measured instead, in the same
place, so a later reader can still see how the numbers came about.
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .framework import Step, StepError, Wizard

#: The starting limits for everything the corners do not decide. These are the
#: same values the limits notebook starts from. An empty list means "no
#: restriction beyond what the microscope itself allows".
_OTHER_LIMITS = {
    "z_galvo_um": {"range": [-250, 250]},
    "z_wide_um": {"range": [0, 8000]},
}


def _machine():
    from ..store import MACHINE

    return MACHINE


def _record_file(name: str, content: dict) -> Path:
    """Write the wizard's record of a measurement to a file the driver can archive."""
    folder = Path(tempfile.mkdtemp(prefix="zmart_wizard_"))
    path = folder / f"{name}.json"
    path.write_text(json.dumps(content, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _status(done: bool, snapshot: Path | None, note: str | None = None) -> dict:
    """A step's status: done or not, a sentence about it, and when it was saved."""
    if not done or snapshot is None:
        return {"done": done, "note": note if done else None, "saved_at": None}
    return {
        "done": True,
        "note": note or f"{_saved_on(snapshot)}.",
        "saved_at": _saved_at(snapshot),
    }


def _saved_at(snapshot: Path) -> str | None:
    """The time in a saved folder's name, as an ISO time the page can compare."""
    try:
        # The folder's name is a UTC time without the zone written in it.
        moment = datetime.strptime(snapshot.name[:26], "%Y-%m-%dT%H-%M-%S-%f")  # noqa: DTZ007
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).isoformat()


def _saved_on(snapshot: Path) -> str:
    """The time in a saved folder's name, in words: "Saved on 1 October 2026 at 14:05 (UTC)"."""
    try:
        moment = datetime.strptime(snapshot.name[:16], "%Y-%m-%dT%H-%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return f"Saved in {snapshot.name}"
    return f"Saved on {moment.day} {moment:%B %Y} at {moment:%H:%M} (UTC)"


def _read_json(path: Path) -> dict | None:
    """A saved settings file, or None when it is missing or cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _number(values: dict, key: str, label: str) -> float:
    try:
        return float(values[key])
    except (KeyError, TypeError, ValueError):
        raise StepError(f"Please enter a number for {label}.") from None


class LeicaSteps:
    """Holds what the steps share: the microscope connection and each step's work in progress."""

    def __init__(self):
        self._scope = None
        self._corners = None
        self._orientation = None
        self._pair = None

    # -- the connection, made when a step first needs the microscope ----------

    def client(self):
        """Connect to LAS X the first time it is needed, and keep that connection."""
        if self._scope is None:
            try:
                from ...methods import connect

                self._scope = connect(load_calibration=False)
            except Exception as why:
                raise StepError(
                    "Could not reach LAS X. Check that LAS X is running with the CAM "
                    f"server switched on, then try again. ({why})"
                ) from why
        return self._scope.client

    # -- step 1: where the data is saved -----------------------------------

    def data_location_status(self):
        from ..data_location import data_location

        folder = data_location(_machine())
        latest = _machine().latest_snapshot("data_location")
        return _status(
            folder is not None, latest, f"Pictures are saved under {folder}."
        )

    def data_location_check(self, values):
        from ..data_location import check

        problems = check(values.get("folder", "").strip())
        return {"problems": problems}

    def data_location_save(self, values):
        from ..data_location import set_data_location

        try:
            path = set_data_location(
                values.get("folder", "").strip(), machine=_machine()
            )
        except ValueError as why:
            raise StepError(str(why)) from None
        return {"saved_to": str(path)}

    # -- step 2: the safe travel limits ------------------------------------

    def limits_status(self):
        latest = _machine().latest_snapshot("limits")
        return _status(latest is not None, latest)

    def limits_corner(self, values):
        from ..limits.measure_limits import CornerRecorder

        if self._corners is None:
            self._corners = CornerRecorder(self.client())
        number = _number(values, "corner", "the corner")
        if number not in (1, 2, 3, 4):
            raise StepError("The corner must be 1, 2, 3 or 4.")
        try:
            position = self._corners.record(int(number))
        except RuntimeError as why:
            raise StepError(str(why)) from None
        return {"corner": int(number), "position": position}

    def limits_save(self, values):
        from ..limits.config import adopt_limits, validate_limits
        from ..limits.limit_rules import SETTER_LIMIT_KEYS

        if self._corners is None:
            raise StepError("Record the four corners first.")
        try:
            spanned = self._corners.limits()
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        limits = {
            "x_um": {"range": [1000, 130000]},
            "y_um": {"range": [1000, 100000]},
            **_OTHER_LIMITS,
            "objective_slot": [],
            # Every setting the driver can limit is listed, each left free, as
            # in the limits notebook; they can be narrowed later in limits.json.
            **{name: [] for name in SETTER_LIMIT_KEYS},
        }
        limits.update(spanned["limits"])
        try:
            checked = validate_limits(limits)
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        record = _record_file(
            "set_limits_wizard_record", {"corners_um": spanned["points_um"]}
        )
        published = adopt_limits(checked, notebook_paths=[record])
        return {
            "x_um": checked["x_um"]["range"],
            "y_um": checked["y_um"]["range"],
            "saved_to": published["snapshot"],
        }

    # -- step 3: the image orientation -------------------------------------

    def orientation_status(self):
        # The driver fills in an unmeasured default orientation the first time
        # it connects, so a saved file alone does not mean this step was done.
        latest = _machine().latest_snapshot("orientation")
        saved = _read_json(latest / "orientation.json") if latest else None
        done = bool(saved and saved.get("measured"))
        return _status(done, latest)

    def orientation_measure(self, values):
        from ..orientation import measure_orientation as mo

        if self._orientation is None or self._orientation.adopted:
            self._orientation = mo.start_session(
                stage_move_um=_number(
                    {"v": values.get("move_um", 40)}, "v", "the stage move"
                )
            )
        session = mo.measure(self._orientation)
        diagnostic = session.paths.reports_dir / mo.DIAGNOSTIC_NAME
        if not session.d4_accepted:
            raise StepError(
                "The orientation could not be worked out"
                + (f": {session.failure_reason}" if session.failure_reason else ".")
                + " Choose a field with clearly recognisable structure and try again."
            )
        return {
            "label": session.d4_label,
            "mirrored": bool(session.is_mirrored),
            "image": str(diagnostic) if diagnostic.is_file() else None,
        }

    def orientation_check(self, values):
        import numpy as np
        from PIL import Image

        from ..orientation import measure_orientation as mo

        if self._orientation is None:
            raise StepError("Measure the orientation first.")
        image, _saved = mo.acquire_validation_image(self._orientation)
        # The check picture itself is saved as OME-Zarr under validation/; the
        # small preview the page shows goes beside the run's other reports, so
        # it never lands inside that image store.
        preview_path = (
            self._orientation.paths.reports_dir / "orientation_validation_preview.jpg"
        )
        shown = Image.fromarray(
            np.rint(mo._normalise_for_display(image) * 255).astype(np.uint8), "L"
        )
        shown.thumbnail((720, 720))
        shown.save(preview_path, quality=78)
        return {"image": str(preview_path)}

    def orientation_save(self, values):
        from ..orientation import measure_orientation as mo

        if self._orientation is None:
            raise StepError("Measure the orientation first.")
        record = _record_file(
            "set_orientation_wizard_record",
            {
                "orientation": self._orientation.d4_label,
                "mirrored": self._orientation.is_mirrored,
            },
        )
        try:
            adopted = mo.adopt_orientation(self._orientation, record)
        except RuntimeError as why:
            raise StepError(str(why)) from None
        return {"saved_to": adopted["snapshot"]}

    # -- step 4: the objective calibration ---------------------------------

    def calibration_status(self):
        # Likewise, the driver fills in an empty calibration (no objectives) the
        # first time it connects; the step is done once a pair has been measured.
        latest = _machine().latest_snapshot("calibration")
        files = (
            [
                latest / "calibration.json",
                *latest.glob("calibrations/*/calibration.json"),
            ]
            if latest
            else []
        )
        measured = {
            slot
            for path in files
            for slot in ((_read_json(path) or {}).get("objectives") or {})
        }
        done = len(measured) >= 2
        slots = ", ".join(sorted(measured))
        return _status(
            done,
            latest,
            f"{_saved_on(latest)}, for the objectives in slots {slots}."
            if done
            else None,
        )

    def calibration_start(self, values):
        from ..optical_calibration import calibrate_objective_pair as cop

        name = str(values.get("pair_name", "")).strip()
        if not name:
            raise StepError("Give the objective pair a name, for example 10x-20x.")
        self._pair = cop.start_session(
            acquisition_name=name,
            session_id=time.strftime("%Y-%m-%d_calibration_%H%M%S"),
            reference_slot=int(
                _number(values, "reference_slot", "the reference objective's slot")
            ),
        )
        return {"pair": name}

    def calibration_measure(self, values):
        from ..optical_calibration import calibrate_objective_pair as cop

        if self._pair is None:
            raise StepError("Start the calibration first.")
        which = values.get("part")
        parts = {
            "focus_reference": cop.measure_parfocality_reference,
            "focus_target": cop.measure_parfocality_target,
            "xy_reference": cop.measure_parcentricity_reference,
            "xy_target": cop.measure_parcentricity_target_and_save,
        }
        if which not in parts:
            raise StepError("Unknown measurement.")
        try:
            outcome = parts[which](self._pair)
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        p = self._pair
        return {
            "part": which,
            "focus_z_ref_um": p.focus_z_ref_um,
            "translation_z_um": p.translation_z_um,
            "translation_xy_um": list(p.translation_xy_um)
            if p.translation_xy_um
            else None,
            "summary": outcome if isinstance(outcome, dict) else None,
        }

    def calibration_save(self, values):
        from ..optical_calibration import adopt_calibration as adopt

        if self._pair is None or self._pair.translation_xy_um is None:
            raise StepError("Finish all four measurements first.")
        record = _record_file(
            "calibrate_objective_pair_wizard_record",
            {
                "pair": self._pair.acquisition_name,
                "translation_z_um": self._pair.translation_z_um,
                "translation_xy_um": self._pair.translation_xy_um,
            },
        )
        try:
            published = adopt.adopt_calibration(self._pair, notebook_paths=[record])
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        return {"saved_to": published["snapshot"]}

    # -- step 5: the zero point ---------------------------------------------

    def origin_status(self):
        record = _machine().read_origin()
        return _status(record is not None, _machine().latest_snapshot("origin"))

    def origin_set(self, values):
        from ...methods import connect

        scope = connect(load_origin=False)
        try:
            answer = scope.set_origin()
        except RuntimeError as why:
            raise StepError(str(why)) from None
        finally:
            scope.close()
        return {"reference": answer["reference"], "saved_to": answer["origin_file"]}


def leica_wizard() -> Wizard:
    """The Leica STELLARIS 5 wizard, with its five steps in order."""
    s = LeicaSteps()
    return Wizard(
        "Leica STELLARIS 5 setup",
        [
            Step(
                "data_location",
                "Data location",
                "Choose the folder on this computer where every run of this microscope "
                "saves its pictures. It must be on a local disk, because a network drive "
                "that pauses for a moment would pause the microscope too.",
                {"check": s.data_location_check, "save": s.data_location_save},
                s.data_location_status,
            ),
            Step(
                "limits",
                "Travel limits",
                "Teach the software how far the stage may safely travel. You drive the "
                "stage to each of four corners, and the wizard records where it stands.",
                {"corner": s.limits_corner, "save": s.limits_save},
                s.limits_status,
            ),
            Step(
                "orientation",
                "Image orientation",
                "Find out how the camera image is turned compared with the stage, so that "
                "moving the stage right moves the picture the way you expect.",
                {
                    "measure": s.orientation_measure,
                    "check": s.orientation_check,
                    "save": s.orientation_save,
                },
                s.orientation_status,
            ),
            Step(
                "calibration",
                "Objective calibration",
                "Measure how far the sample shifts when you change from one objective to "
                "another, so the software can keep the same spot in view.",
                {
                    "start": s.calibration_start,
                    "measure": s.calibration_measure,
                    "save": s.calibration_save,
                },
                s.calibration_status,
                # The X/Y offsets are measured in the image's axes; a new
                # orientation changes what those offsets mean.
                needs=("orientation",),
            ),
            Step(
                "origin",
                "Zero point",
                "Move the stage to the spot that should count as (0, 0, 0), and focus on "
                "it. Every position is reported relative to this spot.",
                {"set": s.origin_set},
                s.origin_status,
            ),
        ],
    )
