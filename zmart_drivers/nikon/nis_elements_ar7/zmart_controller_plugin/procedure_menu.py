"""The named procedures this driver offers, and how to run one.

``get_procedures`` lists them, each with a plain description, so that a
workflow can find out what exists before it asks. ``run_procedure`` runs one
by its name.

There is one: ``save_data``. ``acquire`` saves every picture as soon as it is
taken, so this is only needed when a saving failed (a full disk, say) and the
picture was kept in the output folder's ``.staging`` folder.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path

from ..procedures.save_data.save import save_staged


def get_procedures(handle) -> dict:
    """The named procedures this driver offers, each with a description and the arguments it takes."""
    handle.require_open()
    return {
        "save_data": {
            "description": "save, as OME-Zarr, the pictures that were taken but could not be saved at the time "
            "(they wait in the output folder's .staging folder); acquire saves every picture itself, so this "
            "is only needed after a saving failed, once its cause is fixed",
            "args": ["output_folder"],
        },
    }


def run_procedure(handle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures` and report what it did; ``ValueError`` for an unknown name."""
    handle.require_open()
    name = procedure.get("name")
    if name == "save_data":
        folder = procedure.get("output_folder")
        root = Path(folder).expanduser().resolve() if folder else handle.output_root()
        return {"ran": dict(procedure), **save_staged(root)}
    raise ValueError(
        f"unknown procedure {name!r}; the procedures are {sorted(get_procedures(handle))}"
    )
