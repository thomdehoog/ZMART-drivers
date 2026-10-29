"""The ZEISS steps of the setup wizard, written against the driver's own setup code.

The five steps are those of the Leica driver's wizard: Connect, Define
limits, Define coordinate system origin, Image-to-stage calibration and
Objective calibration. Each does what its notebook does, with the same safety
checks; the wizard only replaces the notebook as the place the operator
presses the buttons.

Connect opens one connection to ZEN, through the ZEN API Gateway, with the
driver's own ``connect()``; the steps after it work through that connection.
The driver is imported inside the functions, so this file can be read, and
the page opened, on a computer without ZEN.

Where a notebook is normally saved as the written record of a measurement,
the wizard saves a small ``.json`` record of what it measured instead, in the
same place, so a later reader can still see how the numbers came about.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .framework import Step, StepError, Wizard

#: How each stage axis of ``limits.json`` is named on the page. The focus
#: drive is ZEN's z: it moves the objective (or the stage) up and down.
_AXES = (
    ("x_um", "X", "µm"),
    ("y_um", "Y", "µm"),
    ("z_um", "Focus (Z)", "µm"),
)

#: How the other entries of ``limits.json`` are named on the page, and a
#: sentence on what each one limits.
_SETTINGS = {
    "objective_position": (
        "Objective positions",
        (
            "Changer positions count from 1, as in ZEN. List the positions automation may turn to, "
            "or leave it unticked for all."
        ),
    ),
    "experiment": (
        "Experiments",
        "The ZEN experiments automation may load and run, by name. Leave it unticked for all.",
    ),
    "set_z_stack": (
        "Z-stack ends (µm)",
        (
            "How high and how low a z-stack's first and last slice may be set, in ZEN's focus numbers. "
            "Every end also stays within the focus range above."
        ),
    ),
}

#: The file name of the orientation sheet: the three snaps side by side.
SHEET_NAME = "orientation_sheet.png"


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
        moment = datetime.strptime(snapshot.name[:26], "%Y-%m-%dT%H-%M-%S-%f")
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC).isoformat()


def _saved_on(snapshot: Path) -> str:
    """The time in a saved folder's name, in words: "Saved on 1 October 2026 at 14:05 (UTC)"."""
    try:
        moment = datetime.strptime(snapshot.name[:16], "%Y-%m-%dT%H-%M").replace(
            tzinfo=UTC
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


def _objective_words(objective: dict | None) -> str:
    """An objective as the page shows it: "position 2 · Plan-Apochromat 20x/0.8"."""
    objective = objective or {}
    return f"position {objective.get('position', '?')} · {objective.get('name') or ''}".strip(
        " ·"
    )


class ZeissSteps:
    """What the steps share: the one connection to the microscope, and each step's work in progress."""

    def __init__(self):
        self._scope = None
        # What Connect was given, so that connecting again goes the same way.
        self._connection: dict = {}
        self._origin = None
        self._orientation = None
        self._sheet: Path | None = None
        # The objective pairs measured in this window, by (reference, target).
        self._pairs: dict[tuple[int, int], object] = {}
        # The wizard opens on the most recent config; the steps' green follows the one chosen.
        self._use(None)

    # -- Connect ------------------------------------------------------------

    def scope(self):
        """The connection Connect opened; every step after it works through this one."""
        if self._scope is None:
            raise StepError("Connect to the microscope first.")
        return self._scope

    def _open(self):
        from ...methods import connect

        try:
            return connect(**self._connection)
        except ValueError as why:
            # The connection was refused before ZEN was touched: say why as it is.
            raise StepError(str(why)) from None
        except Exception as why:
            raise StepError(
                "Could not reach ZEN. Check that ZEN is running, that the ZEN API Gateway is "
                "started (its icon is in the Windows tray), and that the control token is "
                f"right, then try again. ({why})"
            ) from why

    def _reopen(self):
        """Connect again, so what was just saved governs from now on."""
        if self._scope is not None:
            self._scope.close()
        self._scope = None
        self._scope = self._open()

    def connect_status(self):
        return {"done": self._scope is not None, "note": None, "saved_at": None}

    def _use(self, name: str | None) -> str | None:
        """Work in config *name* from now on: the steps read and save there. None, the most recent."""
        from ..store import use_configuration

        kept = _machine().configurations()
        if name is None:
            name = kept[0] if kept else None
        use_configuration(name)
        self._config = name
        return name

    def _previous_data_folder(self) -> str:
        """The output folder a config without one is offered: the most recent config's, else a suggestion."""
        root = _machine().api_root()
        for name in _machine().configurations():
            saved = sorted((root / name / "data_location").glob("*/data_location.json"))
            record = _read_json(saved[-1]) if saved else None
            if isinstance(record, dict) and record.get("folder"):
                return str(record["folder"])
        return _suggested_data_folder()

    def connect_choose(self, values):
        """Choose the config, before connecting: a kept one by name, or (empty) a new one.

        A new config is named now and made on Connect; until then nothing is
        saved in it, so no step is done in it.
        """
        name = str(values.get("config") or "").strip()
        if self._scope is not None:
            raise StepError("Disconnect first to choose another config.")
        if name and name not in _machine().configurations():
            raise StepError(f"There is no config {name!r} on this computer.")
        return {"config": self._use(name or _machine().new_configuration_name())}

    def connect_card(self, values):
        from ...methods import DEFAULT_CONNECTION
        from ...primitives.gateway.client import TOKEN_FILE
        from ..data_location import data_location

        saved = data_location(_machine())
        return {
            # The ZMART Controller's connection words, filled as this driver
            # connects; microscope and API belong to the driver.
            "microscope": DEFAULT_CONNECTION["microscope"],
            "api_type": DEFAULT_CONNECTION["api_type"],
            "host": self._connection.get("host") or DEFAULT_CONNECTION["host"],
            "port": str(self._connection.get("port") or DEFAULT_CONNECTION["port"]),
            "client": self._connection.get("client") or DEFAULT_CONNECTION["client"],
            # On the ZEN computer itself the gateway's token file can stand in
            # for the typed token; the page says so beside the field.
            "token_file": TOKEN_FILE if Path(TOKEN_FILE).is_file() else None,
            # The config in use, and the ones kept here, most recent first.
            "config": self._config or "",
            "configs": _machine().configurations(),
            "output_root": str(saved) if saved else self._previous_data_folder(),
            "output_root_saved": saved is not None,
            "connected": self._scope is not None,
            "checks": self._checks() if self._scope is not None else [],
        }

    def connect_connect(self, values):
        from ...methods import checked_connection
        from ..data_location import data_location, set_data_location

        # Kept for connecting again; the token only for as long as the wizard runs.
        # The config is the one chosen above the card (connect_choose).
        self._connection = {
            key: str(values.get(key) or "").strip() or None
            for key in ("host", "port", "client", "password")
        }
        self._connection["config"] = self._config
        # A connection that will be refused saves nothing, not even the folder.
        try:
            checked_connection({**self._connection, "config": None})
        except ValueError as why:
            raise StepError(str(why)) from None
        # A new config is made now: its folder, then its output folder in it.
        made = None
        if self._config is not None and self._config not in _machine().configurations():
            made = _machine().api_root() / self._config
            made.mkdir(parents=True)
        folder = str(values.get("output_root", "")).strip()
        saved = data_location(_machine())
        if not saved or Path(folder) != Path(saved):
            try:
                set_data_location(folder, machine=_machine())
            except ValueError as why:
                if made is not None:
                    shutil.rmtree(made, ignore_errors=True)
                raise StepError(f"The output folder cannot be used: {why}") from None
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

        handle = self._scope.handle
        snap = hardware_snapshot(handle)
        runtime = handle.client.runtime or {}
        return [
            ["driver", f"zen_api_grpc {__version__}"],
            ["ZEN API", f"zen_api {runtime.get('zen_api_version') or '?'}"],
            [
                "stage",
                f"x {snap['x_um']:.0f} · y {snap['y_um']:.0f} µm · focus {snap['z_um']:.1f} µm",
            ],
            ["objective", _objective_words(snap.get("objective"))],
            ["experiment", (handle.experiment or {}).get("name") or "none loaded"],
            ["ZEN image folder", str(handle.image_output_path or "")],
            ["settings folder", str(_machine().snapshot_root())],
            ["output folder", str(data_location(_machine()))],
        ]

    # -- Define limits --------------------------------------------------------

    def limits_status(self):
        latest = _machine().latest_snapshot("limits")
        return _status(latest is not None, latest)

    def limits_document(self, values):
        """The limits as they stand, every entry of the file, and how to name each row."""
        from ..limits.config import defaults_path, validate_payload

        self.scope()
        latest = _machine().latest_snapshot("limits")
        source = "published" if latest is not None else "defaults"
        path = latest / "limits.json" if latest is not None else defaults_path()
        document = validate_payload(
            json.loads(Path(path).read_text(encoding="utf-8")), path=Path(path)
        )
        return {
            "document": document,
            "source": source,
            "axes": [
                {"key": k, "label": label, "unit": unit} for k, label, unit in _AXES
            ],
            "required": [k for k, _, _ in _AXES],
            "measured": ["x_um", "y_um"],
            "focus": "z_um",
            "settings": {
                key: {"label": label, "note": note}
                for key, (label, note) in _SETTINGS.items()
            },
            # Kept as lists of numbers on the page; the experiment names stay words.
            "allowed_only": ["objective_position", "experiment"],
            "words": ["experiment"],
        }

    def limits_corner(self, values):
        """Where the stage stands now, in ZEN's own numbers: one corner of the safe area."""
        from ...procedures import readings

        here = readings.get_xy(self.scope().handle.client)
        try:
            return {"x": float(here["x_um"]), "y": float(here["y_um"])}
        except (KeyError, TypeError, ValueError):
            raise StepError("Could not read the stage position from ZEN.") from None

    def limits_focus_end(self, values):
        """Where the focus stands now, in ZEN's own numbers: the low or the high end of its safe travel."""
        from ...procedures import readings

        end = values.get("end")
        if end not in ("low", "high"):
            raise StepError("Unknown focus end.")
        here = readings.get_z(self.scope().handle.client)
        try:
            return {"end": end, "z": float(here["z_um"])}
        except (KeyError, TypeError, ValueError):
            raise StepError("Could not read the focus position from ZEN.") from None

    def limits_publish(self, values):
        from ..limits.config import adopt_limits, validate_payload

        self.scope()
        try:
            checked = validate_payload(values.get("document"))
            published = adopt_limits(
                checked,
                notebook_paths=[
                    _record_file(
                        "set_limits_wizard_record",
                        {
                            "limits": checked,
                            "corners_um": values.get("corners"),
                            "focus_um": values.get("focus"),
                        },
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
        return {
            "actuators": [
                ["x (stage)", f"{snap['x_um']:.2f} µm"],
                ["y (stage)", f"{snap['y_um']:.2f} µm"],
                ["z (focus)", f"{snap['z_um']:.2f} µm"],
                ["objective", _objective_words(snap.get("objective"))],
            ],
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_um"],
        }

    def origin_publish(self, values):
        from ...procedures.absolute_coordinate_system import hardware_snapshot

        if self._origin is None:
            raise StepError("Read first.")
        now = hardware_snapshot(self.scope().handle)
        moved = [
            k for k in ("x_um", "y_um", "z_um") if abs(now[k] - self._origin[k]) > 0.5
        ]
        if moved or now["objective"].get("position") != self._origin["objective"].get(
            "position"
        ):
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
        """What is saved: the orientation, its pixel size, and the sheet it was measured from when kept."""
        latest = _machine().latest_snapshot("orientation")
        saved = _read_json(latest / "orientation.json") if latest else None
        if not (saved and saved.get("measured")):
            return {"measured": False}
        sheet = latest / "data" / "notebook" / SHEET_NAME
        frame = saved.get("frame") or {}
        return {
            "measured": True,
            "rotation_deg": saved.get("rotation_deg"),
            "reflection": bool(saved.get("reflection")),
            "pixel_size_um": frame.get("pixel_size_um"),
            "image": str(sheet) if sheet.is_file() else None,
        }

    def orientation_experiments(self, values):
        """The experiments ZEN can load, and the one loaded now; the snaps use the loaded one."""
        listed = self.scope().experiments()
        return {"experiments": listed["experiments"], "loaded": listed["loaded"]}

    def orientation_measure(self, values):
        """Load the chosen experiment if needed, take the three snaps, and work out the turn and the pixel size."""
        from ..orientation import measure_orientation as mo
        from . import picture

        scope = self.scope()
        name = str(values.get("experiment") or "").strip()
        if not name:
            raise StepError("Choose the experiment the snaps should use.")
        if name != (scope.handle.experiment or {}).get("name"):
            try:
                scope.load_experiment(name)
            except (RuntimeError, ValueError) as why:
                raise StepError(f"The experiment could not be loaded: {why}") from None
        move = _number({"v": values.get("move_um", mo.MOVE_UM)}, "v", "the stage move")
        self._orientation = None
        try:
            measured = mo.measure_orientation(scope.handle, move_um=move)
        except RuntimeError as why:
            return {
                "accepted": False,
                "why": f"The orientation could not be worked out: {why}. Choose a field with "
                "clearly recognisable structure, in focus, and Rerun.",
            }
        folder = Path(tempfile.mkdtemp(prefix="zmart_wizard_"))
        self._sheet = picture.write_png(
            folder / SHEET_NAME, picture.sheet(measured["planes"])
        )
        self._orientation = measured
        orientation = measured["orientation"]
        return {
            "accepted": True,
            "rotation_deg": int(orientation.rotate_deg),
            "reflection": bool(orientation.mirrored),
            "pixel_size_um": measured["frame"]["pixel_size_um"],
            "image_size_px": measured["frame"]["image_size_px"],
            "clarity": measured["clarity"],
            "description": mo.describe(measured),
            "image": str(self._sheet),
        }

    def orientation_publish(self, values):
        from ..orientation import measure_orientation as mo

        if self._orientation is None:
            raise StepError("Measure first.")
        measured = self._orientation
        record = _record_file(
            "set_orientation_wizard_record",
            {
                "rotation_deg": measured["document"]["rotation_deg"],
                "reflection": measured["document"]["reflection"],
                "frame": measured["frame"],
                "matrix": measured["matrix"],
                "clarity": measured["clarity"],
                "shifts_px": measured["shifts_px"],
            },
        )
        kept = [record] + (
            [self._sheet] if self._sheet and self._sheet.is_file() else []
        )
        try:
            adopted = mo.adopt_orientation(measured, notebook_paths=kept)
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        # The driver reads the orientation, and the pixel size measured with
        # it, when it connects; connecting again puts them to use.
        self._reopen()
        return {"saved_to": adopted["snapshot"]}

    # -- Objective calibration ------------------------------------------------

    def calibration_status(self):
        # The driver fills in an empty calibration (no objectives) the first
        # time it connects; the step is done once a pair has been measured.
        latest = _machine().latest_snapshot("calibration")
        measured = self._published_offsets(latest)
        done = len(measured) >= 2
        positions = ", ".join(str(p) for p in sorted(measured))
        return _status(
            done,
            latest,
            f"{_saved_on(latest)}, for the objectives at positions {positions}."
            if done
            else None,
        )

    @staticmethod
    def _published_offsets(latest: Path | None) -> dict[int, list[float]]:
        if latest is None:
            return {}
        saved = _read_json(latest / "calibration.json") or {}
        offsets = {}
        for position, entry in (saved.get("objectives") or {}).items():
            if isinstance(entry, dict) and entry.get("translation_um") is not None:
                offsets[int(position)] = [float(v) for v in entry["translation_um"]]
        return offsets

    def calibration_lenses(self, values):
        """The objectives on the changer, the one in place, and the offsets the saved calibration holds."""
        from ...procedures import readings

        client = self.scope().handle.client
        lenses = [
            {
                "position": int(o["position"]),
                "name": str(o.get("name") or "").strip() or f"position {o['position']}",
                "magnification": o.get("magnification"),
            }
            for o in readings.get_objectives(client) or []
        ]
        offsets = self._published_offsets(_machine().latest_snapshot("calibration"))
        reference = next(
            (p for p, t in offsets.items() if all(v == 0 for v in t)), None
        )
        return {
            "lenses": lenses,
            "in_place": (readings.get_objective(client) or {}).get("position"),
            "reference": reference,
            "held": {str(p): t for p, t in offsets.items()},
        }

    def _pair(self, values):
        """The measurement of the chosen reference and target, started once per pair."""
        from ..optical_calibration.calibrate_objective_pair import PairCalibration

        reference = int(_number(values, "reference", "the reference objective"))
        target = int(_number(values, "target", "the target objective"))
        if reference == target:
            raise StepError("The target must be another objective than the reference.")
        key = (reference, target)
        if key not in self._pairs:
            self._pairs[key] = PairCalibration(self.scope().handle)
        return self._pairs[key], reference, target

    def calibration_turn(self, values):
        """Turn the changer to an objective, moving nothing else; the operator then finds the feature again."""
        from ...procedures import changes, readings

        client = self.scope().handle.client
        position = int(_number(values, "position", "the objective"))
        result = changes.set_objective(
            client, readings.get_objectives(client) or [], position=position
        )
        if not result.get("success"):
            raise StepError(
                f"The objective could not be changed: {result.get('message')}"
            )
        return {"in_place": (readings.get_objective(client) or {}).get("position")}

    def calibration_record(self, values):
        """Read where the stage and the focus stand, under the reference or under the target objective."""
        session, reference, target = self._pair(values)
        side = values.get("side")
        expected = {"reference": reference, "target": target}.get(side)
        if expected is None:
            raise StepError("Unknown measurement.")
        from ...procedures import readings

        in_place = (readings.get_objective(session.handle.client) or {}).get("position")
        if in_place != expected:
            raise StepError(
                f"The objective in place is at position {in_place}, not the {side} at position "
                f"{expected}. Turn to the {side} objective first."
            )
        try:
            if side == "reference":
                snap = session.record_reference()
                return self._pair_answer(snap, None)
            snap = session.record_other()
            return self._pair_answer(snap, session.result())
        except RuntimeError as why:
            raise StepError(str(why)) from None

    @staticmethod
    def _pair_answer(snap: dict, result: dict | None) -> dict:
        answer = {
            "position": snap["objective"].get("position"),
            "name": snap["objective"].get("name"),
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_um"],
        }
        if result is not None:
            x, y, z = result["translation_um"]
            answer["translation_um"] = {"x": x, "y": y, "z": z}
        return answer

    def calibration_publish(self, values):
        from ..optical_calibration.calibrate_objective_pair import adopt_pairs

        results = []
        for session in self._pairs.values():
            try:
                results.append(session.result())
            except RuntimeError:
                continue  # a pair not finished here is left out
        if not results:
            raise StepError(
                "Measure an objective first: record the reference, turn, and record the target."
            )
        # The pairs measured against the reference the page shows; by default
        # the reference of the last pair measured.
        chosen = values.get("reference")
        reference = (
            int(_number(values, "reference", "the reference objective"))
            if chosen not in (None, "")
            else int(results[-1]["reference"]["position"])
        )
        results = [r for r in results if int(r["reference"]["position"]) == reference]
        if not results:
            raise StepError(
                f"No objective was measured against the reference at position {reference} yet."
            )
        saved = self._published_offsets(_machine().latest_snapshot("calibration"))
        saved_reference = next(
            (p for p, t in saved.items() if all(v == 0 for v in t)), None
        )
        # A different reference than the saved one starts a new calibration:
        # the old offsets were measured against another objective.
        fresh = saved_reference is not None and saved_reference != reference
        try:
            published = adopt_pairs(results, fresh=fresh)
        except (RuntimeError, ValueError) as why:
            raise StepError(str(why)) from None
        # The driver reads the calibration when it connects; connecting again
        # makes every objective change of this session use it.
        self._reopen()
        return {
            "saved_to": published["snapshot"],
            "replaced": fresh,
            "positions": sorted(int(p) for p in published["calibration"]["objectives"]),
        }


def zeiss_wizard() -> Wizard:
    """The ZEISS ZEN wizard: the driver's five setup steps."""
    s = ZeissSteps()
    return Wizard(
        "ZEISS ZEN setup",
        [
            Step(
                "connect",
                "Connect",
                "Connect to ZEN through the ZEN API Gateway and confirm the folder this microscope "
                "saves its pictures in.",
                {
                    "card": s.connect_card,
                    "choose": s.connect_choose,
                    "connect": s.connect_connect,
                    "disconnect": s.connect_disconnect,
                },
                s.connect_status,
            ),
            Step(
                "limits",
                "Define limits",
                "Say how far the stage and the focus may travel and what the driver is allowed to "
                "set, then save and adopt it; until this exists the driver uses its cautious defaults.",
                {
                    "document": s.limits_document,
                    "corner": s.limits_corner,
                    "focus_end": s.limits_focus_end,
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
                "Measure which way the picture is turned relative to the stage, and how large one pixel is.",
                {
                    "standing": s.orientation_standing,
                    "experiments": s.orientation_experiments,
                    "measure": s.orientation_measure,
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
                    "turn": s.calibration_turn,
                    "record": s.calibration_record,
                    "publish": s.calibration_publish,
                },
                s.calibration_status,
                # The offsets are read from the stage and the focus with the
                # operator's eye, in ZEN's own numbers, so they do not depend
                # on the orientation: nothing has to be measured before them.
            ),
        ],
    )
