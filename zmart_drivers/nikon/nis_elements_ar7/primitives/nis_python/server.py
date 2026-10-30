"""The bridge: a small program that runs inside NIS-Elements and does what the driver asks.

NIS-Elements brings its own Python, and inside that Python, Nikon's ``nis.mac``
offers NIS's macro functions by name: reading the stage, moving it, selecting
an optical configuration, capturing a picture, saving it. This file is started
by a macro (``start_bridge.mac``, written by ``driver_setup/nis_bridge``). It
then listens on this computer only (``127.0.0.1``) for requests from the
driver, which runs outside NIS, and answers each one. A request is one line of
JSON, and so is its reply::

    {"id": 7, "key": "...", "op": "read", "fn": "StgGetPosZ", "args": [{"ptr": "double"}, 0]}
    {"id": 7, "ok": true, "answer": {"return": 1, "values": [250.0, 0]}, "nis_ms": 0.02}

``{"ptr": "double"}`` is an argument NIS fills in, such as a position. The
reply hands back NIS's return value and every argument after the call, so the
value NIS wrote reaches the driver.

The rules that keep this safe, learned on NIS 6.10 and in steps 0 to 2 of the
NIS 7 study on NIS-Elements AR 7.01.03 (25 and 26 October 2026):

- **Every NIS function runs on NIS's own thread.** A capture from another
  thread crashed NIS 6.10. So the socket threads only queue the requests, and
  the macro's loop runs them one at a time by calling :func:`pump`.
- **Only named functions can be called.** Readings come from :data:`READS`,
  changes from :data:`WRITES`, and the capture is one operation of its own
  (:meth:`Bridge.snap`). Any other name is refused.
- **Every request carries the key** this bridge wrote when it started, so only
  a program that can read this user's settings folder can drive the microscope.
  A first line that is not JSON, such as a web page's request, ends the
  connection.
- **The stage never leaves NIS's own travel**: a move outside the limits NIS
  reports is refused here, whatever the driver asked.
- **Every failure comes back as a reply**, never as a dialog or a crash.

This file imports only the standard library and NIS's ``nis`` module, because
NIS's Python has nothing else, and it is loaded by its path. Importing it does
nothing by itself: everything starts in :func:`start`.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
It grew out of the step 1 bridge of thomdehoog/nis-useq-aiagent (branch
``_nis-7-step1``) and the NIS 6.10 bridge of ZMART Drivers.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import socketserver
import sys
import threading
import time
import traceback
from typing import Any

VERSION = "0.1.0"

HOST = "127.0.0.1"  # this computer only
#: Step 0 used 54470 and step 1 54471; the driver has a port of its own.
PORT = 54472

#: A request that NIS's thread has not taken by then never ran, and is
#: answered ``queued_timeout``: the macro loop is busy or stopped.
REQUEST_TIMEOUT_S = 30.0
#: A request NIS is running may take this long: a capture with a long
#: exposure. After that its outcome is unknown (``running_timeout``).
JOB_TIMEOUT_S = 600.0
#: How long one pass of the macro loop waits for a request before handing NIS
#: its thread back. Step 1 measured 17 passes a second with 50 ms; 20 ms keeps
#: NIS's own window more responsive at the same round trip.
PUMP_WAIT_S = 0.02
#: The most requests one pass runs, so a burst cannot hold NIS's thread.
PUMP_MAX_JOBS = 50
#: A bridge whose loop has not pumped for this long answers at once that it
#: is not running, instead of letting the driver wait (NIS's Stop button).
NOT_PUMPING_S = 2.0
#: How many of NIS's dialogs the bridge remembers, newest last.
DIALOGS_KEPT = 20

#: The pointer types ``nis.ptr`` offers, for arguments NIS fills in.
PTR_TYPES = (
    "byte",
    "char",
    "char8",
    "double",
    "dword",
    "int",
    "int64",
    "long",
    "short",
    "word",
)


def _sibling(name: str) -> Any:
    """A file beside this one, loaded by its path: inside NIS this file is not part of a package."""
    import importlib.util

    # Always loaded afresh: NIS's Python lives on between runs of the macro,
    # and an updated table must never meet an old copy of itself.
    module_name = f"zmart_nis_bridge_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = (
        module  # a dataclass looks its module up while it is made
    )
    spec.loader.exec_module(module)
    return module


#: The table of hardware settings (``devices.py``), whose functions the bridge also allows.
devices = _sibling("devices")

#: The NIS functions the driver may read through, and nothing else.
READS = (
    frozenset(
        {
            "Get_InfoStr",
            "StgGetPosXY",
            "StgGetPosZ",
            "StgXY_GetLimits",
            "StgZ_GetLimits",
            "GetOptConfCount",
            "GetOptConfName",
            "GetSelectedOptConf",
            "Stg_IsNosepiecePresent",
            "Stg_GetNosepiecePositions",
            "Stg_GetNosepiecePosition",
            "Stg_GetNosepieceObjectiveName",
            "Stg_IsPFSPresent",
            "Stg_GetPFSStatus",
            "Get_ImageInfo",
            "Get_Calibration",
            "GetOpenedDocumentList",
        }
    )
    | devices.READ_FUNCTIONS
)
#: The NIS functions that change something, sent only as a ``write``: the
#: stage, the objective, the optical configuration, and each row of the table.
WRITES = (
    frozenset({"StgMoveXY", "StgMoveZ", "SelectOptConf", "Stg_SetNosepiecePosition"})
    | devices.WRITE_FUNCTIONS
)
#: The moves, checked against NIS's own travel before they are called.
MOVES = {"StgMoveXY", "StgMoveZ"}

#: What a refusal is, as the reply names it; the driver sorts it by this alone.
KIND_QUEUED_TIMEOUT = "queued_timeout"  # never ran: sending again is safe
KIND_RUNNING_TIMEOUT = "running_timeout"  # outcome unknown: never sent again
KIND_NOT_ALLOWED = "not_allowed"
KIND_BAD_REQUEST = "bad_request"
KIND_NOT_AUTHORISED = "not_authorised"
KIND_CLOSED = "closed"
KIND_NOT_PUMPING = "not_pumping"
KIND_REFUSED = "refused"  # NIS itself, or the bridge's own check, said no

#: How ``GetOpenedDocumentList`` is asked only for the number of open documents.
#: Given room for no names (0 items, each of up to 256 characters), NIS writes
#: nothing and answers how many documents are open. That is all the snap needs,
#: and it never asks NIS to write more names than its text place can hold.
DOCUMENT_COUNT_ARGS = [{"ptr": "char"}, 0, 256]
#: The parts of NIS's description of a picture, in the order
#: ``LoadImageFullDescription`` fills them in (after the file's name).
DESCRIPTION_PARTS = (
    "Optics",
    "Type",
    "Sample ID",
    "Author",
    "Description",
    "Image ID",
    "Group",
    "Capturing",
    "Sampling",
    "Location",
    "Date",
    "Conclusion",
    "Info 1",
    "Info 2",
)
#: ``ImageSaveAs``'s format number for a TIFF with every layer, and no compression.
TIFF_FORMAT = 18
NO_COMPRESSION = 0
#: The file beside a staged TIFF that holds NIS's whole description of the picture.
DESCRIPTION_SUFFIX = ".nis_description.txt"
#: ``CloseCurrentDocument``'s answer to "save changes?": no, and do not ask.
CLOSE_WITHOUT_SAVING = 2
TEXT_SIZE = 256

log = logging.getLogger("zmart.nikon.bridge")
log.propagate = False


class Refused(RuntimeError):
    """A refusal by the bridge's own checks, with the kind the reply carries."""

    def __init__(self, message: str, kind: str = KIND_REFUSED) -> None:
        super().__init__(message)
        self.kind = kind


# =============================================================================
# Calling NIS
# =============================================================================


def make_arg(nis_module: Any, arg: Any) -> Any:
    """One argument as NIS takes it: ``{"ptr": "double"}`` becomes a ``nis.ptr.double()``; others as they are."""
    if isinstance(arg, dict):
        kind = arg.get("ptr")
        if kind not in PTR_TYPES:
            raise ValueError(f"unknown ptr type {kind!r}; one of {list(PTR_TYPES)}")
        return getattr(nis_module.ptr, kind)()
    return arg


def call(nis_module: Any, fn: str, args: list) -> dict:
    """Call NIS's function *fn*: ``{"return": ..., "values": [the arguments after the call]}``."""
    if nis_module is None:
        raise RuntimeError(
            "the nis module is missing: this is not NIS-Elements' own Python"
        )
    values = [make_arg(nis_module, a) for a in args]
    result = getattr(nis_module.mac, fn)(*values)
    return {
        "return": result,
        "values": [v.get() if hasattr(v, "get") else v for v in values],
    }


# =============================================================================
# The bridge
# =============================================================================


class Bridge:
    """One running bridge: its key, its queue of requests, and what it has seen."""

    def __init__(self, nis_module: Any, key: str) -> None:
        self.nis = nis_module
        self.key = key
        self.jobs: queue.Queue = queue.Queue()
        self.server: Server | None = None
        self.started_at = time.monotonic()
        self.last_pump = time.monotonic()
        self.pumps = 0
        self.stop_requested = False
        self.closed = False
        self.dialogs: list[dict] = []
        self.counts = {"requests": 0, "ok": 0, "errors": 0, "timeouts": 0}

    # -- what a request does, on NIS's thread ----------------------------------------------

    def run(self, request: dict) -> Any:
        op = request.get("op")
        fn = request.get("fn")
        args = request.get("args", [])
        if not isinstance(args, list):
            raise Refused("'args' is a list", KIND_BAD_REQUEST)
        if op == "read":
            if fn not in READS:
                raise Refused(
                    f"{fn} is not a reading the bridge allows", KIND_NOT_ALLOWED
                )
            return call(self.nis, fn, args)
        if op == "write":
            if fn not in WRITES:
                raise Refused(
                    f"{fn} is not a change the bridge allows", KIND_NOT_ALLOWED
                )
            if fn in MOVES:
                self.check_travel(fn, args)
            return call(self.nis, fn, args)
        if op == "snap":
            if len(args) != 1 or not isinstance(args[0], str):
                raise Refused(
                    "a snap takes one argument: the path of the TIFF to save",
                    KIND_BAD_REQUEST,
                )
            return self.snap(args[0])
        raise Refused(
            f"unknown op {op!r}; one of ping, read, write, snap, shutdown",
            KIND_BAD_REQUEST,
        )

    def check_travel(self, fn: str, args: list) -> None:
        """Refuse a move outside the travel NIS itself reports, whatever the driver asked."""
        ptr = {"ptr": "double"}
        if fn == "StgMoveXY":
            limits = call(self.nis, "StgXY_GetLimits", [ptr] * 4)
            if limits["return"] != 1:
                raise Refused(
                    f"StgMoveXY refused: NIS's XY limits could not be read ({limits['return']})"
                )
            x0, y0, x1, y1 = (float(v) for v in limits["values"][:4])
            x, y = float(args[0]), float(args[1])
            if not (
                min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1)
            ):
                raise Refused(
                    f"StgMoveXY refused: ({x:g}, {y:g}) um is outside NIS's own travel x {x0:g}..{x1:g}, y {y0:g}..{y1:g}"
                )
        else:
            limits = call(self.nis, "StgZ_GetLimits", [ptr] * 2)
            if limits["return"] != 1:
                raise Refused(
                    f"StgMoveZ refused: NIS's z limits could not be read ({limits['return']})"
                )
            z0, z1 = (float(v) for v in limits["values"][:2])
            z = float(args[0])
            if not min(z0, z1) <= z <= max(z0, z1):
                raise Refused(
                    f"StgMoveZ refused: z {z:g} um is outside NIS's own travel {z0:g}..{z1:g}"
                )

    def snap(self, path: str) -> dict:
        """Take one picture into a new document, save it as a TIFF at *path*, and close that document only.

        All of it runs in one pass of the macro loop, so nothing the operator
        clicks can come in between. The operator's own documents are never
        saved or closed: when ``Capture`` makes no new document, nothing is
        saved and nothing is closed. NIS makes the new document the current
        one, so the saving and the closing act on it alone.
        """
        if not os.path.isabs(path) or not path.lower().endswith((".tif", ".tiff")):
            raise Refused(
                f"a snap saves only to a full path ending in .tif, not {path!r}",
                KIND_BAD_REQUEST,
            )
        if os.path.exists(path):
            raise Refused(
                f"{path} exists already; a capture is never saved over another"
            )
        before = self.document_count()
        started = time.time()
        captured = call(self.nis, "Capture", [])["return"]
        finished = time.time()
        if (
            isinstance(captured, int)
            and not isinstance(captured, bool)
            and captured < 0
        ):
            raise Refused(
                f"Capture answered {captured}: no picture was taken (is the camera on?); nothing was saved"
            )
        after = self.document_count()
        if after != before + 1:
            raise Refused(
                f"Capture left {after} documents open where {before} were, not one more: NIS may have "
                "captured into an existing document (switch on 'capture creates a new document' in NIS). "
                "Nothing was saved or closed."
            )
        info = call(self.nis, "Get_ImageInfo", ["", *([{"ptr": "int"}] * 4)])
        width, height, bits, components = (int(v) for v in info["values"][1:5])
        calibration = call(
            self.nis,
            "Get_Calibration",
            [{"ptr": "char"}, {"ptr": "double"}, {"ptr": "double"}, {"ptr": "int"}],
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        call(self.nis, "ImageSaveAs", [path, TIFF_FORMAT, NO_COMPRESSION])
        saved = os.path.exists(path)
        description = self.save_description(path) if saved else None
        call(self.nis, "CloseCurrentDocument", [CLOSE_WITHOUT_SAVING])
        if not saved:
            raise Refused(
                f"ImageSaveAs returned, but no file appeared at {path}; the new document was closed"
            )
        objective, pixel, aspect, unit = calibration["values"][:4]
        return {
            "path": path,
            "bytes": os.path.getsize(path),
            "documents_open": {"before": before, "after": after},
            "capture_return": captured,
            "started": started,
            "finished": finished,
            "image": {
                "width": width,
                "height": height,
                "bits": bits,
                "components": components,
            },
            "description": description,
            "calibration": {
                "objective": objective,
                "pixel": pixel,
                "aspect": aspect,
                "unit": unit,
            },
        }

    def save_description(self, path: str) -> dict:
        """Write NIS's own description of the saved picture (optics, how it was captured...) beside the TIFF.

        A TIFF keeps little of what NIS knows about how a picture was taken.
        NIS's ``LoadImageFullDescription`` reads back the description NIS
        keeps with the picture, in fourteen parts; the parts that say
        something are written, one per line, to
        ``<picture>.nis_description.txt``, which goes into the store as the
        vendor's own description. When NIS gives none, the picture is still
        saved, and the answer says why.
        """
        target = os.path.splitext(path)[0] + DESCRIPTION_SUFFIX
        try:
            answer = call(
                self.nis,
                "LoadImageFullDescription",
                [path, *([{"ptr": "char"}] * len(DESCRIPTION_PARTS))],
            )
        except Exception as error:  # noqa: BLE001 -- the picture matters more than its description
            return {"path": None, "why": f"{type(error).__name__}: {error}"}
        returned = answer["return"]
        lines = [
            f"{part}: {str(value).strip()}"
            for part, value in zip(DESCRIPTION_PARTS, answer["values"][1:])
            if str(value or "").strip()
        ]
        if (isinstance(returned, int) and returned < 1) or not lines:
            return {
                "path": None,
                "why": f"LoadImageFullDescription answered {returned!r} with no description",
            }
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return {"path": target, "why": None}

    def document_count(self) -> int:
        """How many documents are open in NIS now."""
        return int(
            call(self.nis, "GetOpenedDocumentList", DOCUMENT_COUNT_ARGS)["return"]
        )

    def run_job(self, job: Job) -> None:
        """Run one queued request and fill in its reply. Never raises."""
        with job.lock:
            if job.cancelled:
                return
            job.started = True
        name = job.request.get("fn") or job.request.get("op")
        log.info(
            "RUN   %s", name
        )  # before the call: if NIS hangs, this is the last line
        started = time.perf_counter()
        try:
            job.reply = {"ok": True, "answer": self.run(job.request)}
            self.counts["ok"] += 1
        except BaseException as error:  # noqa: BLE001 -- every failure goes back as a reply
            kind = error.kind if isinstance(error, Refused) else kind_of(error)
            job.reply = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "kind": kind,
            }
            self.counts["errors"] += 1
        job.reply["nis_ms"] = round(1000 * (time.perf_counter() - started), 3)
        log.info("DONE  %s: %s", name, "ok" if job.reply["ok"] else job.reply["error"])
        job.done.set()

    # -- the socket side ------------------------------------------------------------------

    def ping(self) -> dict:
        """Answered on the socket's own thread, so it works even while NIS is busy."""
        return {
            "bridge": VERSION,
            "pid": os.getpid(),
            "uptime_s": round(time.monotonic() - self.started_at, 2),
            "pumps": self.pumps,
            "since_last_pump_s": round(time.monotonic() - self.last_pump, 3),
            "nis": self.nis is not None,
            "dialogs": list(self.dialogs),
        }

    def answer(self, request: dict) -> dict:
        """The reply to one request, after NIS's thread has run it (or why it did not)."""
        self.counts["requests"] += 1
        request_id = request.get("id")

        def refused(error: str, kind: str) -> dict:
            self.counts["errors"] += 1
            return {"id": request_id, "ok": False, "error": error, "kind": kind}

        if not secrets.compare_digest(str(request.get("key", "")), self.key):
            return refused(
                "the key does not match this bridge's: the bridge was restarted, or this is not the driver",
                KIND_NOT_AUTHORISED,
            )
        op = request.get("op")
        if op == "ping":
            return {"id": request_id, "ok": True, "answer": self.ping()}
        if op == "shutdown":
            self.stop_requested = True
            return {"id": request_id, "ok": True, "answer": {"stopping": True}}
        if self.closed:
            return refused(
                "the bridge is closed; start start_bridge.mac again in NIS", KIND_CLOSED
            )
        if time.monotonic() - self.last_pump > NOT_PUMPING_S:
            return refused(
                "the bridge's macro loop is not running (stopped with NIS's Stop button?); start start_bridge.mac again",
                KIND_NOT_PUMPING,
            )
        job = Job(request)
        self.jobs.put(job)
        if not job.done.wait(REQUEST_TIMEOUT_S):
            with job.lock:
                if not job.started:
                    job.cancelled = True
                    self.counts["timeouts"] += 1
                    return refused(
                        f"NIS did not take the request within {REQUEST_TIMEOUT_S:g} s; it never ran",
                        KIND_QUEUED_TIMEOUT,
                    )
            if not job.done.wait(JOB_TIMEOUT_S):
                self.counts["timeouts"] += 1
                return refused(
                    f"NIS has been running the request for {JOB_TIMEOUT_S:g} s; whether it worked is unknown",
                    KIND_RUNNING_TIMEOUT,
                )
        return {"id": request_id, **job.reply}

    # -- the macro loop's side --------------------------------------------------------------

    def pump(self, wait_s: float = PUMP_WAIT_S, max_jobs: int = PUMP_MAX_JOBS) -> int:
        """Run the requests that came in, on the calling thread; how many ran. Never raises."""
        ran = 0
        try:
            self.pumps += 1
            self.last_pump = time.monotonic()
            while ran < max_jobs:
                try:
                    job = (
                        self.jobs.get(timeout=wait_s)
                        if ran == 0 and wait_s > 0
                        else self.jobs.get_nowait()
                    )
                except queue.Empty:
                    break
                self.run_job(job)
                ran += 1
            self.last_pump = time.monotonic()
        except BaseException:  # noqa: BLE001 -- an error here would reach NIS at every pass
            log.error("pump error: %s", traceback.format_exc())
        return ran

    def close(self) -> None:
        """Close the server; a request still queued is answered ``closed``. A second call does nothing."""
        if self.closed:
            return
        self.closed = True
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            with job.lock:
                job.cancelled = True
            job.reply = {
                "ok": False,
                "error": "the bridge closed before NIS ran the request",
                "kind": KIND_CLOSED,
            }
            job.done.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def note_dialog(self, title: str, texts: list[str]) -> None:
        entry = {"t": time.time(), "title": title, "texts": texts[:20]}
        self.dialogs.append(entry)
        del self.dialogs[:-DIALOGS_KEPT]
        log.info("DIALOG %s: %s", title, " | ".join(texts)[:400])


class Job:
    """One queued request, and the reply NIS's thread fills in."""

    def __init__(self, request: dict) -> None:
        self.request = request
        self.lock = threading.Lock()  # decides between "started" and "cancelled"
        self.started = False
        self.cancelled = False
        self.done = threading.Event()
        self.reply: dict = {}


def kind_of(error: BaseException) -> str:
    """Which kind of refusal an error from NIS is: a wrong request, or NIS saying no."""
    if isinstance(error, (ValueError, TypeError, AttributeError)):
        return KIND_BAD_REQUEST
    return KIND_REFUSED


class Handler(socketserver.StreamRequestHandler):
    """One connection: a request line in, a reply line out, until the client leaves or is refused."""

    def handle(self) -> None:
        bridge: Bridge = self.server.bridge  # type: ignore[attr-defined]
        log.info("connection from %s:%s", *self.client_address)
        while True:
            try:
                line = self.rfile.readline()
            except OSError:
                break
            if not line:
                break
            try:
                request = json.loads(line.decode("utf-8", "replace"))
                if not isinstance(request, dict):
                    raise ValueError("a request is a JSON object")  # noqa: TRY004 -- caught just below
            except ValueError:
                # Not the driver (perhaps a web page): say nothing more, and hang up.
                log.warning("a line that is not a request; the connection is closed")
                break
            reply = bridge.answer(request)
            try:
                self.wfile.write(
                    (json.dumps(reply, default=repr) + "\n").encode("utf-8")
                )
            except OSError:
                log.info("the client left before its reply")
                break
            if reply.get("kind") == KIND_NOT_AUTHORISED:
                break
        log.info("connection closed")


class Server(socketserver.ThreadingTCPServer):
    """The socket server; each connection gets a thread, and NIS's thread runs the requests."""

    allow_reuse_address = False  # never two bridges on one port
    daemon_threads = True

    def __init__(self, address: tuple[str, int], bridge: Bridge) -> None:
        super().__init__(address, Handler)
        self.bridge = bridge


def serve(nis_module: Any, key: str, host: str = HOST, port: int = PORT) -> Bridge:
    """Open a bridge over *nis_module* and listen; the caller pumps it. Used by :func:`start` and the tests."""
    bridge = Bridge(nis_module, key)
    bridge.server = Server((host, port), bridge)
    threading.Thread(
        target=bridge.server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="zmart-nis-bridge",
        daemon=True,
    ).start()
    log.info("bridge %s listening on %s:%s", VERSION, host, port)
    return bridge


# =============================================================================
# NIS's dialogs: noted, never clicked (Windows only)
# =============================================================================


def watch_dialogs(bridge: Bridge) -> None:
    """Every 0.3 s, note each new dialog window (class #32770) of this process. Never clicks.

    Ported from the step 1 bridge. A modal NIS dialog holds NIS's thread, so
    a request hangs while it is open; ``ping`` names it, so the driver can say why.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    seen: set = set()

    def text(hwnd, getter, size=1024):
        buffer = ctypes.create_unicode_buffer(size)
        getter(hwnd, buffer, size)
        return buffer.value

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (
            pid.value == os.getpid()
            and user32.IsWindowVisible(hwnd)
            and hwnd not in seen
            and text(hwnd, user32.GetClassNameW, 256) == "#32770"
        ):
            seen.add(hwnd)
            parts: list[str] = []
            child = proc(
                lambda h, _: parts.append(text(h, user32.GetWindowTextW)) or True
            )
            user32.EnumChildWindows(hwnd, child, 0)
            bridge.note_dialog(
                text(hwnd, user32.GetWindowTextW), [p for p in parts if p]
            )
        return True

    callback = proc(visit)
    while not bridge.closed:
        try:
            user32.EnumWindows(callback, 0)
        except Exception as error:  # noqa: BLE001
            log.warning("the dialog watcher stopped: %s", error)
            return
        time.sleep(0.3)


# =============================================================================
# What the macro runs: start, pump (in its loop), stop
# =============================================================================


def _registry() -> dict:
    """Where the running bridge is kept, so a second run of the macro (a reloaded module) still finds it."""
    return sys.modules["__main__"].__dict__.setdefault("_zmart_nis_bridge", {})


def _touch(path: str | None) -> None:
    if path:
        try:
            with open(path, "w"):
                pass
        except OSError as error:
            log.error("could not write the stop file %s: %s", path, error)


def start(
    stop_file: str,
    key_file: str,
    port: int = PORT,
    host: str = HOST,
    log_path: str | None = None,
    nis_module: Any = None,
) -> str:
    """Open the bridge inside NIS (the macro's first line); a short status line for NIS's log.

    Removes a stop file left from before, closes a bridge left by an earlier
    run of the macro, and writes a fresh key to *key_file* for the driver.
    When it cannot listen, it writes the stop file, so the macro ends.
    """
    registry = _registry()
    registry["stop_file"] = stop_file
    if os.path.exists(stop_file):
        os.remove(stop_file)
    old = registry.pop("bridge", None)
    if old is not None:
        old.close()

    if log_path is None:
        log_path = os.path.join(os.environ.get("TEMP", "."), "zmart-nis-bridge.log")
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    if nis_module is None:
        try:
            import nis as nis_module  # exists only inside NIS-Elements
        except ImportError:
            nis_module = None
    key = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "w", encoding="ascii") as handle:
            handle.write(key)
        bridge = serve(nis_module, key, host, port)
    except OSError as error:
        log.error("could not start: %s", error)
        _touch(stop_file)
        return f"ZMART bridge could not start on port {port}: {error}. Stop the macro of an earlier run, or restart NIS-Elements."
    registry["bridge"] = bridge
    if sys.platform == "win32":
        threading.Thread(
            target=watch_dialogs, args=(bridge,), name="zmart-nis-dialogs", daemon=True
        ).start()
    log.info(
        "start: bridge %s, Python %s, pid %s, nis module %s",
        VERSION,
        sys.version.split()[0],
        os.getpid(),
        nis_module is not None,
    )
    return f"ZMART bridge {VERSION} listening on {host}:{port}; log: {log_path}"


def pump() -> None:
    """One pass of the macro loop: run what came in; write the stop file when the loop should end. Never raises."""
    registry = _registry()
    bridge = registry.get("bridge")
    if bridge is None or bridge.stop_requested:
        _touch(registry.get("stop_file"))
        return
    bridge.pump()


def stop() -> str:
    """Close the bridge (the macro's last line, after its loop ended); remove the stop file."""
    registry = _registry()
    bridge = registry.pop("bridge", None)
    stop_file = registry.get("stop_file")
    if stop_file and os.path.exists(stop_file):
        os.remove(stop_file)
    if bridge is None:
        return "ZMART bridge was not running"
    bridge.close()
    log.info("stopped: %s", bridge.counts)
    return f"ZMART bridge stopped after {bridge.counts['requests']} requests"
