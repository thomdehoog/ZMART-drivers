"""Microscope acquisition only.

Public workflow entry point:

    acq = acquire(client, job)

``acquire`` intentionally does not know how files are saved. It records
when a named LAS X job was acquired and returns that context for
``acquisition.save.save``.
"""

from __future__ import annotations

import time
from typing import Any

from ..vendor_interface.product import AcquisitionResult
from . import changes as _commands

__all__ = ["AcquisitionResult", "acquire"]


def acquire(
    client: Any,
    job: str,
    *,
    poll_interval=None,
    poll_timeout=None,
    heartbeat_interval=None,
    start_timeout=None,
) -> AcquisitionResult:
    """Acquire a named LAS X job and return save-agnostic context.

    This is microscope-only: no file detection, OME validation, TIFF
    loading, copying, or summary updates happen here.
    """
    started_at = time.time()
    result = _commands.acquire(
        client,
        job,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
        heartbeat_interval=heartbeat_interval,
        start_timeout=start_timeout,
    )
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(f"acquire failed: {result}")
    return AcquisitionResult(
        job=job,
        started_at=started_at,
        finished_at=finished_at,
        command_result=result,
    )
