"""How a change reaches NIS-Elements through the bridge, as the dispatcher's write loops ask for it.

A change is one :class:`Request`: a ``write`` of one NIS function, or a
``snap``, the whole capture in one request. The write loops
(``dispatcher/write.py``) do not know how it travels; they ask this transfer
to fill in the request, clear the last reply, send, and wait for the reply.
The bridge answers each request at once, so "sent" and "answered" are the
same moment, and ``await_reply`` has nothing to wait for. A refusal by NIS is
kept as the client's last reply, for ``profile.check_reply`` to sort into
temporary or permanent; it is not raised here.

When a change fails or stays unconfirmed, ``dialog_warning_since`` asks the
bridge whether NIS opened a dialog meanwhile, and names it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from .client import BridgeUnavailable, NisRefused

log = logging.getLogger(__name__)


class Request:
    """One change about to be sent: ``write`` or ``snap``, the NIS function, and its arguments once filled in."""

    def __init__(self, op: str, function: str) -> None:
        self.op = op
        self.function = function
        self.args: list = []


class BridgeTransport:
    """The way a change is sent to NIS: one request to the bridge, answered at once."""

    def __init__(self) -> None:
        self._client = None

    def fill(self, request, setup_fn):
        """Write the change's values into the request: ``setup_fn`` sets its arguments."""
        setup_fn(request)

    def clear_reply(self, client):
        """Forget the last reply, so that what is read after the send is this change's answer."""
        client.last_reply = None

    def send(self, client, request, description, *, asynchronous):
        """Send the change. ``(arrived, notes)``; a bridge that cannot be reached raises, and the change fails."""
        self._client = client
        try:
            client.send_change(request.op, request.function, request.args)
        except NisRefused as refused:
            log.debug("%s: refused: %s", description, refused)
        return True, []

    def await_reply(self, client):
        """The bridge answers within the request itself, so the reply is there already."""
        return client.last_reply is not None

    def dialog_warning_since(self, command_started_at, description):
        """A dialog NIS opened since the change began, as the bridge's watcher saw it; None when there is none."""
        if self._client is None:
            return None
        try:
            dialogs = self._client.ping().get("dialogs") or []
        except (BridgeUnavailable, NisRefused):
            return None
        fresh = [d for d in dialogs if d.get("t", 0) >= command_started_at]
        if not fresh:
            return None
        newest = fresh[-1]
        return f"{description} | NIS shows a dialog: {newest.get('title')}: {' | '.join(newest.get('texts') or [])}"


BRIDGE = BridgeTransport()
