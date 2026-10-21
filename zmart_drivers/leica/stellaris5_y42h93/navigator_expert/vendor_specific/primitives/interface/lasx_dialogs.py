"""LAS X's warning dialogs: noticed in its log, answered with OK when OK is all they ask.

When LAS X shows a warning, it waits for the answer: until someone clicks
the dialog, every command waits. On 2026-10-13 one such warning ("Please
keep minimum distance of 8nm") stayed open for 33 hours, and every command,
through the CAM API or directly, waited with it. No command answers a
dialog; only its button does.

LAS X writes each dialog to ``LAS X.log`` the moment it opens
(``MessageBox Warning : <text>``) and its answer when it closes
(``MessageBox Result: OK``), so the log says cheaply whether one is open and
what it says. Only then are LAS X's windows looked at, through Windows UI
Automation: the dialog is a second top-level window of LAS X's process
holding ``MessageBoxContent``, its buttons named by their automation ids
(simulator, 2026-10-14: ``ButtonMessageOk`` alone).

The driver answers a warning whose only button is OK, and writes its text to
the log. It answers nothing else: a question, a dialog with any other
button, or two dialogs at once stay open for the operator, and the command
that met them is refused with the dialog's text.
"""

from __future__ import annotations

import contextlib
import logging
import ctypes
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from ...connection import tuning


log = logging.getLogger(__name__)

__all__ = [
    "Answered",
    "answered_since",
    "Dialog",
    "DialogOpen",
    "WindowsUnavailable",
    "answer_warnings",
    "apartment",
    "forget",
    "import_in_this_threads_mode",
    "initialise_com",
    "keep_watching",
    "stop_watching",
    "lasx_windows",
    "pending",
    "watching",
]

#: Where LAS X's window writes what it shows.
LASX_LOG = Path(r"C:\ProgramData\Leica Microsystems\LAS X\LAS X.log")

#: How long LAS X may take to record the answer after OK is pressed: one window, by the one rule.
ANSWER_WAIT_S = tuning.WINDOW_S

#: How often the watcher of a waiting send looks at the log.
WATCH_INTERVAL_S = tuning.POLL_S

#: The one button the driver presses, and the caption of the dialogs it presses it on.
OK_BUTTON = "ButtonMessageOk"
WARNING = "Warning"

#: COM's answer when a thread's COM is set up already, in another mode: usable as it is.
RPC_E_CHANGED_MODE = -2147417850

#: A thread's COM modes, as Windows names them (``CoGetApartmentType``) and as COM is asked for them.
APTTYPE_MTA = 1
COINIT_MULTITHREADED = 0x0
COINIT_APARTMENTTHREADED = 0x2

#: The start of a dialog's entry in the log: its time, and what follows ``'MessageBox ``.
SHOWN = re.compile(r"^(\S+ \S+) .*?'MessageBox (?!Result: )(.*)$")
ANSWER = re.compile(r"'MessageBox Result: ")
#: Its caption: ``Warning : <text>`` (a text from the hardware server), ``Warning: <text>`` (a
#: form LAS X writes too) or ``Warning! <text>`` (Navigator Expert's).
CAPTION = re.compile(r"^(\w+)(?:!\s*|\s?:\s)(.*)$", re.DOTALL)


def dialog_of(at: str, body: str) -> Dialog:
    """A dialog from its log entry: the caption, and the text with its lines joined."""
    found = CAPTION.match(body.strip())
    caption, text = (found[1], found[2]) if found else ("", body)
    return Dialog(caption=caption, text=" ".join(text.split()), at=at)


def read_entries(text: str) -> tuple[list[tuple[str, Dialog | None]], int]:
    """The dialogs shown and answered in *text*, in order, and how many characters of it they used up.

    Each is ``("shown", Dialog)`` or ``("answered", None)``. An entry spans
    lines until its closing quote; one whose end is not written yet is left,
    with what follows, for the next look.
    """
    entries, used, i = [], 0, 0
    lines = text.splitlines(keepends=True)
    while i < len(lines) and lines[i].endswith("\n"):
        shown = SHOWN.match(lines[i].rstrip("\r\n"))
        j = i
        if shown:
            body = shown[2]
            while not body.rstrip().endswith("'"):
                j += 1
                if j >= len(lines) or not lines[j].endswith("\n"):
                    return entries, used
                body += "\n" + lines[j].rstrip("\r\n")
            entries.append(("shown", dialog_of(shown[1], body.rstrip()[:-1])))
        elif ANSWER.search(lines[i]):
            entries.append(("answered", None))
        used += sum(len(line) for line in lines[i : j + 1])
        i = j + 1
    return entries, used


@dataclass(frozen=True)
class Dialog:
    """A dialog LAS X's window has shown and not yet had answered, as its log has it."""

    caption: str
    text: str
    at: str


@dataclass(frozen=True)
class Answered:
    """A warning the driver answered with OK."""

    dialog: Dialog

    @property
    def message(self) -> str:
        return f"LAS X warning answered with OK: {self.dialog.text!r} (shown {self.dialog.at})"


class DialogOpen(RuntimeError):
    """A LAS X dialog is open that the driver does not answer; the operator has to."""

    def __init__(self, dialog: Dialog, why: str):
        super().__init__(
            f"LAS X dialog open, left for the operator ({why}): "
            f"{dialog.caption} {dialog.text!r} (shown {dialog.at})"
        )
        self.dialog = dialog


class WindowsUnavailable(RuntimeError):
    """LAS X's windows cannot be looked at here (no Windows UI Automation, no LAS X window)."""


class LogState:
    """How far ``LAS X.log`` has been read, and the dialog open at that point."""

    def __init__(self):
        self.size = 0
        self.open: Dialog | None = None
        self.lock = threading.Lock()


state = LogState()

#: One answer at a time: the delivery's watcher and a waiting direct send may both look.
answering = threading.Lock()

#: What the driver answered, with when (``time.monotonic()``), for the results of the commands it met.
history: list[tuple[float, Answered]] = []


def answered_since(moment: float) -> list[Answered]:
    """The warnings answered since *moment* (``time.monotonic()``)."""
    return [answered for at, answered in list(history) if at >= moment]


def forget():
    """Read the log from its start at the next look (a new session, or the tests)."""
    with state.lock:
        state.size, state.open = 0, None
    history.clear()
    reported.clear()


def pending() -> Dialog | None:
    """The dialog LAS X's window shows now, by its log; None when none is open.

    Reads only what the log gained since the last look; a log smaller than
    before was moved aside by LAS X and is read from its start.
    """
    with state.lock:
        try:
            size = LASX_LOG.stat().st_size
        except OSError:
            state.size, state.open = 0, None
            return None
        if size < state.size:
            state.size, state.open = 0, None
        if size > state.size:
            with LASX_LOG.open("rb") as f:
                f.seek(state.size)
                gained = f.read(size - state.size)
            text = gained.decode("latin-1")
            entries, used = read_entries(text)
            state.size += len(text[:used].encode("latin-1"))
            for kind, dialog in entries:
                state.open = dialog if kind == "shown" else None
        return state.open


def answer_warnings() -> Answered | None:
    """Answer an open warning whose only button is OK; refuse when another dialog is open.

    Returns what was answered, or None when no dialog is open. Raises
    :class:`DialogOpen` for a dialog the driver leaves to the operator.
    """
    if pending() is None:
        return None
    with answering:
        return answer_open_warning()


#: The dialogs already logged as left open, so each is logged once however often it is met.
reported: set[tuple[str, str]] = set()


def left_open(dialog: Dialog, why: str) -> DialogOpen:
    """The :class:`DialogOpen` for *dialog*, logged the first time it is met."""
    if (dialog.at, dialog.text) not in reported:
        reported.add((dialog.at, dialog.text))
        log.error(
            "LAS X dialog open, not answered (%s): %s %r",
            why,
            dialog.caption,
            dialog.text,
        )
    return DialogOpen(dialog, why)


def answer_open_warning() -> Answered | None:
    """:func:`answer_warnings`, one caller at a time."""
    dialog = pending()
    if dialog is None:
        return None
    deadline = time.monotonic() + ANSWER_WAIT_S
    while True:
        try:
            windows = lasx_windows().dialogs()
        except WindowsUnavailable as why:
            raise left_open(dialog, str(why)) from why
        if windows:
            break
        if pending() is None:
            return None
        if time.monotonic() >= deadline:
            raise left_open(dialog, "in LAS X's log, not found on screen")
        time.sleep(tuning.ANSWER_POLL_S)
    if (
        dialog.caption != WARNING
        or len(windows) != 1
        or windows[0].buttons != (OK_BUTTON,)
    ):
        buttons = [w.buttons for w in windows]
        raise left_open(
            dialog, f"not a warning with OK alone: {dialog.caption}, buttons {buttons}"
        )
    windows[0].press(OK_BUTTON)
    deadline = time.monotonic() + ANSWER_WAIT_S
    while pending() is not None:
        if time.monotonic() >= deadline:
            raise left_open(dialog, "OK pressed, but the dialog did not close")
        time.sleep(tuning.ANSWER_POLL_S)
    answered = Answered(dialog)
    history.append((time.monotonic(), answered))
    del history[:-100]
    log.warning(answered.message)
    return answered


@contextlib.contextmanager
def watching():
    """Answer OK-only warnings while the block runs; yields the list of what was answered.

    A command sent directly to the hardware server does not return while the
    dialog it caused is open, so the answer has to come from beside it.
    """
    notes: list[Answered] = []
    done = threading.Event()

    def watch():
        while not done.wait(WATCH_INTERVAL_S):
            try:
                answered = answer_warnings()
            except DialogOpen:
                continue
            if answered is not None:
                notes.append(answered)

    watcher = threading.Thread(target=watch, name="lasx-dialog-watcher", daemon=True)
    watcher.start()
    try:
        yield notes
    finally:
        done.set()
        watcher.join(timeout=tuning.WINDOW_S)


sentinel = {"thread": None, "stop": None}
sentinel_lock = threading.Lock()


def keep_watching() -> None:
    """Answer OK-only warnings from now on, whatever the driver does; started once.

    A warning LAS X shows while the driver only reads or waits (an acquire,
    a template run) would otherwise wait for the driver's next write. The
    look is the log's size, so watching costs next to nothing until a
    dialog is logged.
    """
    with sentinel_lock:
        if sentinel["thread"] is not None and sentinel["thread"].is_alive():
            return
        stop = threading.Event()

        def watch():
            while not stop.wait(WATCH_INTERVAL_S):
                try:
                    answer_warnings()
                except DialogOpen:
                    continue
                except Exception as why:  # noqa: BLE001 -- the watcher must outlive any one look
                    log.warning(
                        "the LAS X dialog watcher could not look: %s: %s",
                        type(why).__name__,
                        why,
                    )

        thread = threading.Thread(
            target=watch, name="lasx-dialog-sentinel", daemon=True
        )
        sentinel.update(thread=thread, stop=stop)
        thread.start()


def stop_watching() -> None:
    """Stop :func:`keep_watching`'s watcher (the driver disconnects, or the tests)."""
    with sentinel_lock:
        if sentinel["stop"] is not None:
            sentinel["stop"].set()
        thread = sentinel["thread"]
        sentinel.update(thread=None, stop=None)
    if thread is not None:
        thread.join(timeout=tuning.WINDOW_S)


def apartment() -> int | None:
    """This thread's COM mode (an ``APTTYPE``), or None when COM is not set up in it."""
    kind, qualifier = ctypes.c_int(), ctypes.c_int()
    try:
        ctypes.oledll.ole32.CoGetApartmentType(
            ctypes.byref(kind), ctypes.byref(qualifier)
        )
    except OSError:
        return None
    return kind.value


def import_in_this_threads_mode(import_comtypes) -> None:
    """Import comtypes, which sets up COM as it is imported, in the mode this thread has already.

    comtypes asks for the single-threaded mode unless ``sys.coinit_flags``
    says otherwise, and fails in a thread the CAM API's .NET connection set
    up in the multi-threaded one. The flag is set for the import only.
    """
    if "comtypes" in sys.modules or hasattr(sys, "coinit_flags"):
        import_comtypes()
        return
    mode = apartment()
    if mode is None:
        import_comtypes()
        return
    sys.coinit_flags = (
        COINIT_MULTITHREADED if mode == APTTYPE_MTA else COINIT_APARTMENTTHREADED
    )
    try:
        import_comtypes()
    finally:
        del sys.coinit_flags


def initialise_com(co_initialize) -> None:
    """Set up COM for this thread, or use it as it is when it is set up already.

    The driver's thread has COM set up by pywin32 and the CAM API, in a mode
    of their own; UI Automation works in either mode, so that answer is no
    failure (simulator, 2026-10-14).
    """
    try:
        co_initialize()
    except OSError as why:
        if getattr(why, "winerror", None) != RPC_E_CHANGED_MODE:
            raise


def automation():
    """Windows UI Automation for this thread: its type library and a client, ``(client, uia)``.

    Raises :class:`WindowsUnavailable` where UI Automation cannot be had.
    """
    try:
        import_in_this_threads_mode(lambda: __import__("comtypes.client"))
        import comtypes
        import comtypes.client

        initialise_com(comtypes.CoInitialize)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as client

        return client, quiet_automation(client, comtypes.client.CreateObject)
    except Exception as why:  # noqa: BLE001 -- no UI Automation, whatever the reason
        raise WindowsUnavailable(
            f"Windows UI Automation is not available: {why}"
        ) from why


def quiet_automation(client, create):
    """A UI Automation client whose presses leave the window in front where it is.

    By default UI Automation sets focus to an element before acting on it,
    and focus in LAS X's window brings LAS X to the front: at every Clear,
    the operator's window went behind it (simulator, 2026-10-15). Microsoft's
    newer client (``CUIAutomation8``, Windows 8 on) turns that off with
    ``AutoSetFocus``; the buttons act the same.
    """
    uia = create(client.CUIAutomation8, interface=client.IUIAutomation2)
    uia.AutoSetFocus = False
    return uia


class AutomatedDialog:
    """One of LAS X's dialog windows, through Windows UI Automation."""

    def __init__(self, uia, client, element):
        self.uia, self.client, self.element = uia, client, element
        found = element.FindAll(
            client.TreeScope_Descendants,
            uia.CreatePropertyCondition(
                client.UIA_ControlTypePropertyId, client.UIA_ButtonControlTypeId
            ),
        )
        self.button_elements = {
            found.GetElement(i).CurrentAutomationId: found.GetElement(i)
            for i in range(found.Length)
        }
        self.buttons = tuple(self.button_elements)

    def press(self, button):
        pattern = self.button_elements[button].GetCurrentPattern(
            self.client.UIA_InvokePatternId
        )
        pattern.QueryInterface(self.client.IUIAutomationInvokePattern).Invoke()


class AutomatedWindows:
    """LAS X's top-level windows, through Windows UI Automation (this thread's own)."""

    def __init__(self):
        self.client, self.uia = automation()

    def dialogs(self) -> list[AutomatedDialog]:
        """The dialogs open now: unnamed top-level windows of LAS X's and Navigator Expert's processes.

        Their main windows have names and are passed over: searching inside
        LAS X's took 34 s (simulator, 2026-10-14), all top-level windows'
        names 0.15 s. A window of LAS X's is a dialog when it holds
        ``MessageBoxContent``; one of Navigator Expert's is taken as one
        whatever it holds, and is pressed only if its one button is OK.
        """
        c, uia = self.client, self.uia
        tops = uia.GetRootElement().FindAll(
            c.TreeScope_Children, uia.CreateTrueCondition()
        )
        content = uia.CreatePropertyCondition(
            c.UIA_AutomationIdPropertyId, "MessageBoxContent"
        )
        found, names = [], {}
        for i in range(tops.Length):
            window = tops.GetElement(i)
            process = window.CurrentProcessId
            if process not in names:
                names[process] = process_name(process)
            if names[process] not in DIALOG_PROCESSES or window.CurrentName:
                continue
            if names[process] == "NavigatorExpert.exe" or window.FindFirst(
                c.TreeScope_Descendants, content
            ):
                found.append(AutomatedDialog(uia, c, window))
        if "LMSApplication.exe" not in names.values():
            raise WindowsUnavailable("LAS X's window was not found")
        return found


#: The processes whose dialogs LAS X's and Navigator Expert's warnings appear in.
DIALOG_PROCESSES = ("LMSApplication.exe", "NavigatorExpert.exe")


def process_name(pid: int) -> str:
    """The file name of process *pid*'s program, or "" when Windows does not say."""
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer, size = ctypes.create_unicode_buffer(260), ctypes.c_ulong(260)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        kernel.CloseHandle(handle)


connections = threading.local()


def lasx_windows() -> AutomatedWindows:
    """This thread's view of LAS X's windows; UI Automation objects belong to the thread that made them."""
    windows = getattr(connections, "windows", None)
    if windows is None:
        windows = AutomatedWindows()
        connections.windows = windows
    return windows
