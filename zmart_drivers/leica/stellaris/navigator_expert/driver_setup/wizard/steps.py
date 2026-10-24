"""The Leica steps of the setup wizard, written against the driver's own setup code.

The steps follow the ZMART driver configuration workflow the ZMART Microscopy
operator page used to have: Connect, Define limits, Define coordinate system
origin, Image-to-stage calibration and Objective calibration. Each does what
its notebook does, with the same safety checks; the wizard only replaces the
notebook as the place the operator presses the buttons.

Connect opens one connection to LAS X through the driver's own ``connect()``,
and the steps after it work through that connection. The driver is imported
inside the functions, so this file can be read, and the page opened, on a
computer without LAS X.

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

#: How each stage axis of ``limits.json`` is named on the page.
_AXES = (
    ("x_um", "X", "µm"),
    ("y_um", "Y", "µm"),
    ("z_galvo_um", "Z-galvo", "µm"),
    ("z_wide_um", "Z-wide", "µm"),
)


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


def _suggested_data_folder() -> str:
    """Where pictures go when nothing was chosen yet: the data disk if there is one.

    Only a suggestion, shown in the Connect card's field. The driver itself
    has no default and saves nothing until a folder is confirmed, so pictures
    never land somewhere nobody looked at.
    """
    return r"D:\ZMART-data" if Path("D:/").exists() else r"C:\ZMART-data"


def _figure(fig, path: Path) -> str:
    """Save a matplotlib figure for the page, close it, and answer its path."""
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return str(path)


class LeicaSteps:
    """What the steps share: the one connection to the microscope, and each step's work in progress."""

    def __init__(self):
        self._scope = None
        self._origin = None
        self._orientation = None
        self._pairs: dict[tuple[int, int], object] = {}
        self._calibration_run = time.strftime("%Y-%m-%d_wizard_%H%M%S")

    # -- Connect ------------------------------------------------------------

    def scope(self):
        """The connection Connect opened; every step after it works through this one."""
        if self._scope is None:
            raise StepError("Connect to the microscope first.")
        return self._scope

    def _open(self):
        from ...methods import connect

        try:
            return connect()
        except Exception as why:
            raise StepError(
                "Could not reach LAS X. Check that LAS X is running with the CAM "
                f"server switched on, then try again. ({why})"
            ) from why

    def _reopen(self):
        """Connect again, so what was just saved (the limits) governs from now on."""
        if self._scope is not None:
            self._scope.close()
        self._scope = None
        self._scope = self._open()

    def connect_status(self):
        return {"done": self._scope is not None, "note": None, "saved_at": None}

    def connect_card(self, values):
        from ..data_location import data_location

        saved = data_location(_machine())
        return {
            "microscope": "Leica STELLARIS 5 · stellaris_y42h93",
            "api": "LAS X CAM API · Navigator Expert",
            "data_folder": str(saved) if saved else _suggested_data_folder(),
            "data_folder_saved": saved is not None,
            "connected": self._scope is not None,
            "checks": self._checks() if self._scope is not None else [],
        }

    def connect_connect(self, values):
        from ..data_location import data_location, set_data_location

        folder = str(values.get("data_folder", "")).strip()
        saved = data_location(_machine())
        if not saved or Path(folder) != Path(saved):
            try:
                set_data_location(folder, machine=_machine())
            except ValueError as why:
                raise StepError(f"The data folder cannot be used: {why}") from None
        if self._scope is None:
            self._scope = self._open()
        return {"connected": True, "checks": self._checks()}

    def connect_disconnect(self, values):
        if self._scope is not None:
            self._scope.close()
        self._scope = None
        return {"connected": False}

    def _checks(self) -> list[list[str]]:
        """What Connect found, one row each: what was checked, and what it read."""
        from ... import __version__
        from ...procedures.absolute_coordinate_system import hardware_snapshot
        from ..data_location import data_location

        snap = hardware_snapshot(self._scope.handle)
        objective = snap.get("objective") or {}
        return [
            ["driver", f"navigator_expert {__version__}"],
            [
                "stage",
                f"x {snap['x_um']:.0f} · y {snap['y_um']:.0f} · z-wide {snap['z_wide_um']:.1f} µm",
            ],
            [
                "objective",
                f"slot {objective.get('slotIndex', '?')} · {objective.get('name', '')}".strip(),
            ],
            ["job", str(snap.get("job") or "")],
            ["settings folder", str(_machine().snapshot_root())],
            ["data folder", str(data_location(_machine()))],
        ]

    # -- Define limits --------------------------------------------------------

    def limits_status(self):
        latest = _machine().latest_snapshot("limits")
        return _status(latest is not None, latest)

    def limits_document(self, values):
        """The limits as they stand, every entry of the file, and how to name each row."""
        from ..limits.config import defaults_path, validate_payload
        from ..limits.limit_rules import SETTER_LIMIT_KEYS

        latest = _machine().latest_snapshot("limits")
        source = "published" if latest is not None else "defaults"
        path = latest / "limits.json" if latest is not None else defaults_path()
        document = validate_payload(
            json.loads(Path(path).read_text(encoding="utf-8")), path=Path(path)
        )
        motors = self.scope().motors()
        return {
            "document": document,
            "source": source,
            "axes": [
                {"key": k, "label": label, "unit": unit} for k, label, unit in _AXES
            ],
            "required": [k for k, _, _ in _AXES],
            "measured": ["x_um", "y_um"],
            "slots": {
                "key": "objective_slot",
                "label": "Objective slots",
                "note": "Turret slots count from 0. List the slots automation may turn to, or leave it unticked for all.",
            },
            "settings": list(SETTER_LIMIT_KEYS),
            "actuators": {"x": motors["x"]["options"], "y": motors["y"]["options"]},
        }

    def limits_corner(self, values):
        """Where the chosen motors stand now: one corner of the safe area."""
        here = self.scope().position()
        x_motor = str(values.get("x_motor") or "motoric")
        y_motor = str(values.get("y_motor") or "motoric")
        try:
            x = float(here["x"]["actuator_readings"][x_motor])
            y = float(here["y"]["actuator_readings"][y_motor])
        except (KeyError, TypeError, ValueError):
            raise StepError(
                "Could not read the stage position from those motors."
            ) from None
        return {"x": x, "y": y}

    def limits_publish(self, values):
        from ..limits.config import adopt_limits, validate_payload

        try:
            checked = validate_payload(values.get("document"))
            published = adopt_limits(
                checked,
                notebook_paths=[
                    _record_file(
                        "set_limits_wizard_record",
                        {"limits": checked, "corners_um": values.get("corners")},
                    )
                ],
            )
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        # The driver reads its limits when it connects, so it connects again:
        # from here on the new limits fence every move of the next steps.
        self._reopen()
        return {"saved_to": published["snapshot"]}

    # -- Define coordinate system origin -------------------------------------

    def origin_status(self):
        return _status(
            _machine().read_origin() is not None, _machine().latest_snapshot("origin")
        )

    def origin_standing(self, values):
        record = _machine().read_origin()
        return {"origin": (record or {}).get("origin")}

    def origin_read(self, values):
        """Every drive's reading now, and the objective: what Save and adopt will make (0, 0, 0)."""
        from ...procedures.absolute_coordinate_system import hardware_snapshot

        snap = hardware_snapshot(self.scope().handle)
        self._origin = snap
        return self._origin_rows(snap)

    @staticmethod
    def _origin_rows(snap: dict) -> dict:
        objective = snap.get("objective") or {}
        return {
            "actuators": [
                ["x motoric", f"{snap['x_um']:.2f} µm"],
                ["y motoric", f"{snap['y_um']:.2f} µm"],
                ["z-wide", f"{snap['z_wide_um']:.2f} µm"],
                ["z-galvo", f"{snap['z_galvo_um']:.2f} µm"],
                [
                    "objective",
                    f"slot {objective.get('slotIndex', '?')} · {objective.get('name', '')}".strip(),
                ],
            ],
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_wide_um"] + snap["z_galvo_um"],
        }

    def origin_publish(self, values):
        from ...procedures.absolute_coordinate_system import hardware_snapshot

        if self._origin is None:
            raise StepError("Read first.")
        now = hardware_snapshot(self.scope().handle)
        moved = [
            k
            for k in ("x_um", "y_um", "z_wide_um", "z_galvo_um")
            if abs(now[k] - self._origin[k]) > 0.5
        ]
        if moved or now.get("objective") != self._origin.get("objective"):
            raise StepError(
                "The stage, the focus or the objective changed since Read. Read again, then Save and adopt."
            )
        try:
            answer = self.scope().set_origin()
        except RuntimeError as why:
            raise StepError(str(why)) from None
        return {"saved_to": answer["origin_file"], **self._origin_rows(now)}

    # -- Image-to-stage calibration ------------------------------------------

    def orientation_status(self):
        # The driver fills in an unmeasured default orientation the first time
        # it connects, so a saved file alone does not mean this step was done.
        latest = _machine().latest_snapshot("orientation")
        saved = _read_json(latest / "orientation.json") if latest else None
        return _status(bool(saved and saved.get("measured")), latest)

    def orientation_standing(self, values):
        """What is saved: the orientation, and the sheet it was measured from when kept."""
        from ..orientation import measure_orientation as mo

        latest = _machine().latest_snapshot("orientation")
        saved = _read_json(latest / "orientation.json") if latest else None
        if not (saved and saved.get("measured")):
            return {"measured": False}
        sheet = latest / "reports" / mo.DIAGNOSTIC_NAME
        return {
            "measured": True,
            "rotation_deg": saved.get("rotation_deg"),
            "reflection": bool(saved.get("reflection")),
            "image": str(sheet) if sheet.is_file() else None,
        }

    def orientation_measure(self, values):
        from ..orientation import measure_orientation as mo

        self.scope()
        if self._orientation is None or self._orientation.adopted:
            move = _number(
                {"v": values.get("stage_move_um", 40)}, "v", "the stage move"
            )
            self._orientation = mo.start_session(stage_move_um=move)
        session = mo.measure(self._orientation)
        sheet = session.paths.reports_dir / mo.DIAGNOSTIC_NAME
        answer = {
            "accepted": bool(session.d4_accepted),
            "label": session.d4_label,
            "reflection": bool(session.is_mirrored),
            "image": str(sheet) if sheet.is_file() else None,
        }
        if not session.d4_accepted:
            answer["why"] = (
                "The orientation could not be worked out"
                + (f": {session.failure_reason}" if session.failure_reason else "")
                + ". Choose a field with clearly recognisable structure, in focus, and Rerun."
            )
        return answer

    def orientation_check(self, values):
        import numpy as np
        from PIL import Image

        from ..orientation import measure_orientation as mo

        if self._orientation is None or not self._orientation.d4_accepted:
            raise StepError("Measure the orientation first.")
        image, _saved = mo.acquire_validation_image(self._orientation)
        # The check picture itself is saved as OME-Zarr under validation/; the
        # small preview the page shows goes beside the run's other reports, so
        # it never lands inside that image store.
        preview = (
            self._orientation.paths.reports_dir / "orientation_validation_preview.jpg"
        )
        shown = Image.fromarray(
            np.rint(mo._normalise_for_display(image) * 255).astype(np.uint8), "L"
        )
        shown.thumbnail((720, 720))
        shown.save(preview, quality=78)
        return {"image": str(preview)}

    def orientation_publish(self, values):
        from ..orientation import measure_orientation as mo

        if self._orientation is None:
            raise StepError("Measure first.")
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

    # -- Objective calibration ------------------------------------------------

    def calibration_status(self):
        # The driver fills in an empty calibration (no objectives) the first
        # time it connects; the step is done once a pair has been measured.
        latest = _machine().latest_snapshot("calibration")
        measured = self._published_offsets(latest)
        done = len(measured) >= 2
        slots = ", ".join(str(s) for s in sorted(measured))
        return _status(
            done,
            latest,
            f"{_saved_on(latest)}, for the objectives in slots {slots}."
            if done
            else None,
        )

    @staticmethod
    def _published_offsets(latest: Path | None) -> dict[int, list[float]]:
        if latest is None:
            return {}
        files = [
            latest / "calibration.json",
            *latest.glob("calibrations/*/calibration.json"),
        ]
        offsets = {}
        for path in files:
            for slot, entry in (
                (_read_json(path) or {}).get("objectives") or {}
            ).items():
                if isinstance(entry, dict) and entry.get("translation_um") is not None:
                    offsets[int(slot)] = [float(v) for v in entry["translation_um"]]
        return offsets

    def calibration_lenses(self, values):
        """The objectives on the turret, and the offsets the saved calibration holds for them."""
        from ...procedures import changes, readings

        hw = readings.get_hardware_info(self.scope().client)
        lenses = [
            {
                "slot": slot,
                "name": str(entry.get("name") or "").strip() or f"slot {slot}",
            }
            for slot, entry in sorted(changes.objective_by_slot(hw).items())
        ]
        offsets = self._published_offsets(_machine().latest_snapshot("calibration"))
        reference = next(
            (s for s, t in offsets.items() if all(v == 0 for v in t)), None
        )
        return {
            "lenses": lenses,
            "reference": reference,
            "held": {str(s): t for s, t in offsets.items()},
        }

    def _pair(self, values):
        """The objective-pair measurement for the chosen reference and target, started once."""
        from ..optical_calibration import calibrate_objective_pair as cop

        reference = int(_number(values, "reference", "the reference objective"))
        target = int(_number(values, "target", "the target objective"))
        key = (reference, target)
        if key not in self._pairs:
            try:
                self._pairs[key] = cop.start_session(
                    session_id=self._calibration_run,
                    reference_slot=reference,
                    acquisition_name=f"slot{reference}-slot{target}",
                )
            except (RuntimeError, ValueError) as why:
                raise StepError(str(why)) from None
        return self._pairs[key], reference, target

    def calibration_focus(self, values):
        """Measure the focus under the reference or the target objective (a z-stack each)."""
        import numpy as np

        from ..optical_calibration import calibrate_objective_pair as cop
        from ..optical_calibration.calibration_common import plot_brenner_curve

        self.scope()
        session, reference, target = self._pair(values)
        side = values.get("side")
        try:
            if side == "reference":
                cop.measure_parfocality_reference(session)
                z, scores, peak = (
                    session.ref_z_positions_um,
                    session.ref_z_brenner,
                    session.focus_z_ref_um,
                )
                stack = session.ref_z_stack
            elif side == "target":
                cop.measure_parfocality_target(session)
                z, scores, peak = (
                    session.target_z_positions_um,
                    session.target_z_brenner,
                    session.focus_z_target_um,
                )
                stack = session.target_z_stack
            else:
                raise StepError("Unknown measurement.")
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        if (
            side == "target"
            and session.to_slot is not None
            and int(session.to_slot) != target
        ):
            raise StepError(
                f"The objective in place is slot {session.to_slot}, not the target slot {target}. "
                "Switch to the target objective and measure again."
            )
        lens = reference if side == "reference" else target
        slice_ = (
            stack[int(np.argmin(np.abs(np.asarray(z, dtype=float) - peak)))]
            if stack is not None
            else None
        )
        image = _figure(
            plot_brenner_curve(z, scores, peak, focus_image=slice_),
            session.paths.reports_dir / f"{side}_focus.png",
        )
        return {
            "lens": lens,
            "name": session.hardware_objectives.get(lens, f"slot {lens}"),
            "peak_z_um": peak,
            "translation_z_um": session.translation_z_um,
            "image": image,
        }

    def calibration_xy(self, values):
        """Take the X/Y picture under the reference, then under the target, and match the two."""
        from ..optical_calibration import calibrate_objective_pair as cop
        from ..optical_calibration.calibration_common import plot_overlay

        self.scope()
        session, reference, _target = self._pair(values)
        side = values.get("side")
        try:
            if side == "reference":
                cop.measure_parcentricity_reference(session)
                return {
                    "lens": reference,
                    "name": session.hardware_objectives.get(reference, ""),
                }
            if side != "target":
                raise StepError("Unknown measurement.")
            summary = cop.measure_parcentricity_target_and_save(session)
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        trusted = bool((session.registration or {}).get("trusted"))
        image = None
        if session.ref_image is not None and session.target_image is not None:
            image = _figure(
                plot_overlay(
                    session.ref_image,
                    session.target_image,
                    "Reference (magenta) vs target (green), as acquired",
                    corrected_target=session.corrected_target_image,
                ),
                session.paths.reports_dir / "objective_pair.png",
            )
        txy = session.translation_xy_um or (None, None)
        return {
            "translation_um": {"x": txy[0], "y": txy[1], "z": session.translation_z_um},
            "trusted": trusted,
            "why": None
            if trusted
            else (session.failure_reason or "the two pictures could not be matched"),
            "image": image,
            "summary": summary if isinstance(summary, dict) else None,
        }

    def calibration_publish(self, values):
        from ..optical_calibration import adopt_calibration as adopt

        measured = [s for s in self._pairs.values() if s.translation_xy_um is not None]
        if not measured:
            raise StepError("Measure a preset first.")
        session = measured[-1]
        record = _record_file(
            "calibrate_objective_pair_wizard_record",
            {
                f"slot{s.from_slot}-slot{s.to_slot}": {
                    "translation_xy_um": s.translation_xy_um,
                    "translation_z_um": s.translation_z_um,
                }
                for s in measured
            },
        )
        try:
            published = adopt.adopt_calibration(session, notebook_paths=[record])
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        return {"saved_to": published["snapshot"]}


def leica_wizard() -> Wizard:
    """The Leica STELLARIS 5 wizard: the ZMART driver configuration workflow's five steps."""
    s = LeicaSteps()
    return Wizard(
        "Leica STELLARIS 5 setup",
        [
            Step(
                "connect",
                "Connect",
                "Connect to LAS X and confirm the folder this microscope saves its pictures in.",
                {
                    "card": s.connect_card,
                    "connect": s.connect_connect,
                    "disconnect": s.connect_disconnect,
                },
                s.connect_status,
            ),
            Step(
                "limits",
                "Define limits",
                "Say how far the stage may travel and what the driver is allowed to set, then "
                "save and adopt it; until this exists the driver uses its cautious defaults.",
                {
                    "document": s.limits_document,
                    "corner": s.limits_corner,
                    "publish": s.limits_publish,
                },
                s.limits_status,
            ),
            Step(
                "origin",
                "Define coordinate system origin",
                "Drive to the point the experiments should count from and make it (0, 0, 0).",
                {
                    "standing": s.origin_standing,
                    "read": s.origin_read,
                    "publish": s.origin_publish,
                },
                s.origin_status,
            ),
            Step(
                "orientation",
                "Image-to-stage calibration",
                "Measure which way the picture is turned relative to the stage.",
                {
                    "standing": s.orientation_standing,
                    "measure": s.orientation_measure,
                    "check": s.orientation_check,
                    "publish": s.orientation_publish,
                },
                s.orientation_status,
            ),
            Step(
                "calibration",
                "Objective calibration",
                "Measure where, and at what height, each objective looks relative to one reference objective.",
                {
                    "lenses": s.calibration_lenses,
                    "focus": s.calibration_focus,
                    "xy": s.calibration_xy,
                    "publish": s.calibration_publish,
                },
                s.calibration_status,
                # The X/Y offsets are measured on pictures laid down with the
                # orientation; a new orientation changes what they mean.
                needs=("orientation",),
            ),
        ],
    )
