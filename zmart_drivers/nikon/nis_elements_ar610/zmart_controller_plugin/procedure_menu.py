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
from ..procedures import changes
from ..procedures import readings as _readers
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
        "safe_state": {
            "description": "switch off everything that sends light to the sample: every lamp and laser "
            "line, every shutter and the spinning disk's shutter; safe to run at any time"
        },
        "escape": {
            "description": "lower the objective away from the sample, for example to change the dish; "
            "run refocus afterwards to bring it back to the focus it left"
        },
        "refocus": {
            "description": "bring the objective back to the focus it had before escape"
        },
        "live": {
            "description": "start or stop NIS-Elements' live view; on: True or False (default True)",
            "args": ["on"],
        },
        "auto_exposure": {
            "description": "let NIS-Elements choose the exposure for what is in view now, and answer it",
        },
        "move_piezo_z": {
            "description": "move the piezo focus drive by distance_um, a small relative step within the "
            "limits; faster and finer than the focus drive, for z-stacks of a few micrometres",
            "args": ["distance_um"],
        },
        "center_piezo_z": {
            "description": "move the piezo focus drive back to the middle of its travel"
        },
        "edit_optical_configuration": {
            "description": "save a new exposure_ms into the optical configuration named by configuration, "
            "so every later selection of it uses that exposure",
            "args": ["configuration", "exposure_ms"],
        },
        "copy_optical_configuration": {
            "description": "save a copy of the optical configuration named by configuration as new_name, "
            "with its own exposure_ms when given; the original stays as it was",
            "args": ["configuration", "new_name", "exposure_ms"],
        },
        "delete_optical_configuration": {
            "description": "delete the optical configuration named by configuration from NIS-Elements; "
            "this cannot be undone from the driver",
            "args": ["configuration"],
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
    client = handle.client
    simple = {
        "safe_state": lambda: changes.safe_state(client),
        "escape": lambda: changes.escape(client, True),
        "refocus": lambda: changes.escape(client, False),
        "live": lambda: changes.live(client, procedure.get("on", True)),
        "auto_exposure": lambda: changes.auto_exposure(client),
        "move_piezo_z": lambda: changes.move_piezo_z(
            client, _needed(procedure, "distance_um")
        ),
        "center_piezo_z": lambda: changes.center_piezo_z(client),
        "edit_optical_configuration": lambda: changes.edit_optical_configuration(
            client,
            _needed(procedure, "configuration"),
            _needed(procedure, "exposure_ms"),
        ),
        "copy_optical_configuration": lambda: changes.copy_optical_configuration(
            client,
            _needed(procedure, "configuration"),
            _needed(procedure, "new_name"),
            procedure.get("exposure_ms"),
        ),
        "delete_optical_configuration": lambda: changes.delete_optical_configuration(
            client, _needed(procedure, "configuration")
        ),
    }
    if name in simple:
        result = _checked(handle, simple[name](), name)
        ran = {"ran": dict(procedure), "confirmed": result.get("confirmed")}
        if name == "auto_exposure":
            camera = _readers.get_camera(client)
            ran["exposure_ms"] = camera["exposure_ms"] if camera else None
        if (
            name == "delete_optical_configuration"
            and handle.channel == procedure["configuration"]
        ):
            handle.channel = None
        return ran
    raise ValueError(
        f"unknown procedure {name!r}; the procedures are {sorted(get_procedures(handle))}"
    )


def _needed(procedure: dict, key: str):
    """The argument *key* of *procedure*; a missing one raises ``ValueError`` naming it."""
    if procedure.get(key) is None:
        raise ValueError(f"the procedure {procedure.get('name')!r} needs {key!r}")
    return procedure[key]


def _checked(handle, result: dict, what: str) -> dict:
    """Raise when the change failed, and note it in the session when it was never confirmed."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if result.get("confirmed") is False:
        handle.note_unconfirmed(what, result)
    return result
