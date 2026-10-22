"""How a request reaches LAS X through the CAM API, and how the driver learns that it arrived.

Every change the driver makes is a LAS X request that is filled in and sent. LAS X
answers in two steps: first it says that it received the request, then, a moment
later, it fills in a reply (an error text and a result code) that tells whether
the request worked. How to clear that reply before a send, how to send and wait for
the receipt, how to wait for the reply to be filled in, and how to find out whether
a LAS X dialog box is open and in the way are all things that only LAS X does this
way. They live here, and the dispatcher's engine asks for them through the
``transport`` of the command's profile. Another microscope supplies a transport of
its own and leaves the engine as it is.

Hand a message to LAS X and wait until it says "received".

``UpdateAwaitReceipt`` both hands the message over and waits, inside the
call, until LAS X answers "received" or its timeout passes. "Received" only
means LAS X got the message; whether the setting, move or job switch then
happened is the confirmation's business.

Delivery follows the one rule in ``primitives/cam_api/waiting.py``: ``WINDOWS`` tries of
``WINDOW_S`` each. There is no pause between tries, because each try already
waits a full window for the answer.

Acquisition never comes through here: a second acquire is a second
acquisition, so it is sent once with ``UpdateAsync``.

Before a message goes out, a LAS X warning that asks only for OK is answered,
and a message is not sent while another dialog is open
(``lasx_dialogs.DialogOpen``): LAS X's hardware server waits for every open
dialog, and a message sent into it waits too. While the message is out, a
watcher answers an OK-only warning it causes, which a message sent straight to
the hardware server would otherwise wait for without end.

A CAM API request object that knows its own name.

A change is written into one of the CAM API's request objects (``client.PyApiSetZoomByJobName``
and so on). A route that does not use the CAM API to send it, the direct route, needs to know
which command it is, and the object itself does not say. This wrapper carries the name and
passes everything else (``Model``, ``UpdateAsync``, ``UpdateAwaitReceipt``) on to the object.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from ...procedures import answer_dialogs
from ..log import reads as _log_reader
from . import waiting


log = logging.getLogger(__name__)

#: How long ``_await_echo_result`` checks the echo after delivery before giving
#: up: LAS X filling in whether the command raised an error. It stays outside
#: the window rule in ``waiting`` by decision; read at call time so tests can
#: shrink it.
ECHO_SETTLE_TIMEOUT_S = 1.0


def _await_echo_result(client, timeout=None):
    """Poll the echo until LAS X finishes processing.

    After the receipt confirms that LAS X got the request, LAS X still needs time
    to process it and fill in the echo (``HasError``, ``Error``, ``Result``). This
    polls until the echo shows that processing is done, or until ``timeout``.

    The echo counts as settled when ``Result`` is not 0 (NotDefined) or
    ``HasError`` is true; both are checked because LAS X may set ``HasError``
    without changing ``Result``. It is looked at every ``ANSWER_POLL_S``: it is the
    answer to a command already sent. Its one-second limit stays outside the
    window rule on purpose.

    Args:
        client: LAS X API client.
        timeout: Longest wait in seconds. None uses ``ECHO_SETTLE_TIMEOUT_S``.

    Returns:
        True if the echo settled and is ready for the error check, False if the
        time ran out with the echo still cleared.
    """
    if timeout is None:
        timeout = ECHO_SETTLE_TIMEOUT_S
    deadline = time.perf_counter() + timeout

    while True:
        try:
            result_code = int(client.PyApiCommandEcho.Model.Result)
        except Exception:
            result_code = 0  # Unreadable -> treat as not settled

        try:
            has_error = bool(client.PyApiCommandEcho.Model.HasError)
        except Exception:
            has_error = False  # Unreadable -> treat as not settled

        if result_code != 0 or has_error:
            return True

        if time.perf_counter() >= deadline:
            return False

        time.sleep(waiting.ANSWER_POLL_S)


class LasxTransport:
    """The way requests are sent to LAS X, as the dispatcher's engine asks for it."""

    def clear_reply(self, client):
        """Empty LAS X's reply, so that what is read after the send is the new answer."""
        client.PyApiCommandEcho.Model.HasError = False
        client.PyApiCommandEcho.Model.Error = ""
        try:
            client.PyApiCommandEcho.Model.Result = 0  # NotDefined
        except Exception:
            pass  # Some API versions may not allow Result assignment

    def fill(self, request, setup_fn):
        """Write the command's values into the request: a LAS X request keeps them in its ``Model``."""
        setup_fn(request.Model)

    def send(self, client, api_obj, label, *, asynchronous):
        """Send the request. Returns ``(arrived, answered)``.

        ``arrived`` is whether LAS X confirmed receiving it (a request sent
        without waiting counts as arrived). ``answered`` lists what the driver
        said to LAS X dialog boxes that opened while it was sending.
        """
        fired_at = time.monotonic()
        if asynchronous:
            answer_dialogs.answer_warnings()
            with answer_dialogs.watching():
                api_obj.UpdateAsync()
            arrived = True
        else:
            arrived = deliver(api_obj, label=label)
        answered = [note.message for note in answer_dialogs.answered_since(fired_at)]
        return arrived, answered

    def await_reply(self, client):
        """Wait for LAS X to fill in its reply. True if it did, False if time ran out."""
        return _await_echo_result(client)

    def dialog_warning_since(self, command_started_at, description):
        """Return a post-command LAS X dialog warning, if the log can prove one.

        Diagnostic only: this is called only on failure or unconfirmed paths, and a
        log-reader failure must never add another way for a command to fail.
        """
        try:
            snapshot = _log_reader.parse_msgbox_log()
            text = snapshot.pending_dialog
            observed_at = snapshot.pending_dialog_ts
        except Exception:  # noqa: BLE001
            log.debug("dialog diagnostic failed", exc_info=True)
            return None
        if not text or observed_at is None or observed_at < command_started_at:
            return None
        return f"{description} | LAS X dialog appears open: {text}"


LASX = LasxTransport()


def deliver(api_obj, *, label="message"):
    """Deliver *api_obj*'s message; True once LAS X says "received", else False.

    Raises ``lasx_dialogs.DialogOpen``, with nothing sent, while a LAS X dialog
    the driver does not answer is open.
    """
    answer_dialogs.answer_warnings()
    with answer_dialogs.watching():
        for attempt in range(1, waiting.WINDOWS + 1):
            if api_obj.UpdateAwaitReceipt(waiting.WINDOW_S):
                return True
            log.warning("%s not received (try %d/%d)", label, attempt, waiting.WINDOWS)
    return False


class Request:
    """A CAM request object together with its name, such as ``"PyApiSetZoomByJobName"``."""

    def __init__(self, name, cam_object):
        self.name = name
        self.cam_object = cam_object

    def __getattr__(self, attribute):
        return getattr(self.cam_object, attribute)


def request_of(client, name):
    """The CAM client's request object called *name*, wrapped with its name."""
    return Request(name, getattr(client, name))
