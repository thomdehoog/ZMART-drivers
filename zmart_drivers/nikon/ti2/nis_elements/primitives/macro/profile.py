"""The ``macro`` class's profile: its readings, what counts as an answer, and its slower pace.

The dispatcher is handed one profile, ``dll/profile.py``, which brings this
one's table into its own. What this class says for itself is how often it may
be asked: a camera report costs NIS-Elements about a tenth of a second on its
main thread, the thread that also runs every other request, so the read loop
asks it again only every ``POLL_S``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
import sys

from . import reads as macro_reads

#: How long to wait before asking a macro reading again, within a window. One
#: camera report takes about 0.1 s inside NIS (the bridge's README, Ti2
#: simulator, NIS 6.10); asking more often would keep NIS's main thread busy.
POLL_S = 0.25


def poll_s() -> float:
    """The pace, read at call time so a test can shrink it."""
    return sys.modules[__name__].POLL_S


def camera_present(value) -> bool:
    """A camera report with its size, pixel and exposure as finite numbers."""
    if not isinstance(value, dict):
        return False
    try:
        return all(
            math.isfinite(float(value[key]))
            for key in ("width_px", "height_px", "pixel_um", "exposure_ms")
        )
    except (KeyError, TypeError, ValueError):
        return False


#: The readings of this class, and what counts as an answer to each.
READS = macro_reads.READS
SUCCESS = {"camera": camera_present}
