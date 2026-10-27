"""The connection to FLUOVIEW: one XML-RPC client, paced so that FLUOVIEW is never flooded.

FLUOVIEW's Remote Development Kit (RDK) is an XML-RPC server inside the
FLUOVIEW software, listening on port 8080 by default
(``http://<address>:8080/xmlrpc``). XML-RPC is a plain way of calling
functions on another program over the network: each call is a small HTTP
request, and Python's standard library speaks it, so nothing of Evident's
needs to be installed on the driver's computer. The driver may run on the
FLUOVIEW computer itself (then the address is ``127.0.0.1``) or on another
computer on the same network (the manual's section 2).

The calls are grouped: ``Maintenance.getVersion``, ``Protocol.startProtocol``,
``Parameter.setParameter`` and so on. Every call answers a dictionary with a
``result`` word. :class:`RdkClient` makes the calls, one at a time, with a
short pause between them: the manual's troubleshooting section says that a
burst of commands can make FLUOVIEW drop the connection (a socket error
10054), and a short gap avoids it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import http.client
import logging
import socket
import threading
import time
import xmlrpc.client
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Where the RDK listens unless FLUOVIEW was told otherwise (``-rdkport`` in
#: ``cellSens_FV.ini``, the manual's section 6).
DEFAULT_PORT = 8080

#: The shortest time between two calls, so FLUOVIEW is never flooded. Not
#: measured on an FV4000 yet: the manual only says that too many commands in
#: a short time can cut the connection.
MIN_GAP_S = 0.02

#: How long one call may take before the network layer gives up. A scan is
#: never waited for inside a call (the driver polls the progress instead), so
#: a call that takes longer than this is a stuck FLUOVIEW, not a slow one.
CALL_TIMEOUT_S = 30.0


class ConnectionLost(RuntimeError):
    """FLUOVIEW cannot be reached: it is closed, the address is wrong, or the network is down."""


class CallInterrupted(RuntimeError):
    """A call went out, but its answer never came back whole (the connection was reset).

    The manual names this case: FLUOVIEW resets the connection when commands
    arrive too quickly. The call may or may not have been carried out, so the
    caller decides, by its own rule, whether it may be sent again.
    """


@dataclass(frozen=True)
class RdkAddress:
    """Where FLUOVIEW's RDK listens: the computer and the port."""

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/xmlrpc"


class _TimeoutTransport(xmlrpc.client.Transport):
    """The standard HTTP transport, with a timeout on every connection."""

    def __init__(self, timeout_s: float):
        super().__init__()
        self._timeout_s = timeout_s

    def make_connection(self, host):
        connection = http.client.HTTPConnection(host, timeout=self._timeout_s)
        self._connection = host, connection
        return connection


class RdkClient:
    """One connection to FLUOVIEW's RDK. ``call(method, payload)`` answers the reply dictionary.

    *host* and *port* say where FLUOVIEW listens. *min_gap_s* is the pause
    kept between two calls and *timeout_s* how long one call may take.
    ``last_reply`` holds the dictionary of the most recent call, which the
    write loops read back after a send (``transfer.py``).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        *,
        min_gap_s: float = MIN_GAP_S,
        timeout_s: float = CALL_TIMEOUT_S,
    ):
        self.address = RdkAddress(host, int(port))
        self.min_gap_s = min_gap_s
        self._proxy = xmlrpc.client.ServerProxy(
            self.address.url,
            transport=_TimeoutTransport(timeout_s),
            allow_none=False,
            # Binary answers (the pixels of a picture) come back as plain bytes.
            use_builtin_types=True,
        )
        self._lock = threading.Lock()
        self._last_call_at = 0.0
        self.last_reply: dict | None = None
        #: Every call made, newest last, for the running log and the tests: (method, payload).
        self.calls_made = 0

    def __repr__(self) -> str:
        return f"RdkClient({self.address.url})"

    def call(self, method: str, payload: dict | None = None) -> dict:
        """Call *method* (``"Parameter.getParameter"``) with *payload*, and answer FLUOVIEW's reply.

        The reply is the dictionary FLUOVIEW sent, ``result`` and all; it is
        not checked here, so a reading can look at the code itself
        (``parsing.require_ok`` does that for most callers). Raises
        :class:`ConnectionLost` when FLUOVIEW cannot be reached at all and
        :class:`CallInterrupted` when the connection broke during the call.
        """
        target = self._proxy
        for part in method.split("."):
            target = getattr(target, part)
        with self._lock:
            self._pace()
            try:
                reply = target(payload) if payload is not None else target()
            except (ConnectionRefusedError, socket.gaierror, TimeoutError) as why:
                raise ConnectionLost(
                    f"FLUOVIEW does not answer at {self.address.url} ({why}); check that "
                    "FLUOVIEW is running, that its RDK is switched on, and the address and port"
                ) from why
            except (
                ConnectionResetError,
                ConnectionAbortedError,
                http.client.RemoteDisconnected,
            ) as why:
                raise CallInterrupted(
                    f"{method}: the connection to FLUOVIEW broke ({why})"
                ) from why
            except OSError as why:
                raise ConnectionLost(
                    f"{method}: FLUOVIEW could not be reached ({why})"
                ) from why
            except xmlrpc.client.ProtocolError as why:
                raise ConnectionLost(
                    f"{method}: {self.address.url} answered HTTP {why.errcode} {why.errmsg}; "
                    "is this FLUOVIEW's RDK port?"
                ) from why
            finally:
                self._last_call_at = time.monotonic()
                self.calls_made += 1
        if not isinstance(reply, dict):
            reply = {"result": "ERROR", "answer": reply}
        self.last_reply = reply
        return reply

    def _pace(self) -> None:
        """Wait out the rest of the gap since the previous call, if any."""
        wait = self.min_gap_s - (time.monotonic() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)


def connect_client(
    host: str = "127.0.0.1", port: int = DEFAULT_PORT, **options
) -> RdkClient:
    """Open the connection to FLUOVIEW at *host*:*port*; the connection itself is made on the first call.

    This is the one function ``methods.connect()`` uses to make the client,
    so a test can stand a mock in here.
    """
    return RdkClient(host, port, **options)
