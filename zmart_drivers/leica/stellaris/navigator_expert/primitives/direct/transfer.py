"""Sending a change straight to LAS X's hardware server: the direct route, and the element it sends.

The same commands the CAM API ends in, sent as the XML elements LAS X logs for them
(``primitives/direct/translations.py``). A change the driver makes is first written
into the CAM API's request object, as always; this transport then turns the fields
into the element and sends it, and puts LAS X's answer where the driver's error
checks read the CAM API's.

When the hardware server cannot be reached, or a command has no translation, nothing
is sent and the route says so (:class:`TransportUnavailable`), so the next route (the
CAM API) carries the command. Once an element has gone out, the route never hands the
command on: LAS X may already have carried it out.

One element sent to LAS X's hardware server, and the answer that comes back.

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

import json
import logging
import os
import time
from pathlib import Path
from ...dispatcher.write import TransportUnavailable
from . import client as hardware_server
from .client import HardwareServerUnavailable
from ...procedures.clear_cache.data_container import (
    experiment_is_open,
    experiment_of,
    new_experiment,
    open_experiments,
)
from .state_tree import last_image_id, job_sequence
from .writes import ENUM_FIELDS, TRANSLATIONS, NoTranslation
from ...procedures import answer_dialogs
from ..cam_api import transfer as _cam
import queue
import re
import threading
from xml.sax.saxutils import quoteattr
from ..cam_api import waiting
from .writes import Fields


log = logging.getLogger(__name__)

#: The name the direct route gives a project it makes, followed by its number.
NEW_PROJECT = "ZMART_Experiment"


def remembered_experiment_file() -> Path:
    """Where the direct route writes down the last experiment it found, for the next start of the driver.

    Under the Windows user's own profile (``%LOCALAPPDATA%\\ZMART``), as LAS X's
    Data Container belongs to that user too.
    """
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "ZMART" / "leica-stellaris-direct-route.json"


def read_remembered_experiment() -> int | None:
    """The experiment written down by an earlier run; None when there is none or it cannot be read."""
    try:
        number = json.loads(remembered_experiment_file().read_text(encoding="utf-8"))[
            "experiment"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def write_remembered_experiment(number: int) -> None:
    """Write down *number*; a file that cannot be written only costs the next start its memory."""
    file = remembered_experiment_file()
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"experiment": number}), encoding="utf-8")
    except OSError:
        pass


def new_project_name(open_projects: dict[int, str]) -> str:
    """``ZMART_Experiment<N>``, N one above the highest such name among *open_projects* (1 for the first)."""
    numbers = [
        int(name[len(NEW_PROJECT) :])
        for name in open_projects.values()
        if name.startswith(NEW_PROJECT) and name[len(NEW_PROJECT) :].isdigit()
    ]
    return f"{NEW_PROJECT}{max(numbers, default=0) + 1}"


#: The name the direct route gives a project it makes, followed by its number.


class DirectTransport:
    """The direct route, as the send loop asks for a transport."""

    def __init__(self):
        self.enum_types = {}
        # The Data Container experiment the last acquire's image went into: see images_experiment_id.
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

        fired_at = time.monotonic()
        answer = send(xml)
        self.answered(client, answer)
        return True, [note.message for note in answer_dialogs.answered_since(fired_at)]

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

        LAS X's window names it from its project tree, which only the window has;
        the direct route chooses it from the Data Container, and only ever a project
        open in LAS X. A project deleted in LAS X's window stays in the Data
        Container, closed, with its images, and an image sent into it is seen
        nowhere (STELLARIS, 2026-10-15: image 2135 into deleted project 698). In
        this order:

        1. The project of the last image LAS X made.
        2. The project remembered from an earlier acquire, kept on this connection
           and in a file (:func:`remembered_experiment_file`), because the last
           image is often gone by the next acquire.
        3. The newest project open in LAS X.
        4. With none open, a new project, ``ZMART_Experiment<N>``, as LAS X makes
           one itself when it captures with no project open.

        Anything the Data Container cannot answer refuses the acquire; nothing is
        sent then, and the CAM API carries it.
        """
        try:
            last = last_image_id()
        except HardwareServerUnavailable as why:
            raise NoTranslation(
                f"no last image from LAS X's state tree: {why}"
            ) from why
        try:
            chosen = self.open_project_for(last)
        except Exception as why:  # noqa: BLE001 -- any Data Container failure refuses, nothing is sent
            raise NoTranslation(
                f"LAS X's Data Container did not give a project for the image: {why}"
            ) from why
        if chosen != self.experiment:
            write_remembered_experiment(chosen)
        self.experiment = chosen
        return chosen

    def open_project_for(self, last: int) -> int:
        """The project chosen by the order in :meth:`images_experiment_id`, for last image *last* (0: none)."""
        try:
            found = experiment_of(last) if last else None
        except Exception:  # noqa: BLE001 -- the last image was removed, as the driver does after saving
            found = None
        remembered = (
            self.experiment
            if self.experiment is not None
            else read_remembered_experiment()
        )
        for candidate in (found, remembered):
            if candidate is not None and experiment_is_open(candidate):
                return candidate
        projects = open_experiments()
        if projects:
            return max(projects)
        return new_experiment(new_project_name(projects))

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
            answer_dialogs.answer_warnings()
        except answer_dialogs.DialogOpen:
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
