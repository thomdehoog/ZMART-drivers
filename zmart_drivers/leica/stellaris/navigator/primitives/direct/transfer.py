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
import math
import queue
import re
import threading
import time
from dataclasses import dataclass
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
                tree = STATE_TREE.match(xml)
                if tree is not None:
                    outcome["answer"] = write_into_state_tree(tree[1], tree[2])
                else:
                    outcome["answer"] = str(hardware_server.connected_server().SendCommand2(xml, ""))
            except Exception as why:  # noqa: BLE001 -- handed to the caller as it is
                hardware_server.forget_connection()
                outcome["error"] = why
            done.set()


senders = {"current": None}
senders_lock = threading.Lock()

#: Every command the driver may send to LCS, by name. LCS takes many more,
#: calibration and service commands among them; the transfer refuses each
#: one that is not here, before anything reaches LCS. A new change adds its
#: commands here, in the same step as its builder in ``writes.py``.
ALLOWED_COMMANDS = frozenset(
    {
        "SetZoom",
        "SetScanSpeed",
        "SetConfocalScanFieldRotation",
        "SetLineAverage",
        "SetPinholeAU",
        "SetStagePositionInHwOnly",
        "SetZPosition",
        "SetSlotPosition",
        "StopAcquiring",
        "CaptureSingleFrame",
        "StartAcquiring",
        "SetStageUseMode",
        "SetStageIrregularTileScanPositions",
        "ATL_SuppressAtlasAttachment",
        "ScanMode",
        "ActivateAutoFocus",
        "StageMoveToCoordinates",
        "SetZUseMode",
        "SetZStackDefinition",
        "SetStackStepSize",
        "SetLaserLineIntensity",
        "ActivateShutter",
        "SetFrameAverage",
        "SetLineAccu",
        "SetFrameAccu",
        "SetFormat",
        "SetScannerToResonant",
        "FilterWheel_ApiSetSlot",
        "ATL_Block_Save_To_XML",
        "ATL_Block_Load_From_XML",
        "SetLut",
        "SetMotCorrPosition",
        "StartMotCorrAutomation",
    }
)

@dataclass(frozen=True)
class TreeWrite:
    """One value the driver may write into LCS's state tree, and the values it may take.

    ``kind`` is ``int`` or ``float``. A value is one of ``choices``, or lies
    within ``within`` (a fixed range LCS keeps to), or lies between the two
    nodes ``between`` names beside it: LCS's own minimum and maximum, read
    at the moment of the write; or it is one of the values in the node
    ``listed`` names beside it, LCS's own list, read at the write.
    """

    path: tuple[str, ...]
    kind: type = int
    choices: frozenset = frozenset()
    within: tuple[float, float] | None = None
    between: tuple[str, str] | None = None
    listed: str | None = None


HW_PARAMETER = ("Confocal", "HWParameter")

#: The values the driver may write straight into LCS's state tree, by name.
#: LAS X's window changes these with no command LCS logs, and a loaded setting
#: does not change them; every other node is refused. None of them moves the
#: stage or the objective or makes light (simulator, 2026-11-01).
#:
#: LCS does not keep to its own range when a value is written here: a pinhole
#: of 2000 micrometres (its range is 100 to 1000) raised an error and still
#: opened the pinhole to its maximum, and an emission wavelength of 2000 nm
#: was kept as 800. So every value is checked here first, and one outside
#: the range is refused before anything is written.
STATE_TREE_WRITES = {
    # Bidirectional X: 1 one way, 2 both ways; the frame rate follows it.
    "ScanDirectionX": TreeWrite(("Confocal", "ScanningParameter", "ScanDirectionX"), choices=frozenset({1, 2})),
    # Phase X, which lines up the two directions of a bidirectional scan.
    "PhaseX": TreeWrite((*HW_PARAMETER, "PhaseX"), float, between=("PhaseXMin", "PhaseXMax")),
    # The pinhole, in metres; LCS works out its size in Airy units from it.
    "Pinhole": TreeWrite((*HW_PARAMETER, "Pinhole"), float, between=("PinholeMin", "PinholeMax")),
    # The unit LAS X shows the pinhole in: 1 micrometres, 2 Airy units.
    "PinholeUnit": TreeWrite((*HW_PARAMETER, "PinholeUnit"), choices=frozenset({1, 2})),
    # The emission wavelength the Airy unit is worked out for, in whole nm.
    # LCS gives no minimum or maximum, but keeps it within 380 to 800 nm.
    "EmissionWaveLengthForAiryAnddzCalculation": TreeWrite(
        (*HW_PARAMETER, "EmissionWaveLengthForAiryAnddzCalculation"), within=(380, 800)
    ),
    # The bit depth, one of LCS's list (8, 12, 16 on the simulator); LCS
    # ignored 10, which is not on it.
    "Resolution": TreeWrite(("Confocal", "ScanningParameter", "Resolution"), listed="ResolutionList"),
    # The z-stack's step mode: 2 is System Optimized, where LCS takes its own
    # optimal step (and may stretch the end to fit whole steps), 1 keeps the
    # step size. 0, the number of steps, is not written.
    "StackCalculationMode": TreeWrite(("Acquire", "AcquireZStackInfo", "StackCalculationMode"), choices=frozenset({1, 2})),
    # SmartCORR's range: the collar positions it scans through, in percent,
    # and how many. The window writes them here (simulator, 2026-11-01); LCS
    # gives them no range, the collar's is 0 to 100 percent.
    "AutomationBegin": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationBegin"), float, within=(0, 100)),
    "AutomationEnd": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationEnd"), float, within=(0, 100)),
    "AutomationSteps": TreeWrite(("Microscope", "MotCorrAutomationInfo", "AutomationSteps"), within=(2, 100)),
}

#: A write into the state tree, as the writes build it: ``<StateTree Command="name" Value="n"/>``.
STATE_TREE = re.compile(r'^\s*<StateTree\s+Command="([^"]+)"\s+Value="([^"]+)"\s*/>\s*$')

#: An element's command, wherever it stands among the attributes (LAS X's window
#: writes ``FileName`` before ``Command`` in its setting commands).
COMMAND = re.compile(r'^\s*<\w+\b[^>]*?\sCommand="([^"]+)"')


class NotAllowed(RuntimeError):
    """An element whose command is not in :data:`ALLOWED_COMMANDS`; it was not sent."""


def tree_value(name: str, text: str):
    """The value *text* to write into the node *name*, when the list allows it; :class:`NotAllowed` otherwise.

    LCS's own range, for a value that has one, is checked at the write
    (:func:`write_into_state_tree`).
    """
    if name not in STATE_TREE_WRITES:
        raise NotAllowed(f"not written: {name} is not a value this driver writes into the state tree")
    entry = STATE_TREE_WRITES[name]
    try:
        value = entry.kind(text)
    except ValueError:
        raise NotAllowed(f"not written: {name} takes a {entry.kind.__name__}, not {text}") from None
    if entry.kind is float and not math.isfinite(value):
        raise NotAllowed(f"not written: {name} takes a finite number, not {text}")
    if entry.choices and value not in entry.choices:
        raise NotAllowed(f"not written: {name} takes {sorted(entry.choices)}, not {text}")
    if entry.within is not None and not entry.within[0] <= value <= entry.within[1]:
        raise NotAllowed(f"not written: {name} takes {entry.within[0]} to {entry.within[1]}, not {text}")
    return value


def write_into_state_tree(name: str, text: str) -> str:
    """Write one listed value into LCS's state tree; answer as LAS X answers a command.

    A value with a range of LCS's own is first checked against that range,
    read now; outside it, nothing is written and the answer is a refusal
    (``Error="1"``) with the reason inside.
    """
    from . import state_tree

    entry = STATE_TREE_WRITES[name]
    value = tree_value(name, text)
    if entry.listed is not None:
        nodes = state_tree.server_nodes()
        listed = state_tree.node_value(nodes, state_tree.node_at(nodes, (*entry.path[:-1], entry.listed)))
        if value not in tuple(listed or ()):
            shown = ", ".join(str(item) for item in listed or ())
            return f'<Result Error="1">{name} {text} is not one LAS X offers ({shown}); not written</Result>'
    if entry.between is not None:
        nodes = state_tree.server_nodes()
        low, high = (
            state_tree.node_value(nodes, state_tree.node_at(nodes, (*entry.path[:-1], bound)))
            for bound in entry.between
        )
        # A hair of room, so that a value given in other units (micrometres
        # for metres) and landing on the edge is not refused by rounding.
        room = 1e-9 * max(abs(low), abs(high))
        if not low - room <= value <= high + room:
            return f'<Result Error="1">{name} {text} is outside the range of LAS X, {low} to {high}; not written</Result>'
    state_tree.change_node(entry.path, value)
    return '<Result Error="0"/>'


def allowed(xml: str) -> str:
    """The command *xml* carries, when the driver may send it; :class:`NotAllowed` otherwise."""
    tree = STATE_TREE.match(xml or "")
    if tree is not None:
        tree_value(tree[1], tree[2])
        return tree[1]
    found = COMMAND.match(xml or "")
    if found is None:
        raise NotAllowed(f"not sent: no command can be read in {xml[:80]!r}")
    if found[1] not in ALLOWED_COMMANDS:
        raise NotAllowed(f"not sent: {found[1]} is not a command this driver sends")
    return found[1]


def send(xml: str) -> str:
    """Send one element to the hardware server; return LAS X's answer. Never sent twice.

    A server that does not answer within the rule's windows (a dialog the
    element caused waits for the operator) ends the wait with
    ``HardwareServerUnavailable``. The element stays with LAS X, which may
    still apply it once the operator answers; the next element goes through
    a new sender. A command not in :data:`ALLOWED_COMMANDS` raises
    :class:`NotAllowed`, and nothing is sent.
    """
    from . import profile as waiting

    allowed(xml)

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
