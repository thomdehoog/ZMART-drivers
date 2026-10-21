"""The named procedures the instrument offers, and how to run one.

``get_procedures`` lists them with a plain description each, so a workflow can discover
what exists. ``run_procedure`` runs one by name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from ..autofocus.autofocus import run_autofocus
from ..clear_cache.clear import clear
from ..save_data.save_captures import save_waiting
from ..absolute_coordinate_system.frame import zero_z_galvo
from ...session.handle import ZmartHandle, job_catalog, require_open


def get_procedures(handle: ZmartHandle) -> dict:
    """The named procedures this instrument offers (discover-then-apply)."""
    require_open(handle)
    _, autofocus = job_catalog(handle)
    return {
        "zero_z_galvo": {
            "description": "park the z-galvo at 0 without changing the focus "
            "(its offset is transferred onto z-wide), freeing the galvo's "
            "full travel for a following z-stack"
        },
        "save_data": {
            "description": "write the captures taken and not yet saved to disk, with their "
            "planes, projections and logs; the controller's acquire does this for its own "
            "capture, so run it after an acquisition taken without saving",
            "args": ["label"],
        },
        "clear_cache": {
            "description": "have LAS X let go of the pictures of the captures saved so far in "
            "this session, freeing the disk its cache fills; acquiring and saving never do "
            "this, so run it when you choose, for example between plates"
        },
        "autofocus": {
            "description": "run a LAS X autofocus job (capture only, nothing saved); "
            "restores the previously selected job and returns the focus readback",
            "args": ["job"],
            "jobs": [j["Name"] for j in autofocus if j.get("Name")],
        },
    }


def run_procedure(handle: ZmartHandle, procedure: dict) -> dict:
    """Run a procedure from :func:`get_procedures`; report what ran."""
    require_open(handle)
    name = procedure.get("name")
    if name == "zero_z_galvo":
        return {"ran": dict(procedure), **zero_z_galvo(handle)}
    if name == "save_data":
        return {
            "ran": dict(procedure),
            "saved": save_waiting(handle, procedure.get("label")),
        }
    if name == "clear_cache":
        return {"ran": dict(procedure), **clear(handle)}
    if name == "autofocus":
        return run_autofocus(handle, procedure)
    raise ValueError(f"unknown procedure {name!r}")
