"""The named procedures the instrument offers, and how to run one.

``get_procedures`` lists them with a plain description each, so a workflow
can discover what exists. ``run_procedure`` runs one by name; a name it does
not list is refused.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

PROCEDURES = {
    "software_autofocus": {
        "description": "ZEN's software autofocus, with the focus search set up in the loaded experiment; the focus "
        "drive ends where the picture is sharpest. Optional timeout_s, in seconds",
        "args": ["timeout_s"],
    },
    "find_surface": {
        "description": "the Definite Focus finds the coverslip surface and moves the focus there; only on stands that have one"
    },
    "store_focus": {
        "description": "the Definite Focus remembers the focus where it stands now"
    },
    "recall_focus": {
        "description": "the Definite Focus goes back to the focus it remembered"
    },
    "stop": {"description": "stop whatever ZEN is acquiring"},
    "save_data": {
        "description": "move the captures taken and not yet saved into their folders, with their records; the "
        "controller's acquire does this for its own capture, so run it after captures taken without saving",
        "args": ["label"],
    },
}


def get_procedures(microscope) -> dict:
    """The named procedures this instrument offers."""
    microscope.handle.require_open()
    return {name: dict(spec) for name, spec in PROCEDURES.items()}


def run_procedure(microscope, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; what it answered. A procedure that fails raises."""
    name = procedure.get("name")
    if name == "software_autofocus":
        return microscope.autofocus(timeout_s=procedure.get("timeout_s"))
    if name == "find_surface":
        return microscope.find_surface()
    if name == "store_focus":
        return microscope.store_focus()
    if name == "recall_focus":
        return microscope.recall_focus()
    if name == "stop":
        return microscope.stop()
    if name == "save_data":
        return {"saved": microscope.save(procedure.get("label"))}
    raise ValueError(
        f"unknown procedure {name!r}; the procedures are {sorted(PROCEDURES)}"
    )
