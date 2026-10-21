"""Which ways of sending a change this microscope has, and in what order they are tried.

Today there is one: the CAM API (``transport.py``). A second route, sending the
same commands straight to LAS X's hardware server, can be added to ``ROUTES`` and
put in front of ``WRITE_ROUTES``; the CAM API then stays behind it as the
fallback, used when the direct route could not send anything. A route that has
sent a command is never followed by another for the same command.

Every command profile sends through :data:`ROUTED`, which reads this policy for each
command, so changing ``WRITE_ROUTES`` takes effect on the next one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from ....dispatcher.transport_chain import TransportChain
from . import transport as _transport

#: Every way of sending that exists, by name.
ROUTES = {"cam": _transport.LASX}

#: The routes tried for a change, best first. Each name must be in ``ROUTES``.
WRITE_ROUTES = ("cam",)


def write_routes():
    """The routes to try now, as ``[(name, transport), ...]``, best first."""
    unknown = [name for name in WRITE_ROUTES if name not in ROUTES]
    if unknown:
        raise ValueError(
            f"WRITE_ROUTES names {unknown}, but the routes that exist are {sorted(ROUTES)}"
        )
    return [(name, ROUTES[name]) for name in WRITE_ROUTES]


#: The transport every command profile sends through.
ROUTED = TransportChain(write_routes)
