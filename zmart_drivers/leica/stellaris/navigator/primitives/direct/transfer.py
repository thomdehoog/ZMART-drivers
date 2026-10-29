"""Sending a change to LAS X's hardware server: the direct route, the only route of this driver.

A change is a short list of XML elements (``writes.py``), the ones LAS X's
own window sends. Each goes to the hardware server with ``SendCommand2``,
from a thread of its own, because the server answers only once a dialog the
element caused is closed; the write loops answer LAS X's dialogs around the
send (``dispatcher/write.py``, handed ``procedures/answer_dialogs.py``).

LAS X answers ``<Result Error="0"/>`` accepted, ``Error="1"`` refused with
its reason inside, ``Error="2"`` accepted with the value adjusted, and a
bare ``<Result/>`` for the ATL commands. The answer to the last element
sent goes into the client's ``reply``, where the profile's ``check_reply``
reads it. The elements of one change are sent in order and stop at the
first LAS X refuses, so a tile scan whose preparation was refused is never
started.

When the hardware server cannot be reached nothing is sent and the route
says so (:class:`TransportUnavailable`). Once an element has gone out it is
never handed on or sent again by the route: LAS X may already have carried
it out; sending again is the write loops' decision, under the change's own
rules (a capture is never sent again).

Learned from LAS X's own command log; nothing here is guessed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from datetime import datetime

from ...dispatcher.write import TransportUnavailable
from . import client as hardware_server
from .client import LASX_LOG, HardwareServerUnavailable

log = logging.getLogger(__name__)


class DirectTransport:
    """The direct route, as the write loops ask for a transport."""

    #: Named for the result's ``route``; there is one route in this driver.
    carried_by = "direct"

    def fill(self, request, setup_fn):
        """Write the change's elements into the request, afresh before every send."""
        setup_fn(request)

    def clear_reply(self, client):
        """Empty the reply, so that what is read after the send is LAS X's new answer."""
        client.reply.clear()

    def send(self, client, request, label, *, asynchronous):
        """Send the change's elements one after the other; return ``(arrived, notes)`` like any transport.

        Raises :class:`TransportUnavailable` while nothing has been sent.
        LAS X's refusal is not an error here: it goes into the reply, and
        the elements after it are not sent.
        """
        if not request.elements:
            raise TransportUnavailable(f"{request.name} has no element to send")
        try:
            hardware_server.connected_server()
        except HardwareServerUnavailable as why:
            raise TransportUnavailable(str(why)) from why
        notes = []
        for number, xml in enumerate(request.elements, start=1):
            answer = send(xml)
            client.reply.update(
                accepted=accepted(answer),
                text=answer_text(answer),
                answer=answer,
                element=xml,
            )
            if not client.reply["accepted"]:
                if number < len(request.elements):
                    notes.append(
                        f"{label} | LAS X refused element {number} of {len(request.elements)}; "
                        f"the rest were not sent"
                    )
                break
        return True, notes

    def await_reply(self, client):
        """LAS X's answer came with the send, so there is nothing more to wait for."""
        return True

    def dialog_warning_since(self, command_started_at, description):
        """A dialog LAS X's window opened since the change began, by its log; None when none did.

        Diagnostic only, asked when a change failed or stayed unconfirmed: a
        log that cannot be read must never add another way for a change to
        fail.
        """
        try:
            shown = last_dialog_shown()
        except Exception:
            log.debug("the dialog diagnostic could not read LAS X's log", exc_info=True)
            return None
        if shown is None or shown[0] < command_started_at:
            return None
        return f"{description} | LAS X dialog appears open: {shown[1]}"


DIRECT = DirectTransport()


# =============================================================================
# One element sent, and LAS X's answer
# =============================================================================


class Sender:
    """A thread with its own connection to the hardware server, sending one element at a time.

    The hardware server answers a command only once a dialog the command
    caused is closed, so the command is sent from here and waited for by
    :func:`send`, which can stop waiting.
    """

    def __init__(self):
        self.waiting = queue.Queue()
        threading.Thread(target=self.run, name="lasx-direct-sender", daemon=True).start()

    def run(self):
        while True:
            xml, outcome, done = self.waiting.get()
            try:
                outcome["answer"] = str(hardware_server.connected_server().SendCommand2(xml, ""))
            except Exception as why:  # noqa: BLE001 -- handed to the caller as it is
                hardware_server.forget_connection()
                outcome["error"] = why
            done.set()


senders = {"current": None}
senders_lock = threading.Lock()


def send(xml: str) -> str:
    """Send one element to the hardware server; return LAS X's answer. Never sent twice.

    A server that does not answer within the rule's windows (a dialog the
    element caused waits for the operator) ends the wait with
    ``HardwareServerUnavailable``. The element stays with LAS X, which may
    still apply it once the operator answers; the next element goes through
    a new sender.
    """
    from . import profile as waiting

    with senders_lock:
        if senders["current"] is None:
            senders["current"] = Sender()
        sender = senders["current"]
    outcome, done = {}, threading.Event()
    sender.waiting.put((xml, outcome, done))
    deadline = time.monotonic() + waiting.WINDOWS * waiting.WINDOW_S
    while not done.wait(waiting.POLL_S):
        if time.monotonic() >= deadline:
            abandon(sender)
            raise hardware_server.HardwareServerUnavailable(
                f"the hardware server did not answer {xml[:120]} within "
                f"{waiting.WINDOWS} windows of {waiting.WINDOW_S}s"
            )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def abandon(sender):
    """Leave *sender* with the element LAS X holds; the next one gets a new sender."""
    with senders_lock:
        if senders["current"] is sender:
            senders["current"] = None


RESULT = re.compile(r"<Result\b([^>]*?)/?>")
ERROR = re.compile(r'\bError="([^"]*)"')
MESSAGE = re.compile(r"<Result\b[^>]*>(.*)</Result>", re.DOTALL)

#: LAS X's answer codes: 0 accepted, 1 refused, 2 accepted with the value
#: adjusted (pinhole 100 airy was set to 2.512 on the simulator). An adjusted
#: value counts as accepted and the difference is left to the read-back. The
#: ATL commands answer a bare ``<Result/>``, with no code: accepted.
ACCEPTED_CODES = ("0", "", "2")


def accepted(answer: str) -> bool:
    """Whether LAS X took the element."""
    result = RESULT.search(answer or "")
    if result is None:
        return False
    code = ERROR.search(result[1])
    return code is None or code[1] in ACCEPTED_CODES


def answer_text(answer: str) -> str:
    """What LAS X wrote inside its answer: the reason for a refusal or an adjustment."""
    match = MESSAGE.search(answer or "")
    return match[1].strip() if match else ""


# =============================================================================
# A dialog in the way, by LAS X's own log
# =============================================================================

#: How a dialog's entry starts in ``LAS X.log``: its time, then what the window showed.
SHOWN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) .*?'MessageBox (?!Result: )(.*)$")
ANSWERED = "'MessageBox Result: "
#: How much of the log's end is read for the last dialog.
TAIL_BYTES = 64 * 1024


def last_dialog_shown() -> tuple[float, str] | None:
    """The last dialog LAS X's window showed and has not had answered: when (``time.time()``) and its text.

    Reads the end of ``LAS X.log``. None when the log is not there, or the
    last dialog in it was answered.
    """
    with LASX_LOG.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("latin-1").splitlines()
    shown = None
    for line in lines:
        found = SHOWN.match(line)
        if found:
            shown = (found[1], found[2].strip(" '"))
        elif ANSWERED in line:
            shown = None
    if shown is None:
        return None
    at = datetime.strptime(shown[0], "%Y-%m-%d %H:%M:%S.%f").timestamp()
    return at, shown[1]
