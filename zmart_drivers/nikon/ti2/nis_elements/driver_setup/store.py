"""Where this microscope's settings are kept on the computer, and how they are read and saved.

Everything the operator sets up once (the limits, the origin, the
orientation, the objective calibration, the data location) is saved on the
microscope computer, in its configuration folder, never in the repository::

    <root>/nikon/<microscope>/nis_elements/          the API root
        config_20261026_1915/                        a config (optional; see below)
            limits/<datetime>/limits.json
            origin/<datetime>/origin.json
            orientation/<datetime>/orientation.json
            calibration/<datetime>/calibration.json
            data_location/<datetime>/data_location.json

``<root>`` is ``C:\\ProgramData\\zmart-microscopy`` on Windows (the
``ZMART_MICROSCOPY_ROOT`` environment variable overrides it; the tests set
it to a throwaway folder). Each item is a folder of dated records, newest
last; nothing is ever overwritten, so one can always see what the microscope
was told and when. The newest record of each item is the one ``connect()``
loads.

A config is a folder of its own under the API root, named by when it was
made. Connecting with a ``config`` reads and saves every item in that
folder, so one computer can keep more than one setup (two sample holders,
say). Without a config, the items sit in the API root itself.

The limits, the orientation and the calibration ship with defaults inside the
driver (each item's ``defaults/`` folder). The defaults are used only when
no record has been saved yet, and the limits say so loudly, because the
defaults are not this microscope's measured values. The origin and the data
location have no default: without an origin, positions are plain stage
coordinates; without a data location, nothing is saved.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROGRAMDATA_ROOT_ENV = "ZMART_MICROSCOPY_ROOT"

#: The items, and the file each one's record is kept in.
ITEMS = {
    "limits": "limits.json",
    "origin": "origin.json",
    "orientation": "orientation.json",
    "calibration": "calibration.json",
    "data_location": "data_location.json",
}
#: The items that ship with a default, and the folder under driver_setup/ that holds it.
BUNDLED = {
    "limits": "limits",
    "orientation": "orientation",
    "calibration": "optical_calibration",
}

# A record's folder is named by the moment it was written, in UTC with
# microseconds, without colons so it is a legal Windows name, and so that
# sorting the names sorts the records in time.
_RECORD_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"
_RECORD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z$")
_CONFIGURATION_RE = re.compile(r"^config_\d{8}_\d{4}(-\d+)?$")

#: The config this process reads and saves its settings in; None, the API root itself.
_in_use: str | None = None


def default_root() -> Path:
    """The configuration folder of this computer, when no environment variable names one."""
    if platform.system() == "Windows":
        return (
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "zmart-microscopy"
        )
    if platform.system() == "Darwin":
        return Path("/Library/Application Support/zmart-microscopy")
    return Path("/etc/zmart-microscopy")


def record_name(moment: datetime) -> str:
    """The folder name of a record written at *moment*."""
    return moment.astimezone(UTC).strftime(_RECORD_FORMAT)


def is_record_name(name: str) -> bool:
    return bool(_RECORD_RE.match(name))


def is_configuration_name(name: str) -> bool:
    """True if *name* is a config folder's name, ``config_YYYYMMDD_HHMM``."""
    return bool(_CONFIGURATION_RE.match(str(name)))


def use_configuration(name: str | None) -> None:
    """Read and save the settings in config *name* from now on; None, in the API root."""
    global _in_use
    if name is not None and not is_configuration_name(name):
        raise ValueError(f"{name!r} is not a config's name (config_YYYYMMDD_HHMM)")
    _in_use = name


def configuration_in_use() -> str | None:
    """The config the settings are read and saved in, or None for the API root."""
    return _in_use


def _driver_root() -> Path:
    return Path(__file__).resolve().parents[1]  # nis_elements/


def write_json(path: Path, payload: dict) -> None:
    """Write *payload* as readable JSON, keeping the order the writer chose."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


@dataclass(frozen=True)
class MachineProfile:
    """Where this microscope's settings live on disk, and the reading and writing of them.

    ``programdata_root=None`` means the ``ZMART_MICROSCOPY_ROOT`` environment
    variable, else this computer's configuration folder. The tests hand in a
    throwaway folder instead.
    """

    vendor: str = "nikon"
    microscope_id: str = "ti2"
    api: str = "nis_elements"
    programdata_root: Path | None = None

    # --- the folders ---------------------------------------------------------

    def root(self) -> Path:
        if self.programdata_root is not None:
            return Path(self.programdata_root)
        env = os.environ.get(PROGRAMDATA_ROOT_ENV)
        return Path(env) if env else default_root()

    def api_root(self) -> Path:
        """The microscope API's folder, which holds its configs."""
        return self.root() / self.vendor / self.microscope_id / self.api

    def snapshot_root(self) -> Path:
        """Where the settings are read and saved: the config in use, else the API root."""
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
        local = (moment or datetime.now()).strftime("%Y%m%d_%H%M")
        name, n = f"config_{local}", 1
        while (self.api_root() / name).exists():
            n += 1
            name = f"config_{local}-{n}"
        return name

    def ensure_layout(self) -> Path:
        """Make the folder of every item, so a first save finds its place."""
        root = self.snapshot_root()
        for item in ITEMS:
            (root / item).mkdir(parents=True, exist_ok=True)
        return root

    def item_root(self, item: str) -> Path:
        if item not in ITEMS:
            raise ValueError(
                f"unknown setup item {item!r}; the items are {sorted(ITEMS)}"
            )
        return self.snapshot_root() / item

    def bundled_default_path(self, item: str) -> Path:
        """The default that ships with the driver for *item*, under its ``defaults/`` folder."""
        if item not in BUNDLED:
            raise ValueError(f"{item} has no bundled default")
        return (
            _driver_root() / "driver_setup" / BUNDLED[item] / "defaults" / ITEMS[item]
        )

    # --- the records ----------------------------------------------------------

    def records(self, item: str) -> list[Path]:
        """The dated record folders of *item* that hold its file, oldest first."""
        root = self.item_root(item)
        if not root.is_dir():
            return []
        return sorted(
            p
            for p in root.iterdir()
            if p.is_dir() and is_record_name(p.name) and (p / ITEMS[item]).is_file()
        )

    def latest_record(self, item: str) -> Path | None:
        """The newest record folder of *item*, or None when none was saved."""
        found = self.records(item)
        return found[-1] if found else None

    def latest_path(self, item: str) -> Path | None:
        """The newest saved file of *item*, or None when none was saved."""
        latest = self.latest_record(item)
        return latest / ITEMS[item] if latest else None

    def read_record(self, item: str) -> dict | None:
        """The newest saved record of *item* as a dictionary, or None when none was saved.

        A file that exists but cannot be read raises ``OSError``, and one that
        is not valid JSON raises ``ValueError``; the loaders turn both into a
        clear error rather than quietly using a default.
        """
        path = self.latest_path(item)
        if path is None:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_record(
        self,
        item: str,
        payload: dict,
        *,
        moment: datetime | None = None,
        archive_paths: Iterable[str | Path] = (),
    ) -> Path:
        """Save one new dated record of *item*, whole, and answer its file's path.

        *archive_paths* are files or folders copied into the record beside
        the file, such as the notebook that measured it. The record is
        written into a staging folder first and renamed into place at the
        end, so a record is never half there. A record must sort after the
        newest one; when the clock stands still or went back, the next
        microsecond is used.
        """
        self.item_root(item).mkdir(parents=True, exist_ok=True)
        latest = self.latest_record(item)
        if moment is None:
            moment = datetime.now(UTC)
            if latest is not None and record_name(moment) <= latest.name:
                moment = datetime.strptime(latest.name, _RECORD_FORMAT).replace(
                    tzinfo=UTC
                )
                moment += timedelta(microseconds=1)
        name = record_name(moment)
        if latest is not None and name <= latest.name:
            raise ValueError(
                f"a new {item} record {name!r} would not sort after the newest, {latest.name!r}; "
                "the system clock moved backwards"
            )
        target = self.item_root(item) / name
        staging = target.parent / f".{name}.partial"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            write_json(staging / ITEMS[item], payload)
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
        return target / ITEMS[item]


#: The settings of this computer's microscope, as the driver reads and saves them.
MACHINE = MachineProfile()
