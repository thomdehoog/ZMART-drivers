"""Where the stage and the z drives are, and which job is selected, from LAS X's state tree.

The hardware server keeps LAS X's state as a tree of named nodes, reached
through ``ServerNodes``: ``GetNode(id)`` gives a node's name, parent and
children (the root is node 1), ``RegisterNode(id, out value)`` its value.
Under ``Acquire/ATL/CurrentSequenceList`` LAS X keeps the job sequence: the
sequence's own block id, the selected element, and per job
(``SequenceBlockInfo``) its ``Name``, ``BlockID`` and ``ElementID``: what
selecting a job by its element needs.

The ids here are live: after an experiment load LAS X gave the jobs new ids
(HiRes 145 -> 152) while the CAM API's own job list kept the old ones.

The stage is ``Stage/PositionX`` and ``Stage/PositionY``, each z drive an
``Acquire/ZDriveList/ZDrive`` named by its ``ZUseMode`` (1 z-galvo, 2 z-wide)
with ``CurrentHardwarePosition``, where the drive truly is; all in metres.

Walking the tree by name costs about 270 ms; a node read by its id about
0.5 ms. So the ids found are remembered, and each use first checks that the
node still carries the name it had (one ``GetNode``); a node that does not is
looked up afresh, which is what an experiment load or a LAS X restart needs.

This module only reads. ``RegisterNode`` also subscribes the connection to
the node's updates; nothing listens, and the subscription ends with the
connection. ``ChangeNodes``, which writes, is never called.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .hardware_server import (
    HardwareServerUnavailable,
    connected_server,
    forget_connection,
)

SEQUENCE_PATH = ("Acquire", "ATL", "CurrentSequenceList")
STAGE_PATHS = {"x": ("Stage", "PositionX"), "y": ("Stage", "PositionY")}
Z_DRIVES_PATH = ("Acquire", "ZDriveList")

#: The driver's name for each z drive, by the ``ZUseMode`` LAS X gives it.
Z_USE_MODE = {"z-galvo": 1, "z-wide": 2}

#: Node ids found so far, by path; a path is a tuple of names from the root.
remembered = {}


@dataclass(frozen=True)
class Job:
    block_id: int
    element_id: int


@dataclass(frozen=True)
class JobSequence:
    """The job sequence as LAS X has it now."""

    block_id: int
    selected_element: int
    jobs: dict[str, Job] = field(default_factory=dict)

    @property
    def selected_job(self) -> str | None:
        return next(
            (
                name
                for name, job in self.jobs.items()
                if job.element_id == self.selected_element
            ),
            None,
        )


def out_parameter():
    """The by-reference variant pywin32 needs for ``RegisterNode``'s out value; None without pywin32."""
    try:
        import pythoncom
        from win32com.client import VARIANT
    except ImportError:
        return None
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)


def node_value(nodes, node_id):
    """A node's value. pywin32 hands the out value back as the call's return value."""
    out = out_parameter()
    returned = nodes.RegisterNode(node_id, out)
    held = getattr(out, "value", None)
    return held if held is not None else returned


def children(nodes, node_id):
    return nodes.GetNode(node_id)[3] or ()


def child_named(nodes, node_id, name):
    return next(
        (c for c in children(nodes, node_id) if nodes.GetNode(c)[0] == name), None
    )


def forget_nodes():
    """Forget every remembered node id, so the next read looks each up afresh."""
    remembered.clear()


def still_named(nodes, node_id, name):
    """Whether *node_id* still exists under *name*: the check of a remembered id."""
    return nodes.GetNode(node_id)[0] == name


def node_at(nodes, path):
    """The id of the node at *path*: the remembered one if it still fits, else found by name."""
    known = remembered.get(path)
    if known is not None and still_named(nodes, known, path[-1]):
        return known
    node = 1
    for depth, name in enumerate(path, start=1):
        found = child_named(nodes, node, name)
        if found is None:
            raise HardwareServerUnavailable(
                f"the state tree has no {'/'.join(path)} (missing {name!r})"
            )
        node = found
        remembered[path[:depth]] = node
    return node


def z_drive_node(nodes, drive):
    """The ``CurrentHardwarePosition`` node of one z drive, remembered by the drive's name."""
    path = (*Z_DRIVES_PATH, drive, "CurrentHardwarePosition")
    known = remembered.get(path)
    if known is not None and still_named(nodes, known, path[-1]):
        return known
    for candidate in children(nodes, node_at(nodes, Z_DRIVES_PATH)):
        if nodes.GetNode(candidate)[0] != "ZDrive":
            continue
        if int(child_value(nodes, candidate, "ZUseMode")) == Z_USE_MODE[drive]:
            found = child_named(nodes, candidate, "CurrentHardwarePosition")
            if found is not None:
                remembered[path] = found
                return found
    raise HardwareServerUnavailable(f"the state tree has no {drive} under {'/'.join(Z_DRIVES_PATH)}")


def read(what, reading):
    """Run *reading* on the state tree; anything that goes wrong is ``HardwareServerUnavailable``.

    A failed read drops the connection and the remembered ids, so the next
    read attaches and looks up afresh (LAS X may have restarted).
    """
    try:
        return reading(connected_server().ServerNodes)
    except HardwareServerUnavailable:
        raise
    except Exception as why:
        forget_connection()
        forget_nodes()
        raise HardwareServerUnavailable(
            f"the hardware server's state tree did not answer {what}: {why}"
        ) from why


def metres(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HardwareServerUnavailable(f"the state tree answered {what} = {value!r}")
    return float(value)


def get_xy() -> dict:
    """The stage position, shaped as the CAM API reader gives it: metres and micrometres."""

    def reading(nodes):
        x = metres(node_value(nodes, node_at(nodes, STAGE_PATHS["x"])), "Stage/PositionX")
        y = metres(node_value(nodes, node_at(nodes, STAGE_PATHS["y"])), "Stage/PositionY")
        return {"x": x, "y": y, "x_um": x * 1e6, "y_um": y * 1e6}

    return read("the stage position", reading)


def read_z_um(drive: str) -> float:
    """Where one z drive truly is, in micrometres; *drive* is ``"z-galvo"`` or ``"z-wide"``.

    ``CurrentHardwarePosition``, in the same frame as the job settings'
    ``zPosition``, and current also for the drive that carries a job's
    z-stack, whose job settings stop refreshing.
    """
    if drive not in Z_USE_MODE:
        raise ValueError(f"no z drive {drive!r}; the state tree knows {sorted(Z_USE_MODE)}")

    def reading(nodes):
        return metres(node_value(nodes, z_drive_node(nodes, drive)), drive) * 1e6

    return read(f"where {drive} is", reading)


def get_selected_job() -> dict | None:
    """The selected job, shaped as the log reader gives it: ``Name``, ``ID`` (its live block id), ``IsSelected``.

    None when the tree names no selected job.
    """
    sequence = job_sequence()
    name = sequence.selected_job
    if name is None:
        return None
    return {"Name": name, "ID": sequence.jobs[name].block_id, "IsSelected": True}


def child_value(nodes, node_id, name):
    found = child_named(nodes, node_id, name)
    if found is None:
        raise HardwareServerUnavailable(f"state tree node {node_id} has no {name!r}")
    return node_value(nodes, found)


def job_sequence() -> JobSequence:
    """The jobs, their live block and element ids, and which is selected.

    The per-job nodes are looked up each time: an experiment load renumbers
    them. Raises :class:`HardwareServerUnavailable` when the server cannot be
    reached or its tree lacks the sequence.
    """

    def reading(nodes):
        node = node_at(nodes, SEQUENCE_PATH)
        jobs = {}
        for info in children(nodes, node):
            if nodes.GetNode(info)[0] == "SequenceBlockInfo":
                jobs[str(child_value(nodes, info, "Name"))] = Job(
                    block_id=int(child_value(nodes, info, "BlockID")),
                    element_id=int(child_value(nodes, info, "ElementID")),
                )
        return JobSequence(
            block_id=int(child_value(nodes, node, "CurrentSequenceBlockID")),
            selected_element=int(child_value(nodes, node, "CurrentSelectedElementID")),
            jobs=jobs,
        )

    return read("the job sequence", reading)


#: The scan states of the node ``Acquire/AcquisitionState``, by value; the same
#: names the CAM API's scan status reports.
SCAN_STATES = (
    "eScanIdle",
    "eScanContinuous",
    "eScanSingle",
    "eScanSeries",
    "eScanBusy",
)


@dataclass(frozen=True)
class AcquisitionStatus:
    """What LAS X is doing now."""

    scan_state: str
    selected_job: str | None
    running_job: str | None
    running_setting: int
    paused: bool


def acquisition_status() -> AcquisitionStatus:
    """The scan state, the selected and the running job, from LAS X's state tree.

    ``Acquire/AcquisitionState`` is the scan state; under ``Acquire/ATL`` the
    running block, its setting and the pause state; the running and the
    selected element under its ``CurrentSequenceList``. A running job is named
    by its element in the job sequence; None when nothing runs (element 0).
    Raises :class:`HardwareServerUnavailable` like :func:`job_sequence`.
    """
    sequence = job_sequence()
    try:
        nodes = connected_server().ServerNodes
        acquire = child_named(nodes, 1, "Acquire")
        atl = child_named(nodes, acquire, "ATL")
        sequence_node = child_named(nodes, atl, "CurrentSequenceList")
        state = int(child_value(nodes, acquire, "AcquisitionState"))
        running_element = int(
            child_value(nodes, sequence_node, "CurrentRunningElementID")
        )
        return AcquisitionStatus(
            scan_state=SCAN_STATES[state]
            if 0 <= state < len(SCAN_STATES)
            else f"unknown ({state})",
            selected_job=sequence.selected_job,
            running_job=next(
                (
                    name
                    for name, job in sequence.jobs.items()
                    if running_element and job.element_id == running_element
                ),
                None,
            ),
            running_setting=int(child_value(nodes, atl, "CurrentRunningSettingID")),
            paused=bool(child_value(nodes, atl, "IsPause")),
        )
    except HardwareServerUnavailable:
        raise
    except Exception as why:
        forget_connection()
        raise HardwareServerUnavailable(
            f"the hardware server's state tree did not answer: {why}"
        ) from why
