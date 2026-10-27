"""The connection to the bridge inside NIS-Elements, and the errors it can raise.

NIS-Elements cannot be called from another program directly. The nis-bridge
(the ``nis_bridge`` package of thomdehoog/nis-useq-aiagent) is a small server
that a NIS macro runs inside NIS-Elements' own Python; it takes requests over
a socket on this computer, carries each one out on NIS's main thread, and
answers. :class:`Bridge` is one such connection, as the driver uses it: one
request at a time, every error turned into one of three kinds the rest of
the driver understands:

- ``ValueError``: the request was wrong (an unknown optical configuration, a
  missing argument);
- ``RuntimeError``: NIS-Elements refused or failed, or had not started the
  request when the driver stopped waiting;
- :class:`BridgeLost`: the bridge could not be reached, or the connection
  dropped; the session can ``reconnect()`` once the macro runs again.

The ``nis_bridge`` package is imported only when a connection is made, so the
driver can be imported, and its offline tests collected, where it is not
installed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: Where the bridge listens unless the connection says otherwise: on this
#: computer only, on the port the bridge's own settings name.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 54470
#: How long one ordinary request may take before the driver stops waiting.
DEFAULT_TIMEOUT_S = 30.0

#: What to do when the bridge does not answer, said in every such error.
START_THE_BRIDGE = (
    "start NIS-Elements and run start_bridge.mac in it (Macro > Run Macro From "
    "File...), or `python -m nis_bridge.start`; the bridge's README says how"
)


class BridgeLost(RuntimeError):
    """The bridge could not be reached, or the connection to it dropped."""


@dataclass(frozen=True)
class Answered:
    """What the bridge answered to the last request sent: a result, or the error."""

    result: Any = None
    error: Exception | None = None


class Bridge:
    """One connection to the bridge, used one request at a time.

    ``info`` is what the bridge said when the connection was made: its own
    version, the protocol version and the NIS-Elements version. ``reply`` is
    where the transfer leaves the answer to the last change it sent, so the
    profile can check it.
    """

    def __init__(self, host: str, port: int, timeout_s: float) -> None:
        self.host, self.port, self.timeout_s = host, int(port), float(timeout_s)
        self.lock = threading.Lock()
        self.reply: Answered | None = None
        self._client = _open(host, self.port, self.timeout_s)
        self.info: dict = dict(self._client.info)

    @property
    def closed(self) -> bool:
        """True after :meth:`close`, or after an error ended the connection."""
        return self._client is None or self._client.closed

    def request(self, op: str, *, timeout_s: float | None = None, **args: Any) -> Any:
        """Send one request and answer its result; see the module note for the errors.

        *timeout_s* overrides how long to wait for this one request, for a
        capture or a focus sweep that takes longer than an ordinary request.
        """
        from nis_bridge.client import NisConnectionError

        if self.closed:
            raise BridgeLost(
                "the connection to NIS-Elements is closed; reconnect() once the "
                f"bridge answers again ({START_THE_BRIDGE})"
            )
        with self.lock:
            try:
                return self._client.request(op, timeout=timeout_s, **args)
            except NisConnectionError as why:
                raise BridgeLost(str(why)) from why

    def reconnect(self) -> None:
        """Open the connection again, after the bridge was restarted or a request timed out."""
        self.close()
        self._client = _open(self.host, self.port, self.timeout_s)
        self.info = dict(self._client.info)

    def close(self) -> None:
        """Close the socket. The bridge itself keeps running inside NIS-Elements."""
        client, self._client = self._client, None
        if client is not None:
            client.close()


def _open(host: str, port: int, timeout_s: float):
    """A connected ``NisClient``; :class:`BridgeLost` with what to do when there is no bridge."""
    try:
        from nis_bridge.client import NisClient, NisConnectionError
    except ImportError as why:
        raise BridgeLost(
            "the nis_bridge package is not installed; install the driver's extra "
            '(pip install -e ".[nikon]") or the bridge itself from '
            "thomdehoog/nis-useq-aiagent (1_nis_bridge)"
        ) from why
    try:
        return NisClient(host, port, timeout_s)
    except NisConnectionError as why:
        raise BridgeLost(f"{why} To fix it, {START_THE_BRIDGE}.") from why


def connect_bridge(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Bridge:
    """Connect to the bridge inside NIS-Elements and hand back the :class:`Bridge`.

    Raises :class:`BridgeLost`, saying what to do, when nothing answers at
    *host* and *port*, or when the bridge speaks another protocol version
    than the installed ``nis_bridge`` package.
    """
    bridge = Bridge(host, port, timeout_s)
    log.info(
        "connected to the bridge %s at %s:%s; NIS-Elements %s",
        bridge.info.get("bridge"),
        host,
        port,
        bridge.info.get("nis"),
    )
    return bridge
