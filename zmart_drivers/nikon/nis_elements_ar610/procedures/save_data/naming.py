"""How a capture's files and folders are named, and where in the output folder they go.

A capture is named by its stem: the acquisition type and the position label
the workflow gave, made safe for a file name. Its store is
``data/<stem>.ome.zarr`` and its log ``log/<stem>.log.json``, under the
output folder. The pictures NIS-Elements writes wait in ``.staging/<session>``
until they are saved, and that folder is emptied as they are.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

EPOCH = 1767225600  # 2026-01-01 00:00:00 UTC
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_UNSAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_-]")
#: The longest stem a capture is named by, leaving room for the folders it
#: is saved in on a Windows computer that keeps the old 260-character limit.
STEM_LIMIT = 80


def run_hash(start_time: float | None = None) -> str:
    """Six sortable base-36 characters for a moment: the session's short name."""
    value = int((time.time() if start_time is None else start_time) - EPOCH)
    if value < 0:
        raise ValueError(f"start_time is before the convention's epoch {EPOCH}")
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return ("".join(reversed(digits)) or "0").rjust(6, "0")


@dataclass(frozen=True)
class Naming:
    """The name of one capture's files: the acquisition type and the position label, made safe."""

    stem: str

    def __post_init__(self) -> None:
        if not isinstance(self.stem, str) or not self.stem:
            raise ValueError("the stem must be a non-empty text")
        if len(self.stem) > STEM_LIMIT:
            raise ValueError(
                f"the stem is longer than {STEM_LIMIT} characters: {self.stem!r}"
            )
        object.__setattr__(self, "stem", _UNSAFE_STEM_RE.sub("_", self.stem))


def store_name(naming: Naming) -> str:
    """The capture's store: ``<stem>.ome.zarr``."""
    return f"{naming.stem}.ome.zarr"


def free_stem(output_root: Path | str, stem: str) -> str:
    """``stem``, or ``stem_001``, ``stem_002``, ...: the first no earlier capture in this folder used."""
    taken = data_dir(output_root)
    candidate, number = stem, 0
    while (taken / store_name(Naming(stem=candidate))).exists():
        number += 1
        candidate = f"{stem}_{number:03d}"
    return candidate


def data_dir(output_root: Path | str) -> Path:
    """Where the stores go: ``data``, leaving room beside it for what is made from them."""
    return Path(output_root) / "data"


def log_dir(output_root: Path | str) -> Path:
    """``log``: what the driver was asked for each capture, and what it did."""
    return Path(output_root) / "log"


def staging_dir(output_root: Path | str, session_hash6: str) -> Path:
    """Where NIS-Elements writes a capture's TIFF until it is saved: ``.staging/<session>``."""
    return Path(output_root) / ".staging" / session_hash6


def write_log(output_root: Path | str, naming: Naming, entry: dict) -> Path:
    """Write *entry* as the capture's log, whole, and answer its path."""
    path = log_dir(output_root) / f"{naming.stem}.log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.writing")
    writing.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    os.replace(writing, path)
    return path
