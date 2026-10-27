"""Where this microscope's settings live on disk, and how a new record of one is saved.

Everything the operator sets up once (the limits, the origin, the
orientation, the objective calibration, the data location) is kept on the
computer that runs the driver, never in the repository, under::

    <root>/evident/fv4000/rdk/
        limits/<datetime>/limits.json
        calibration/<datetime>/calibration.json
        orientation/<datetime>/orientation.json
        origin/<datetime>/origin.json
        data_location/<datetime>/data_location.json

The root is ``C:\\ProgramData\\zmart-microscopy`` on Windows, or wherever the
environment variable ``ZMART_MICROSCOPY_ROOT`` points (the tests point it at
a temporary folder). Each item has its own folder, with one dated folder per
time it was saved; nothing is ever overwritten, and the newest dated folder is
the one the driver loads. So you can always see what the microscope was told,
and when.

The settings can also be grouped in a *config*: a folder of its own beside
those item folders, named by when it was made (``config_20261026_1915``) and
chosen when connecting (the ``config`` connection word). With no config in
use, the item folders sit in the driver's folder itself.

``<datetime>`` is UTC with microseconds, written so that it is a legal Windows
folder name (no colons) and sorts in time order::

    2026-10-26T14-30-00-123456Z

The limits, the orientation and the calibration have a bundled default inside
the driver (``<item>/defaults/``), used only when nothing was saved yet or the
saved file cannot be read. The origin and the data location have none: with
no origin, positions are plain stage coordinates; with no data location,
nothing is saved.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PROGRAMDATA_ROOT = Path(r"C:\ProgramData\zmart-microscopy")
PROGRAMDATA_ROOT_ENV = "ZMART_MICROSCOPY_ROOT"

_SNAPSHOT_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"
_SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z$")
_CONFIGURATION_RE = re.compile(r"^config_\d{8}_\d{4}(-\d+)?$")

CALIBRATION_FILENAME = "calibration.json"
LIMITS_FILENAME = "limits.json"
ORIENTATION_FILENAME = "orientation.json"
ORIGIN_FILENAME = "origin.json"
DATA_LOCATION_FILENAME = "data_location.json"
FRAME_FILENAME = "frame.json"

SUBSYSTEMS = (
    "limits",
    "calibration",
    "orientation",
    "origin",
    "data_location",
    "frame",
)
#: The file each item keeps in its dated folder.
FILENAME = {
    "limits": LIMITS_FILENAME,
    "calibration": CALIBRATION_FILENAME,
    "orientation": ORIENTATION_FILENAME,
    "origin": ORIGIN_FILENAME,
    "data_location": DATA_LOCATION_FILENAME,
    "frame": FRAME_FILENAME,
}
#: The items with a bundled default, and the folder under driver_setup/ that holds it.
BUNDLED = {
    "limits": "limits",
    "calibration": "optical_calibration",
    "orientation": "orientation",
}
_SEED_MOMENT = datetime(1970, 1, 1, tzinfo=UTC)

#: The config this process reads and saves its settings in; None, the driver's folder itself.
_in_use: str | None = None


def format_snapshot_name(moment: datetime) -> str:
    """A time as a dated folder's name, in UTC."""
    return moment.astimezone(UTC).strftime(_SNAPSHOT_FORMAT)


def is_snapshot_name(name: str) -> bool:
    return bool(_SNAPSHOT_RE.match(name))


def is_configuration_name(name: str) -> bool:
    return bool(_CONFIGURATION_RE.match(str(name)))


def use_configuration(name: str | None) -> None:
    """Read and save the settings in the config *name* from now on; None, in the driver's folder."""
    global _in_use
    if name is not None and not is_configuration_name(name):
        raise ValueError(f"{name!r} is not a config's name (config_YYYYMMDD_HHMM)")
    _in_use = name


def configuration_in_use() -> str | None:
    return _in_use


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")


@dataclass(frozen=True)
class MachineProfile:
    """Where this microscope's settings live. ``programdata_root=None`` reads the environment variable, else the Windows default."""

    vendor: str = "evident"
    microscope_id: str = "fv4000"
    api: str = "rdk"
    programdata_root: Path | None = None

    def root(self) -> Path:
        if self.programdata_root is not None:
            return Path(self.programdata_root)
        env = os.environ.get(PROGRAMDATA_ROOT_ENV)
        return Path(env) if env else DEFAULT_PROGRAMDATA_ROOT

    def api_root(self) -> Path:
        """The driver's folder, which holds its configs and, without one in use, the item folders."""
        return self.root() / self.vendor / self.microscope_id / self.api

    def snapshot_root(self) -> Path:
        """Where the settings are read and saved: the config in use, else the driver's folder."""
        return self.api_root() / _in_use if _in_use else self.api_root()

    def configurations(self) -> list[str]:
        """The configs kept on this computer, by folder name, the most recent first."""
        root = self.api_root()
        if not root.is_dir():
            return []
        return sorted(
            (
                p.name
                for p in root.iterdir()
                if p.is_dir() and is_configuration_name(p.name)
            ),
            reverse=True,
        )

    def new_configuration_name(self, moment: datetime | None = None) -> str:
        """The folder name a config made now gets: ``config_YYYYMMDD_HHMM``, one not yet taken."""
        local = (moment or datetime.now()).strftime("%Y%m%d_%H%M")  # noqa: DTZ005  (the clock on the wall, so the folder reads like the day)
        name, n = f"config_{local}", 1
        while (self.api_root() / name).exists():
            n += 1
            name = f"config_{local}-{n}"
        return name

    def ensure_layout(self) -> Path:
        """Make the item folders, if they are not there yet."""
        root = self.snapshot_root()
        for subsystem in SUBSYSTEMS:
            (root / subsystem).mkdir(parents=True, exist_ok=True)
        return root

    def bundled_default_path(self, filename: str) -> Path:
        """The default that ships with the driver for *filename* (``driver_setup/<item>/defaults/``)."""
        subsystem = next(name for name, file in FILENAME.items() if file == filename)
        return (
            Path(__file__).resolve().parent / BUNDLED[subsystem] / "defaults" / filename
        )

    def subsystem_root(self, subsystem: str) -> Path:
        if subsystem not in SUBSYSTEMS:
            raise ValueError(
                f"unknown setup item {subsystem!r}; the items are {SUBSYSTEMS}"
            )
        return self.snapshot_root() / subsystem

    def snapshots(self, subsystem: str) -> list[Path]:
        """The dated folders of *subsystem* that hold its file, oldest first."""
        root = self.subsystem_root(subsystem)
        if not root.is_dir():
            return []
        return sorted(
            (
                p
                for p in root.iterdir()
                if p.is_dir()
                and is_snapshot_name(p.name)
                and (p / FILENAME[subsystem]).is_file()
            ),
            key=lambda p: p.name,
        )

    def latest_snapshot(self, subsystem: str) -> Path | None:
        found = self.snapshots(subsystem)
        return found[-1] if found else None

    # --- loading ---------------------------------------------------------------------

    def resolve(self, filename: str) -> Path:
        """The newest saved *filename*; for the limits, raises when none was published; the others are seeded from their default."""
        subsystem = next(name for name, file in FILENAME.items() if file == filename)
        latest = self.latest_snapshot(subsystem)
        if latest is not None:
            return latest / filename
        if subsystem == "limits":
            raise FileNotFoundError(
                "no limits have been published for this microscope yet; publish them "
                "with driver_setup/limits/notebooks/set_limits.ipynb"
            )
        if subsystem not in BUNDLED:
            raise FileNotFoundError(f"no {filename} has been saved for this microscope")
        # The first connect seeds the item from its bundled default, so the
        # file an operator later edits or replaces is always there to see.
        seeded = self.publish_snapshot(
            self._next_auto_moment(subsystem), subsystem=subsystem, payload=None
        )
        return seeded / filename

    def limits_path(self) -> Path:
        return self.resolve(LIMITS_FILENAME)

    def orientation_path(self) -> Path:
        return self.resolve(ORIENTATION_FILENAME)

    def calibration_path(self) -> Path:
        return self.resolve(CALIBRATION_FILENAME)

    def origin_path(self) -> Path:
        """The newest saved origin, or a path that does not exist when none was saved."""
        latest = self.latest_snapshot("origin")
        return (
            (latest / ORIGIN_FILENAME)
            if latest
            else (self.subsystem_root("origin") / ORIGIN_FILENAME)
        )

    def read_origin(self) -> dict | None:
        """The newest saved origin, or None when none has been saved. A file that cannot be read raises."""
        path = self.origin_path()
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_data_location(self) -> dict | None:
        latest = self.latest_snapshot("data_location")
        if latest is None:
            return None
        return json.loads((latest / DATA_LOCATION_FILENAME).read_text(encoding="utf-8"))

    def read_frame(self) -> dict | None:
        latest = self.latest_snapshot("frame")
        if latest is None:
            return None
        return json.loads((latest / FRAME_FILENAME).read_text(encoding="utf-8"))

    # --- saving ----------------------------------------------------------------------

    def write_origin(self, payload: dict, *, moment: datetime | None = None) -> Path:
        return (
            self.publish_snapshot(moment, subsystem="origin", payload=payload)
            / ORIGIN_FILENAME
        )

    def write_data_location(
        self, payload: dict, *, moment: datetime | None = None
    ) -> Path:
        return (
            self.publish_snapshot(moment, subsystem="data_location", payload=payload)
            / DATA_LOCATION_FILENAME
        )

    def write_frame(self, payload: dict, *, moment: datetime | None = None) -> Path:
        return (
            self.publish_snapshot(moment, subsystem="frame", payload=payload)
            / FRAME_FILENAME
        )

    def publish_snapshot(
        self,
        moment: datetime | None = None,
        *,
        subsystem: str | None = None,
        payload: dict | None = None,
        archive_paths: Iterable[str | Path] = (),
        limits: dict | None = None,
        calibration: dict | None = None,
        orientation: dict | None = None,
    ) -> Path:
        """Save one new dated record of one item, whole, and answer its folder.

        Name the item with *subsystem* and give its *payload*, or use one of
        the keyword shortcuts ``limits=``, ``calibration=`` or
        ``orientation=``. A payload of None copies the item's bundled default
        (for seeding). *archive_paths* are files or folders kept beside the
        record, such as the notebook that measured it. The folder is written
        under a temporary name and renamed into place whole, so a reader
        never sees a half-written record.
        """
        given = [
            (name, value)
            for name, value in (
                ("limits", limits),
                ("calibration", calibration),
                ("orientation", orientation),
            )
            if value is not None
        ]
        if subsystem is None:
            if len(given) != 1:
                raise ValueError("publish_snapshot takes exactly one item's payload")
            subsystem, payload = given[0]
        if moment is None:
            moment = datetime.now(UTC)
            latest = self.latest_snapshot(subsystem)
            if latest is not None and format_snapshot_name(moment) <= latest.name:
                moment = self._next_auto_moment(subsystem)
        target = self.new_snapshot_dir(moment, subsystem)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.partial"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            filename = FILENAME[subsystem]
            if payload is not None:
                _write_json(staging / filename, payload)
            else:
                shutil.copy2(self.bundled_default_path(filename), staging / filename)
            for source in archive_paths:
                source = Path(source)
                destination = staging / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target

    def new_snapshot_dir(self, moment: datetime, subsystem: str) -> Path:
        """The folder a record saved at *moment* gets; it must sort after the newest one, so time never runs backwards here."""
        name = format_snapshot_name(moment)
        latest = self.latest_snapshot(subsystem)
        if latest is not None and name <= latest.name:
            raise ValueError(
                f"a new {subsystem} record {name!r} would not sort after the newest, {latest.name!r}; "
                "the computer's clock moved backwards"
            )
        return self.subsystem_root(subsystem) / name

    def _next_auto_moment(self, subsystem: str) -> datetime:
        """The time stamp of a record the driver makes by itself (a seed): just after the newest, or 1970 when there is none."""
        latest = self.latest_snapshot(subsystem)
        if latest is None:
            return _SEED_MOMENT
        return datetime.strptime(latest.name, _SNAPSHOT_FORMAT).replace(
            tzinfo=UTC
        ) + timedelta(microseconds=1)


MACHINE = MachineProfile()
