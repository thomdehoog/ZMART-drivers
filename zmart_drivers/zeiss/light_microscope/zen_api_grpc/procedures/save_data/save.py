"""Saving a capture: move the CZI ZEN wrote into the run's folder, write the record beside it, describe the planes.

ZEN writes every acquisition as one CZI file in its image output folder on
the ZEN computer. The driver does not rewrite the pixels: the CZI is ZEISS's
own format, and every ZEISS tool and most others read it. Saving a capture
means moving that file into the folder the workflow asked for (renaming it
on the same disk when it can, copying otherwise), writing the capture's
record beside it as ``<name>.zmart.json`` (the controller's state, the
capture record, the frame of the pixels and the driver's own facts, in the
words of the ZMART metadata convention), and describing the planes.

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
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers import metadata_convention as convention

from . import planes as _planes
from .naming import Naming
from .product import SavedAcquisition

log = logging.getLogger(__name__)

#: How long to wait for ZEN to finish writing the file after its call answered, in seconds.
FILE_WAIT_S = 30.0
#: How long the file must keep the same size to count as finished, in seconds.
FILE_SETTLE_S = 0.5

#: The name of the vendor's software, as the record names it.
VENDOR = "zeiss-zen"


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
    """Save one pending capture: find ZEN's CZI, move it, write the record, describe the planes.

    *pending* is the ``PendingCapture`` ``acquire`` left in the session.
    Raises ``RuntimeError`` when the file ZEN wrote cannot be found here, and
    ``FileExistsError`` when the name is taken after all.
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
    czi = move_file(source, naming.czi(folder))

    shape = _planes.read_czi_shape(czi)
    channels, heights, time_points = _dimensions(handle, pending, shape)
    planes = _planes.describe_planes(
        path=czi,
        channels=channels,
        heights_um=heights,
        time_points=time_points,
        x_um=pending.at["x"],
        y_um=pending.at["y"],
    )
    if shape and shape.get("pixel_size_um") and shape.get("width_px"):
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
        pixels_to_position=handle.orientation.image_to_stage
        if handle.orientation is not None
        else ((1, 0), (0, 1)),
        vendor=VENDOR,
        vendor_facts={
            **(pending.state.get("vendor") or {}),
            "czi": {
                "name": czi.name,
                "shape": shape,
                "written_by_zen_as": acq.output_name,
                "zen_folder": pending.zen_folder,
            },
            "unconfirmed": list(pending.unconfirmed),
        },
    )
    sidecar = naming.sidecar(folder)
    sidecar.write_text(
        json.dumps(notes.as_json(), indent=2, default=str) + "\n", encoding="utf-8"
    )
    return SavedAcquisition(
        position_label=pending.label,
        acquisition_type=pending.acquisition_type,
        files=[str(czi), str(sidecar)],
        planes=planes,
        czi=czi,
        sidecar=sidecar,
        record=record,
    )


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
