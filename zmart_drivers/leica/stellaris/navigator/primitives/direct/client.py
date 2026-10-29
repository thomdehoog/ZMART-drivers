"""LAS X's hardware server: how the driver reaches it, and the connection a session keeps.

The plain Navigator driver has one way into LAS X: its hardware server, the
part of LAS X that holds the instrument's state and takes the commands LAS
X's own window sends. While LAS X runs, the hardware server can be reached
through Windows' running-object table. The driver attaches to what LAS X
already runs and creates nothing. Through that one connection it reads the
state tree (``state_tree.py``, ``reads.py``) and sends the same elements the
window sends (``transfer.py``, ``writes.py``).

There is no CAM API here: Leica's CAM API belongs to the Navigator Expert
add-in, and it does not connect while LAS X shows its plain Navigator
(simulator, 2026-10-15). That is why this driver exists beside
``navigator_expert``.

:class:`Client` is what a session holds and what the dispatcher is handed
with every reading and change. It is small: the name the driver connected
under, and the last answer LAS X gave to a change, which the profile's
``check_reply`` reads. COM connections belong to the thread that made them,
so the connection itself is kept per thread, below.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

#: The running hardware server's entry in Windows' running-object table.
SERVER_CLASS_ID = "{22036761-7A0A-11D2-BB7E-00104B43E7C1}"

#: The driver's name for each z drive, and the server property holding its position.
Z_PROPERTY = {"z-galvo": "ZPosZGalvo", "z-wide": "ZPosZDrive"}

#: Where LAS X is installed, unless ``ZMART_LASX_FOLDER`` says otherwise. The
#: Data Container's .NET client (``LMSDataContainerWrapper.dll``) is looked
#: for here and in the Navigator Expert add-in folder below it.
LASX_FOLDER = Path(r"C:\Program Files\Leica Microsystems CMS GmbH\LAS X")
LASX_FOLDER_ENV = "ZMART_LASX_FOLDER"
DATA_CONTAINER_DLL = "LMSDataContainerWrapper.dll"

#: Where LAS X's window writes what it shows, dialogs included.
LASX_LOG = Path(r"C:\ProgramData\Leica Microsystems\LAS X\LAS X.log")

connections = threading.local()


class HardwareServerUnavailable(RuntimeError):
    """The hardware server could not be reached, or gave no answer."""


@dataclass
class Client:
    """One session's connection to LAS X, as the dispatcher is handed it.

    ``name`` is what the driver calls itself in its log. ``reply`` is LAS
    X's answer to the last element sent for a change: ``accepted`` (True
    when LAS X took it), ``text`` (the reason inside LAS X's answer, for a
    refusal or an adjusted value) and ``answer`` (the whole answer). It is
    emptied before every send and read by the profile's ``check_reply``.
    """

    name: str = "ZMART"
    reply: dict = field(default_factory=dict)


def connect(client_name: str = "ZMART") -> Client:
    """Reach LAS X's hardware server once, and hand back the session's :class:`Client`.

    Raises :class:`HardwareServerUnavailable` when LAS X is not running, or
    its hardware server cannot be reached from this computer.
    """
    connected_server()
    return Client(name=client_name)


def attach():
    """Attach to the running hardware server; one COM connection per thread.

    COM objects belong to the thread that made them, so each thread
    initialises COM and keeps its own connection.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError as why:
        raise HardwareServerUnavailable(f"pywin32 is not installed: {why}") from why
    try:
        pythoncom.CoInitialize()
        running = pythoncom.GetActiveObject(SERVER_CLASS_ID)
        return win32com.client.Dispatch(running.QueryInterface(pythoncom.IID_IDispatch))
    except Exception as why:
        raise HardwareServerUnavailable(
            f"LAS X's hardware server is not running here: {why}"
        ) from why


def connected_server():
    """This thread's connection to the hardware server, made on first use."""
    server = getattr(connections, "server", None)
    if server is None:
        server = attach()
        connections.server = server
    return server


def forget_connection():
    """Drop this thread's connection, so the next read attaches afresh."""
    connections.server = None


def read_hardware_z_um(drive: str) -> float:
    """The true position of one z drive, in micrometres.

    *drive* is ``"z-galvo"`` or ``"z-wide"``. A kept connection that fails
    (LAS X restarted) is made again once. Raises
    :class:`HardwareServerUnavailable` rather than returning anything that
    is not a fresh position.
    """
    if drive not in Z_PROPERTY:
        raise ValueError(
            f"no z drive {drive!r}; the hardware server knows {sorted(Z_PROPERTY)}"
        )
    name = Z_PROPERTY[drive]
    for attempt in ("kept connection", "fresh connection"):
        try:
            metres = getattr(connected_server().Hardware, name)
            break
        except HardwareServerUnavailable:
            raise
        except Exception as why:
            forget_connection()
            if attempt == "fresh connection":
                raise HardwareServerUnavailable(
                    f"the hardware server did not answer {name}: {why}"
                ) from why
    if (
        isinstance(metres, bool)
        or not isinstance(metres, (int, float))
        or not math.isfinite(metres)
    ):
        raise HardwareServerUnavailable(
            f"the hardware server answered {name} = {metres!r}"
        )
    return float(metres) * 1e6


def lasx_folder() -> Path:
    """Where LAS X is installed: ``ZMART_LASX_FOLDER`` when set, else the usual place."""
    chosen = os.environ.get(LASX_FOLDER_ENV)
    return Path(chosen) if chosen else LASX_FOLDER


def data_container_runtime() -> Path:
    """The folder holding LAS X's Data Container client, ``LMSDataContainerWrapper.dll``.

    LAS X's own folder first, then the Navigator Expert add-in folder, which
    carries a copy. Raises ``RuntimeError`` naming both when neither has it,
    so the operator knows where to look.
    """
    root = lasx_folder()
    candidates = (root, root / "AddIns" / "NavigatorExpert")
    for folder in candidates:
        if (folder / DATA_CONTAINER_DLL).is_file():
            return folder
    raise RuntimeError(
        f"{DATA_CONTAINER_DLL} was not found in {' or '.join(str(c) for c in candidates)}; "
        f"is LAS X installed there? Set {LASX_FOLDER_ENV} to its folder otherwise"
    )
