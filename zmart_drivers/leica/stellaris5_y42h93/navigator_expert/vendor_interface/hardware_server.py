"""LAS X's hardware server, read for where a z drive is.

While LAS X runs, its hardware server can be reached through Windows'
running-object table, and it gives each z drive's position, in metres:
``Hardware.ZPosZGalvo`` and ``Hardware.ZPosZDrive``. The CAM API cannot:
once a job's z-stack is on a drive, the job settings stop refreshing that
drive's ``zPosition`` (seen on the simulator, 2026-08-25; on 2026-10-12 the
server gave the galvo at -2.38e-10 m while the settings said 0, and followed
a +5 µm move the settings never saw).

This module only reads. It attaches to what LAS X already runs and creates
nothing; it never writes a property. The only commands sent through it are
the reads of ``direct_reader``.
"""

from __future__ import annotations

import math
import threading

#: The running hardware server's entry in Windows' running-object table.
SERVER_CLASS_ID = "{22036761-7A0A-11D2-BB7E-00104B43E7C1}"

#: The driver's name for each z drive, and the server property holding its position.
Z_PROPERTY = {"z-galvo": "ZPosZGalvo", "z-wide": "ZPosZDrive"}

connections = threading.local()


class HardwareServerUnavailable(RuntimeError):
    """The hardware server could not be reached, or gave no position."""


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

    *drive* is ``"z-galvo"`` or ``"z-wide"``; the number is in the same frame
    as the job settings' ``zPosition``. A kept connection that fails (LAS X
    restarted) is made again once. Raises :class:`HardwareServerUnavailable`
    rather than returning anything that is not a fresh position.
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
