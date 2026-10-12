"""A stand-in for the hardware server's state tree (``ServerNodes``), shaped as on the simulator.

Root (1) -> Acquire -> ATL -> CurrentSequenceList, holding
``CurrentSequenceBlockID``, ``CurrentSelectedElementID`` and one
``SequenceBlockInfo`` per job (``Name``, ``BlockID``, ``ElementID``) — the
nodes read on the LAS X simulator on 2026-10-13. The tree is built afresh
from *jobs* on every call, so a test can change the jobs (a reloaded
experiment) between reads.
"""


class StandInNodes:
    """``GetNode(id) -> (name, rights, parent, children)``; ``RegisterNode(id, out) -> value``."""

    def __init__(self, jobs, *, sequence=151, selected=None):
        self.jobs = jobs  # callable: () -> {name: (block_id, element_id)}
        self.sequence = sequence
        self.selected = selected  # callable: () -> selected job name
        self.status = {}  # AcquisitionState, CurrentRunningBlockID, ... overriding rest values
        self.reads = 0

    def tree(self):
        nodes = {
            1: ("Root", 0, (2, 99)),
            99: ("Confocal", 1, ()),
            2: ("Acquire", 1, (8, 3)),
            8: ("AcquisitionState", 2, ()),
            3: ("ATL", 2, (4, 9, 10, 11, 12)),
            9: ("CurrentRunningBlockID", 3, ()),
            10: ("CurrentRunningSettingID", 3, ()),
            11: ("IsPause", 3, ()),
            12: ("PauseState", 3, ()),
        }
        values = {8: 0, 9: 0, 10: 0, 11: False, 12: 0}
        by_name = {
            "AcquisitionState": 8,
            "CurrentRunningBlockID": 9,
            "CurrentRunningSettingID": 10,
            "IsPause": 11,
        }
        children = [5, 6, 7]
        nodes[5], values[5] = ("CurrentSequenceBlockID", 4, ()), self.sequence
        nodes[7], values[7] = ("StageUseMode", 4, ()), 0
        selected_name = self.selected() if self.selected else None
        values[6] = 0
        nodes[6] = ("CurrentSelectedElementID", 4, ())
        node = 100
        for name, (block, element) in self.jobs().items():
            info = node
            nodes[info] = (
                "SequenceBlockInfo",
                4,
                (info + 1, info + 2, info + 3, info + 4),
            )
            nodes[info + 1], values[info + 1] = ("Name", info, ()), name
            nodes[info + 2], values[info + 2] = ("Type", info, ()), 1
            nodes[info + 3], values[info + 3] = ("BlockID", info, ()), block
            nodes[info + 4], values[info + 4] = ("ElementID", info, ()), element
            if name == selected_name:
                values[6] = element
            children.append(info)
            node += 10
        nodes[13], values[13] = ("CurrentRunningElementID", 4, ()), 0
        children.append(13)
        by_name["CurrentRunningElementID"] = 13
        nodes[4] = ("CurrentSequenceList", 3, tuple(children))
        for name, value in self.status.items():
            values[by_name[name]] = value
        return nodes, values

    def GetNode(self, node_id):
        nodes, _ = self.tree()
        name, parent, children = nodes.get(node_id, ("", 0, None))
        return name, 2, parent, children or None

    def RegisterNode(self, node_id, out):
        self.reads += 1
        _, values = self.tree()
        return values.get(node_id)
