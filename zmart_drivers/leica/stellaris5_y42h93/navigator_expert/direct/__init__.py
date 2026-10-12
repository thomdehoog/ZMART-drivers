"""The direct route: the driver's commands sent straight to LAS X's hardware server.

Experimental, for comparing with the CAM route; nothing in the driver uses
it. ``route(client)`` returns a client on which every command in
``actions/set.py`` runs unchanged — its own checks, the limits gate, the
idle pre-check, the readback confirmation and the re-fire — while each CAM
command is delivered as the hardware-server element the CAM API itself ends
in (``translations.py``, read from ``lcsCommand.log``). Job settings,
hardware info and stage XY are read from the hardware server too
(``reads.py``); the job list and the scan status still come from the CAM
API. A command without a verified translation is refused.

Rules for adding a command: it has a CAM twin; its element is read from the
log of the CAM twin's own run; it goes through the same limits gate; a move
runs only with the machine's measured limits; no hardware-server property is
ever written.
"""

from .client import DirectRoute, route

__all__ = ["DirectRoute", "route"]
