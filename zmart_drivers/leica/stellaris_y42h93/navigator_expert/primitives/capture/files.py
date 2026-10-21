"""LAS X source-side file primitives.

This module stays deliberately small. Collection from LAS X's cache lives
in ``lasx_data_container``; persistence and OME checks live in
``save`` / ``ome``.

Source naming (LAS X auto-export)::

    image--L0000--J08--E00--X00--Y00--T0000--Z00--C00.ome.tif
    metadata/image--L0000--J08--E00--T0000.ome.xml
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# How long a file may sit WITHOUT GROWING before the wait gives up on it. A
# file that is still being written is never timed out: every change in size
# restarts this budget, so a large export takes as long as it takes. Only a
# file that stops changing yet never becomes readable (stuck, still locked)
# exhausts it.
DEFAULT_FILE_STABILITY_TIMEOUT_S = 120
# Detection window: how long to wait for native AutoSave to produce ANY output
# (a project directory or OME-TIFF) after the scan completes before concluding
# AutoSave is disabled. Once a project appears the collector waits WITHOUT a
# deadline for the file to flush -- a long/large save is healthy, not a failure.
DEFAULT_EXPORT_COMPLETION_TIMEOUT_S = 60.0
DEFAULT_EXPORT_COMPLETION_POLL_INTERVAL_S = 0.5


# Exports often land on SMB shares whose mtime resolution is coarse (1-2 s)
# and whose clock can drift from the driver host. Without an allowance, a
# genuinely fresh export can be rejected as stale ("No ... files found").
# The residual risk — accepting a stale file written within the allowance
# before the acquire — is bounded by this constant and by
# _detect_from_mtime's refusal of ambiguous multi-candidate matches.
MTIME_SKEW_ALLOWANCE_S = 2.0


def _relative_posix(path: Path, base: Path, *, fallback_to_str: bool) -> str | None:
    """Return *path* relative to *base* as a POSIX string.

    When *path* is not under *base*, fall back to the POSIX form of the
    absolute path (``fallback_to_str=True``) or to ``None``
    (``fallback_to_str=False``).
    """
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/") if fallback_to_str else None


# ---------------------------------------------------------------------------
# File-stability helpers. LAS X writes its files asynchronously and holds
# locks while it does; these wait until a file is complete and released
# before anyone reads it.
# ---------------------------------------------------------------------------


def _is_file_locked(path):
    """Return True if *path* is locked by another process (Windows).

    Opens the file in read+write mode; a ``PermissionError`` means
    another process (typically LAS X) holds an exclusive lock.
    """
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _wait_file_stable(path, timeout, poll_interval=0.5, stable_readings=3):
    """Block until *path* has stable size and is unlocked.

    Requires *stable_readings* consecutive checks where the file
    exists, has non-zero size, the size hasn't changed, and the file
    is not locked.

    ``timeout`` is a no-progress budget, not a total: it restarts every
    time the file's size changes, so a file that is still being written
    is never given up on, however long the export takes. The wait only
    returns False when the file has gone ``timeout`` seconds without
    growing and still is not readable (absent, empty, or locked).
    """
    path = Path(path)
    since_progress = time.perf_counter()
    consecutive = 0
    last_size = -1

    while (time.perf_counter() - since_progress) < timeout:
        try:
            if not path.is_file():
                consecutive = 0
                time.sleep(poll_interval)
                continue

            size = path.stat().st_size
            locked = _is_file_locked(path)

            if size == last_size and size > 0 and not locked:
                consecutive += 1
                if consecutive >= stable_readings:
                    return True
            else:
                consecutive = 0

            if size != last_size:
                since_progress = (
                    time.perf_counter()
                )  # still growing: the budget restarts
            last_size = size
        except OSError:
            consecutive = 0

        time.sleep(poll_interval)

    return False
