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
the jobs (the active one and the ones there are) this way; Thom added what
LAS X is set to now (scan status and mode, zoom, scan speed, format,
averaging, pinhole, objective, field of view, z-stack), read-only.
:data:`READS` names them all; the direct reader reads nothing else, and
never writes.

What LAS X is set to now is the current setting of the selected job: the
tree holds one set of scanning parameters, the one LAS X scans with, where
the CAM API's job settings are per job. On the simulator (2026-10-13) every
one that the log's job settings for the selected job also carry agreed
with them; the CAM API could not be compared, it refused connections then.

Every function raises :class:`HardwareServerUnavailable` when the hardware
server cannot be reached or does not answer, rather than returning anything
that is not a fresh reading.
"""

from __future__ import annotations

import math

from .hardware_server import HardwareServerUnavailable
from .state_tree import (
    SCAN_STATES,
    STAGE_PATHS,
    Z_USE_MODE,
    children,
    job_sequence,
    node_at,
    node_value,
    read,
    remembered,
    remembered_child,
    still_named,
    z_drive_node,
)

#: What the direct reader reads.
READS = (
    "xy",
    "z",
    "selected_job",
    "jobs",
    "scan_status",
    "scan_mode",
    "zoom",
    "scan_speed",
    "format",
    "averaging",
    "pinhole",
    "objective",
    "fov",
    "z_stack",
)

SCANNING = ("Confocal", "ScanningParameter")
HARDWARE = ("Confocal", "HWParameter")
Z_STACK = ("Acquire", "AcquireZStackInfo")
IMAGE_SIZE = ("Acquire", "AcquireImageSizeInfo")


def settings(what, paths):
    """The values of the nodes at *paths* (a dict of name -> path), read in one go."""

    def reading(nodes):
        return {name: node_value(nodes, node_at(nodes, path)) for name, path in paths.items()}

    return read(what, reading)


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


def get_scan_status() -> str:
    """The scan status, named as :func:`api_reader.get_scan_status` names it (``eScanIdle`` ...).

    ``Acquire/AcquisitionState``; a state the reader does not know is
    ``Unknown``, which the routed readers count as no answer, as the CAM
    API's.
    """
    state = settings("the scan status", {"state": ("Acquire", "AcquisitionState")})["state"]
    return SCAN_STATES[state] if isinstance(state, int) and 0 <= state < len(SCAN_STATES) else "Unknown"


def get_scan_mode() -> str:
    """The scan mode (``xyz``, ``xyt`` ...), as the job settings' ``scanMode``."""
    return settings("the scan mode", {"mode": ("Acquire", "ScanDimensionList", "ScanModeName")})["mode"]


def get_zoom() -> dict:
    """The zoom with its range, as the job settings' ``zoom``: ``current``, ``min``, ``max``."""
    return settings(
        "the zoom",
        {
            "current": (*SCANNING, "Zoom"),
            "min": (*SCANNING, "ZoomMin"),
            "max": (*SCANNING, "ZoomMax"),
        },
    )


def get_scan_speed() -> dict:
    """The scan speed, as the job settings' ``scanSpeed``: ``value``, ``unit``, ``isResonant``."""
    return settings(
        "the scan speed",
        {
            "value": (*SCANNING, "Speed"),
            "unit": (*SCANNING, "SpeedUnit"),
            "isResonant": (*SCANNING, "IsResonantConfocalScanner"),
        },
    )


def get_format() -> str:
    """The image format in pixels, as the job settings' ``format`` (``"512 x 512"``)."""
    return settings("the format", {"format": (*SCANNING, "CurrentFormat")})["format"]


def get_averaging() -> dict:
    """Line and frame averaging and accumulation, named as the job settings' active setting names them."""
    return settings(
        "the averaging",
        {
            "lineAverage": (*SCANNING, "LineAverage"),
            "lineAccumulation": (*SCANNING, "LineAccu"),
            "frameAverage": (*SCANNING, "FrameAverage"),
            "frameAccumulation": (*SCANNING, "FrameAccumulation"),
        },
    )


def get_pinhole() -> dict:
    """The pinhole: ``airy`` (as the job settings' ``pinholeAiry``: ``value``, ``min``, ``max``) and ``metres``."""
    found = settings(
        "the pinhole",
        {
            "value": (*HARDWARE, "PinholeAU"),
            "min": (*HARDWARE, "PinholeAUMin"),
            "max": (*HARDWARE, "PinholeAUMax"),
            "metres": (*HARDWARE, "Pinhole"),
        },
    )
    metres_open = metres(found.pop("metres"), "Pinhole")
    return {"airy": found, "metres": metres_open}


def get_objective() -> dict:
    """The objective in the light path, as the job settings' ``objective``.

    ``Microscope/SlotPosition`` is its slot, counted from 0 like the job
    settings' ``slotIndex``; ``Microscope/ObjectiveList`` holds one
    ``ObjectiveInfo`` per slot, in slot order. Raises
    :class:`HardwareServerUnavailable` for a slot the list has no objective for.
    """

    def reading(nodes):
        slot = node_value(nodes, node_at(nodes, ("Microscope", "SlotPosition")))
        info = objective_node(nodes, slot)
        return {
            key: node_value(nodes, remembered_child(nodes, info, field, (*OBJECTIVES, slot, field)))
            for key, field in OBJECTIVE_FIELDS.items()
        } | {"slotIndex": slot}

    return read("the objective", reading)


OBJECTIVES = ("Microscope", "ObjectiveList")

#: The job settings' ``objective`` keys, and the ``ObjectiveInfo`` node each is read from.
OBJECTIVE_FIELDS = {
    "name": "Name",
    "magnification": "Magnification",
    "numericalAperture": "NumericalAperture",
    "immersion": "Immersion",
    "objectiveNumber": "OrderNumber",
    "isMotCorr": "IsMotCorrApplicable",
}


def objective_node(nodes, slot):
    """The ``ObjectiveInfo`` node of *slot*, remembered while it still is one."""
    known = remembered.get((*OBJECTIVES, slot))
    if known is not None and still_named(nodes, known, "ObjectiveInfo"):
        return known
    infos = [
        c
        for c in children(nodes, node_at(nodes, OBJECTIVES))
        if nodes.GetNode(c)[0] == "ObjectiveInfo"
    ]
    if not isinstance(slot, int) or not 0 <= slot < len(infos):
        raise HardwareServerUnavailable(
            f"the state tree has no objective in slot {slot} ({len(infos)} listed)"
        )
    remembered[(*OBJECTIVES, slot)] = infos[slot]
    return infos[slot]


def get_fov() -> tuple[float, float]:
    """The scan field's width and height in metres, as :func:`api_reader.get_fov` gives them."""
    size = settings(
        "the field of view",
        {"x": (*IMAGE_SIZE, "ImageSizeX"), "y": (*IMAGE_SIZE, "ImageSizeY")},
    )
    return metres(size["x"], "ImageSizeX"), metres(size["y"], "ImageSizeY")


def get_z_stack() -> dict | None:
    """The z-stack, as the job settings' raw ``stack`` in micrometres; None without one (one section).

    ``begin``, ``end``, ``stepSize``, ``size``, ``sections`` and ``mode``,
    the drive that carries it (``Acquire/ZStackUseMode``, coded like the
    drives' ``ZUseMode``: 1 z-galvo, 2 z-wide). Only the case without a
    stack has been seen on the simulator so far.
    """
    found = settings(
        "the z-stack",
        {
            "begin": (*Z_STACK, "BeginValue"),
            "end": (*Z_STACK, "EndValue"),
            "stepSize": (*Z_STACK, "StepSize"),
            "sections": (*Z_STACK, "SectionsCount"),
            "mode": ("Acquire", "ZStackUseMode"),
        },
    )
    if not isinstance(found["sections"], int) or found["sections"] <= 1:
        return None
    drives = {code: drive for drive, code in Z_USE_MODE.items()}
    if found["mode"] not in drives:
        raise HardwareServerUnavailable(f"the state tree names z-stack drive {found['mode']!r}")
    begin = metres(found["begin"], "BeginValue") * 1e6
    end = metres(found["end"], "EndValue") * 1e6
    return {
        "begin": begin,
        "end": end,
        "stepSize": metres(found["stepSize"], "StepSize") * 1e6,
        "size": abs(end - begin),
        "sections": found["sections"],
        "mode": drives[found["mode"]],
    }
