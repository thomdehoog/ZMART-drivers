"""The named procedures the instrument offers, and how to run one.

``get_procedures`` lists them with a plain description each, so a workflow
can discover what exists. ``run_procedure`` runs one by name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ..driver_setup.origin.set_origin import set_origin
from ..primitives.dll.writes import (
    AUTOFOCUS_MAX_SPEED,
    AUTOFOCUS_RANGE_UM,
    AUTOFOCUS_SPEED,
)
from ..procedures.focus import autofocus, lock_focus
from ..procedures.save_data.save_waiting import save_waiting


def get_procedures(handle) -> dict:
    """The named procedures this instrument offers, each with what it does and the arguments it takes."""
    handle.require_open()
    return {
        "autofocus": {
            "description": "run NIS-Elements' image-based focus sweep around the current focus and stop "
            f"where the picture is sharpest; range_um (default {AUTOFOCUS_RANGE_UM:g}) is the range "
            f"around the current focus, speed (default {AUTOFOCUS_SPEED}, 0 slow and thorough to "
            f"{AUTOFOCUS_MAX_SPEED} fast); needs a sample with contrast in view",
            "args": ["range_um", "speed"],
        },
        "lock_focus": {
            "description": "switch the Perfect Focus System on (and wait for it to lock on the coverslip) "
            "or off; on: True or False (default True); switch it off before a z-stack, or it fights the moves",
            "args": ["on"],
        },
        "save_data": {
            "description": "write the captures taken and not yet saved as OME-Zarr stores; the "
            "controller's acquire does this for its own capture, so run it after a capture taken without saving",
            "args": ["label"],
        },
        "set_origin": {
            "description": "make the current stage position the (0, 0, 0) of the coordinate frame and save "
            "it for every later connection; a setup step, run once with the stage on the chosen spot"
        },
    }


def run_procedure(handle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; report what ran. An unknown name raises ``ValueError``."""
    handle.require_open()
    name = procedure.get("name")
    if name == "autofocus":
        return {
            "ran": dict(procedure),
            **autofocus(handle, procedure.get("range_um"), procedure.get("speed")),
        }
    if name == "lock_focus":
        return {"ran": dict(procedure), **lock_focus(handle, procedure.get("on", True))}
    if name == "save_data":
        return {
            "ran": dict(procedure),
            "saved": save_waiting(handle, procedure.get("label")),
        }
    if name == "set_origin":
        return {"ran": dict(procedure), **set_origin(handle)}
    raise ValueError(
        f"unknown procedure {name!r}; the procedures are {sorted(get_procedures(handle))}"
    )
