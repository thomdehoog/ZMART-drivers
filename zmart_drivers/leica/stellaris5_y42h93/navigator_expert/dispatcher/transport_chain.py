"""Several ways of sending one command, tried in order.

A microscope may be reached more than one way: through its vendor interface,
and also directly. The send loop asks for one transport. This class is that one
transport, and behind it it holds an ordered list: the first route that can send
is used, and the next one only when the first could not.

"Could not" has a narrow meaning, so that a command is never done twice. A
route raises :class:`TransportUnavailable` only when nothing has left yet (the
connection was not there, the route has no way to say this command). Anything
else, such as a send that went out but was never acknowledged, is not a reason to
try another route: the command may already have been carried out, and sending it
again is the send loop's decision, made under the command's own rules (an
acquisition is never sent again).

The routes and their order are the microscope's choice, handed in as a function
so that a change of policy takes effect on the next command.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class TransportUnavailable(RuntimeError):
    """A route could not send this command, and nothing has left yet, so another route may try."""


class TransportChain:
    """One transport made of an ordered list of routes; the first that can send does.

    *routes* is a function that returns the routes as ``[(name, transport), ...]``,
    best first. It is called for every command.
    """

    def __init__(self, routes):
        self._routes = routes
        self.carried_by = None  # the name of the route that carried the last command; None if none did

    def fill(self, request, setup_fn):
        """Write the command's values into the request, as the preferred route does.

        All routes share one request, so it is filled once, before any route sends.
        """
        routes = self._routes()
        if not routes:
            raise TransportUnavailable("no route is configured")
        routes[0][1].fill(request, setup_fn)

    def clear_reply(self, client):
        """Nothing to do yet: each route clears its own reply just before it sends."""

    def send(self, client, request, label, *, asynchronous):
        """Send through the first route that can; say so when a later one had to.

        Returns ``(arrived, answered)`` like a single transport. Raises
        :class:`TransportUnavailable` when no route could send.
        """
        self.carried_by = None
        skipped = []
        for name, transport in self._routes():
            try:
                transport.clear_reply(client)
                arrived, answered = transport.send(
                    client, request, label, asynchronous=asynchronous
                )
            except TransportUnavailable as why:
                log.warning("%s: the %s route could not send: %s", label, name, why)
                skipped.append(f"the {name} route could not send ({why})")
                continue
            self._active = transport
            self.carried_by = name
            if skipped:
                answered = [
                    f"{label} | {'; '.join(skipped)}; sent through the {name} route instead",
                    *answered,
                ]
            return arrived, answered
        raise TransportUnavailable(
            f"{label} | no route could send: {'; '.join(skipped) or 'none is configured'}"
        )

    def await_reply(self, client):
        """Wait on the route that carried the command."""
        return self._active.await_reply(client)

    def dialog_warning_since(self, command_started_at, description):
        """Ask the route that carried the command whether a dialog is in the way."""
        active = getattr(self, "_active", None)
        if active is None:
            return None
        return active.dialog_warning_since(command_started_at, description)
