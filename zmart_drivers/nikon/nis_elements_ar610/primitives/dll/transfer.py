"""How a change reaches NIS-Elements: one bridge request, answered before the call returns.

The dispatcher's write loops ask a transfer to fill in a request, clear the
reply, send, wait for the reply and report an open dialog. The bridge makes
this simple: a request is one line of JSON over a socket, and the bridge
answers it only after NIS-Elements has carried it out. So ``send`` returns
with the answer already in hand, ``await_reply`` has nothing to wait for, and
the profile reads the answer from ``client.reply`` (:class:`Answered` in
``client.py``). Sending "without a receipt" makes no difference over the
bridge: every request is answered.

A request is a plain dictionary, ``{"op", "args", "timeout_s"}``: the
bridge's request name, its arguments, and how long this driver waits for it.
The same transfer carries the ``macro`` class's changes; the bridge decides
inside NIS which route serves a request.

NIS-Elements can open a blocking dialog of its own (a *Command Interpreter
Error* when a macro call fails on a device that is not there). The bridge
cannot see it, so ``dialog_warning_since`` answers None; what the driver can
tell is in the error the request came back with.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ...dispatcher.write import TransportUnavailable
from .client import Answered, BridgeLost

log = logging.getLogger(__name__)


def request_of(op: str, timeout_s: float | None = None) -> dict:
    """A request the loops can fill in and send: the bridge's *op*, empty arguments, the wait."""
    return {"op": op, "args": {}, "timeout_s": timeout_s}


class BridgeTransfer:
    """What the write loops ask of the way to NIS-Elements, over the bridge."""

    def fill(self, request: dict, setup_fn) -> None:
        """Have the change write its values into the request's arguments."""
        setup_fn(request["args"])

    def clear_reply(self, client) -> None:
        """Forget the last answer, so what is read after the send is this send's."""
        client.reply = None

    def send(self, client, request: dict, description: str, *, asynchronous: bool):
        """Send the request and keep its answer; ``(delivered, notes for the log)``.

        The bridge answers every request, so the answer, or the error, is in
        ``client.reply`` when this returns. A connection that is already
        closed raises :class:`TransportUnavailable`, since nothing has left:
        the loops then fail the change with that reason. A connection that
        drops while the request is out is kept as the reply's error: the
        request may still have run inside NIS-Elements, so the change is not
        sent again on another route; the profile sorts it.
        """
        if client.closed:
            raise TransportUnavailable(
                f"{description} | the connection to NIS-Elements is closed; reconnect() first"
            )
        try:
            result = client.request(
                request["op"], timeout_s=request["timeout_s"], **request["args"]
            )
        except (ValueError, RuntimeError) as why:  # BridgeLost is a RuntimeError too
            client.reply = Answered(error=why)
            if isinstance(why, BridgeLost):
                log.error(
                    "%s | the connection to NIS-Elements dropped: %s", description, why
                )
            return True, []
        client.reply = Answered(result=result)
        return True, []

    def await_reply(self, client) -> bool:
        """The reply arrived with the send; there is nothing to wait for."""
        return client.reply is not None

    def dialog_warning_since(self, started_at, description):
        """The bridge cannot see NIS-Elements' dialogs, so there is nothing to report."""
        return


#: The one transfer every change of this driver goes through.
BRIDGE = BridgeTransfer()
