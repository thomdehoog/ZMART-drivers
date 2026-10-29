"""The named procedures the instrument offers, and how to run one.

``get_procedures`` lists them with a plain description each, so a workflow can
discover what exists. ``run_procedure`` runs one by name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ..procedures import acquire as _acquire
from ..procedures.absolute_coordinate_system import zero_z_galvo
from ..procedures.clear_cache.clear import clear
from ..procedures.save_data.save_waiting import save_waiting


def get_procedures(handle) -> dict:
    """The named procedures this instrument offers (discover, then run)."""
    handle.require_open()
    return {
        "zero_z_galvo": {
            "description": "park the z-galvo at 0 without changing the focus "
            "(its offset is transferred onto z-wide), freeing the galvo's "
            "full travel for a following z-stack"
        },
        "tile_scan": {
            "description": "take one picture of several fields at the given positions "
            "(x, y pairs in micrometres from the origin, at the current focus) with the "
            "plain Navigator's Preview, and keep it for save_data as one store per field",
            "args": ["positions", "position_label", "acquisition_type", "output_folder"],
        },
        "save_data": {
            "description": "write the pictures taken and not yet saved to disk, with their "
            "planes, projections and logs; the controller's acquire does this for its own "
            "capture, so run it after a tile scan, or an acquisition taken without saving",
            "args": ["label"],
        },
        "clear_cache": {
            "description": "have LAS X let go of the pictures of the captures saved so far in "
            "this session, freeing the disk its cache fills; acquiring and saving never do "
            "this, so run it when you choose, for example between plates"
        },
    }


def run_procedure(handle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; report what ran."""
    handle.require_open()
    name = procedure.get("name")
    if name == "zero_z_galvo":
        return {"ran": dict(procedure), **zero_z_galvo(handle)}
    if name == "tile_scan":
        return {
            "ran": dict(procedure),
            **_acquire.tile_scan(
                handle,
                procedure.get("positions") or [],
                position_label=procedure.get("position_label"),
                acquisition_type=procedure.get("acquisition_type") or "tile_scan",
                output_folder=procedure.get("output_folder"),
            ),
        }
    if name == "save_data":
        return {"ran": dict(procedure), "saved": save_waiting(handle, procedure.get("label"))}
    if name == "clear_cache":
        return {"ran": dict(procedure), **clear(handle)}
    raise ValueError(f"unknown procedure {name!r}")
