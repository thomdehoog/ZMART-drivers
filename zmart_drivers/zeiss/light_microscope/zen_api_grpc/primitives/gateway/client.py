"""The connection to ZEN: the ZEN API Gateway, its services, and the thread that runs the calls.

ZEN can be driven from outside through the **ZEN API**: a set of services
(the stage, the focus drive, the objective changer, the experiments) that ZEN
offers over the network with gRPC, a way of calling functions on another
program. A small separate program, the **ZEN API Gateway**, stands in front
of ZEN: it encrypts the connection (TLS) and lets a call through only when it
carries the gateway's *control token*, a password the gateway generates. ZEISS
publishes the Python classes for the services as the ``zen_api`` package
(2025.10.1 for ZEN 3.13), and this file is the one place in the driver that
imports from it. Everything else talks in plain Python values, so when ZEISS
renames a service or a field, the fix lands here.

The ``zen_api`` classes are *asynchronous*: each call is a coroutine, a piece
of work that an event loop runs. The rest of the driver is plain blocking
Python, as a notebook wants it, so :class:`ZenClient` keeps one event loop
running in a thread of its own and hands each call to it, waiting for the
answer. One loop for the whole session matters: the encrypted connection
belongs to the loop that opened it, and a streamed answer (an experiment's
status, frame by frame) can only be read by that same loop.

Everything that needs the ``zen_api`` package or ``grpclib`` is imported
inside a function, so the driver imports and its offline tests run on a
computer without them. The offline stand-in (``testing/helpers/mock_zen_api.py``)
hands this client fake services and a fake channel; the loop thread and the
waiting are then exercised for real.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on the ZEN API examples ZEISS publishes in its OAD repository
(``initialize_zenapi`` in ``zen_api_utils``), and on the ZEN driver Thom de
Hoog wrote for ZMART in September 2026.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import configparser
import logging
import queue
import ssl
import threading
from collections.abc import Callable, Iterator
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The ZEN API services the driver uses, by the short name the rest of the
#: driver calls them, each with the ``zen_api`` module and class that provides
#: it. 2025.10.1 (ZEN 3.13) keeps the XY stage under ``lm.hardware.v2``; the
#: generic ``hardware.v1`` stage service (any axis, motion state) exists too,
#: but not every stand provides it, so the driver stays with the LM one.
SERVICES = {
    "stage": ("lm.hardware.v2", "StageServiceStub"),
    "focus": ("lm.hardware.v2", "FocusServiceStub"),
    "objective": ("lm.hardware.v2", "ObjectiveChangerServiceStub"),
    "experiment": ("acquisition.v1beta", "ExperimentServiceStub"),
    "zstack": ("lm.acquisition.v1", "ZStackServiceStub"),
    "tracks": ("lm.acquisition.v1", "TrackServiceStub"),
    "autofocus": ("lm.acquisition.v1", "ExperimentSwAutofocusServiceStub"),
    "definite_focus": ("lm.acquisition.v1", "DefiniteFocusServiceStub"),
}

#: The port the gateway listens on unless its appsettings.json says otherwise.
DEFAULT_PORT = 5002

#: Where the gateway keeps the root certificate it generated, on the ZEN
#: computer. Installs differ in how the folder is spelled; both are tried.
CERTIFICATE_PATHS = (
    r"C:\ProgramData\Carl Zeiss\ZEN APIGateway\Certificates\ZEN APIPersonalSigningRootCA.pem",
    r"C:\ProgramData\Carl Zeiss\ZenApiGateway\Certificates\ZenApiPersonalSigningRootCA.pem",
)

#: Where the gateway writes its global control token, on the ZEN computer.
TOKEN_FILE = r"C:\ProgramData\Carl Zeiss\ZEN APIGateway\GlobalControlToken.txt"

#: How long one ordinary call (a reading, a short move) may take before it
#: counts as timed out, in seconds. Acquisitions and autofocus get their own
#: numbers in ``profile.py``, because they take as long as they take.
CALL_TIMEOUT_S = 30.0

#: How long opening the connection may take, in seconds.
CONNECT_TIMEOUT_S = 10.0


class GatewayUnreachable(RuntimeError):
    """The ZEN API Gateway could not be reached, so nothing was sent to ZEN."""


def zen_api_installed() -> bool:
    """Whether ZEISS's ``zen_api`` package is installed here."""
    return find_spec("zen_api") is not None


def zen_api_version() -> str | None:
    """The installed ``zen_api`` package's version (``"2025.10.1"``), or None without it."""
    try:
        from importlib.metadata import version

        return version("zen_api")
    except Exception:  # noqa: BLE001 -- the version is for the record only
        return None


def zen_api_module(name: str):
    """The ``zen_api`` module called *name*, such as ``lm.hardware.v2``.

    Raises ``ImportError`` saying what to install when the package is missing.
    """
    try:
        return import_module(f"zen_api.{name}")
    except ImportError as why:
        raise ImportError(
            f"ZEISS's zen_api package is not installed, or has no module {name!r} "
            f"({why}); install the zen_api 2025.10.1 wheel from the OAD repository, "
            "as the driver's README says"
        ) from why


def real_message(path: str):
    """A new request message from its path, ``"lm.hardware.v2:StageServiceMoveToRequest"``."""
    module, _, name = path.partition(":")
    return getattr(zen_api_module(module), name)()


def real_stub(key: str, channel, metadata):
    """The service called *key*, bound to the connection and the control token."""
    module, name = SERVICES[key]
    return getattr(zen_api_module(module), name)(channel=channel, metadata=metadata)


def ssl_context(cert_file: str | Path | None) -> ssl.SSLContext:
    """The encryption settings the gateway requires.

    With *cert_file*, the gateway's certificate is checked against that root
    certificate, so the driver knows it talks to the gateway it means to.
    Without one the connection is still encrypted but not checked; that is
    only for the same computer, and the driver says so when it connects.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if cert_file is not None:
        context.load_verify_locations(cafile=str(cert_file))
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["h2"])
    return context


def real_channel(host: str, port: int, context: ssl.SSLContext):
    """The gRPC connection to the gateway; made on the loop thread that will use it."""
    from grpclib.client import Channel

    return Channel(host=host, port=port, ssl=context)


def find_certificate(cert_file: str | Path | None) -> Path | None:
    """The root certificate to check the gateway with: the one given, else the gateway's own.

    None when neither is there; the caller decides whether to go on unchecked.
    """
    candidates = [cert_file] if cert_file else list(CERTIFICATE_PATHS)
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    if cert_file:
        raise FileNotFoundError(
            f"the gateway's root certificate {cert_file} does not exist; it is in the "
            "gateway's Certificates folder on the ZEN computer"
        )
    return None


def read_config_ini(path: str | Path) -> dict:
    """The connection settings of a ZEISS-style ``config.ini`` (``[api]``: host, port, cert_file, control-token).

    This is the file ZEISS's own examples use, so an operator who already has
    one can hand it to the driver as it is. A relative ``cert_file`` is taken
    next to the ini file.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"the ZEN API config.ini {path} does not exist")
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if "api" not in parser:
        raise ValueError(f"{path} has no [api] section")
    api = parser["api"]
    cert = api.get("cert_file")
    if cert and not Path(cert).is_absolute():
        cert = str((path.parent / cert).resolve())
    return {
        "host": api.get("host", "127.0.0.1"),
        "port": int(api.get("port", str(DEFAULT_PORT))),
        "cert_file": cert or None,
        "token": api.get("control-token", api.get("control_token", "")) or None,
    }


_STREAM_END = object()


class ZenClient:
    """One open connection to ZEN, used from plain blocking Python.

    The client is made with the pieces it needs (so that the offline stand-in
    can hand in fakes): *channel_factory* makes the connection, on the loop
    thread; *stub_factory* makes a service from its key, the channel and the
    metadata; *message_factory* makes a request message from its path.
    *metadata* is the ``control-token`` header every call carries.

    ``reply`` is where the transfer keeps ZEN's answer to the last change,
    so the dispatcher can check it; ``runtime`` records what this session
    connected to, for ``get_info``.
    """

    def __init__(
        self,
        *,
        metadata: list[tuple[str, str]],
        channel_factory: Callable[[], Any],
        stub_factory: Callable[[str, Any, Any], Any],
        message_factory: Callable[[str], Any],
        call_timeout_s: float = CALL_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self._metadata = metadata
        self._stub_factory = stub_factory
        self._message_factory = message_factory
        self._call_timeout_s = call_timeout_s
        self._stubs: dict[str, Any] = {}
        self._closed = False
        self.reply: Any = None
        self.runtime: dict = {}
        # One event loop for the whole session, in its own thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="zen-api-loop", daemon=True
        )
        self._thread.start()
        # The connection is made on that loop, which is the one that will use it.
        self._channel = self._on_loop(channel_factory, timeout=connect_timeout_s)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _on_loop(self, fn: Callable[[], Any], *, timeout: float) -> Any:
        """Run a plain function on the loop thread and hand back what it returns."""
        done: concurrent.futures.Future = concurrent.futures.Future()

        def run():
            try:
                done.set_result(fn())
            except BaseException as why:  # noqa: BLE001 -- handed to the caller's thread
                done.set_exception(why)

        self._loop.call_soon_threadsafe(run)
        return done.result(timeout)

    # --- requests ---------------------------------------------------------------------

    def message(self, path: str):
        """A new, empty request message, from its path (``"lm.hardware.v2:FocusServiceMoveToRequest"``)."""
        return self._message_factory(path)

    def stub(self, key: str):
        """The service called *key* (``"stage"``, ``"focus"``, ...), made once and kept."""
        if key not in self._stubs:
            self._stubs[key] = self._stub_factory(key, self._channel, self._metadata)
        return self._stubs[key]

    def begin(
        self, service: str, method: str, message, *, timeout: float | None = None
    ):
        """Hand one call to the loop and come back at once; the future holds the answer.

        *timeout* is how long the call may take, in seconds; None takes the
        client's own, and 0 waits as long as it takes.
        """
        if self._closed:
            raise RuntimeError("the connection to ZEN is closed")
        coroutine = getattr(self.stub(service), method)(message)
        future = asyncio.run_coroutine_threadsafe(
            self._bounded(coroutine, timeout), self._loop
        )
        return future

    async def _bounded(self, coroutine, timeout: float | None):
        limit = self._call_timeout_s if timeout is None else timeout
        if not limit:
            return await coroutine
        return await asyncio.wait_for(coroutine, limit)

    def call(self, service: str, method: str, message, *, timeout: float | None = None):
        """Make one call and wait for ZEN's answer; raise what the call raised.

        A call that takes longer than *timeout* raises ``TimeoutError``, which
        the profile sorts as temporary. The answer is the response message,
        read by its fields.
        """
        future = self.begin(service, method, message, timeout=timeout)
        try:
            return future.result()
        except TimeoutError as why:
            raise TimeoutError(
                f"ZEN did not answer {service}.{method} within "
                f"{self._call_timeout_s if timeout is None else timeout} s"
            ) from why

    def stream(
        self, service: str, method: str, message, *, item_timeout_s: float | None = None
    ) -> Iterator:
        """Read a streamed answer (an experiment's status changes) item by item, blocking.

        The stream is read on the loop thread and handed over through a queue.
        Stopping early cancels it. An item that does not arrive within
        *item_timeout_s* raises ``TimeoutError``.
        """
        if self._closed:
            raise RuntimeError("the connection to ZEN is closed")
        items: queue.Queue = queue.Queue()
        task: dict[str, Any] = {}

        async def pump():
            try:
                async for item in getattr(self.stub(service), method)(message):
                    items.put(("item", item))
            except asyncio.CancelledError:
                pass
            except Exception as why:  # noqa: BLE001 -- handed to the reader's thread
                items.put(("error", why))
            finally:
                items.put(("done", _STREAM_END))

        def schedule():
            task["task"] = self._loop.create_task(pump())

        self._loop.call_soon_threadsafe(schedule)

        def cancel():
            running = task.get("task")
            if running is not None and not running.done():
                running.cancel()

        def generator():
            try:
                while True:
                    try:
                        kind, payload = items.get(timeout=item_timeout_s)
                    except queue.Empty as why:
                        raise TimeoutError(
                            f"no item within {item_timeout_s} s"
                        ) from why
                    if kind == "item":
                        yield payload
                    elif kind == "error":
                        raise payload
                    else:
                        return
            finally:
                self._loop.call_soon_threadsafe(cancel)

        return generator()

    # --- closing ----------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Close the connection and stop the loop thread. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._channel is not None:
                closing = self._channel.close()
                if asyncio.iscoroutine(closing):
                    asyncio.run_coroutine_threadsafe(closing, self._loop).result(5.0)
        except Exception:
            log.debug("closing the channel failed", exc_info=True)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)


def open_gateway(
    host: str,
    port: int,
    *,
    token: str,
    cert_file: str | Path | None = None,
    call_timeout_s: float = CALL_TIMEOUT_S,
) -> ZenClient:
    """Open the connection to the ZEN API Gateway at *host*:*port* and hand back the client.

    *token* is the gateway's global control token. *cert_file* is the root
    certificate to check the gateway with; None looks for the gateway's own
    in its usual folder and, when it is not there, connects without the
    check, with a warning. Raises :class:`GatewayUnreachable` when the
    gateway does not answer, so the caller knows nothing was sent.
    """
    if not token:
        raise ValueError(
            "the gateway's control token is missing: give it as the connection's "
            f"password, or point zen_config_ini at a config.ini that holds it ({TOKEN_FILE} "
            "on the ZEN computer has it)"
        )
    certificate = find_certificate(cert_file)
    if certificate is None:
        log.warning(
            "no root certificate for the ZEN API Gateway was found; the connection to "
            "%s:%s is encrypted but the gateway is not checked. Give cert_file to check it.",
            host,
            port,
        )
    context = ssl_context(certificate)
    try:
        client = ZenClient(
            metadata=[("control-token", token)],
            channel_factory=lambda: real_channel(host, port, context),
            stub_factory=real_stub,
            message_factory=real_message,
            call_timeout_s=call_timeout_s,
        )
    except ImportError:
        raise
    except Exception as why:
        raise GatewayUnreachable(
            f"the ZEN API Gateway at {host}:{port} could not be reached: {why}"
        ) from why
    client.runtime = {
        "host": host,
        "port": port,
        "certificate": str(certificate) if certificate else None,
        "zen_api_version": zen_api_version(),
        "services": {key: ".".join(where) for key, where in SERVICES.items()},
    }
    return client
