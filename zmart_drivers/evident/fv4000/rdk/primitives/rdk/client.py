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


class _NotReached(OSError):
    """The connection to FLUOVIEW could not even be opened, so nothing was sent."""


class _OneTryTransport(xmlrpc.client.Transport):
    """The standard HTTP transport, changed in two ways that matter for a microscope.

    First, every call is sent exactly once. Python's own transport quietly
    sends a request a second time when the connection is reset during it,
    which for ``Protocol.startProtocol`` would be a second picture. Here a
    reset is reported, and the driver's own rules decide whether the call
    may go again (a capture never does).

    Second, every call opens its own connection, with a timeout, and closes it
    afterwards. A connection kept open between calls could have been closed by
    FLUOVIEW in the meantime, and the standard transport's answer to that is
    the very resend this class removes. The connection is opened before the
    request is written, so a failure to open it is told apart from a failure
    after the request went out (:class:`_NotReached`).
    """

    def __init__(self, timeout_s: float):
        super().__init__()
        self._timeout_s = timeout_s

    def make_connection(self, host):
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        address, self._extra_headers, _x509 = self.get_host_info(host)
        self._connection = (
            host,
            http.client.HTTPConnection(address, timeout=self._timeout_s),
        )
        return self._connection[1]

    def request(self, host, handler, request_body, verbose=False):
        connection = self.make_connection(host)
        try:
            try:
                connection.connect()
            except OSError as why:
                raise _NotReached(str(why)) from why
            return self.single_request(host, handler, request_body, verbose)
        finally:
            self.close()


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
            transport=_OneTryTransport(timeout_s),
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
            except _NotReached as why:
                # Nothing was sent: FLUOVIEW is closed, the RDK is off, or the address is wrong.
                raise ConnectionLost(
                    f"FLUOVIEW does not answer at {self.address.url} ({why}); check that "
                    "FLUOVIEW is running, that its RDK is switched on, and the address and port"
                ) from why
            except (OSError, http.client.HTTPException) as why:
                # The request went out, but its answer never came back whole: a reset (the
                # manual's error 10054), a dropped connection, or no answer within the timeout.
                raise CallInterrupted(
                    f"{method}: the connection to FLUOVIEW broke after the call was sent ({why}); "
                    "FLUOVIEW may or may not have carried it out, so look at FLUOVIEW before trying again"
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
