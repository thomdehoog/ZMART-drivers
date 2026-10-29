"""Saving a capture: ZEN's CZI file becomes one OME-Zarr store in the run's folder, and its planes are described.

ZEN writes every acquisition as one CZI file in its image output folder on
the ZEN computer. Every ZMART driver saves a capture as one OME-Zarr store
(the ZMART metadata convention), so that the ZMART Viewer and ZMART Analysis
read every microscope's captures the same way. Saving a capture therefore
means:

1. reading the CZI's planes and ZEN's description of them;
2. writing them as ``data/<name>.ome.zarr`` in the run's folder, turned onto
   the stage's axes and placed where they were taken, with the controller's
   state, the capture record and ZEN's own description inside the store
   (``ome_zarr.py`` says what the store holds);
3. for a z-stack, writing the brightest pixel over z as a second store in
   ``projections/``, a quick overview of the stack;
4. moving ZEN's CZI into ``data/metadata/vendor/``, so nothing ZEN wrote is
   lost and ZEN's own image folder does not fill up (``KEEP_CZI``);
5. writing ZMART's account of the capture as plain text beside it, in
   ``data/metadata/ZMART_state/<name>.json``.

The CZI is only moved once the store has been written and its pixels read
back, so a capture that cannot be saved is never lost: it stays in ZEN's
folder and the session keeps it for another try.

When the driver runs on another computer than ZEN, ZEN's image folder must
be reachable here, under the path ZEN reports or under the one the
connection's ``zen_images`` names. A file that cannot be found is a failed
save, with where ZEN left it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers import metadata_convention as convention

from . import ome_zarr
from . import planes as _planes
from .naming import Naming
from .product import SavedAcquisition

log = logging.getLogger(__name__)

#: How long to wait for ZEN to finish writing the file after its call answered, in seconds.
FILE_WAIT_S = 30.0
#: How long the file must keep the same size to count as finished, in seconds.
FILE_SETTLE_S = 0.5

#: The name of the vendor's software, as the record names it.
VENDOR = ome_zarr.VENDOR

#: Whether ZEN's own CZI is kept, in ``data/metadata/vendor/``, once the
#: store is written. The store holds every pixel and ZEN's whole description,
#: so it can be set to False to save disk space; ZEN's copy is then deleted.
KEEP_CZI = True

#: What is projected from a capture with more than one plane, into
#: ``projections/``: its maximum over z (``"max"``), or nothing (``"none"``).
PROJECTION = "max"


def where_zen_put_it(
    zen_folder: str, output_name: str, seen_here: str | Path | None
) -> Path:
    """The CZI's path as this computer sees it: ZEN's folder, or the folder ``zen_images`` names in its place."""
    folder = Path(seen_here) if seen_here else Path(zen_folder)
    return folder / f"{output_name}.czi"


def wait_for_file(
    path: Path, *, timeout_s: float | None = None, settle_s: float | None = None
) -> bool:
    """Whether *path* exists and has stopped growing within *timeout_s* (``FILE_WAIT_S`` when None)."""
    timeout_s = FILE_WAIT_S if timeout_s is None else timeout_s
    settle_s = FILE_SETTLE_S if settle_s is None else settle_s
    deadline = time.monotonic() + timeout_s
    last_size, last_change = None, time.monotonic()
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size != last_size:
                last_size, last_change = size, time.monotonic()
            elif size > 0 and time.monotonic() - last_change >= settle_s:
                return True
        time.sleep(0.05)
    return False


def move_file(source: Path, destination: Path) -> Path:
    """Move *source* to *destination*, never over an existing file: a rename on the same disk, a copy otherwise."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(
            f"{destination} exists already; a capture never overwrites another"
        )
    try:
        os.replace(source, destination)
    except OSError:
        shutil.copy2(source, destination)
        try:
            source.unlink()
        except OSError as why:
            log.warning(
                "the CZI was copied, but ZEN's copy could not be removed: %s", why
            )
    return destination


def save(handle, pending, *, controller_version: str | None = None) -> SavedAcquisition:
    """Save one pending capture as an OME-Zarr store, keep ZEN's CZI beside it, and describe the planes.

    *pending* is the ``PendingCapture`` ``acquire`` left in the session.
    Raises ``RuntimeError`` when the file ZEN wrote cannot be found or read
    here, and ``FileExistsError`` when the name is taken after all. Until
    the store is written, ZEN's file is left where it is.
    """
    acq = pending.acq
    source = where_zen_put_it(pending.zen_folder, acq.output_name, handle.zen_images)
    if not wait_for_file(source):
        raise RuntimeError(
            f"ZEN says it wrote {acq.output_name}.czi, but {source} is not there. ZEN's image folder is "
            f"{pending.zen_folder!r}; when the driver runs on another computer, give the connection "
            "zen_images, the path under which this computer sees that folder"
        )
    naming: Naming = pending.naming
    folder = Path(pending.output_root)
    store_path = naming.store(folder)
    if store_path.exists():
        raise FileExistsError(
            f"{store_path} exists already; a capture never overwrites another"
        )

    shape = _planes.read_czi_shape(source)
    czi = ome_zarr.read_czi(source)
    _channels, heights, _time_points = _dimensions(handle, pending, shape)
    matrix = (
        handle.orientation.image_to_stage
        if handle.orientation is not None
        else ((1, 0), (0, 1))
    )
    capture = ome_zarr.gather(
        czi,
        image_to_stage=matrix,
        heights_um=heights,
        centre_um=(pending.at["x"], pending.at["y"]),
        readings=_stage_readings(pending),
        raw_z_um=[_raw_focus(pending, height) for height in heights],
        # ZEN's description of the objective in place: its name, magnification and NA.
        objective=(pending.state.get("vendor") or {}).get("objective"),
    )
    if shape and shape.get("pixel_size_um") and shape.get("width_px"):
        # The camera's own numbers, before the turn: what state() reports.
        handle.last_frame = {
            "image_size_px": [int(shape["width_px"]), int(shape["height_px"])],
            "pixel_size_um": [float(v) for v in shape["pixel_size_um"]],
            "from": f"the capture {naming.stem}",
            "objective": pending.at.get("objective"),
        }

    record = capture_record(pending, acq, controller_version)
    notes = convention.Notes(
        controller_state=pending.state.get("controller_state"),
        capture_record=record,
        pixels_to_position=matrix,
        vendor=VENDOR,
        vendor_facts={
            **(pending.state.get("vendor") or {}),
            "czi": {
                "written_by_zen_as": f"{acq.output_name}.czi",
                "zen_folder": pending.zen_folder,
                "kept_as": _relative(naming.czi(folder), folder) if KEEP_CZI else None,
                "shape": shape,
            },
            "unconfirmed": list(pending.unconfirmed),
        },
        vendor_descriptions=(czi["description"],) if czi["description"] else (),
        image_description=acq.output_name,
    )
    store = ome_zarr.write_store(capture, store_path, notes=notes)

    projection = None
    if PROJECTION == "max" and capture.volume.shape[2] > 1:
        projection = ome_zarr.write_store(
            ome_zarr.max_projection(capture),
            naming.projection(folder),
            notes=replace(
                notes,
                capture_record={
                    **record,
                    "derived_from": {
                        "store": _relative(store, naming.projection(folder).parent),
                        "operation": "max over z",
                    },
                },
            ),
        )

    # The store is written and checked: only now does ZEN's file leave its folder.
    kept = None
    if KEEP_CZI:
        kept = move_file(source, naming.czi(folder))
    else:
        try:
            source.unlink()
        except OSError as why:
            log.warning("ZEN's copy %s could not be removed: %s", source, why)

    state = naming.state(folder)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {**notes.as_json(), "store": _relative(store, folder)},
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    files = [str(path) for path in (store, projection, kept, state) if path is not None]
    return SavedAcquisition(
        position_label=pending.label,
        acquisition_type=pending.acquisition_type,
        files=files,
        planes=_planes.describe_planes(capture, store),
        store=store,
        czi=kept,
        state=state,
        projection=projection,
        record=record,
    )


def _stage_readings(pending) -> dict[str, float]:
    """What the stage's own drives read at the capture, keyed ``<axis>/<actuator>`` as the convention keeps them."""
    readings = {}
    for axis in ("x", "y"):
        found = ((pending.state.get("xyz") or {}).get(axis) or {}).get(
            "actuator_readings"
        ) or {}
        for actuator, value in found.items():
            try:
                readings[f"{axis}/{actuator}"] = float(value)
            except (TypeError, ValueError):
                continue
    return readings


def _raw_focus(pending, height_um: float) -> float:
    """ZEN's own focus reading for a slice at *height_um* in the frame: the frame's z, put back."""
    return (
        float(height_um)
        + float(pending.origin.get("z_um") or 0.0)
        + float(pending.objective_translation_um[2])
    )


def _relative(path: Path, start: Path) -> str:
    """*path* from *start*, with forward slashes, so a record stays true when the run is moved."""
    return Path(os.path.relpath(path, start)).as_posix()


def _dimensions(
    handle, pending, shape: dict | None
) -> tuple[list[str], list[float], int]:
    """The channels, the slice heights (frame, CZI order) and the time points of a capture."""
    stack = pending.zstack if not pending.acq.snap else None
    if shape:
        channels = shape["channels"] or [f"channel {i}" for i in range(shape["size_c"])]
        channels = (
            channels[: shape["size_c"]]
            if len(channels) >= shape["size_c"]
            else channels
            + [f"channel {i}" for i in range(len(channels), shape["size_c"])]
        )
        slices, time_points = shape["size_z"], shape["size_t"]
    else:
        channels = list(pending.channels) or ["channel 0"]
        slices = int(stack["slices"]) if stack and stack.get("is_zstack") else 1
        status = pending.status or {}
        time_points = (
            max(1, int(status.get("time_points_count") or 1))
            if not pending.acq.snap
            else 1
        )
    origin, delta = pending.origin, pending.objective_translation_um

    def to_frame(z_raw_um: float) -> float:
        return z_raw_um - origin["z_um"] - delta[2]

    heights = _planes.slice_heights_um(stack, slices, pending.at["z"], to_frame)
    return channels, heights, time_points


def capture_record(pending, acq, controller_version: str | None) -> dict:
    """The capture in the controller's words: the keys of ``metadata_convention.CAPTURE_RECORD_KEYS``."""
    return {
        "position_label": pending.label,
        "acquisition_type": pending.acquisition_type,
        "acquisition_settings": {
            "asked": dict(pending.asked),
            "used": dict(pending.settings),
        },
        "started": datetime.fromtimestamp(acq.started_at, tz=UTC).isoformat(
            timespec="seconds"
        ),
        "finished": datetime.fromtimestamp(acq.finished_at, tz=UTC).isoformat(
            timespec="seconds"
        ),
        "xyz": pending.state.get("xyz"),
        "driver": pending.state.get("driver"),
        "controller": {"version": controller_version},
    }


def save_waiting(handle, label: str | None = None) -> list[dict]:
    """Save the captures waiting in the session (only *label* when given); one answer each, in the controller's words.

    A capture that cannot be saved raises, and the ones before it stay saved;
    the failed one stays in the session for another try.
    """
    handle.require_open()
    chosen = [
        pending for pending in handle.unsaved if label is None or pending.label == label
    ]
    if label is not None and not chosen:
        raise ValueError(f"no capture labelled {label!r} is waiting to be saved")
    saved = []
    for pending in chosen:
        done = save(handle, pending, controller_version=controller_version())
        handle.unsaved.remove(pending)
        saved.append(done.as_dict())
    return saved


def controller_version() -> str | None:
    """The ZMART Controller's version when it is installed, for the record; None without it."""
    try:
        from zmart_controller import __version__
    except Exception:  # noqa: BLE001 -- the controller is an extra
        return None
    return __version__
