"""How a request reaches LAS X, and how the driver learns that it arrived.

Every change the driver makes is a LAS X request that is filled in and sent. LAS X
answers in two steps: first it says that it received the request, then, a moment
later, it fills in a reply (an error text and a result code) that tells whether
the request worked. How to clear that reply before a send, how to send and wait for
the receipt, how to wait for the reply to be filled in, and how to find out whether
a LAS X dialog box is open and in the way are all things that only LAS X does this
way. They live here, and the dispatcher's engine asks for them through the
``transport`` of the command's profile. Another microscope supplies a transport of
its own and leaves the engine as it is.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
        University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
"""

import logging
import time

from ...dispatcher import timing as _timing
from ..primitives.api.delivery import deliver
from ..primitives.interface import lasx_dialogs
from ..primitives.log import log_reader as _log_reader

log = logging.getLogger(__name__)

#: How long ``_await_echo_result`` checks the echo after delivery before giving
#: up: LAS X filling in whether the command raised an error. It stays outside
#: the window rule in ``tuning`` by decision; read at call time so tests can
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

        time.sleep(_timing.ANSWER_POLL_S)


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

    def send(self, client, api_obj, label, *, asynchronous):
        """Send the request. Returns ``(arrived, answered)``.

        ``arrived`` is whether LAS X confirmed receiving it (a request sent
        without waiting counts as arrived). ``answered`` lists what the driver
        said to LAS X dialog boxes that opened while it was sending.
        """
        fired_at = time.monotonic()
        if asynchronous:
            lasx_dialogs.answer_warnings()
            with lasx_dialogs.watching():
                api_obj.UpdateAsync()
            arrived = True
        else:
            arrived = deliver(api_obj, label=label)
        answered = [note.message for note in lasx_dialogs.answered_since(fired_at)]
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
