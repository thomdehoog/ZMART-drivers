"""The change the ``macro`` class sends: the camera exposure.

``Camera_ExposureSet`` is not exported by the DLL; the bridge reaches it by
name through NIS's Python, which works because only a value goes in. Reading
the exposure back takes the macro route (``reads.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ..dll.writes import Expect, Write

#: How close the exposure read back must come, in milliseconds. The camera
#: applies whole milliseconds on the Ti2 simulator, so a request of 20.4 ms
#: reads back as 20 ms and still counts.
EXPOSURE_TOLERANCE_MS = 1.0


def exposure_within(exposure_ms: float, tolerance_ms: float):
    """The camera reading shows an exposure within ``tolerance_ms`` of the target."""

    def check(camera) -> bool:
        try:
            return abs(float(camera["exposure_ms"]) - exposure_ms) <= tolerance_ms
        except (KeyError, TypeError, ValueError):
            return False

    return check


def set_exposure(exposure_ms: float, *, tolerance_ms: float | None = None) -> Write:
    """Set the camera exposure to *exposure_ms* and read it back from the camera report.

    Setting the same exposure again is harmless, so an unconfirmed change is
    sent again between the windows.
    """
    tolerance = EXPOSURE_TOLERANCE_MS if tolerance_ms is None else tolerance_ms

    def fill(args):
        args.update(exposure_ms=float(exposure_ms))

    return Write(
        command="set_exposure",
        values={"exposure_ms": exposure_ms},
        request="set_exposure",
        fill=fill,
        description=f"exposure to {exposure_ms:g} ms",
        confirm=Expect("camera", exposure_within(exposure_ms, tolerance)),
    )
