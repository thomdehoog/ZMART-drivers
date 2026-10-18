r"""ZMART Controller adapter for the Navigator Expert driver.

The list of commands the controller can call on this driver, one function per
command, each taking the opaque handle that :func:`connect` returns. The
controller stays free of vendor details: this file is where the two meet.

The work itself lives in ``procedures/``:

- ``session.py``: the handle and the questions every command asks.
- ``frame.py``: the stage position in the controller's frame, ``set_xyz``,
  ``get_xyz``, ``set_origin`` and the allowed range.
- ``acquisition.py``: ``acquire`` and the log it saves.
- ``instrument.py``: ``get_state``, ``set_state``, the procedures, ``get_info``.

What stays here is opening and closing the connection, and the table that
hands the commands to the controller.

Answers: through the controller, every command answers
``{"success": True, "content": ...}``, the shape the controller documents for
every driver. The functions return the content alone, and :func:`ops_table`
wraps them for the controller. Failures are always raised, never reported as
``success: False``.

Limits are enforced below this file, in the change definitions themselves
(``procedures/changes.py`` and ``dispatcher/gate.py``). A failed limits
handshake at connect leaves the session read-only: every command that would
move or change something refuses with the recorded reason.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

from ... import connect as _session
from ...configuration import data_location as _data_location
from ...configuration import session_state as _session_state
from ...dispatcher import gate as _gate
from ...output.naming import run_hash
from ...procedures import frame as _frame
from ...procedures.acquisition import acquire, get_acquisition_settings
from ...procedures.frame import get_actuators, get_xyz, set_origin, set_xyz
from ...procedures.instrument import (
    get_info,
    get_procedures,
    get_state,
    run_procedure,
    set_state,
)
from ...procedures.session import ZmartHandle

# The public surface of this module, as ``zmart_adapter/__init__.py`` re-exports it.
__all__ = [
    "CONNECTION",
    "ZmartHandle",
    "acquire",
    "connect",
    "disconnect",
    "get_acquisition_settings",
    "get_actuators",
    "get_info",
    "get_procedures",
    "get_state",
    "get_xyz",
    "ops_table",
    "run_procedure",
    "set_origin",
    "set_state",
    "set_xyz",
]

log = logging.getLogger(__name__)

# The connection settings used for any key the caller leaves out; the
# docstring of zmart_controller_plugin.py explains each one. ``microscope`` names this
# instrument. Image orientation is enabled by IMAGE_SAVE in config/profiles.py;
# only the orientation measurement explicitly saves raw pixels.
# The connection settings used for any key the caller leaves out; the
# docstring of zmart_controller_plugin.py explains each one. ``microscope`` names this
# instrument. Image orientation is enabled by IMAGE_SAVE in config/profiles.py;
# only the orientation measurement explicitly saves raw pixels.
CONNECTION = {
    "microscope": "stellaris5-y42h93",
    "client": "PythonClient",
    "api_delay_ms": None,
    "output_root": None,
    "load_limits": True,
    "load_calibration": True,
    "load_origin": True,
}


def connect(connection: dict | None = None) -> ZmartHandle:
    """Open the CAM client and return the controller handle.

    Args:
        connection: The connection settings, all optional; a key left out
            takes its value from :data:`CONNECTION`, so an empty dictionary
            connects with the defaults. ``client`` and ``api_delay_ms`` feed
            the CAM connection; ``output_root`` is where :func:`acquire`
            saves; ``load_origin`` (default True) decides whether the saved
            frame origin is loaded.

    Delegates to the driver's own connection entry point
    (:func:`navigator_expert.connect_microscope`), which loads this microscope's
    three machine-local configs and installs each so the whole session works
    from one consistent picture: the **instrument limits** (into the commands gate),
    the **camera-to-stage orientation**, and the **per-objective calibration**
    (both into the per-connection session registry). Image orientation follows
    ``IMAGE_SAVE`` in ``config/profiles.py``. The connection dict may override
    limits/calibration loading and pick a named calibration set.

    The limits load is fail-soft: an invalid or opted-out ``limits.json``
    falls back to the bundled default envelope (loudly warned) rather than
    leaving the session unable to move; the hardcoded physical backstop bounds
    every move regardless.

    The frame origin is loaded here as well, from the newest ``origin.json``
    that :func:`set_origin` saved on this computer. Every session therefore
    starts in the same frame, and the origin keeps the objective it was
    captured under, so objective changes are handled exactly as they would
    be right after ``set_origin``. When no origin has been saved yet, the
    frame is the plain absolute stage coordinates and a warning is logged.
    Setting ``load_origin`` to False in the connection dict skips the saved
    origin on purpose and starts in stage coordinates.

    Raises:
        RuntimeError: when a saved origin exists but cannot be read or does
            not contain the expected values. The frame would otherwise
            silently fall back to stage coordinates and every move would
            land somewhere other than intended, so connect stops instead.
            The origin is checked before LAS X is contacted, so nothing is
            left half-connected.
        Whatever :func:`connect_microscope` raises when LAS X is
        unreachable; the controller passes that to the caller unchanged.
    """
    connection = {**CONNECTION, **(connection or {})}
    saved_origin = (
        _frame.load_saved_origin() if connection.get("load_origin", True) else None
    )
    saved_location = _load_data_location()
    client = _session.connect_microscope(
        client_name=connection.get("client", "PythonClient"),
        api_delay_ms=connection.get("api_delay_ms"),
        load_limits=connection.get("load_limits", True),
        load_calibration=connection.get("load_calibration", True),
        calibration_name=connection.get("calibration_name"),
    )
    handle = ZmartHandle(client=client, connection=dict(connection), hash6=run_hash())
    loaded = _session_state.get(client)
    handle.translations = loaded.translations if loaded is not None else None
    if saved_origin is not None:
        handle.origin = saved_origin
    handle.data_location = saved_location
    return handle


def _load_data_location() -> Path | None:
    """This microscope's set-up data location, or None when it was never set up.

    A record that cannot be read stops the connection with how to fix it,
    rather than being taken for "not set up".
    """
    try:
        return _data_location.data_location()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"this microscope's saved data location could not be read ({exc}); "
            "set it again with configuration/data_location/notebooks/set_data_location.ipynb"
        ) from exc


def disconnect(handle: ZmartHandle) -> None:
    """Mark the handle closed and drop this connection's loaded driver state.

    The CAM client itself has no teardown, and none exists to call: the public
    methods of ``LasxApiClientPyModel`` (the client `connect_python_client`
    returns) are ``Connect``/``ConnectAsync`` (plus a COM-style ``Release``,
    not a session close) -- there is no ``Disconnect``/``Close``/``Dispose``.
    Reconnecting without disconnecting the previous handle was
    also verified live (both connections independently ping and read state
    correctly; the first stays usable after the second connects) -- a
    resource leak on the LAS X side, not a corruption/dead-end risk. Without
    this function's ``uninstall`` call, a disconnected client's
    ``FunctionLimits`` stayed installed in the gate's module-level registry
    indefinitely; a reconnect on a new handle re-runs the handshake and
    installs its own state regardless, but a stale entry left behind after
    disconnect is real teardown debt, not just cosmetic.
    """
    _gate.uninstall(handle.client)
    _session_state.uninstall(handle.client)
    handle.closed = True


# The commands that answer in the controller's shape. connect returns the
# handle and disconnect returns nothing, so those two are handed over as they are.
_ANSWERING_OPS = (
    "get_acquisition_settings",
    "get_actuators",
    "get_xyz",
    "set_xyz",
    "acquire",
    "get_state",
    "set_state",
    "get_procedures",
    "run_procedure",
    "get_info",
)


def _answered(function):
    """Wrap a command so it answers the way the controller documents.

    The controller promises every workflow the same answer from every
    microscope: ``{"success": ..., "content": ...}``. The functions in this
    module return only the content. Each of them raises when something goes
    wrong, so an answer that comes back at all is a success, and the content
    is exactly what the function returned.
    """

    @functools.wraps(function)
    def command(*args, **kwargs):
        return {"success": True, "content": function(*args, **kwargs)}

    return command


def ops_table() -> dict[str, Any]:
    """The functions this driver hands to the controller, one per command.

    ``connect`` and ``disconnect`` are handed over unchanged. Every other
    command is wrapped so that, called through the controller, it answers
    ``{"success": True, "content": ...}``. Called directly from this module,
    the same functions return the content alone.
    """
    functions = {
        "connect": connect,
        "disconnect": disconnect,
        "get_acquisition_settings": get_acquisition_settings,
        "get_actuators": get_actuators,
        "get_xyz": get_xyz,
        "set_xyz": set_xyz,
        "acquire": acquire,
        "get_state": get_state,
        "set_state": set_state,
        "get_procedures": get_procedures,
        "run_procedure": run_procedure,
        "get_info": get_info,
    }
    return {
        name: _answered(function) if name in _ANSWERING_OPS else function
        for name, function in functions.items()
    }
