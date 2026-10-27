r"""Where this microscope's settings are kept on the computer, and how a new one is saved.

Everything the operator sets up once (the limits, the origin, the image
orientation, the data location) is saved in the computer's configuration
folder, never in the repository, so that updating the driver never loses it::

    <ProgramData>/zmart-microscopy/nikon/<microscope>/nis_elements_ar7/
        limits/<datetime>/limits.json
        origin/<datetime>/origin.json
        orientation/<datetime>/orientation.json
        data_location/<datetime>/data_location.json
        pixel_size/<datetime>/pixel_size.json
        logs/running_log.txt

``<ProgramData>`` is ``C:\ProgramData`` on Windows; the environment variable
``ZMART_MICROSCOPY_ROOT`` moves it, which the tests use. ``<microscope>`` is
the connection's ``microscope`` name. Each item keeps one dated folder per
time it was saved, and the newest is the one in use, so nothing is ever
overwritten and one can always see what the microscope was told, and when.
The dates are in UTC, written so that their alphabetical order is their
order in time (``2026-10-27T09-30-00-123456Z``).

The settings can also be kept in a config: a folder of its own
(``config_YYYYMMDD_HHMM``) holding the same tree, chosen when connecting.
Without one, the tree sits in the folder above directly.

The bridge's own files (the macro that starts it, its stop file and its
key) are shared by every microscope on the computer, in
``<ProgramData>/zmart-microscopy/nikon/bridge/``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/store.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_PROGRAMDATA_ROOT = Path(r"C:\ProgramData\zmart-microscopy")
PROGRAMDATA_ROOT_ENV = "ZMART_MICROSCOPY_ROOT"
VENDOR = "nikon"
API = "nis_elements_ar7"
#: The microscope name used when a connection names none.
DEFAULT_MICROSCOPE = "nikon-microscope"

#: Each item and the file it keeps in its dated folder.
FILES = {
    "limits": "limits.json",
    "origin": "origin.json",
    "orientation": "orientation.json",
    "data_location": "data_location.json",
    "pixel_size": "pixel_size.json",
}
#: The items that ship with a default in the driver, used until one is saved.
BUNDLED = {"limits", "orientation"}

_SNAPSHOT_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"
_SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z$")
_CONFIGURATION_RE = re.compile(r"^config_\d{8}_\d{4}(-\d+)?$")
_MICROSCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: The config this process reads and saves its settings in; None, the microscope's folder itself.
_in_use: str | None = None


def programdata_root() -> Path:
    """The computer's configuration folder for ZMART: ``ZMART_MICROSCOPY_ROOT``, or ``C:\\ProgramData\\zmart-microscopy``."""
    env = os.environ.get(PROGRAMDATA_ROOT_ENV)
    return Path(env) if env else DEFAULT_PROGRAMDATA_ROOT


def bridge_folder() -> Path:
    """Where the bridge's macro, stop file and key are kept, for every Nikon microscope on this computer."""
    return programdata_root() / VENDOR / "bridge"


def is_snapshot_name(name: str) -> bool:
    return bool(_SNAPSHOT_RE.match(name))


def is_configuration_name(name: str) -> bool:
    return bool(_CONFIGURATION_RE.match(str(name)))


def use_configuration(name: str | None) -> None:
    """Read and save the settings in config *name* from now on; None, in the microscope's own folder."""
    global _in_use
    if name is not None and not is_configuration_name(name):
        raise ValueError(f"{name!r} is not a config's name (config_YYYYMMDD_HHMM)")
    _in_use = name


def configuration_in_use() -> str | None:
    return _in_use


def check_microscope_name(name: str) -> str:
    """*name* when it can be a folder's name (letters, digits, ``.``, ``_`` and ``-``); ValueError otherwise."""
    name = str(name).strip()
    if not _MICROSCOPE_RE.match(name):
        raise ValueError(
            f"the microscope's name {name!r} must be letters, digits, '.', '_' or '-', such as nikon-ti2-zmb"
        )
    return name


def _driver_root() -> Path:
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class MachineProfile:
    """Where one microscope's settings live on disk.

    ``programdata_root=None`` takes :func:`programdata_root` at the moment it
    is asked, so a test that points ``ZMART_MICROSCOPY_ROOT`` elsewhere
    reaches every profile.
    """

    microscope: str = DEFAULT_MICROSCOPE
    programdata_root: Path | None = None

    def root(self) -> Path:
        return (
            Path(self.programdata_root)
            if self.programdata_root is not None
            else programdata_root()
        )

    def microscope_root(self) -> Path:
        """The microscope's folder, which holds its configs."""
        return self.root() / VENDOR / self.microscope / API

    def settings_root(self) -> Path:
        """Where the settings are read and saved: the config in use, else the microscope's folder."""
        return self.microscope_root() / _in_use if _in_use else self.microscope_root()

    def configurations(self) -> list[str]:
        """The configs kept on this computer, the most recent first."""
        root = self.microscope_root()
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

    def logs_folder(self) -> Path:
        return self.settings_root() / "logs"

    def item_root(self, item: str) -> Path:
        if item not in FILES:
            raise ValueError(
                f"unknown setup item {item!r}; the items are {sorted(FILES)}"
            )
        return self.settings_root() / item

    def snapshots(self, item: str) -> list[Path]:
        """The dated folders of *item* that hold its file, oldest first."""
        root = self.item_root(item)
        if not root.is_dir():
            return []
        return sorted(
            (
                p
                for p in root.iterdir()
                if p.is_dir()
                and is_snapshot_name(p.name)
                and (p / FILES[item]).is_file()
            ),
            key=lambda p: p.name,
        )

    def saved_path(self, item: str) -> Path | None:
        """The newest saved file of *item*, or None when it was never saved."""
        snapshots = self.snapshots(item)
        return snapshots[-1] / FILES[item] if snapshots else None

    def bundled_default(self, item: str) -> Path:
        """The default *item* that ships with the driver, in its folder under ``driver_setup/``."""
        if item not in BUNDLED:
            raise ValueError(f"{item!r} has no bundled default")
        return _driver_root() / "driver_setup" / item / "defaults" / FILES[item]

    def read(self, item: str) -> dict | None:
        """The newest saved *item*, or None when it was never saved.

        A file that exists but is not valid JSON raises ``ValueError``, and one
        that cannot be read raises ``OSError``: a damaged setting is said, never
        taken for "not set up".
        """
        path = self.saved_path(item)
        if path is None:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(
        self, item: str, payload: dict, *, moment: datetime | None = None
    ) -> Path:
        """Save *payload* as a new dated folder of *item*, written whole; the saved file's path.

        The folder is written under a temporary name and then renamed, so a
        reader never sees half a file. A date that does not sort after the
        newest (a clock set back) is moved on by one microsecond.
        """
        moment = moment or datetime.now(UTC)
        name = moment.astimezone(UTC).strftime(_SNAPSHOT_FORMAT)
        snapshots = self.snapshots(item)
        if snapshots and name <= snapshots[-1].name:
            newest = datetime.strptime(snapshots[-1].name, _SNAPSHOT_FORMAT).replace(
                tzinfo=UTC
            )
            name = (newest + timedelta(microseconds=1)).strftime(_SNAPSHOT_FORMAT)
        root = self.item_root(item)
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{name}.partial"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            with (staging / FILES[item]).open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            os.replace(staging, root / name)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return root / name / FILES[item]

    def ensure_layout(self) -> Path:
        """Make the microscope's settings folder and one folder per item, if they are not there."""
        root = self.settings_root()
        for item in FILES:
            (root / item).mkdir(parents=True, exist_ok=True)
        return root


#: The profile of the microscope this process works with; ``methods.connect`` names it.
MACHINE = MachineProfile()


def use_microscope(name: str) -> MachineProfile:
    """Work with the settings of the microscope called *name* from now on; its profile."""
    global MACHINE
    MACHINE = MachineProfile(microscope=check_microscope_name(name))
    return MACHINE


def machine() -> MachineProfile:
    """The profile in use now (read at call time, so :func:`use_microscope` reaches every caller)."""
    return MACHINE
