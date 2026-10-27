"""The driver's side of the bridge: one socket connection to the Python inside NIS-Elements.

:class:`NisClient` connects to the bridge (``server.py``, started inside NIS
by ``start_bridge.mac``) and sends one request at a time, waiting for its
reply before the next. A reading goes through :meth:`NisClient.read`, which
accepts only the bridge's readings. A change (a move, a selection, a capture)
is sent only by the transfer (``transfer.py``), through
:meth:`NisClient.send_change`: that way every change passes the limits gate
of the dispatcher first, and a notebook holding a client can read but never
move the stage around the gate. A test checks that nothing else calls it.

Two things can go wrong, and each has its own error:

- :class:`BridgeUnavailable`: the bridge cannot be reached, was closed, or its
  macro loop stopped. The connection is dropped, and the next call connects
  afresh by itself, so a restarted bridge is picked up without reconnecting by hand.
- :class:`NisRefused`: the bridge answered, but with an error: a request NIS
  did not take in time, a function the bridge does not allow, wrong arguments,
  or NIS itself saying no. Its ``kind`` says which, and ``code`` holds NIS's
  ``DR_`` code when there was one.

A reply that does not come in time closes the socket, so that a late reply
can never be taken for the answer to the next request.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from pathlib import Path
from typing import Any

from . import server as bridge

DEFAULT_HOST = bridge.HOST
DEFAULT_PORT = bridge.PORT
#: How long a reading waits for its reply, unless its window leaves less.
READ_TIMEOUT_S = 10.0
#: How long a change waits: longer than the bridge itself waits, so the
#: bridge always answers first and says whether the change ran.
CHANGE_TIMEOUT_S = bridge.REQUEST_TIMEOUT_S + bridge.JOB_TIMEOUT_S + 30.0

#: The refusals that mean the connection is of no more use: drop it and connect afresh next time.
GONE = {bridge.KIND_CLOSED, bridge.KIND_NOT_PUMPING, bridge.KIND_NOT_AUTHORISED}


class BridgeUnavailable(RuntimeError):
    """The bridge inside NIS-Elements cannot be reached, was closed, or its macro loop is not running."""


class NisRefused(RuntimeError):
    """The bridge answered with an error instead of a value; ``kind`` says which, ``code`` is NIS's DR code."""

    def __init__(
        self, message: str, kind: str = bridge.KIND_REFUSED, code: int | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code


def ptr(kind: str) -> dict:
    """An argument NIS fills in, of the given ``nis.ptr`` type: ``ptr("double")`` for a position."""
    if kind not in bridge.PTR_TYPES:
        raise ValueError(f"unknown ptr type {kind!r}; one of {list(bridge.PTR_TYPES)}")
    return {"ptr": kind}


class NisClient:
    """One connection to the bridge.

    *key* is the bridge's key; when it is None, it is read from *key_file*,
    which the bridge writes each time it starts, and read again on every
    reconnect, so a restarted bridge is found.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        key: str | None = None,
        key_file: str | Path | None = None,
        timeout_s: float = READ_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self._key = key
        self.key_file = Path(key_file) if key_file else None
        self._sock: socket.socket | None = None
        self._stream: Any = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._replies = threading.local()

    # -- the connection ---------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @property
    def last_reply(self) -> dict | None:
        """The reply to this thread's last request, kept for the transfer's error check."""
        return getattr(self._replies, "reply", None)

    @last_reply.setter
    def last_reply(self, value: dict | None) -> None:
        self._replies.reply = value

    def key(self) -> str:
        if self._key is not None:
            return self._key
        if self.key_file is None:
            raise BridgeUnavailable("no key for the bridge: give key or key_file")
        try:
            return self.key_file.read_text(encoding="ascii").strip()
        except OSError as error:
            raise BridgeUnavailable(
                f"the bridge's key could not be read from {self.key_file} ({error}); is start_bridge.mac running in NIS-Elements?"
            ) from None

    def connect(self) -> dict:
        """Open the connection and answer the bridge's ``ping``.

        Raises :class:`BridgeUnavailable` when nothing listens, or when the
        bridge running in NIS is another version than this driver's: then the
        macro must be written again (``driver_setup/nis_bridge``) and restarted.
        """
        with self._lock:
            self._open()
        answer = self.ping()
        if answer.get("bridge") != bridge.VERSION:
            self.close()
            raise BridgeUnavailable(
                f"the bridge in NIS is version {answer.get('bridge')}, this driver's is {bridge.VERSION}: "
                "write the macro again (python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge) "
                "and restart start_bridge.mac in NIS"
            )
        return answer

    def _open(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout_s
            )
        except OSError as error:
            raise BridgeUnavailable(
                f"no bridge at {self.host}:{self.port} ({error}). Is NIS-Elements running, and was "
                "start_bridge.mac started in it (Macro > Run Macro From File)?"
            ) from None
        self._sock = sock
        self._stream = sock.makefile("rwb")

    def close(self) -> None:
        """Close the connection; the bridge keeps running in NIS. A second call does nothing."""
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            self._stream.close()
        except OSError:
            pass
        finally:
            sock.close()

    # -- the requests ---------------------------------------------------------------------

    def ping(self) -> dict:
        """The bridge's version, NIS's process, how lately its loop ran, and NIS's newest dialogs."""
        return self._send({"op": "ping"})

    def shutdown(self) -> dict:
        """Ask the bridge's macro loop to end; the macro then closes the bridge."""
        return self._send({"op": "shutdown"})

    def read(
        self, fn: str, args: list | None = None, *, timeout_s: float | None = None
    ) -> dict:
        """Call one of NIS's readings: ``{"return": ..., "values": [the arguments after the call]}``.

        It waits *timeout_s* for the reply, or the time :meth:`reading_within`
        gave this thread, or :data:`READ_TIMEOUT_S`.
        """
        if fn not in bridge.READS:
            raise NisRefused(
                f"{fn} is not a reading; changes are sent through the dispatcher",
                bridge.KIND_NOT_ALLOWED,
            )
        timeout_s = timeout_s or getattr(self._replies, "read_timeout_s", None)
        return self._send(
            {"op": "read", "fn": fn, "args": list(args or [])}, timeout_s=timeout_s
        )

    @contextlib.contextmanager
    def reading_within(self, seconds: float):
        """Within this block, every reading of this thread waits at most *seconds* for its reply.

        The read loop uses it, so that a reading keeps to its window of the
        waiting rule however many calls it makes.
        """
        before = getattr(self._replies, "read_timeout_s", None)
        self._replies.read_timeout_s = seconds
        try:
            yield
        finally:
            self._replies.read_timeout_s = before

    def send_change(self, op: str, fn: str, args: list) -> dict:
        """Send one change (``op`` is ``write`` or ``snap``). Only the transfer calls this, after the limits gate."""
        if op not in ("write", "snap"):
            raise ValueError(f"a change is a write or a snap, not {op!r}")
        return self._send(
            {"op": op, "fn": fn, "args": list(args)}, timeout_s=CHANGE_TIMEOUT_S
        )

    def _send(self, request: dict, *, timeout_s: float | None = None) -> Any:
        with self._lock:
            self._open()
            self._next_id += 1
            request = {"id": self._next_id, "key": self.key(), **request}
            what = request.get("fn") or request["op"]
            try:
                self._sock.settimeout(timeout_s or self.timeout_s)
                self._stream.write((json.dumps(request) + "\n").encode("utf-8"))
                self._stream.flush()
                line = self._stream.readline()
                if not line:
                    raise OSError("the bridge closed the connection")
                reply = json.loads(line)
            except (OSError, ValueError) as error:
                self.close()
                self.last_reply = None
                raise BridgeUnavailable(
                    f"the bridge did not answer {what!r}: {error}"
                ) from None
        self.last_reply = reply
        if reply.get("id") not in (None, request["id"]):
            self.close()
            raise BridgeUnavailable(
                f"the bridge answered request {reply.get('id')} instead of {request['id']}"
            )
        if not reply.get("ok"):
            kind = str(reply.get("kind") or bridge.KIND_REFUSED)
            message = str(reply.get("error") or "the bridge gave no reason")
            if kind in GONE:
                self.close()
                raise BridgeUnavailable(message)
            raise NisRefused(message, kind)
        return reply.get("answer")


def is_listening(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout_s: float = 0.5
) -> bool:
    """Whether something accepts connections on the bridge's address."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False
