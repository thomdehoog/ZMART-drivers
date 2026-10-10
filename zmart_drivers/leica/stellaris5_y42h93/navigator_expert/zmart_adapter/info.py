"""Build the live, workflow-facing setup information for the Leica adapter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def output_root(handle: Any, save_source_root: Callable[[], Path]) -> Path:
    """Return the workflow root, discovered beside native AutoSave when omitted."""
    root = handle.connection.get("output_root")
    if root:
        return Path(root).expanduser().resolve()
    try:
        path = save_source_root().parent / "ZMART-microscopy"
    except Exception as exc:
        raise RuntimeError(
            "output_root is not set and could not be discovered from LAS X native AutoSave"
        ) from exc
    path.mkdir(parents=True, exist_ok=True)
    handle.connection["output_root"] = str(path)
    return path
