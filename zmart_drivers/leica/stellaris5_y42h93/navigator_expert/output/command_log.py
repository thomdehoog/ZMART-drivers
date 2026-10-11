"""The log of one capture: what the driver was asked, and what it did.

One JSON file a capture, ``<acquisition>/log/<folder>_<hash>_<label>.log.json``,
beside ``data``: the settings asked for and the ones used, each step the
driver took (selecting the job, taking up backlash, the capture itself) with
what LAS X answered, what was sent but never confirmed, when the capture
started and finished, where every plane was taken, and which files were
saved. The pictures and their metadata say what was captured; this says how.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .naming import Naming, log_dir

__all__ = ["write"]


def write(output_root: Path | str, naming: Naming, entry: dict) -> Path:
    """Write *entry* as the capture's log, whole, and return its path."""
    path = log_dir(output_root, naming.folder) / f"{naming.folder}_{naming.hash6}_{naming.position_label}.log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.writing")
    writing.write_text(json.dumps(entry, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(writing, path)
    return path
