"""LAS X's job sequence, read from its hardware server's state tree.

The hardware server keeps LAS X's state as a tree of named nodes, reached
through ``ServerNodes``: ``GetNode(id)`` gives a node's name, parent and
children (the root is node 1), ``RegisterNode(id, out value)`` its value.
Under ``Acquire/ATL/CurrentSequenceList`` LAS X keeps the job sequence: the
sequence's own block id, the selected element, and per job
(``SequenceBlockInfo``) its ``Name``, ``BlockID`` and ``ElementID``: what
selecting a job by its element needs.

The ids here are live: after an experiment load LAS X gave the jobs new ids
(HiRes 145 -> 152) while the CAM API's own job list kept the old ones.

This module only reads. ``RegisterNode`` also subscribes the connection to
the node's updates; nothing listens, and the subscription ends with the
connection. ``ChangeNodes``, which writes, is never called.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .hardware_server import (
    HardwareServerUnavailable,
    connected_server,
    forget_connection,
)

SEQUENCE_PATH = ("Acquire", "ATL", "CurrentSequenceList")


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


def child_value(nodes, node_id, name):
    found = child_named(nodes, node_id, name)
    if found is None:
        raise HardwareServerUnavailable(f"state tree node {node_id} has no {name!r}")
    return node_value(nodes, found)


def job_sequence() -> JobSequence:
    """The jobs, their live block and element ids, and which is selected.

    Raises :class:`HardwareServerUnavailable` when the server cannot be
    reached or its tree lacks the sequence.
    """
    try:
        nodes = connected_server().ServerNodes
        node = 1
        for name in SEQUENCE_PATH:
            found = child_named(nodes, node, name)
            if found is None:
                raise HardwareServerUnavailable(
                    f"the state tree has no {'/'.join(SEQUENCE_PATH)} (missing {name!r})"
                )
            node = found
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
    except HardwareServerUnavailable:
        raise
    except Exception as why:
        forget_connection()
        raise HardwareServerUnavailable(
            f"the hardware server's state tree did not answer: {why}"
        ) from why


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
