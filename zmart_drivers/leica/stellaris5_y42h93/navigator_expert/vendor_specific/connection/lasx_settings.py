"""How the driver opens the LAS X CAM API: the pacing and where the API is installed.

The connection reads ``LASX_API`` each time it opens, so a test or a
validator can hand in other settings by replacing it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LasxApiProfile:
    """Connection-level LAS X API settings.

    ``DelayInMilliseconds`` is Leica's client-side pacing knob. Keeping it in
    the profile makes the default explicit and keeps hardware-specific API
    timing out of scripts and workflows. ``runtime_root`` is the LAS X-installed
    NavigatorExpert add-in directory that contains the CAM API assemblies.
    """

    delay_ms: int | None = 250
    runtime_root: str = (
        r"C:\Program Files\Leica Microsystems CMS GmbH\LAS X\AddIns\NavigatorExpert"
    )


LASX_API = LasxApiProfile()
