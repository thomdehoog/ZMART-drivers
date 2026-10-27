"""How a capture's files and folders are named, and where in the output folder each goes.

A capture is named by its *stem*: the acquisition type, then the position
label, such as ``overview_A1``. A character that cannot be in a file name
becomes ``_``. A name already used in the folder gets ``_001``, ``_002``, and
so on, so no capture is ever written over another.

Inside the output folder::

    data/<stem>.ome.zarr        the picture, one OME-Zarr store
    log/<stem>.log.json         what the driver was asked for this capture, and what it did
    .staging/<stem>.tif         NIS's TIFF, while it waits to be converted
    .staging/<stem>.json        the note taken at the capture, beside it

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/save_data/naming.py``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

#: The longest stem. Windows' old limit of 260 characters for a whole path
#: must leave room for the folders the store is saved in and for the files
#: deep inside a Zarr store.
STEM_LIMIT = 80
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def stem_of(acquisition_type: str, position_label: str) -> str:
    """The stem ``<acquisition type>_<position label>``, safe for a file name; ``ValueError`` when too long."""
    stem = _UNSAFE.sub("_", f"{acquisition_type}_{position_label}")
    if len(stem) > STEM_LIMIT:
        raise ValueError(
            f"the name {stem!r} is longer than {STEM_LIMIT} characters; use a shorter acquisition type or label"
        )
    return stem


def data_dir(output_root: str | Path) -> Path:
    return Path(output_root) / "data"


def log_dir(output_root: str | Path) -> Path:
    return Path(output_root) / "log"


def staging_dir(output_root: str | Path) -> Path:
    return Path(output_root) / ".staging"


def store_path(output_root: str | Path, stem: str) -> Path:
    return data_dir(output_root) / f"{stem}.ome.zarr"


def free_stem(output_root: str | Path, stem: str) -> str:
    """*stem*, or ``stem_001``, ``stem_002``, ...: the first that no earlier capture in this folder used."""
    candidate, number = stem, 0
    while (
        store_path(output_root, candidate).exists()
        or (staging_dir(output_root) / f"{candidate}.tif").exists()
    ):
        number += 1
        candidate = f"{stem}_{number:03d}"
    return candidate


def write_json(path: Path, entry: dict) -> Path:
    """Write *entry* as JSON at *path*, whole: under a temporary name first, then renamed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.writing")
    writing.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    os.replace(writing, path)
    return path


def write_log(output_root: str | Path, stem: str, entry: dict) -> Path:
    """Write the capture's log, ``log/<stem>.log.json``; its path."""
    return write_json(log_dir(output_root) / f"{stem}.log.json", entry)
