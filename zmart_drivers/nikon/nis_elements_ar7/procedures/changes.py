"""The named changes of a NIS-Elements session: move the stage, select an optical configuration, turn the nosepiece, capture.

Each one checks its arguments, builds its change as plain data
(``primitives/nis_python/writes.py``) and hands it to the dispatcher's
``run()``, which asks the limits once, sends it, and reads it back. A change
refused before anything was sent answers the same shape, with ``success``
False and the reason.

What ``success`` means: the change was accepted, not that it took effect.
A caller that needs proof checks ``confirmed``. The capture is the one
change that answers with its own proof: :func:`take_picture` raises unless a
new picture was taken and saved.

Two checks come before the dispatcher, because NIS would answer them badly:
``move_z`` is refused while Perfect Focus holds the focus (the move would
fight it, as step 0 of the NIS 7 study noted), and an optical configuration
NIS does not list is refused before NIS is asked (NIS 7 answers such
mistakes in some functions with a dialog that blocks it).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

from ..dispatcher import dispatch
from ..primitives.nis_python import profile, writes
from . import readings


def move_xy(
    client: Any, x_um: float, y_um: float, *, tolerance_um: float | None = None
) -> dict:
    """Move the stage to ``(x_um, y_um)`` in NIS's own stage coordinates, and read it back."""
    return dispatch.run(
        profile, client, writes.move_xy(x_um, y_um, tolerance_um=tolerance_um)
    )


def move_z(client: Any, z_um: float, *, tolerance_um: float | None = None) -> dict:
    """Move the focus drive to ``z_um`` in NIS's own coordinates, and read it back; refused while Perfect Focus is on."""
    focus_lock = readings.reading(client, "pfs")
    if (
        focus_lock is not None
        and focus_lock.error is None
        and focus_lock.value.get("on")
    ):
        return dispatch.refused(
            f"the focus move to {z_um:g} um was refused: Perfect Focus is on ({focus_lock.value['meaning']}) and holds the focus; "
            "switch it off in NIS first",
            logged=True,
        )
    return dispatch.run(profile, client, writes.move_z(z_um, tolerance_um=tolerance_um))


def select_optical_configuration(client: Any, name: str) -> dict:
    """Select the optical configuration *name*; ``ValueError`` when NIS has none of that name."""
    known = readings.optical_configurations(client)
    if name not in known:
        raise ValueError(f"NIS has no optical configuration {name!r}; it has {known}")
    return dispatch.run(profile, client, writes.select_optical_configuration(name))


def set_objective(client: Any, position: int) -> dict:
    """Turn the nosepiece to *position* (counted from 1); ``ValueError`` when no objective sits there."""
    if isinstance(position, bool) or not isinstance(position, int):
        raise ValueError(  # noqa: TRY004 -- the driver answers a wrong request with ValueError
            f"an objective position is a whole number, counted from 1, not {position!r}"
        )
    turret = readings.nosepiece(client)
    if not turret["present"]:
        raise ValueError(
            "NIS reports no motorised nosepiece, so the objective cannot be changed from here"
        )
    if position not in turret["objectives"]:
        raise ValueError(
            f"no objective at position {position}; the objectives are {turret['objectives']}"
        )
    return dispatch.run(profile, client, writes.set_objective(position))


def take_picture(client: Any, path: str) -> dict:
    """Take one picture and have NIS save it as a TIFF at *path*; the bridge's answer.

    The answer holds the picture's size, its bits, its calibration, NIS's
    name for its document, and when it was taken. Raises ``RuntimeError``
    when the picture was refused by the limits, not taken, or not saved; then
    nothing of the operator's in NIS was saved or closed.
    """
    result = dispatch.run(profile, client, writes.capture(path))
    reply = client.last_reply or {}
    if not result.get("success") or not reply.get("ok"):
        raise RuntimeError(f"the capture failed: {result.get('message')}")
    return dict(reply["answer"])
