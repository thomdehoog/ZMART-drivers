"""Write a picture read from LAS X's cache as OME-Zarr: one store per field, with its projection.

The pixels are collected from LAS X's Data Container cache as LAS X writes
them (``collect.py``); LAS X's AutoSave is not used. A capture becomes one
store; a tile scan becomes one store per field, each placed where its tile
was taken. OME checks live in ``ome_canonical`` and ``ome_zarr``.

Saving never touches LAS X's cache. Every saved field names the picture it
was read from (``release_target``), so that the ``clear_cache`` procedure
can have LAS X let go of it later, once, after every field is saved.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
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

from zmart_drivers import metadata_convention as convention

from ...driver_setup.orientation import Orientation
from . import collect as _files
from . import ome_canonical as _materialize
from . import ome_zarr
from .collect import (
    DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
    DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    collect_picture,
)
from .naming import (
    Naming,
    build_state_name,
    data_dir,
    metadata_dir,
    projections_dir,
    state_dir,
)
from .ome_canonical import VENDOR
from .product import AcquisitionResult, ExportedAcquisition, SavedAcquisition

log = logging.getLogger(__name__)

#: What ``save`` writes a capture as: one OME-Zarr image (``ome_zarr``).
FORMAT = "ome-zarr"

#: What ``save`` projects from a capture with more than one plane, into
#: ``<acquisition>/projections``: its max over z, or nothing.
PROJECTIONS = ("max", "none")


def save(
    acq: AcquisitionResult,
    output_root: str | Path,
    namings: list[Naming],
    *,
    place: Callable[[int, tuple[float, float, float] | None, list[int]], Callable[[int], dict]] | None,
    lineage: dict | None = None,
    notes: convention.Notes | None = None,
    orientation: Orientation | None = None,
    export_completion_timeout_s: float = DEFAULT_EXPORT_COMPLETION_TIMEOUT_S,
    export_completion_poll_interval_s: float = DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S,
    cache_root: str | Path | None = None,
    projection: str | None = None,
) -> list[SavedAcquisition]:
    """Persist the picture *acq* took into *output_root*: one OME-Zarr store per field, named by *namings*.

    The picture is collected from LAS X's Data Container cache
    (``collect_picture``) and each field is written as one store. *place*
    answers, for a field (its number, its stage position in micrometres as
    LAS X took the tile, or None for a capture, and the planes' z numbers),
    where on the canvas each plane was taken: an OME-Zarr store is always
    placed. *notes* are what the store says besides its pixels and the OME
    fields: the controller state, the capture record, the driver's own
    facts. By default, every plane is reoriented losslessly with this
    microscope's active orientation as it is written; pass
    ``Orientation()`` explicitly only when raw camera pixels are required.
    *cache_root* names the Data Container cache folder when it is not the
    one the registry gives.

    *projection* is one of ``PROJECTIONS`` and defaults to the profile's
    (``IMAGE_SAVE.projection``). A field with more than one plane then also
    gets its max projection, in the same format, in ``projections``; a
    single plane is its own projection and gets none.

    Raises ``ValueError`` when *namings* does not name every field.
    """
    from .save_waiting import IMAGE_SAVE

    projection = IMAGE_SAVE.projection if projection is None else projection
    if projection not in PROJECTIONS:
        raise ValueError(f"unknown projection {projection!r}; the projections are {PROJECTIONS}")
    if orientation is None:
        from ...driver_setup.orientation import rig_orientation

        orientation = rig_orientation() if IMAGE_SAVE.apply_orientation else Orientation()
    captures = collect_picture(
        acq,
        cache_root=cache_root,
        timeout_s=export_completion_timeout_s,
        poll_interval_s=export_completion_poll_interval_s,
    )
    if len(namings) != len(captures):
        raise ValueError(f"the picture has {len(captures)} fields, {len(namings)} names were given")

    saved = []
    for field, (exported, naming) in enumerate(zip(captures, namings)):
        first = exported.metadata.planes[min(exported.metadata.planes)]
        stage = (first.position_x_um, first.position_y_um, first.position_z_um)
        placement = None
        if place is not None:
            placement = lambda depths, field=field, stage=stage: place(
                field, stage if acq.picture == "tile_scan" else None, depths
            )
        exported = replace(exported, metadata=on_the_canvas(exported.metadata, exported, placement))
        try:
            stored = _persist_store(
                exported, Path(output_root), naming,
                lineage=lineage, notes=notes, orientation=orientation, placement=placement,
            )
        except FileNotFoundError as why:
            say_when_the_path_is_too_long(why)
            raise
        if projection != "none" and exported.metadata.size_z > 1:
            stored = replace(
                stored,
                projection_paths=_persist_projection(
                    exported, Path(output_root), naming,
                    orientation=orientation, placement=placement, notes=notes,
                    projected_from=next(iter(stored.image_paths.values())),
                    vendor_files=stored.vendor_metadata_paths,
                ),
            )
        saved.append(replace(stored, release_target=exported.release_target))
    return saved


#: The longest file path Windows allows unless "long paths" are switched on, less a margin
#: for the characters its own checks add. It is Windows' 260, counting the drive and the end.
WINDOWS_PATH_LIMIT = 259


def say_when_the_path_is_too_long(error: FileNotFoundError, on_windows: bool = os.name == "nt") -> None:
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
    where = placement(sorted({index.z for index in indices}))
    projected = ome_zarr.max_projection(ome_zarr.gather(exported, orientation=orientation, where=where))
    store = projections_dir(output_root) / f"{naming.stem}.ome.zarr"
    record = dict((notes.capture_record if notes else None) or {})
    record["derived_from"] = {
        "store": Path(os.path.relpath(projected_from, store)).as_posix(),
        "operation": "max over z",
    }
    told = _the_stores_notes(
        replace(notes or convention.Notes(), capture_record=record), vendor_files, orientation
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
    where = placement(sorted({index.z for index in indices})) if placement is not None else None
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
    placement,
) -> SavedAcquisition:
    """The capture as one OME-Zarr image in ``data``, beside the vendor's metadata and the state."""
    vendor_records = _persist_vendor_metadata(exported, output_root, naming)
    indices = [index for position in exported.positions for index in position.planes]
    where = placement(sorted({index.z for index in indices})) if placement is not None else None
    store = data_dir(output_root) / f"{naming.stem}.ome.zarr"
    if where is None:
        raise ValueError(
            "an OME-Zarr store is placed by its canvas position, and none was given "
            "(save's placement); without it the image would lie at an invented place"
        )
    capture = ome_zarr.gather(exported, orientation=orientation, where=where)
    vendor_files = [output_root / record["path"] for record in vendor_records]
    ome_zarr.write_store(
        capture, store, notes=_the_stores_notes(notes or convention.Notes(), vendor_files, orientation)
    )
    printed = _print_state(notes, output_root, naming)

    summary_path = output_root / "summary.json"
    summary = _load_summary(summary_path)
    _upsert_summary_record(
        summary,
        {
            "naming": _naming_to_dict(naming),
            "image_path": _rel_posix(store, output_root),
            "format": FORMAT,
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
        vendor_metadata_paths=tuple(output_root / record["path"] for record in vendor_records),
        state_paths=(printed,) if printed is not None else (),
        read_plane=lambda index: ome_zarr.read_plane(store, capture.store_position[index]),
    )


def _the_stores_notes(notes: convention.Notes, vendor_files, orientation: Orientation) -> convention.Notes:
    """*notes* with what every store says: the turn applied to its pixels, and LAS X's description."""
    files = [Path(path) for path in vendor_files]
    return replace(
        notes,
        vendor=VENDOR,
        pixels_to_position=orientation.image_to_stage,
        vendor_descriptions=tuple(path.read_text(encoding="utf-8") for path in files),
        # What LAS X calls the image is only in its description's file name.
        image_description=", ".join(path.name.split(".")[0] for path in files) or None,
    )


def _print_state(notes: convention.Notes | None, output_root: Path, naming: Naming) -> Path | None:
    """Write one acquisition's state beside its images, once.

    The state is already embedded in every plane's OME-XML, but reading it
    there costs opening a picture and parsing annotations. Printed in
    ``data/metadata/ZMART_state`` it can simply be read.
    """
    if notes is None:
        return None
    destination = state_dir(output_root) / build_state_name(naming)
    if not destination.exists():
        _write_json_atomic(destination, notes.as_json())
    return destination


def _persist_vendor_metadata(exported: ExportedAcquisition, output_root: Path, naming: Naming) -> list[dict]:
    if not exported.vendor_metadata_sources:
        return []
    # The vendor's own account of the capture is metadata like ZMART's own, so
    # it sits under the same roof rather than beside `data`.
    vendor_dir = metadata_dir(output_root) / "vendor" / _safe_component(exported.source_exporter)
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
                "source": _rel_posix(source.path, exported.source_root) if source.path is not None else None,
            }
        )
    return records


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _materialize._with_tmp_suffix(path)
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _load_summary(summary_path: Path) -> dict:
    """Existing summary content, or a fresh structure when absent or corrupt."""
    if summary_path.is_file():
        try:
            with summary_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            # A corrupted summary must not abort a save whose images are
            # already written; rebuild it from this save on.
            log.warning("summary.json unreadable, starting fresh (%s): %s", summary_path, e)
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
    return {"stem": n.stem, "t": n.t, "c": n.c, "z": n.z}


def _physical_size_record(metadata, orientation: Orientation) -> dict:
    """The stored pixel size: x and y swap when the orientation turns the image a quarter."""
    x, y = metadata.physical_size_x_um, metadata.physical_size_y_um
    if orientation.swaps_axes:
        x, y = y, x
    return {"x": x, "y": y, "z": metadata.physical_size_z_um, "unit": "um"}


def _safe_component(value: str) -> str:
    out = []
    for ch in value:
        out.append(ch if ch.isalnum() or ch in {".", "-", "_"} else "_")
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


def placed_at(at: dict) -> Callable[[Any, Any, Any], Callable[[int], dict]]:
    """A ``place`` for :func:`save` that puts every plane of every field at the one canvas position *at*.

    For the setup and the hardware checks, which run before there is an
    origin and place a picture by the stage's own numbers.
    """

    def place(field, stage_um, depths):
        return lambda depth: dict(at)

    return place
