"""Which ways of sending a change this microscope has, and in what order they are tried.

Two: the direct route (``direct_transport.py``, straight to LAS X's hardware server)
and the CAM API (``transport.py``). The direct route is tried first; the CAM API
carries a command when the direct route could not send it (the hardware server is
not reachable, or the command has no direct translation). A route that has sent a
command is never followed by another for the same command.

The order can be changed without editing code, for a comparison or to switch the
direct route off: set the environment variable ``ZMART_WRITE_ROUTES`` to
``cam`` (the CAM API only), ``direct`` or ``direct,cam`` (the default).

Every command profile sends through :data:`ROUTED`, which reads this policy for each
command.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import os

from ...dispatcher.write import TransportChain
from ..cam_api import transfer as _transport
from .transfer import DIRECT

#: Every way of sending that exists, by name.
ROUTES = {"direct": DIRECT, "cam": _transport.LASX}

#: The routes tried for a change, best first. Each name must be in ``ROUTES``.
WRITE_ROUTES = tuple(
    name.strip()
    for name in os.environ.get("ZMART_WRITE_ROUTES", "direct,cam").split(",")
    if name.strip()
)


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
