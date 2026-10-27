"""Where this microscope saves its pictures, set up once like the limits and the orientation.

The data location is the folder every run of this microscope goes under, for
example ``D:\\ZMART-data``. It is chosen once, with this item's notebook
(``notebooks/set_data_location.ipynb``), saved in the microscope's own
settings (``data_location/<datetime>/data_location.json``), and loaded at
every connect. The driver reports it to the controller as its ``output_root``.

There is no default: a microscope that was not told where its data goes saves
nothing, and says which notebook to run. A session can still choose another
folder for itself (the connection's ``output_root``), and a single capture
another again (``acquire``'s ``output_folder``).

A folder must be a full path to an existing folder this computer can write
to, on a local disk: a capture is written while the microscope waits, and a
network share that stalls for a moment would stall the microscope with it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from ..store import MACHINE, MachineProfile

__all__ = ["check", "data_location", "set_data_location"]

#: Windows' answer for a drive that lives on another computer.
DRIVE_REMOTE = 4


def check(folder: str | Path) -> list[str]:
    """What keeps *folder* from holding this microscope's data, in words; empty when nothing does."""
    path = Path(folder)
    if str(path).startswith(("\\\\", "//")):
        return [
            f"{path} is on a network share; save on a local disk and copy to the share after the run"
        ]
    if not path.is_absolute():
        return [
            f"{path} is not a full path (start it with the drive, for example D:\\ZMART-data)"
        ]
    if not path.exists():
        return [f"{path} does not exist; make the folder first"]
    if not path.is_dir():
        return [f"{path} is not a folder"]
    if _on_a_network_drive(path):
        return [
            f"{path} is on a network drive; save on a local disk and copy to the share after the run"
        ]
    probe = path / f".zmart-write-check-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as why:
        return [f"{path} cannot be written to ({why})"]
    return []


def set_data_location(folder: str | Path, *, machine: MachineProfile = MACHINE) -> Path:
    """Check *folder* and save it as this microscope's data location; answers the saved record's path.

    Raises ``ValueError`` with what is wrong when the folder cannot hold the data.
    """
    problems = check(folder)
    if problems:
        raise ValueError("; ".join(problems))
    return machine.write_data_location({"folder": str(Path(folder).resolve())})


def data_location(machine: MachineProfile = MACHINE) -> Path | None:
    """This microscope's data location, or None when it was never set up. A record that cannot be read raises."""
    record = machine.read_data_location()
    if record is None:
        return None
    folder = record.get("folder") if isinstance(record, dict) else None
    if not isinstance(folder, str) or not folder:
        raise ValueError(f"the saved data location does not name a folder: {record!r}")
    return Path(folder)


def _on_a_network_drive(path: Path) -> bool:
    """Whether *path*'s drive is mapped from another computer (Windows only; elsewhere never)."""
    if sys.platform != "win32":
        return False
    import ctypes

    drive = os.path.splitdrive(str(path.resolve()))[0] + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_REMOTE
