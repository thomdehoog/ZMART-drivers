"""The Nikon steps of the setup wizard, written against the driver's own setup code.

The steps are the same five as the Leica driver's wizard: Connect, Define
limits, Define coordinate system origin, Image-to-stage calibration and
Objective calibration. Each does what its notebook does, with the same safety
checks; the wizard only replaces the notebook as the place the operator
presses the buttons.

Connect opens one connection to NIS-Elements through the driver's own
``connect()``, and the steps after it work through that connection. The
driver is imported inside the functions, so this file can be read, and the
page opened, on a computer without NIS-Elements.

Where a notebook is normally saved as the written record of a measurement, the
wizard saves a small ``.json`` record of what it measured instead, in the same
place, so a later reader can still see how the numbers came about.

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

#: How each stage axis of ``limits.json`` is named on the page.
_AXES = (
    ("x_um", "X", "µm"),
    ("y_um", "Y", "µm"),
    ("z_um", "Z (focus drive)", "µm"),
)

#: How far the stage may have drifted, in micrometres, between Read and Save
#: on the origin step before the wizard asks to read again.
_STILL_UM = 0.5


def _machine():
    from ..store import MACHINE

    return MACHINE


def _record_file(name: str, content: dict) -> Path:
    """Write the wizard's record of a measurement to a file the driver can keep with the settings."""
    folder = Path(tempfile.mkdtemp(prefix="zmart_wizard_"))
    path = folder / f"{name}.json"
    path.write_text(json.dumps(content, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _status(done: bool, record: Path | None, note: str | None = None) -> dict:
    """A step's status: done or not, a sentence about it, and when it was saved."""
    if not done or record is None:
        return {"done": done, "note": note if done else None, "saved_at": None}
    return {
        "done": True,
        "note": note or f"{_saved_on(record)}.",
        "saved_at": _saved_at(record),
    }


def _saved_at(record: Path) -> str | None:
    """The time in a saved folder's name, as an ISO time the page can compare."""
    try:
        # The folder's name is a UTC time; its trailing Z is left out here.
        moment = datetime.strptime(record.name[:26], "%Y-%m-%dT%H-%M-%S-%f")
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC).isoformat()


def _saved_on(record: Path) -> str:
    """The time in a saved folder's name, in words: "Saved on 1 October 2026 at 14:05 (UTC)"."""
    try:
        moment = datetime.strptime(record.name[:16], "%Y-%m-%dT%H-%M").replace(
            tzinfo=UTC
        )
    except ValueError:
        return f"Saved in {record.name}"
    return f"Saved on {moment.day} {moment:%B %Y} at {moment:%H:%M} (UTC)"


def _read_json(path: Path | None) -> dict | None:
    """A saved settings file, or None when it is missing or cannot be read."""
    if path is None:
        return None
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


def _preview(pixels, path: Path) -> str:
    """Save a picture as a small greyscale JPEG for the page, stretched so a faint sample shows; answer its path."""
    import numpy as np
    from PIL import Image

    values = np.asarray(pixels, dtype=float)
    low, high = np.percentile(values, (1, 99.5))
    scaled = np.clip((values - low) / max(high - low, 1e-9), 0, 1)
    shown = Image.fromarray(np.rint(scaled * 255).astype(np.uint8), "L")
    shown.thumbnail((720, 720))
    path.parent.mkdir(parents=True, exist_ok=True)
    shown.save(path, quality=80)
    return str(path)


def _published_offsets(record: Path | None) -> dict[int, dict]:
    """The objectives the saved calibration holds a measured offset for: ``{slot: {"name", "translation_um"}}``."""
    saved = _read_json(record / "calibration.json") if record else None
    found = {}
    for slot, entry in ((saved or {}).get("objectives") or {}).items():
        if isinstance(entry, dict) and entry.get("translation_um") is not None:
            found[int(slot)] = {
                "name": str(entry.get("name") or ""),
                "translation_um": [float(v) for v in entry["translation_um"]],
            }
    return found


class NikonSteps:
    """What the steps share: the one connection to the microscope, and each step's work in progress."""

    def __init__(self):
        self._scope = None
        # What Connect was given, so that connecting again goes the same way.
        self._connection: dict = {}
        self._origin = None
        self._orientation = None
        # The objective calibration: for each (reference, target) pair, where
        # the stage stood with the feature centred under each of the two.
        self._pairs: dict[tuple[int, int], dict[str, dict]] = {}
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
            return connect(
                **{k: v for k, v in self._connection.items() if v is not None}
            )
        except ValueError as why:
            # The connection was refused before NIS-Elements was asked: say why as it is.
            raise StepError(str(why)) from None
        except Exception as why:
            raise StepError(
                "Could not reach NIS-Elements. Check that NIS-Elements is open and that "
                f"start_bridge.mac is running in it, then try again. ({why})"
            ) from why

    def _reopen(self):
        """Connect again, so what was just saved (the limits) governs from now on."""
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
        from ..data_location import data_location

        saved = data_location(_machine())
        return {
            # The microscope and how it is reached belong to the driver; the
            # host and the bridge's port can be typed.
            "microscope": DEFAULT_CONNECTION["microscope"],
            "api_type": DEFAULT_CONNECTION["api_type"],
            "host": self._connection.get("host") or DEFAULT_CONNECTION["host"],
            "port": str(self._connection.get("port") or DEFAULT_CONNECTION["port"]),
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

        host = str(values.get("host") or "").strip() or None
        port_text = str(values.get("port") or "").strip()
        try:
            port = int(port_text) if port_text else None
        except ValueError:
            raise StepError(
                f"The port is a whole number, such as 54470, not {port_text!r}."
            ) from None
        # Kept for connecting again. The config is the one chosen above the card.
        self._connection = {"host": host, "port": port, "config": self._config}
        # A connection that will be refused saves nothing, not even the folder.
        try:
            checked_connection({"host": host, "port": port})
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
        objective = snap.get("objective") or {}
        info = handle.client.info or {}
        return [
            ["driver", f"nis_elements_ar610 {__version__}"],
            ["NIS-Elements", str(info.get("nis") or "not reported")],
            ["bridge", str(info.get("bridge") or "not reported")],
            [
                "stage",
                f"x {snap['x_um']:.0f} · y {snap['y_um']:.0f} · z {snap['z_um']:.1f} µm",
            ],
            [
                "objective",
                f"slot {objective.get('slot', '?')} · {objective.get('name', '')}".strip(),
            ],
            ["settings folder", str(_machine().snapshot_root())],
            ["output folder", str(data_location(_machine()))],
        ]

    # -- Define limits --------------------------------------------------------

    def limits_status(self):
        latest = _machine().latest_record("limits")
        return _status(latest is not None, latest)

    def limits_document(self, values):
        """The limits as they stand, every entry of the file, and how to name each row."""
        from ..limits.config import defaults_path, validate_payload
        from ..limits.limit_rules import SETTING_LIMIT_KEYS

        latest = _machine().latest_path("limits")
        source = "published" if latest is not None else "defaults"
        path = latest if latest is not None else defaults_path()
        document = validate_payload(
            json.loads(Path(path).read_text(encoding="utf-8")), path=Path(path)
        )
        self.scope()
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
                "note": "Nosepiece slots count from 1. List the slots automation may turn to, "
                "or leave it unticked for all.",
            },
            "settings": list(SETTING_LIMIT_KEYS),
            # The Ti2's stage has one motor per axis, so there is one to read.
            "actuators": {"x": ["stage"], "y": ["stage"]},
        }

    def limits_corner(self, values):
        """Where the stage stands now: one corner of the safe area, in NIS-Elements' own coordinates."""
        from ...procedures import readings

        here = readings.get_xyz(self.scope().client)
        if not here:
            raise StepError(
                "Could not read the stage position. Check that the bridge answers."
            )
        return {"x": float(here["x_um"]), "y": float(here["y_um"])}

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
        return {"saved_to": published["record"]}

    # -- Define coordinate system origin -------------------------------------

    def origin_status(self):
        latest = _machine().latest_record("origin")
        saved = _read_json(latest / "origin.json") if latest else None
        return _status(bool(saved and saved.get("origin")), latest)

    def origin_standing(self, values):
        saved = _read_json(_machine().latest_path("origin"))
        return {"origin": (saved or {}).get("origin")}

    def origin_read(self, values):
        """The stage, the focus drive and the objective now: what Save and adopt will make (0, 0, 0)."""
        from ...procedures.absolute_coordinate_system import hardware_snapshot

        snap = hardware_snapshot(self.scope().handle)
        self._origin = snap
        return self._origin_rows(snap)

    @staticmethod
    def _origin_rows(snap: dict) -> dict:
        objective = snap.get("objective") or {}
        return {
            "actuators": [
                ["x stage", f"{snap['x_um']:.2f} µm"],
                ["y stage", f"{snap['y_um']:.2f} µm"],
                ["z focus drive", f"{snap['z_um']:.2f} µm"],
                [
                    "objective",
                    f"slot {objective.get('slot', '?')} · {objective.get('name', '')}".strip(),
                ],
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
            k
            for k in ("x_um", "y_um", "z_um")
            if abs(now[k] - self._origin[k]) > _STILL_UM
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
        latest = _machine().latest_record("orientation")
        saved = _read_json(latest / "orientation.json") if latest else None
        return _status(bool(saved and saved.get("measured")), latest)

    def orientation_standing(self, values):
        """What is saved: the turn, whether it is mirrored, and the pixel size it measured."""
        saved = _read_json(_machine().latest_path("orientation"))
        if not (saved and saved.get("measured")):
            return {"measured": False}
        return {
            "measured": True,
            "rotation_deg": saved.get("rotation_deg"),
            "reflection": bool(saved.get("mirrored")),
            "pixel_size_um": saved.get("pixel_size_um"),
        }

    def orientation_measure(self, values):
        """Step the stage along x and y, compare the pictures, and answer the turn found."""
        from ..orientation import measure_orientation as mo

        handle = self.scope().handle
        step = _number(
            {"v": values.get("stage_move_um", mo.STEP_UM)}, "v", "the stage move"
        )
        self._orientation = None
        try:
            measured = mo.measure(handle, step_um=step)
        except (RuntimeError, ValueError) as why:
            return {
                "accepted": False,
                "why": f"The orientation could not be worked out: {why}. Choose a field with "
                "clearly recognisable structure, in focus, and Rerun.",
            }
        self._orientation = measured
        turn = measured["orientation"]
        return {
            "accepted": True,
            "rotation_deg": turn.rotate_deg,
            "reflection": turn.mirrored,
            "pixel_size_um": measured["pixel_size_um"],
            "rows": [
                ["turn", f"{turn.rotate_deg}° clockwise"],
                ["mirrored", "yes" if turn.mirrored else "no"],
                ["pixel size", f"{measured['pixel_size_um']:.4f} µm"],
                [
                    "clarity of the x and y steps",
                    " · ".join(
                        f"{measured['peak_ratios'][axis]:.1f}" for axis in ("x", "y")
                    ),
                ],
            ],
        }

    def orientation_check(self, values):
        """One picture, turned with the measured orientation, so the operator can see it follows the stage."""
        from ...procedures import acquire

        if self._orientation is None:
            raise StepError("Measure the orientation first.")
        pixels = acquire.snap_picture(self.scope().handle)
        turned = self._orientation["orientation"].apply(pixels)
        folder = Path(tempfile.mkdtemp(prefix="zmart_wizard_check_"))
        return {"image": _preview(turned, folder / "orientation_check.jpg")}

    def orientation_publish(self, values):
        from ..orientation import adopt_orientation

        if self._orientation is None:
            raise StepError("Measure first.")
        measured = self._orientation
        record = _record_file(
            "set_orientation_wizard_record",
            {
                "orientation": measured["orientation"].as_dict(),
                "matrix": measured["matrix"],
                "pixel_size_um": measured["pixel_size_um"],
                "shifts": measured["shifts"],
                "peak_ratios": measured["peak_ratios"],
                "step_um": measured["step_um"],
            },
        )
        try:
            adopted = adopt_orientation(
                measured["orientation"],
                pixel_size_um=measured["pixel_size_um"],
                archive_paths=[record],
            )
        except (OSError, ValueError) as why:
            raise StepError(str(why)) from None
        # This session lays pictures down the new way from now on, as a new connection would.
        self.scope().handle.orientation = measured["orientation"]
        return {"saved_to": adopted["record"]}

    # -- Objective calibration ------------------------------------------------

    def calibration_status(self):
        # The driver fills in an empty calibration (no objectives) the first
        # time it connects; the step is done once a pair has been measured.
        latest = _machine().latest_record("calibration")
        measured = _published_offsets(latest)
        done = len(measured) >= 2
        slots = ", ".join(str(s) for s in sorted(measured))
        return _status(
            done,
            latest,
            f"{_saved_on(latest)}, for the objectives in slots {slots}."
            if done
            else None,
        )

    def calibration_lenses(self, values):
        """The objectives on the nosepiece, and the offsets the saved calibration holds for them."""
        from ...procedures import readings

        found = readings.get_objectives(self.scope().client) or {}
        lenses = [
            {"slot": int(slot), "name": str(name).strip()}
            for slot, name in sorted((found.get("objectives") or {}).items())
            # An empty slot has no name in NIS-Elements; there is nothing to calibrate there.
            if str(name).strip()
        ]
        offsets = _published_offsets(_machine().latest_record("calibration"))
        reference = next(
            (
                s
                for s, entry in offsets.items()
                if all(v == 0 for v in entry["translation_um"])
            ),
            None,
        )
        return {
            "lenses": lenses,
            "current": found.get("current"),
            "reference": reference,
            "held": {str(s): entry["translation_um"] for s, entry in offsets.items()},
        }

    def calibration_turn(self, values):
        """Turn the nosepiece to *slot* and move nothing else; the operator then centres the feature again."""
        from ...procedures import changes

        slot = int(_number(values, "slot", "the objective slot"))
        result = changes.set_objective(self.scope().client, slot)
        if not result.get("success"):
            raise StepError(
                f"The nosepiece did not turn to slot {slot}: {result.get('message')}"
            )
        return {"slot": slot}

    def calibration_record(self, values):
        """Read where the stage stands with the feature centred and in focus, under the reference or the target."""
        from ...procedures.absolute_coordinate_system import hardware_snapshot

        reference = int(_number(values, "reference", "the reference objective"))
        target = int(_number(values, "target", "the target objective"))
        side = values.get("side")
        if side not in ("reference", "target"):
            raise StepError("Unknown measurement.")
        wanted = reference if side == "reference" else target
        snap = hardware_snapshot(self.scope().handle)
        objective = snap.get("objective") or {}
        if objective.get("slot") != wanted:
            raise StepError(
                f"The objective in place is slot {objective.get('slot', '?')}, not slot {wanted}. "
                f"Turn to slot {wanted} first, centre and focus the feature, then record."
            )
        pair = self._pairs.setdefault((reference, target), {})
        pair[side] = {
            "slot": wanted,
            "name": objective.get("name", ""),
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_um"],
        }
        answer = {"side": side, **pair[side]}
        if "reference" in pair and "target" in pair:
            answer["translation_um"] = dict(
                zip(("x", "y", "z"), self._offset(pair), strict=True)
            )
        return answer

    @staticmethod
    def _offset(pair: dict) -> list[float]:
        """The target's offset from the reference: where the stage stood under the target, minus under the reference."""
        return [
            pair["target"][k] - pair["reference"][k] for k in ("x_um", "y_um", "z_um")
        ]

    def calibration_publish(self, values):
        from ..optical_calibration import model

        reference = int(_number(values, "reference", "the reference objective"))
        measured = {
            target: pair
            for (ref, target), pair in self._pairs.items()
            if ref == reference and "reference" in pair and "target" in pair
        }
        if not measured:
            raise StepError("Record a preset under both objectives first.")
        saved = _published_offsets(_machine().latest_record("calibration"))
        objectives = {
            reference: {
                "name": next(iter(measured.values()))["reference"]["name"],
                "translation_um": [0.0, 0.0, 0.0],
            }
        }
        # The saved calibration counts every offset from its own reference.
        # When that is another objective, its other entries are counted again
        # from the new reference, so that every offset in the file shares one.
        if saved and reference not in saved:
            others = sorted(set(saved) - set(measured) - {reference})
            if others:
                raise StepError(
                    f"The saved calibration counts from another objective and does not hold slot "
                    f"{reference}, so its offsets for slots {others} cannot be counted from it. "
                    "Choose one of the saved objectives as the reference, or measure those slots too."
                )
        shift = saved.get(reference, {}).get("translation_um", [0.0, 0.0, 0.0])
        for slot, entry in saved.items():
            if slot != reference and slot not in measured:
                objectives[slot] = {
                    "name": entry["name"],
                    "translation_um": [
                        v - s
                        for v, s in zip(entry["translation_um"], shift, strict=True)
                    ],
                }
        for target, pair in measured.items():
            objectives[target] = {
                "name": pair["target"]["name"],
                "translation_um": self._offset(pair),
            }
        record = _record_file(
            "calibrate_objective_pair_wizard_record",
            {
                "reference": reference,
                "pairs": {
                    f"slot{reference}-slot{target}": pair
                    for target, pair in measured.items()
                },
            },
        )
        try:
            published = model.adopt_calibration(objectives, archive_paths=[record])
        except (OSError, ValueError) as why:
            raise StepError(str(why)) from None
        # This session moves by the new offsets from now on, as a new connection would.
        handle = self.scope().handle
        handle.translations, handle.calibration_info = (
            model.load_objective_calibration()
        )
        return {"saved_to": published["record"]}


def nikon_wizard() -> Wizard:
    """The wizard for a Nikon microscope with NIS-Elements AR 6.10: the five setup steps."""
    s = NikonSteps()
    return Wizard(
        "Nikon NIS-Elements AR 6.10 setup",
        [
            Step(
                "connect",
                "Connect",
                "Connect to NIS-Elements through the bridge and confirm the folder this "
                "microscope saves its pictures in.",
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
                "Measure where, and at what height, each objective looks relative to one "
                "reference objective.",
                {
                    "lenses": s.calibration_lenses,
                    "turn": s.calibration_turn,
                    "record": s.calibration_record,
                    "publish": s.calibration_publish,
                },
                s.calibration_status,
                # The offsets are measured in the stage's own coordinates, but
                # the operator centres the feature in pictures laid down with
                # the orientation; a new orientation is worth a second look.
                needs=("orientation",),
            ),
        ],
    )
