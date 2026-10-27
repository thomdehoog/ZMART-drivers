"""The named procedures the instrument offers, and how to run one.

``get_procedures`` lists them with a plain description each, so a workflow can
discover what exists. ``run_procedure`` runs one by name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations


def get_procedures(handle) -> dict:
    """The named procedures this instrument offers."""
    handle.require_open()
    return {
        "save_data": {
            "description": "read the captures taken and not yet saved back from FLUOVIEW and write them to disk as OME-Zarr, "
            "with their projections and logs; the controller's acquire does this for its own capture, so run it after a capture taken without saving",
            "args": ["label"],
        },
        "stop_scan": {
            "description": "stop the imaging that is running in FLUOVIEW, and wait until it is idle"
        },
        "live_view": {
            "description": "start or stop FLUOVIEW's live, repeating scan, as its Live button does; 'on' is true or false (default true)",
            "args": ["on"],
        },
        "move_piezo": {
            "description": "move the piezo to 'z_um' micrometres from FLUOVIEW's own Z origin, within the limits; the focus motor is not moved",
            "args": ["z_um"],
        },
    }


def run_procedure(microscope, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures` on the *microscope*; raises for an unknown name."""
    microscope.handle.require_open()
    name = procedure.get("name")
    if name == "save_data":
        return {
            "ran": dict(procedure),
            "saved": microscope.save(procedure.get("label")),
        }
    if name == "stop_scan":
        return {"ran": dict(procedure), **microscope.stop()}
    if name == "live_view":
        return {
            "ran": dict(procedure),
            **microscope.live(bool(procedure.get("on", True))),
        }
    if name == "move_piezo":
        if "z_um" not in procedure:
            raise ValueError("move_piezo needs z_um, the piezo position in micrometres")
        return {
            "ran": dict(procedure),
            **microscope.move_piezo(float(procedure["z_um"])),
        }
    raise ValueError(f"unknown procedure {name!r}")
