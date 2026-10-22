"""Persist acquired LAS X exports into the workflow layout.

Public workflow:

    acq = acquire(client, job)
    saved = save(client, acq, output_root, naming)

``save`` owns persistence only. The pixels are collected from LAS X's Data
Container cache as LAS X writes them (``lasx_data_container``); LAS X's
AutoSave is not used. OME checks live in ``ome``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...driver_setup.orientation import Orientation
from . import files as _files
from . import materialize as _materialize
from . import ome_zarr
from zmart_drivers import metadata_convention as convention
from .ome_canonical import VENDOR
from .files import DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S, DEFAULT_EXPORT_COMPLETION_TIMEOUT_S
from .collect import collect_lasx_data_container
from .naming import (
    Naming,
    build_state_name,
    data_dir,
    metadata_dir,
    projections_dir,
    state_dir,
)
from .product import AcquisitionResult, ExportedAcquisition
from .saved_acquisition import SavedAcquisition

log = logging.getLogger(__name__)

#: What ``save`` writes a capture as: one OME-Zarr image (``ome_zarr``).
FORMAT = "ome-zarr"

#: What ``save`` projects from a capture with more than one plane, into
#: ``<acquisition>/projections``: its max over z, or nothing.
PROJECTIONS = ("max", "none")


def save(
    client: Any,
    acq: AcquisitionResult,
    output_root: str | Path,
    naming: Naming,
    *,
    lineage: dict | None = None,
    notes: convention.Notes | None = None,
    orientation: Orientation | None = None,
    export_completion_timeout_s: float = DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    export_completion_poll_interval_s: float = (
        DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S
    ),
    cache_root: str | Path | None = None,
    placement: Callable[[list[int]], Callable[[int], dict]] | None = None,
    projection: str | None = None,
) -> SavedAcquisition:
    """Persist the files produced for *acq* into *output_root*.

    The capture is collected from LAS X's Data Container cache as a
    writer-agnostic ``ExportedAcquisition``, which this function persists.
    *notes* are what the store says besides its pixels and the OME fields
    (layers 3 and 4 of ZMART-controller's store agreement): the controller
    state, the capture record, the driver's own facts. The store always
    says the turn applied to its pixels and holds LAS X's description of
    the image, byte for byte. By default, every plane is reoriented
    losslessly with this microscope's active orientation as it is written.
    Pass ``Orientation()`` explicitly only when raw camera pixels are required,
    as in the orientation measurement itself. *cache_root* names the Data
    Container cache folder when it is not the one the registry gives.

    Saving never touches LAS X's cache. The saved capture names the picture it
    was read from (``release_target``), so that the ``clear_cache`` procedure can
    have LAS X let go of it later.

    The capture becomes one OME-Zarr image store. *placement*, handed the
    planes' z numbers, answers where on the sample each was taken, and places
    the store there. ``SavedAcquisition.read_plane`` reads a saved plane back
    by the plane number the capture gave it.

    *projection* is one of ``PROJECTIONS`` and defaults to the profile's
    (``IMAGE_SAVE.projection``). A capture with more than one plane then
    also gets its max projection, in the same format, in ``projections``;
    a single plane is its own projection and gets none, so nothing is
    written twice.
    """
    from .save_settings import IMAGE_SAVE

    projection = IMAGE_SAVE.projection if projection is None else projection
    if projection not in PROJECTIONS:
        raise ValueError(
            f"unknown projection {projection!r}; the projections are {PROJECTIONS}"
        )
    if orientation is None:
        from ...driver_setup.orientation import rig_orientation

        orientation = (
            rig_orientation() if IMAGE_SAVE.apply_orientation else Orientation()
        )
    exported = collect_lasx_data_container(
        client,
        acq,
        cache_root=cache_root,
        timeout_s=export_completion_timeout_s,
        poll_interval_s=export_completion_poll_interval_s,
    )
    exported = replace(
        exported, metadata=on_the_canvas(exported.metadata, exported, placement)
    )
    try:
        saved = _persist_store(
            exported,
            Path(output_root),
            naming,
            lineage=lineage,
            notes=notes,
            orientation=orientation,
            placement=placement,
        )
    except FileNotFoundError as why:
        say_when_the_path_is_too_long(why)
        raise
    if projection != "none" and exported.metadata.size_z > 1:
        saved = replace(
            saved,
            projection_paths=_persist_projection(
                exported,
                Path(output_root),
                naming,
                orientation=orientation,
                placement=placement,
                notes=notes,
                projected_from=next(iter(saved.image_paths.values())),
                vendor_files=saved.vendor_metadata_paths,
            ),
        )
    return replace(saved, release_target=exported.release_target)


#: The longest file path Windows allows unless "long paths" are switched on, less a margin
#: for the characters its own checks add. It is Windows' 260, counting the drive and the end.
WINDOWS_PATH_LIMIT = 259


def say_when_the_path_is_too_long(
    error: FileNotFoundError, on_windows: bool = os.name == "nt"
) -> None:
    """Raise a plain explanation when *error* is Windows refusing a path that is too long.

    Windows answers a path longer than 259 characters with "file not found", which does
    not say what is wrong. While a capture is written, the store keeps temporary files
    whose paths are longer than the finished ones, so a folder that works for short
    labels can fail for longer ones. Does nothing when the path is not long.
    """
    path = str(error.filename or "")
    if not on_windows or len(path) < WINDOWS_PATH_LIMIT - 10:
        return
    raise RuntimeError(
        f"the capture could not be saved because a file path is {len(path)} characters long, "
        f"and Windows on this computer allows at most {WINDOWS_PATH_LIMIT} (long paths are "
        "switched off). Either save into a shorter folder, use a shorter acquisition type "
        "and position label, or switch long paths on in Windows (the LongPathsEnabled setting). "
        f"The path was: {path}"
    ) from error


def _persist_projection(
    exported: ExportedAcquisition,
    output_root: Path,
    naming: Naming,
    *,
    orientation: Orientation,
    placement,
    notes: convention.Notes | None,
    projected_from: Path,
    vendor_files,
) -> tuple[Path, ...]:
    """The capture's max projection in ``projections``, as an OME-Zarr image.

    Its capture record says which store it was made from, and how.
    """
    indices = [index for position in exported.positions for index in position.planes]
    where = (
        placement(sorted({index.z for index in indices}))
        if placement is not None
        else None
    )
    projected = ome_zarr.max_projection(
        ome_zarr.gather(exported, orientation=orientation, where=where)
    )
    store = projections_dir(output_root) / f"{naming.stem}.ome.zarr"
    record = dict((notes.capture_record if notes else None) or {})
    record["derived_from"] = {
        "store": Path(os.path.relpath(projected_from, store)).as_posix(),
        "operation": "max over z",
    }
    told = _the_stores_notes(
        replace(notes or convention.Notes(), capture_record=record),
        vendor_files,
        orientation,
    )
    return (ome_zarr.write_store(projected, store, notes=told),)


def on_the_canvas(metadata, exported: ExportedAcquisition, placement) -> object:
    """Every plane's position as its canvas position, the frame the workflow drives in.

    *placement*, handed the planes' z numbers, answers where on the canvas
    each was taken (the middle of the frame in x and y). What the
    microscope's own drives read stays beside it, in each plane's
    actuators. Without a placement no position is claimed at all: the
    microscope's stage numbers are in another frame, and a file that
    called them its position would put the picture somewhere else.
    """
    indices = [index for position in exported.positions for index in position.planes]
    where = (
        placement(sorted({index.z for index in indices}))
        if placement is not None
        else None
    )
    planes = {}
    for index, plane in metadata.planes.items():
        at = where(index.z) if where is not None else {}
        planes[index] = replace(
            plane,
            position_x_um=at.get("x_um"),
            position_y_um=at.get("y_um"),
            position_z_um=at.get("z_um"),
        )
    return replace(metadata, planes=planes)


def _persist_store(
    exported: ExportedAcquisition,
    output_root: Path,
    naming: Naming,
    *,
    lineage: dict | None,
    notes: convention.Notes | None,
    orientation: Orientation,
    placement: Callable[[list[int]], Callable[[int], dict]] | None,
) -> SavedAcquisition:
    """The capture as one OME-Zarr image in ``data``, beside the vendor's metadata and the state."""
    vendor_records = _persist_vendor_metadata(exported, output_root, naming)
    indices = [index for position in exported.positions for index in position.planes]
    where = (
        placement(sorted({index.z for index in indices}))
        if placement is not None
        else None
    )
    store = data_dir(output_root) / f"{naming.stem}.ome.zarr"
    if where is None:
        raise ValueError(
            "an OME-Zarr store is placed by its canvas position, and none was given "
            "(save's placement); without it the image would lie at an invented place"
        )
    capture = ome_zarr.gather(exported, orientation=orientation, where=where)
    vendor_files = [output_root / record["path"] for record in vendor_records]
    ome_zarr.write_store(
        capture,
        store,
        notes=_the_stores_notes(notes or convention.Notes(), vendor_files, orientation),
    )
    printed = _print_state(notes, output_root, naming)

    summary_path = output_root / "summary.json"
    summary = _load_summary(summary_path)
    _upsert_summary_record(
        summary,
        {
            "naming": _naming_to_dict(naming),
            "image_path": _rel_posix(store, output_root),
            "format": "ome-zarr",
            "source": _rel_posix(exported.source_dir, exported.source_root),
            "source_exporter": exported.source_exporter,
            "canonical_metadata": True,
            "vendor_metadata": vendor_records,
            "physical_size_um": _physical_size_record(exported.metadata, orientation),
            "lineage": lineage,
        },
    )
    _write_json_atomic(summary_path, summary)
    return SavedAcquisition(
        image_paths={index: store for index in sorted(indices)},
        naming=naming,
        vendor_metadata_paths=tuple(
            output_root / record["path"] for record in vendor_records
        ),
        state_paths=(printed,) if printed is not None else (),
        read_plane=lambda index: ome_zarr.read_plane(
            store, capture.store_position[index]
        ),
    )


def _the_stores_notes(
    notes: convention.Notes, vendor_files, orientation: Orientation
) -> convention.Notes:
    """*notes* with what every store says: the turn applied to its pixels, and LAS X's description.

    *vendor_files* are this capture's own descriptions, as saved beside it.
    """
    files = [Path(path) for path in vendor_files]
    return replace(
        notes,
        vendor=VENDOR,
        pixels_to_position=orientation.image_to_stage,
        vendor_descriptions=tuple(path.read_text(encoding="utf-8") for path in files),
        # What LAS X calls the image is only in its description's file name.
        image_description=", ".join(path.name.split(".")[0] for path in files) or None,
    )


def _print_state(
    notes: convention.Notes | None, output_root: Path, naming: Naming
) -> Path | None:
    """Write one acquisition's state beside its images, once.

    The state is already embedded in every plane's OME-XML, but reading it
    there costs opening a picture and parsing annotations. Printed in
    ``data/metadata/ZMART_state`` it can simply be read. One file per acquisition -- the
    planes of a capture share the state it was captured under -- so the write
    is skipped once it exists rather than repeated per plane.
    """

    if notes is None:
        return None
    destination = state_dir(output_root) / build_state_name(naming)
    if not destination.exists():
        _write_json_atomic(destination, notes.as_json())
    return destination


def _persist_vendor_metadata(
    exported: ExportedAcquisition,
    output_root: Path,
    naming: Naming,
) -> list[dict]:
    if not exported.vendor_metadata_sources:
        return []
    # The vendor's own account of the capture is metadata like ZMART's own, so
    # it sits under the same roof rather than beside `data` -- what is beside
    # `data` is what was made from the pixels afterwards, and this was not.
    vendor_dir = (
        metadata_dir(output_root) / "vendor" / _safe_component(exported.source_exporter)
    )
    vendor_dir.mkdir(parents=True, exist_ok=True)

    records = []
    used_names: set[str] = set()
    for i, source in enumerate(exported.vendor_metadata_sources):
        name = _unique_vendor_name(_safe_component(source.name), used_names, i)
        dest = vendor_dir / name
        _materialize.save_vendor_metadata_atomic(source, dest)
        records.append(
            {
                "path": _rel_posix(dest, output_root),
                "sha256": _sha256(dest),
                "source": (
                    _rel_posix(source.path, exported.source_root)
                    if source.path is not None
                    else None
                ),
            }
        )
    return records


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _materialize._with_tmp_suffix(path)
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _load_summary(summary_path: Path) -> dict:
    """Existing summary content, or a fresh structure when absent/corrupt."""
    if summary_path.is_file():
        try:
            with summary_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            # An externally corrupted summary must not abort a save whose
            # images are already written; rebuild it from this save on.
            log.warning(
                "summary.json unreadable, starting fresh (%s): %s", summary_path, e
            )
    return {"acquisitions": []}


def _upsert_summary_record(data: dict, record: dict) -> None:
    """Insert *record* into the in-memory summary, replacing by image_path."""
    acqs = data.setdefault("acquisitions", [])
    new_path = record.get("image_path")
    for i, existing in enumerate(acqs):
        if new_path is not None and existing.get("image_path") == new_path:
            acqs[i] = record
            break
    else:
        acqs.append(record)


def _rel_posix(p: Path, base: Path) -> str:
    return _files._relative_posix(p, base, fallback_to_str=True)


def _naming_to_dict(n: Naming) -> dict:
    return {
        "stem": n.stem,
        "t": n.t,
        "c": n.c,
        "z": n.z,
    }


def _physical_size_record(metadata, orientation: Orientation) -> dict:
    """The stored pixel size: x and y swap when the orientation turns the image a quarter."""
    x, y = metadata.physical_size_x_um, metadata.physical_size_y_um
    if orientation.swaps_axes:
        x, y = y, x
    return {"x": x, "y": y, "z": metadata.physical_size_z_um, "unit": "um"}


def _safe_component(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in {".", "-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("._")
    return cleaned or "metadata"


def _unique_vendor_name(name: str, used: set[str], index: int) -> str:
    if name not in used:
        used.add(name)
        return name
    path = Path(name)
    stem = path.stem or "metadata"
    suffix = path.suffix
    candidate = f"{stem}_{index:03d}{suffix}"
    while candidate in used:
        index += 1
        candidate = f"{stem}_{index:03d}{suffix}"
    used.add(candidate)
    return candidate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
