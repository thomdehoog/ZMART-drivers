"""The named changes of a session: move the stage, select a channel, set the exposure, change the objective, lock or sweep the focus, take a picture.

Each one checks its arguments, builds its change as plain data
(``primitives/dll/writes.py`` and ``primitives/macro/writes.py``) and hands it
to the dispatcher's ``run()``, which asks the limits once, sends it over the
bridge, and reads it back. Then it adds what the caller needs to the result:
``answered``, what the bridge replied (a move's position as NIS read it
back, a capture's file and pixel size), and for a move its target.

Result contract: ``result["success"]`` means the change was accepted, not
that it took effect. A caller that needs proof checks ``result["confirmed"]``.
The one exception is a capture: :func:`take_picture` raises when it failed,
because a workflow must never go on as if a picture had been taken.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ..dispatcher import dispatch
from ..primitives.dll import profile as dll_profile
from ..primitives.dll import writes
from ..primitives.macro import writes as macro_writes

log = logging.getLogger(__name__)


def _run(client, change, *, max_retries=None) -> dict:
    """Run *change* through the dispatcher and add what the bridge answered, when anything was sent."""
    result = dispatch.run(dll_profile, client, change, max_retries=max_retries)
    sent = result["timing"].get("attempts", 0) > 0
    answered = client.reply if sent else None
    result["answered"] = (
        answered.result if answered is not None and answered.error is None else None
    )
    return result


def move_xyz(client, x_um, y_um, z_um, *, tolerance_um=None, max_retries=None) -> dict:
    """Move the stage and the focus drive to an absolute position, in NIS's own micrometres.

    The result carries ``position``, the target, and ``answered``, where NIS
    read the stage back right after the move; ``confirmed`` says whether the
    driver's own read-back saw it there, within ``tolerance_um``.
    """
    for label, value in (("x_um", x_um), ("y_um", y_um), ("z_um", z_um)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return dispatch.refused(f"{label}={value!r} is not a number", position=None)
    change = writes.move_xyz(
        float(x_um), float(y_um), float(z_um), tolerance_um=tolerance_um
    )
    result = _run(client, change, max_retries=max_retries)
    result["position"] = (
        dict(change.values) if result["timing"].get("attempts", 0) > 0 else None
    )
    return result


def select_optical_configuration(client, name, *, max_retries=None) -> dict:
    """Select the optical configuration *name*; NIS cannot report the selection, so there is no read-back."""
    if not isinstance(name, str) or not name:
        return dispatch.refused(
            f"an optical configuration is named by a text, not {name!r}"
        )
    return _run(
        client, writes.select_optical_configuration(name), max_retries=max_retries
    )


def set_exposure(client, exposure_ms, *, tolerance_ms=None, max_retries=None) -> dict:
    """Set the camera exposure and read it back; ``answered`` holds the value NIS applied (it may round)."""
    if (
        isinstance(exposure_ms, bool)
        or not isinstance(exposure_ms, (int, float))
        or exposure_ms <= 0
    ):
        return dispatch.refused(
            f"the exposure is a positive number of milliseconds, not {exposure_ms!r}"
        )
    return _run(
        client,
        macro_writes.set_exposure(float(exposure_ms), tolerance_ms=tolerance_ms),
        max_retries=max_retries,
    )


def set_objective(client, slot, *, before_fire=None, max_retries=None) -> dict:
    """Turn the nosepiece to *slot* (counted from 1), without moving the stage.

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes ``before_fire``, called with the
    slot just before anything is sent, which may answer a refusal, and then
    nothing is sent.
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
        return dispatch.refused(
            f"a nosepiece slot is a whole number from 1, not {slot!r}"
        )
    if before_fire is not None:
        refusal = before_fire(slot)
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return _run(client, writes.set_objective(slot), max_retries=max_retries)


def set_pfs(client, on, *, max_retries=None) -> dict:
    """Switch the Perfect Focus System on (waiting for it to lock) or off, and read its state back."""
    if not isinstance(on, bool):
        return dispatch.refused(f"the PFS is switched with True or False, not {on!r}")
    return _run(client, writes.set_pfs(on), max_retries=max_retries)


def autofocus(client, range_um=None, speed=None) -> dict:
    """Run NIS's image-based focus sweep once; ``answered`` is the position it ended at."""
    range_um = writes.AUTOFOCUS_RANGE_UM if range_um is None else range_um
    speed = writes.AUTOFOCUS_SPEED if speed is None else speed
    if (
        isinstance(range_um, bool)
        or not isinstance(range_um, (int, float))
        or range_um <= 0
    ):
        return dispatch.refused(
            f"the focus sweep's range is a positive number of micrometres, not {range_um!r}"
        )
    if (
        isinstance(speed, bool)
        or not isinstance(speed, int)
        or not 0 <= speed <= writes.AUTOFOCUS_MAX_SPEED
    ):
        return dispatch.refused(
            f"the focus sweep's speed is a whole number from 0 to {writes.AUTOFOCUS_MAX_SPEED}, not {speed!r}"
        )
    return _run(client, writes.autofocus(float(range_um), speed))


def take_picture(client, path) -> dict:
    """Capture one picture and have NIS save it as a TIFF at *path*; answers ``{"path", "pixel_size_um"}``.

    The capture is sent once and never again. It raises ``RuntimeError``
    when the limits refuse it, the bridge fails it, or NIS reports no file.
    """
    result = _run(client, writes.snap(str(path)))
    if not result["success"] or not result["answered"]:
        raise RuntimeError(f"the capture failed: {result.get('message')}")
    answered = dict(result["answered"])
    answered["result"] = result
    return answered
