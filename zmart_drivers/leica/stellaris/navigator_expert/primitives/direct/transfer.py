"""Sending a change straight to LAS X's hardware server: the direct route, and the element it sends.

The same commands the CAM API ends in, sent as the XML elements LAS X logs for them
(``primitives/direct/writes.py``). A change the driver makes is first written
into the CAM API's request object, as always; this transport then turns the fields
into the element and sends it, and puts LAS X's answer where the driver's error
checks read the CAM API's.

When the hardware server cannot be reached, or a command has no translation, nothing
is sent and the route says so (:class:`TransportUnavailable`), so the next route (the
CAM API) carries the command. Once an element has gone out, the route never hands the
command on: LAS X may already have carried it out.

One element sent to LAS X's hardware server, and the answer that comes back.

A command is the XML element the CAM API itself ends in, for example
``<Scanner Command="SetZoom" Zoom="1.5" BlockId="152"/>`` (``writes.py``).
It goes to the hardware server with ``SendCommand2``, from a thread of its own
because the server answers only once a dialog the command caused is closed; the
write loop answers LAS X's dialogs around the send (``dispatcher/write.py``).

An acquire names the experiment its image goes into. The change hands in what
opens it (``Write.experiment``, carried on the request); the route asks it only
when it builds the acquire's element, after the limits and the wait for idle.
LAS X answers ``<Result Error="0"/>`` accepted, ``Error="1"`` refused with its
reason inside, ``Error="2"`` accepted with the value adjusted.

Learned from LAS X's own command log; nothing here is guessed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from ...dispatcher.write import TransportUnavailable
from . import client as hardware_server
from .client import HardwareServerUnavailable
from .state_tree import job_sequence
from .writes import ENUM_FIELDS, TRANSLATIONS, NoTranslation
from ..cam_api import transfer as _cam
import queue
import re
import threading
from xml.sax.saxutils import quoteattr
from ..cam_api import profile as waiting
from .writes import Fields


log = logging.getLogger(__name__)


class DirectTransport:
    """The direct route, as the send loop asks for a transport."""

    def __init__(self):
        self.enum_types = {}
        # What opens the experiment of the request being sent: see images_experiment_id.
        self.experiment = None

    # --- what the send loop asks of a transport -------------------------------------

    def fill(self, request, setup_fn):
        """Write the command's values into the request, remembering the enum types it had before."""
        self.enum_types_of(request.name, request.Model)
        setup_fn(request.Model)

    def clear_reply(self, client):
        """Empty the reply, so that what is read after the send is LAS X's new answer."""
        _cam.LASX.clear_reply(client)

    def send(self, client, request, label, *, asynchronous):
        """Send the command's element; return ``(arrived, answered)`` like any transport.

        Raises :class:`TransportUnavailable` while nothing has been sent. LAS X's
        refusal is not an error here: it goes into the reply, as the CAM API's does.
        """
        name = getattr(request, "name", None)
        self.experiment = getattr(request, "experiment", None)
        translation = TRANSLATIONS.get(name)
        if translation is None:
            raise TransportUnavailable(f"{name} has no direct translation")
        try:
            hardware_server.connected_server()
        except HardwareServerUnavailable as why:
            raise TransportUnavailable(str(why)) from why
        try:
            xml = element_xml(
                translation,
                request.Model,
                self,
                self.enum_types_of(name, request.Model),
            )
        except (NoTranslation, HardwareServerUnavailable) as why:
            raise TransportUnavailable(str(why)) from why

        answer = send(xml)
        self.answered(client, answer)
        return True, []

    def await_reply(self, client):
        """LAS X's answer came with the send, so there is nothing more to wait for."""
        return True

    def dialog_warning_since(self, command_started_at, description):
        return _cam.LASX.dialog_warning_since(command_started_at, description)

    # --- what the translations ask of the route --------------------------------------

    def enum_types_of(self, name, model) -> dict:
        """The enum types of a CAM model's fields, kept from the first look, before anything wrote them."""
        if name not in self.enum_types:
            self.enum_types[name] = {
                field: type(getattr(model, field))
                for field in ENUM_FIELDS
                if hasattr(model, field) and type(getattr(model, field)) is not int
            }
        return self.enum_types[name]

    def job(self, job_name):
        """The job's live ids, from the hardware server's state tree, looked up now.

        From the tree, not the CAM API's job list: after an experiment load LAS X
        gives every job a new id, and the CAM API's list can keep the old ones
        (2026-10-13: CAM 145, LAS X 152 for HiRes).
        """
        try:
            found = job_sequence().jobs.get(job_name)
        except HardwareServerUnavailable as why:
            raise NoTranslation(f"no job ids from LAS X's state tree: {why}") from why
        if found is None:
            raise NoTranslation(f"no job {job_name!r} in LAS X's job sequence")
        return found

    def block_id(self, job_name) -> int:
        """The job's id in LAS X, the hardware server's ``BlockId``."""
        return self.job(job_name).block_id

    def element_id(self, job_name) -> int:
        """The job's element in LAS X's job sequence, by which a job is selected."""
        return self.job(job_name).element_id

    def sequence_id(self) -> int:
        """The job sequence's own block id."""
        try:
            return job_sequence().block_id
        except HardwareServerUnavailable as why:
            raise NoTranslation(
                f"no job sequence from LAS X's state tree: {why}"
            ) from why

    def images_experiment_id(self) -> int:
        """The Data Container experiment, LAS X's project, an acquire's image goes into.

        The request carries what opens it (``Write.experiment``, for LAS X
        ``procedures/clear_cache/data_container.experiment_for_capture``); it is
        asked here, as the element is built. When the request carries none, or
        it fails, the acquire is refused: nothing is sent, and the CAM API
        carries it.
        """
        if self.experiment is None:
            raise NoTranslation("the acquire names no experiment to open for its image")
        try:
            return self.experiment()
        except Exception as why:  # noqa: BLE001 -- any failure refuses, nothing is sent
            raise NoTranslation(str(why)) from why

    def answered(self, client, answer):
        """Record LAS X's answer in the reply, as the CAM API records its own."""
        echo = client.PyApiCommandEcho.Model
        if accepted(answer):
            echo.HasError, echo.Error = False, answer_text(answer)
            _set_result(echo, 1)
        else:
            echo.HasError, echo.Error = True, answer_text(answer) or answer
            _set_result(echo, 2)


def _set_result(echo, code):
    try:
        echo.Result = code
    except Exception:  # noqa: BLE001 -- some API versions do not allow assigning Result
        pass


DIRECT = DirectTransport()


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

    A server that does not answer within the rule's four windows (a dialog
    the element caused waits for the operator) ends the wait with
    ``HardwareServerUnavailable``. The element stays with LAS X, which may
    still apply it once the operator answers; the next element goes through
    a new sender.
    """
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
