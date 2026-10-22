"""One element sent to LAS X's hardware server, and the answer that comes back.

A command is the XML element the CAM API itself ends in, for example
``<Scanner Command="SetZoom" Zoom="1.5" BlockId="152"/>`` (``translations.py``).
It goes to the hardware server with ``SendCommand2``, from a thread of its own
because the server answers only once a dialog the command caused is closed.
LAS X answers ``<Result Error="0"/>`` accepted, ``Error="1"`` refused with its
reason inside, ``Error="2"`` accepted with the value adjusted.

Learned from LAS X's own command log; nothing here is guessed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import queue
import re
import threading
import time
from xml.sax.saxutils import quoteattr

from . import hardware_server
from ..cam_api import waiting
from ...procedures import answer_dialogs as lasx_dialogs
from .writes import Fields, NoTranslation

RESULT = re.compile(r"<Result\b([^>]*?)/?>")
ERROR = re.compile(r'\bError="([^"]*)"')
MESSAGE = re.compile(r"<Result\b[^>]*>(.*)</Result>", re.DOTALL)


def number_text(value) -> str:
    """A value as LAS X writes it: ``5`` for 5.0, ``2E-06``, ``-1.928E-05``, ``0.066025``."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        text = format(value, ".15g")
        return text.replace("e", "E")
    return str(value)


def beam_route_xml(route: str) -> str:
    """``"40;2"`` as the ``BeamRoute`` element: one ``BeamPosition`` per level.

    An empty route is the element with no levels, ``<BeamRoute Version="0"/>``:
    the CAM API sent it so for a filter wheel without a route (2026-10-12), and
    LAS X's own serializer writes it so (``HwSerializationHelper``).
    """
    positions = [p for p in str(route or "").split(";") if p]
    if not positions:
        return '<BeamRoute Version="0"/>'
    levels = "".join(
        f'<BeamPosition BeamPositionLevel="{level}" BeamPosition={quoteattr(p)}/>'
        for level, p in enumerate(positions)
    )
    return f'<BeamRoute Version="0">{levels}</BeamRoute>'


class Sender:
    """A thread with its own connection to the hardware server, sending one element at a time.

    The hardware server answers a command only once a dialog the command
    caused is closed, so the command is sent from here and waited for by
    :func:`send`, which can stop waiting.
    """

    def __init__(self):
        self.waiting = queue.Queue()
        threading.Thread(
            target=self.run, name="lasx-direct-sender", daemon=True
        ).start()

    def run(self):
        while True:
            xml, outcome, done = self.waiting.get()
            try:
                outcome["answer"] = str(
                    hardware_server.connected_server().SendCommand2(xml, "")
                )
            except Exception as why:  # noqa: BLE001 -- handed to the caller as it is
                hardware_server.forget_connection()
                outcome["error"] = why
            done.set()


senders = {"current": None}
senders_lock = threading.Lock()


def send(xml: str) -> str:
    """Send one element to the hardware server; return LAS X's answer. Never sent twice.

    While the answer is awaited, a LAS X warning that asks only for OK is
    answered; any other dialog ends the wait with
    ``lasx_dialogs.DialogOpen``, its text, and a server that neither answers
    nor shows a dialog within the rule's four windows with
    ``HardwareServerUnavailable``. Either way the element stays with LAS X,
    which may still apply it once the operator answers; the next element
    goes through a new sender.
    """
    with senders_lock:
        if senders["current"] is None:
            senders["current"] = Sender()
        sender = senders["current"]
    outcome, done = {}, threading.Event()
    sender.waiting.put((xml, outcome, done))
    deadline = time.monotonic() + waiting.WINDOWS * waiting.WINDOW_S
    while not done.wait(waiting.POLL_S):
        try:
            lasx_dialogs.answer_warnings()
        except lasx_dialogs.DialogOpen:
            abandon(sender)
            raise
        if time.monotonic() >= deadline:
            abandon(sender)
            raise hardware_server.HardwareServerUnavailable(
                f"the hardware server did not answer {xml[:120]} within "
                f"{waiting.WINDOWS} windows of {waiting.WINDOW_S}s, and LAS X shows no dialog"
            )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def abandon(sender):
    """Leave *sender* with the element LAS X holds; the next one gets a new sender."""
    with senders_lock:
        if senders["current"] is sender:
            senders["current"] = None


#: LAS X's answer codes: 0 accepted, 1 refused, 2 accepted with the value
#: adjusted (pinhole 100 airy was set to 2.512 on the simulator). The CAM API
#: reports an adjusted value as accepted and leaves the difference to the
#: read-back; so does the direct route. The ATL commands (job selection,
#: save, load) answer a bare ``<Result/>``, with no code: accepted
#: (simulator, 2026-10-14).
ACCEPTED_CODES = ("0", "", "2")


def accepted(answer: str) -> bool:
    result = RESULT.search(answer or "")
    if result is None:
        return False
    code = ERROR.search(result[1])
    return code is None or code[1] in ACCEPTED_CODES


def answer_text(answer: str) -> str:
    """What LAS X wrote inside its answer: the reason for a refusal or an adjustment."""
    match = MESSAGE.search(answer or "")
    return match[1].strip() if match else ""


def element_xml(translation, model, route, enum_types) -> str:
    """The element for the fields the command wrote into *model*.

    Raises :class:`NoTranslation` when the command has no translation or one of
    its values has no known hardware-server form.
    """
    if translation is None:
        raise NoTranslation("this command has no direct translation yet")
    pairs = list(translation.attributes(Fields(model, enum_types), route))
    pairs.insert(translation.command_at, ("Command", translation.command))
    attributes = "".join(
        f" {name}={quoteattr(number_text(value))}" for name, value in pairs
    )
    if translation.beam_route:
        return f"<{translation.element}{attributes}>{beam_route_xml(model.BeamRoute)}</{translation.element}>"
    return f"<{translation.element}{attributes}/>"
