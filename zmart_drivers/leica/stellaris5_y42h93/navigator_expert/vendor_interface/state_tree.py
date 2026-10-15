"""LAS X's state tree: how it is reached, walked and read. The direct reader reads through it.

The hardware server keeps LAS X's state as a tree of named nodes, reached
through ``ServerNodes``: ``GetNode(id)`` gives a node's name, parent and
children (the root is node 1), ``RegisterNode(id, out value)`` its value.
Under ``Acquire/ATL/CurrentSequenceList`` LAS X keeps the job sequence: the
sequence's own block id, the selected element, and per job
(``SequenceBlockInfo``) its ``Name``, ``BlockID`` and ``ElementID``: what
selecting a job by its element needs. ``IsAutoFocusOnStart`` there marks
Navigator Expert's autofocus job (STELLARIS, 2026-10-15: set for the AF job
alone, in agreement with the CAM API's ``IsAutofocus``).

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

from dataclasses import dataclass, field

from .hardware_server import (
    HardwareServerUnavailable,
    connected_server,
    connections,
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
    autofocus: bool = False  # Navigator Expert's autofocus job (IsAutoFocusOnStart)


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


def server_nodes():
    """This thread's ``ServerNodes``, kept with its connection.

    Asking the connection for it costs about 4 ms, a node read by its id
    0.6 ms (simulator, 2026-10-13), so it is asked for once per connection.
    """
    server = connected_server()
    kept = getattr(connections, "nodes", None)
    if kept is None or kept[0] is not server:
        kept = (server, server.ServerNodes)
        connections.nodes = kept
    return kept[1]


def read(what, reading):
    """Run *reading* on the state tree; anything that goes wrong is ``HardwareServerUnavailable``.

    A failed read drops the connection and the remembered ids, so the next
    read attaches and looks up afresh (LAS X may have restarted).
    """
    try:
        return reading(server_nodes())
    except HardwareServerUnavailable:
        raise
    except Exception as why:
        forget_connection()
        forget_nodes()
        raise HardwareServerUnavailable(
            f"the hardware server's state tree did not answer {what}: {why}"
        ) from why


def child_value(nodes, node_id, name):
    found = child_named(nodes, node_id, name)
    if found is None:
        raise HardwareServerUnavailable(f"state tree node {node_id} has no {name!r}")
    return node_value(nodes, found)


def named(nodes, parent, name):
    """The child *name* of *parent*; raises when there is none."""
    found = child_named(nodes, parent, name)
    if found is None:
        raise HardwareServerUnavailable(f"state tree node {parent} has no {name!r}")
    return found


def layout(nodes, path, build):
    """The value nodes under *path*, as ``build(nodes, node, kids)`` finds them, remembered while its children stay the same.

    For parts of the tree with repeated nodes (jobs, objectives, light
    sources), which a path of names cannot pick out. An experiment load
    renumbers the per-job nodes, which changes the sequence's children, so
    its layout is found afresh then.
    """
    node = node_at(nodes, path)
    kids = tuple(children(nodes, node))
    known = remembered.get(("layout", *path))
    if known is not None and known[0] == kids:
        return known[1]
    found = build(nodes, node, kids)
    remembered[("layout", *path)] = (kids, found)
    return found


def of_name(nodes, kids, name):
    """The nodes among *kids* named *name*, in their order."""
    return [kid for kid in kids if nodes.GetNode(kid)[0] == name]


def sequence_layout(nodes, node, kids):
    """``(sequence block id node, selected element node, [(name, block id, element id, autofocus node) per job])``.

    A job without an ``IsAutoFocusOnStart`` node has None there and counts as
    not the autofocus job.
    """
    return (
        named(nodes, node, "CurrentSequenceBlockID"),
        named(nodes, node, "CurrentSelectedElementID"),
        [
            (
                named(nodes, info, "Name"),
                named(nodes, info, "BlockID"),
                named(nodes, info, "ElementID"),
                child_named(nodes, info, "IsAutoFocusOnStart"),
            )
            for info in of_name(nodes, kids, "SequenceBlockInfo")
        ],
    )


def job_sequence() -> JobSequence:
    """The jobs, their live block and element ids, and which is selected.

    Raises :class:`HardwareServerUnavailable` when the server cannot be
    reached or its tree lacks the sequence.
    """

    def reading(nodes):
        block, selected, per_job = layout(nodes, SEQUENCE_PATH, sequence_layout)
        jobs = {
            str(node_value(nodes, name)): Job(
                block_id=int(node_value(nodes, block_id)),
                element_id=int(node_value(nodes, element_id)),
                autofocus=autofocus is not None and bool(node_value(nodes, autofocus)),
            )
            for name, block_id, element_id, autofocus in per_job
        }
        return JobSequence(
            block_id=int(node_value(nodes, block)),
            selected_element=int(node_value(nodes, selected)),
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
        nodes = server_nodes()
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
