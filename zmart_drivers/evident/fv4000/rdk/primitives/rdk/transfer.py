"""How a change reaches FLUOVIEW and how its reply comes back, as the write loops ask for it.

A change is one XML-RPC call. Unlike LAS X, FLUOVIEW answers in the call
itself: the reply, with its ``result`` word, is back before the call returns.
So there is no receipt to wait for and no echo to watch; the transport makes
the call, keeps the reply on the client (``last_reply``), and the profile's
``check_reply`` reads it. The dispatcher's write loops (``dispatcher/write.py``)
ask the transport only for the five things below and never look inside a
request.

A request is a small dictionary, ``{"method": ..., "payload": {...}}``, made by
the profile from a ``Write`` (``writes.py``) once the limits allowed it.

FLUOVIEW in REMOTE mode shows no pop-up dialogs (``Remote.setRemoteMode``, the
manual's section 3.2.2), so there is no dialog to report after a failed send.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from .client import CallInterrupted

log = logging.getLogger(__name__)


class RdkTransport:
    """The way requests are sent to FLUOVIEW, as the dispatcher's write loops ask for it."""

    def fill(self, request, setup_fn):
        """Write the change's values into the request: its ``payload`` dictionary."""
        setup_fn(request["payload"])

    def clear_reply(self, client):
        """Forget the previous reply, so what is read after the send is the new one."""
        client.last_reply = None

    def send(self, client, request, description, *, asynchronous):
        """Make the call. Answers ``(arrived, notes)``: the reply is on the client afterwards.

        A connection that breaks during the call leaves it unknown whether
        FLUOVIEW carried the change out; the call is reported as not
        arrived, with a note, and the write loops decide by the change's own
        rule whether to send it again (a capture never is).
        """
        try:
            client.call(request["method"], request["payload"] or None)
        except CallInterrupted as why:
            log.warning("%s | %s", description, why)
            return False, [f"{description} | {why}"]
        return True, []

    def await_reply(self, client):
        """The reply came back with the call, so it is already there."""
        return client.last_reply is not None

    def dialog_warning_since(self, command_started_at, description):
        """FLUOVIEW in REMOTE mode opens no dialogs, so there is never one to report."""
        return


RDK = RdkTransport()
