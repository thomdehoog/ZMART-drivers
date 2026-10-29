"""The Nikon steps of the setup wizard, written against the driver's own setup code.

The steps follow the Leica driver's wizard, which follows the ZMART driver
configuration workflow: Connect, Define limits, Define coordinate system
origin, Image-to-stage orientation, and, where the Leica wizard calibrates its
objectives, Hardware settings: what NIS reports this microscope has, tried one
at a time. Each step does what its notebook does, with the same safety checks;
the wizard only replaces the notebook as the place the operator presses the
buttons.

Connect opens one connection to NIS-Elements through the driver's own
``connect()``, and the steps after it work through that connection. The
driver is imported inside the functions, so this file can be read, and the
page opened, on a computer without NIS-Elements.

Where a notebook is normally saved as the written record of a measurement,
the wizard saves a small ``.json`` record of what it measured instead, beside
the saved setting, so a later reader can still see how the numbers came about.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/wizard/steps.py``.
"""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

from .framework import Step, StepError, Wizard

#: How each stage axis of ``limits.json`` is named on the page.
AXES = (("x_um", "X", "µm"), ("y_um", "Y", "µm"), ("z_um", "Z (focus)", "µm"))

#: Where the wizard keeps the record that the hardware settings were looked at.
HARDWARE_RECORD = "hardware_reviewed.json"


def _machine():
    from .. import store

    return store.machine()


def _record_file(name: str, content: dict) -> Path:
    """Write the wizard's record of a measurement to a file the driver can keep beside the setting."""
    folder = Path(tempfile.mkdtemp(prefix="zmart_wizard_"))
    path = folder / f"{name}.json"
    path.write_text(json.dumps(content, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _saved_at(path: Path | None) -> str | None:
    """When a saved setting was saved, from its dated folder's name, as an ISO time the page can compare."""
    if path is None:
        return None
    try:
        moment = datetime.strptime(path.parent.name, "%Y-%m-%dT%H-%M-%S-%fZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    return moment.isoformat()


def _saved_on(path: Path) -> str:
    """When a saved setting was saved, in words: "Saved on 1 October 2026 at 14:05 (UTC)"."""
    when = _saved_at(path)
    if when is None:
        return f"Saved in {path.parent.name}."
    moment = datetime.fromisoformat(when)
    return f"Saved on {moment.day} {moment:%B %Y} at {moment:%H:%M} (UTC)."


def _status(done: bool, path: Path | None, note: str | None = None) -> dict:
    """A step's status: done or not, a sentence about it, and when it was saved."""
    if not done or path is None:
        return {"done": done, "note": note if done else None, "saved_at": None}
    return {"done": True, "note": note or _saved_on(path), "saved_at": _saved_at(path)}


def _number(values: dict, key: str, label: str) -> float:
    try:
        return float(values[key])
    except (KeyError, TypeError, ValueError):
        raise StepError(f"Please enter a number for {label}.") from None


def _suggested_data_folder() -> str:
    """Where pictures go when nothing was chosen yet: the data disk if there is one (only a suggestion)."""
    return r"D:\ZMART-data" if Path("D:/").exists() else r"C:\ZMART-data"


def _new_configuration_name() -> str:
    """A name for a new config: ``config_YYYYMMDD_HHMM``, with ``-2``, ``-3``... when that minute is taken."""
    base = datetime.now(UTC).strftime("config_%Y%m%d_%H%M")
    kept = set(_machine().configurations())
    name, number = base, 1
    while name in kept:
        number += 1
        name = f"{base}-{number}"
    return name


def png(pixels, path: Path) -> str:
    """Write a picture as an 8-bit grey PNG for the page, stretched so its faint parts show; its path.

    Only the standard library and numpy: a PNG is a few chunks of
    compressed rows.
    """
    import numpy as np

    values = np.asarray(pixels, dtype=float)
    low, high = np.percentile(values, (1, 99.5))
    grey = np.clip((values - low) / max(high - low, 1e-9) * 255, 0, 255).astype(
        np.uint8
    )
    height, width = grey.shape
    rows = b"".join(b"\x00" + grey[r].tobytes() for r in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )
    return str(path)


class NikonSteps:
    """What the steps share: the one connection to the microscope, and each step's work in progress."""

    def __init__(self, connection: dict | None = None):
        self._scope = None
        #: What Connect was given, so that connecting again goes the same way.
        self._connection: dict = dict(connection or {})
        self._origin = None
        self._orientation = None
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
            # Refused before NIS was reached: say why as it is.
            raise StepError(str(why)) from None
        except Exception as why:
            raise StepError(
                "Could not reach NIS-Elements. Check that NIS-Elements is running and that "
                f"start_bridge.mac is running in it (Macro > Run Macro From File), then try again. ({why})"
            ) from why

    def _reopen(self):
        """Connect again, so that what was just saved (the limits) governs from now on."""
        if self._scope is not None:
            self._scope.close()
        self._scope = None
        self._scope = self._open()

    def _use(self, name: str | None) -> str | None:
        """Work in config *name* from now on: the steps read and save there. None, the most recent."""
        from .. import store

        kept = _machine().configurations()
        if name is None:
            name = kept[0] if kept else None
        store.use_configuration(name)
        self._config = name
        return name

    def connect_status(self):
        return {"done": self._scope is not None, "note": None, "saved_at": None}

    def connect_choose(self, values):
        """Choose the config before connecting: a kept one by name, or (empty) a new one, made on Connect."""
        name = str(values.get("config") or "").strip()
        if self._scope is not None:
            raise StepError("Disconnect first to choose another config.")
        if name and name not in _machine().configurations():
            raise StepError(f"There is no config {name!r} on this computer.")
        return {"config": self._use(name or _new_configuration_name())}

    def connect_card(self, values):
        from ...methods import DEFAULT_CONNECTION
        from ..data_location import data_location
        from ..nis_bridge import macro

        saved = data_location(_machine())
        start_bridge = macro.files()["macro"]
        return {
            "microscope": self._connection.get("microscope")
            or DEFAULT_CONNECTION["microscope"],
            "api_type": DEFAULT_CONNECTION["api_type"],
            "host": "localhost",
            "port": self._connection.get("port") or DEFAULT_CONNECTION["port"],
            "config": self._config or "",
            "configs": _machine().configurations(),
            "output_root": str(saved) if saved else _suggested_data_folder(),
            "output_root_saved": saved is not None,
            "bridge_macro": str(start_bridge),
            "bridge_macro_written": start_bridge.is_file(),
            "connected": self._scope is not None,
            "checks": self._checks() if self._scope is not None else [],
        }

    def connect_write_macro(self, values):
        """Write ``start_bridge.mac`` for this computer, and say what to do with it in NIS."""
        from ...methods import DEFAULT_CONNECTION
        from ..nis_bridge import macro

        port = int(self._connection.get("port") or DEFAULT_CONNECTION["port"])
        path = macro.write_macro(port=port)
        return {
            "bridge_macro": str(path),
            "next": "In NIS-Elements: Macro > Run Macro From File, and pick this file. Leave it running.",
        }

    def connect_connect(self, values):
        from .. import store
        from ..data_location import data_location, set_data_location

        microscope = str(values.get("microscope") or "").strip() or None
        try:
            if microscope:
                store.check_microscope_name(microscope)
            port = (
                int(values.get("port"))
                if values.get("port") not in (None, "")
                else None
            )
        except (TypeError, ValueError) as why:
            raise StepError(
                f"The microscope's name or the port cannot be used: {why}"
            ) from None
        self._connection = {
            key: value
            for key, value in (
                ("microscope", microscope),
                ("port", port),
                ("config", self._config),
            )
            if value is not None
        }
        if microscope:
            store.use_microscope(microscope)
            store.use_configuration(self._config)
        made = None
        if self._config is not None and self._config not in _machine().configurations():
            made = _machine().microscope_root() / self._config
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
        from ...procedures import readings
        from ..data_location import data_location

        client = self._scope.client
        xy, z = readings.xy(client), readings.z(client)
        objective = readings.objective(client)
        present = sum(bool(e["devices"]) for e in readings.devices(client).values())
        return [
            ["driver", f"nis_elements_ar7 {__version__}"],
            ["NIS-Elements", str(self._scope.handle.nis_version)],
            [
                "stage",
                f"x {xy['x_um']:.0f} · y {xy['y_um']:.0f} · z {z['z_um']:.1f} µm (NIS's own numbers)",
            ],
            [
                "objective",
                f"position {objective['position']} · {objective['name'] or ''}".strip(),
            ],
            [
                "optical configuration",
                str(readings.selected_optical_configuration(client) or "none selected"),
            ],
            ["hardware settings", f"{present} kinds found"],
            ["settings folder", str(_machine().settings_root())],
            ["output folder", str(data_location(_machine()))],
        ]

    # -- Define limits --------------------------------------------------------

    def limits_status(self):
        path = _machine().saved_path("limits")
        return _status(path is not None, path)

    def limits_document(self, values):
        """The limits as they stand, what NIS offers to choose from, and how to name each row."""
        from ...primitives.nis_python import devices as device_table
        from ...procedures import readings
        from ..limits import config

        saved = _machine().saved_path("limits")
        path = saved or _machine().bundled_default("limits")
        document = config.load(path)
        client = self.scope().client
        inventory = readings.devices(client)
        return {
            "document": document,
            "source": "published" if saved else "defaults",
            "axes": [
                {"key": k, "label": label, "unit": unit} for k, label, unit in AXES
            ],
            "travel": readings.stage_limits(client),
            "objectives": {
                str(k): v for k, v in readings.nosepiece(client)["objectives"].items()
            },
            "optical_configurations": readings.optical_configurations(client),
            "guarded": [
                {
                    "key": row.key,
                    "says": row.says,
                    "unit": row.unit,
                    "kind": row.kind,
                    "devices": [
                        d["instance"]
                        for d in inventory.get(row.key, {}).get("devices", [])
                    ],
                }
                for row in device_table.TABLE
                if row.guarded and inventory.get(row.key, {}).get("devices")
            ],
        }

    def limits_corner(self, values):
        """Where the stage stands now, in NIS's own numbers: one corner of the safe area."""
        from ...procedures import readings

        here = readings.xy(self.scope().client)
        return {"x": here["x_um"], "y": here["y_um"]}

    def limits_focus(self, values):
        """Where the focus stands now, in NIS's own numbers: the lowest or the highest it may go."""
        from ...procedures import readings

        return {"z": readings.z(self.scope().client)["z_um"]}

    def limits_publish(self, values):
        from ..limits import config

        try:
            checked = config.validate_payload(values.get("document"))
            published = config.adopt_limits(
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
        return {"saved_to": published["limits_path"]}

    # -- Define coordinate system origin -------------------------------------

    def origin_status(self):
        path = _machine().saved_path("origin")
        return _status(path is not None, path)

    def origin_standing(self, values):
        record = _machine().read("origin")
        return {"origin": (record or {}).get("origin")}

    def _snapshot(self) -> dict:
        from ...procedures import readings

        client = self.scope().client
        return {
            **readings.xy(client),
            **readings.z(client),
            "objective": readings.objective(client),
        }

    @staticmethod
    def _origin_rows(snap: dict) -> dict:
        objective = snap.get("objective") or {}
        return {
            "actuators": [
                ["x", f"{snap['x_um']:.2f} µm"],
                ["y", f"{snap['y_um']:.2f} µm"],
                ["z (focus)", f"{snap['z_um']:.2f} µm"],
                [
                    "objective",
                    f"position {objective.get('position', '?')} · {objective.get('name') or ''}".strip(),
                ],
            ],
            "x_um": snap["x_um"],
            "y_um": snap["y_um"],
            "z_um": snap["z_um"],
        }

    def origin_read(self, values):
        """Where the stage, the focus and the nosepiece stand now: what Save and adopt will make (0, 0, 0)."""
        self._origin = self._snapshot()
        return self._origin_rows(self._origin)

    def origin_publish(self, values):
        if self._origin is None:
            raise StepError("Read first.")
        now = self._snapshot()
        moved = [
            k for k in ("x_um", "y_um", "z_um") if abs(now[k] - self._origin[k]) > 0.5
        ]
        if moved or now["objective"] != self._origin["objective"]:
            raise StepError(
                "The stage, the focus or the objective changed since Read. Read again, then Save and adopt."
            )
        try:
            answer = self.scope().set_origin()
        except RuntimeError as why:
            raise StepError(str(why)) from None
        return {"saved_to": answer["origin_file"], **self._origin_rows(now)}

    # -- Image-to-stage orientation ------------------------------------------

    def orientation_status(self):
        from ..orientation import load_rig_orientation

        _, info = load_rig_orientation(_machine())
        path = _machine().saved_path("orientation")
        return _status(bool(info.get("measured")), path)

    def orientation_standing(self, values):
        from ..orientation import load_rig_orientation
        from ..orientation.measure_orientation import label

        orientation, info = load_rig_orientation(_machine())
        return {"measured": bool(info.get("measured")), "label": label(orientation)}

    def orientation_measure(self, values):
        """Take three pictures (here, after +x, after +y), move back, and work out the turn."""
        import tifffile

        from ..orientation import measure_orientation as mo
        from ..orientation import reorient_array

        step = values.get("step_um")
        step = (
            _number({"v": step}, "v", "the stage move")
            if step not in (None, "")
            else None
        )
        folder = Path(tempfile.mkdtemp(prefix="zmart_orientation_"))
        try:
            found = mo.measure(self.scope(), step_um=step, folder=folder)
        except (RuntimeError, ValueError) as why:
            raise StepError(f"The pictures could not be taken: {why}") from None
        self._orientation = found
        answer = {
            "accepted": found.accepted,
            "label": found.label,
            "step_um": found.step_um,
            "why": found.why,
            "rotate_deg": found.chosen.rotate_deg,
            "mirrored": found.chosen.mirrored,
        }
        if found.accepted:
            first = tifffile.imread(found.pictures[0])
            if first.ndim == 3:
                first = first.mean(axis=-1) if first.shape[-1] == 3 else first[0]
            answer["image"] = png(
                reorient_array(first, found.chosen), folder / "oriented.png"
            )
        return answer

    def orientation_publish(self, values):
        """Save the orientation: the one measured, or the one chosen by hand on the page."""
        from ..orientation import Orientation, save_orientation

        try:
            rotate = int(
                values.get(
                    "rotate_deg",
                    self._orientation.chosen.rotate_deg if self._orientation else 0,
                )
            )
            mirrored = bool(
                values.get(
                    "mirrored",
                    self._orientation.chosen.mirrored if self._orientation else False,
                )
            )
            orientation = Orientation(rotate_deg=rotate, mirrored=mirrored)
        except (TypeError, ValueError) as why:
            raise StepError(f"That orientation cannot be used: {why}") from None
        if values.get("by_hand") is not True and (
            self._orientation is None or not self._orientation.accepted
        ):
            raise StepError("Measure first, or choose the orientation by hand.")
        path = save_orientation(orientation, machine=_machine())
        record = {
            "orientation": {"rotate_deg": rotate, "mirrored": mirrored},
            "how": "by hand"
            if values.get("by_hand") is True
            else "measured from three pictures",
            "measurement": None
            if self._orientation is None
            else {
                "step_um": self._orientation.step_um,
                "scores": self._orientation.scores,
            },
        }
        Path(path).with_name("set_orientation_wizard_record.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
        self._reopen()  # the driver reads the orientation when it connects
        return {"saved_to": str(path)}

    # -- Hardware settings -----------------------------------------------------

    def _hardware_record(self) -> Path:
        return _machine().settings_root() / "wizard" / HARDWARE_RECORD

    def hardware_status(self):
        path = self._hardware_record()
        if not path.is_file():
            return {"done": False, "note": None, "saved_at": None}
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"done": False, "note": None, "saved_at": None}
        return {
            "done": True,
            "note": f"Looked at on {record.get('when', '?')}.",
            "saved_at": record.get("saved_at"),
        }

    def hardware_inventory(self, values):
        """Every hardware setting NIS reports on this microscope, with its value now and what it takes."""
        from ...procedures import readings

        found = []
        for key, entry in readings.devices(self.scope().client).items():
            for item in entry["devices"]:
                found.append(
                    {
                        "name": key
                        + (f"/{item['instance']}" if item["instance"] else ""),
                        "label": item.get("label"),
                        "group": entry["group"],
                        "says": entry["says"],
                        "kind": entry["kind"],
                        "unit": entry["unit"],
                        "positions": item.get("positions"),
                        "value": item["value"],
                        "guarded": entry["guarded"],
                    }
                )
        return {"settings": found}

    def hardware_set(self, values):
        """Set one hardware setting, through the limits, and read it back."""
        name = str(values.get("name") or "")
        value = values.get("value")
        try:
            done = self.scope().set_device(name, value)
            now = self.scope().device(name)
        except (ValueError, RuntimeError) as why:
            raise StepError(str(why)) from None
        return {"name": name, "value": now, "unconfirmed": done["unconfirmed"]}

    def hardware_publish(self, values):
        """Note that the hardware settings were looked at, with what they were then."""
        inventory = self.hardware_inventory({})
        now = datetime.now(UTC)
        path = self._hardware_record()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "when": f"{now:%d %B %Y at %H:%M} (UTC)",
                    "saved_at": now.isoformat(),
                    **inventory,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return {"saved_to": str(path)}


def nikon_wizard(connection: dict | None = None) -> Wizard:
    """The Nikon NIS-Elements AR 7 wizard: the ZMART driver configuration workflow's five steps."""
    s = NikonSteps(connection)
    return Wizard(
        "Nikon NIS-Elements AR 7 setup",
        [
            Step(
                "connect",
                "Connect",
                "Start the bridge in NIS-Elements, connect to it, and confirm the folder this microscope saves its pictures in.",
                {
                    "card": s.connect_card,
                    "choose": s.connect_choose,
                    "macro": s.connect_write_macro,
                    "connect": s.connect_connect,
                    "disconnect": s.connect_disconnect,
                },
                s.connect_status,
            ),
            Step(
                "limits",
                "Define limits",
                "Say how far the stage and the focus may travel, which objectives and optical configurations "
                "automation may choose, and which guarded hardware it may set; then save and adopt it. Until this "
                "exists the driver uses its cautious defaults.",
                {
                    "document": s.limits_document,
                    "corner": s.limits_corner,
                    "focus": s.limits_focus,
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
                "Image-to-stage orientation",
                "Measure which way the camera's picture is turned relative to the stage, from three pictures.",
                {
                    "standing": s.orientation_standing,
                    "measure": s.orientation_measure,
                    "publish": s.orientation_publish,
                },
                s.orientation_status,
            ),
            Step(
                "hardware",
                "Hardware settings",
                "See the shutters, lamps, filters and other hardware NIS reports on this microscope, try each one, "
                "and note that they look right.",
                {
                    "inventory": s.hardware_inventory,
                    "set": s.hardware_set,
                    "publish": s.hardware_publish,
                },
                s.hardware_status,
            ),
        ],
    )
