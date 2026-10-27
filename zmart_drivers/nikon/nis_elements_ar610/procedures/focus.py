"""Finding and holding the focus: NIS's image-based sweep, and the Perfect Focus System.

Two ways to focus, both NIS-Elements' own:

- :func:`autofocus` runs NIS's image-based focus sweep, which moves the focus
  drive through a range around where it stands and stops where the picture
  was sharpest. It needs a sample with contrast in view.
- :func:`lock_focus` switches the Perfect Focus System (PFS) on, which holds
  the focus on the coverslip by its reflection and waits until it has
  locked, or off. A locked PFS fights every later focus move, so a workflow
  that takes z-stacks switches it off first.

Both answer the result of the change, with the position afterwards in the
frame ``get_xyz`` uses.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from . import absolute_coordinate_system as _frame
from . import changes


def autofocus(handle, range_um: float | None = None, speed: int | None = None) -> dict:
    """Run NIS's image-based focus sweep once; raise ``RuntimeError`` when it found no focus."""
    handle.require_open()
    result = changes.autofocus(handle.client, range_um, speed)
    if not result.get("success"):
        raise RuntimeError(f"the focus sweep failed: {result.get('message')}")
    return {"result": result, "position": _frame.get_xyz(handle)}


def lock_focus(handle, on: bool = True) -> dict:
    """Switch the Perfect Focus System on (and wait for it to lock) or off; raise when NIS refused."""
    handle.require_open()
    result = changes.set_pfs(handle.client, bool(on))
    if not result.get("success"):
        raise RuntimeError(
            f"switching the PFS {'on' if on else 'off'} failed: {result.get('message')}"
        )
    if not result.get("confirmed"):
        handle.note_unconfirmed(f"lock_focus({on})", result)
    return {
        "result": result,
        "pfs": result.get("answered"),
        "position": _frame.get_xyz(handle),
    }
