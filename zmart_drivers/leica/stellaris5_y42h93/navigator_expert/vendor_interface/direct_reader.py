"""The direct reader: LAS X's live state, read from its hardware server.

The third reader beside :mod:`api_reader` (the CAM API) and
:mod:`log_reader` (LAS X's log). It reads the hardware server's state tree
(:mod:`state_tree`), which holds what LAS X itself works from, so it is
neither late like the CAM API's answers nor waiting for a log line. Its
functions carry the other readers' names and give answers of the same
shape, so a caller cannot tell which reader answered; the routed readers
(``dispatcher.read``) say so in a reading's ``source``.

Measured on the simulator, 2026-10-13: the stage in 1.1 ms (CAM API 20 ms,
worst 57 ms), a new stage position 3.3-4.2 ms after a move (CAM API
22-58 ms), both z drives in 1.1 ms.

Leica agreed (2026-10-13) to the driver reading the stage, the z drives and
the jobs (the active one and the ones there are) this way. :data:`READS`
names them; the direct reader reads nothing else, and never writes.

Every function raises :class:`HardwareServerUnavailable` when the hardware
server cannot be reached or does not answer, rather than returning anything
that is not a fresh reading.
"""

from __future__ import annotations

import math

from .hardware_server import HardwareServerUnavailable
from .state_tree import (
    STAGE_PATHS,
    Z_USE_MODE,
    job_sequence,
    node_at,
    node_value,
    read,
    z_drive_node,
)

#: What the direct reader reads: what Leica agreed to.
READS = ("xy", "z", "selected_job", "jobs")


def metres(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HardwareServerUnavailable(f"the state tree answered {what} = {value!r}")
    return float(value)


def get_xy() -> dict:
    """The stage position, shaped as :func:`api_reader.get_xy` gives it: metres and micrometres."""

    def reading(nodes):
        x = metres(node_value(nodes, node_at(nodes, STAGE_PATHS["x"])), "Stage/PositionX")
        y = metres(node_value(nodes, node_at(nodes, STAGE_PATHS["y"])), "Stage/PositionY")
        return {"x": x, "y": y, "x_um": x * 1e6, "y_um": y * 1e6}

    return read("the stage position", reading)


def get_jobs() -> list[dict]:
    """Every job, in the sequence's order, shaped as :func:`log_reader.get_jobs` gives them.

    Per job ``Name``, ``ID`` (its live block id, which an experiment load
    changes while the CAM API's job list keeps the old one) and
    ``IsSelected``. The CAM API's list also says ``IsAutofocus`` and
    ``ScanMode``, which the tree's job sequence does not hold.
    """
    sequence = job_sequence()
    ordered = sorted(sequence.jobs.items(), key=lambda item: item[1].element_id)
    return [
        {
            "Name": name,
            "ID": job.block_id,
            "IsSelected": job.element_id == sequence.selected_element,
        }
        for name, job in ordered
    ]


def get_job_by_name(job_name) -> dict | None:
    """One job of :func:`get_jobs` by its name; None when LAS X has no such job."""
    return next((job for job in get_jobs() if job["Name"] == job_name), None)


def get_selected_job() -> dict | None:
    """The selected job, shaped as :func:`log_reader.get_selected_job` gives it.

    ``Name``, ``ID`` (the live block id) and ``IsSelected``; None when LAS X
    has no job selected.
    """
    return next((job for job in get_jobs() if job["IsSelected"]), None)


def read_z_um(drive: str) -> float:
    """Where one z drive truly is, in micrometres; *drive* is ``"z-galvo"`` or ``"z-wide"``.

    The drive's ``CurrentHardwarePosition``, in the same frame as the job
    settings' ``zPosition``, and current also for the drive that carries a
    job's z-stack, whose job settings stop refreshing.
    """
    if drive not in Z_USE_MODE:
        raise ValueError(f"no z drive {drive!r}; the direct reader knows {sorted(Z_USE_MODE)}")

    def reading(nodes):
        return metres(node_value(nodes, z_drive_node(nodes, drive)), drive) * 1e6

    return read(f"where {drive} is", reading)


def read_zwide_um() -> float:
    """Where z-wide is, in micrometres; named as :func:`log_reader.read_zwide_um`."""
    return read_z_um("z-wide")
