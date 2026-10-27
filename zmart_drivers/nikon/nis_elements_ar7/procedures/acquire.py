"""Taking a picture and saving it: the ``acquire`` step.

There is one way to take a picture, the trigger ``snap``: one picture with
the microscope's settings as they are. Which objective and optical
configuration it is taken with is the state (``set_state``).

The steps, in order:

1. Read where the stage is. When NIS cannot read it, nothing is captured: a
   picture that cannot be placed on the sample is of no use.
2. Refuse when this microscope has recorded the objective in place as not
   calibrated in NIS, before anything is captured.
3. Read the state, for the store's notes.
4. Capture: the bridge's ``snap`` takes the picture into a new document, has
   NIS save it as a TIFF in ``.staging``, and closes that document only.
5. Remember the picture's size and pixel size for this objective and optical
   configuration (``driver_setup/pixel_size``), and read the stage again. A
   difference of more than ``STAGE_CHANGE_NOTE_UM`` (Perfect Focus moving the
   focus, say) is noted.
6. Write the note of the capture beside the TIFF, and convert both to one
   OME-Zarr store (``save_data/save.py``). The TIFF goes only once the store
   is written; when the conversion fails, both stay and the error says where.

Acquiring and saving are one step here, because the picture is off NIS as
soon as the capture answers: there is no cache in NIS to save from later, or
to clear.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/acquire.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..driver_setup import pixel_size
from ..primitives.nis_python.reads import calibration_from
from . import changes, readings
from .absolute_coordinate_system import get_xyz, hardware_snapshot
from .save_data import naming, save
from .state import get_state

log = logging.getLogger(__name__)

#: This driver, as the capture record names it.
DRIVER_PACKAGE = __package__.rpartition(".")[0]

#: How far the stage may read differently after the capture before it is noted.
STAGE_CHANGE_NOTE_UM = 1.0


def get_acquisition_settings(handle) -> dict:
    """How a capture is triggered: ``snap``, one picture with the settings as they are."""
    handle.require_open()
    return {"trigger": {"options": ["snap"], "default": "snap", "active": "snap"}}


def resolved_settings(handle, asked: dict | None) -> dict:
    """The acquisition settings asked for, with the active value of each one left out; ``ValueError`` for an unknown one."""
    menu = get_acquisition_settings(handle)
    used = {name: spec["active"] for name, spec in menu.items()}
    for name, value in (asked or {}).items():
        if name not in menu:
            raise ValueError(
                f"unknown acquisition setting {name!r}; the settings are {sorted(menu)}"
            )
        if value not in menu[name]["options"]:
            raise ValueError(
                f"{name} {value!r} is not offered; the options are {menu[name]['options']}"
            )
        used[name] = value
    return used


def acquire(
    handle,
    *,
    position_label: str | None = None,
    acquisition_settings: dict | None = None,
    acquisition_type: str = "acquisition",
    output_folder: str | None = None,
) -> dict:
    """Take one picture, save it as an OME-Zarr store, and answer its record: ``files``, ``planes`` and more.

    *position_label* names the place (the next number of this session, from
    ``000000``, when None). The files are named after *acquisition_type* and
    the label, in *output_folder*, else in this session's output folder.
    Raises ``ValueError`` for a wrong request, and ``RuntimeError`` when the
    stage cannot be read, the objective is not calibrated, or the capture or
    its saving failed.
    """
    handle.require_open()
    used = resolved_settings(handle, acquisition_settings)
    if not isinstance(acquisition_type, str) or not acquisition_type.strip():
        raise ValueError("the acquisition type is a non-empty text, such as 'overview'")
    output_root = (
        Path(output_folder).expanduser().resolve()
        if output_folder is not None
        else handle.output_root()
    )
    if position_label is None:
        position_label = f"{handle.position_counter:06d}"
        handle.position_counter += 1
    stem = naming.free_stem(
        output_root, naming.stem_of(acquisition_type, str(position_label))
    )

    try:
        at = get_xyz(handle)
        at_raw = {
            axis: at[axis]["actuator_readings"]["motoric"] for axis in ("x", "y", "z")
        }
    except RuntimeError as why:
        raise RuntimeError(
            f"the stage cannot be read, so nothing was captured: {why}"
        ) from why
    objective = readings.objective(handle.client)
    configuration = readings.selected_optical_configuration(handle.client)
    known = pixel_size.lookup(objective["name"], configuration)
    if known is not None and not known["calibrated"]:
        raise RuntimeError(
            f"the objective {objective['name']!r} is not calibrated in NIS-Elements, so a picture could not be placed "
            "on the sample; calibrate it in NIS (Calibration), then capture again. Nothing was captured."
        )
    state = get_state(handle)
    noted_before = len(handle.unconfirmed)

    tif = naming.staging_dir(output_root) / f"{stem}.tif"
    snap = changes.take_picture(handle.client, str(tif))
    calibration = calibration_from(
        **{k: snap["calibration"][k] for k in ("objective", "pixel", "aspect", "unit")}
    )
    pixel_size.remember(
        objective["name"],
        configuration,
        width=snap["image"]["width"],
        height=snap["image"]["height"],
        pixel_size_um=calibration["pixel_size_um"],
    )
    try:
        after_raw = {key[0]: value for key, value in hardware_snapshot(handle).items()}
    except RuntimeError as why:
        after_raw = None
        log.warning("the stage could not be read after the capture: %s", why)
    if after_raw is not None and any(
        abs(after_raw[axis] - at_raw[axis]) > STAGE_CHANGE_NOTE_UM for axis in at_raw
    ):
        handle.note_unconfirmed(
            f"acquire {stem}: the stage moved during the capture",
            f"before {at_raw}, after {after_raw} (um); the picture is placed where the stage stood before",
        )

    from .. import __version__

    note = {
        "position_label": str(position_label),
        "acquisition_type": acquisition_type,
        "stem": stem,
        "output_root": str(output_root),
        "asked": dict(acquisition_settings or {}),
        "used": used,
        "at": at,
        "at_raw": at_raw,
        "after_raw": after_raw,
        "origin": dict(handle.origin),
        "objective": objective,
        "optical_configuration": configuration,
        "controller_state": state,
        "snap": snap,
        "orientation": {
            "rotate_deg": handle.orientation.rotate_deg,
            "mirrored": handle.orientation.mirrored,
            "image_to_stage": [list(row) for row in handle.orientation.image_to_stage],
        },
        "driver": {"name": DRIVER_PACKAGE, "version": __version__},
        "nis_version": handle.nis_version,
        "microscope": handle.connection.get("microscope"),
        "session": handle.hash6,
        "unconfirmed": list(handle.unconfirmed[noted_before:]),
    }
    naming.write_json(save.note_path(tif), note)
    try:
        return save.convert(tif)
    except Exception as why:
        raise RuntimeError(
            f"the picture was taken and kept at {tif}, but could not be saved as OME-Zarr: {why}. "
            "Run the save_data procedure to try again once the cause is fixed."
        ) from why
