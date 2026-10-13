"""A stand-in for the hardware server's state tree (``ServerNodes``), shaped as on the simulator.

The nodes read on the LAS X simulator on 2026-10-13:

- ``Stage/PositionX``, ``Stage/PositionY`` (metres);
- ``Acquire/AcquisitionState`` (the scan state, 0 = idle);
- ``Acquire/ZDriveList/ZDrive`` per drive: ``ZUseMode`` (1 z-galvo, 2 z-wide),
  ``CurrentPosition`` and ``CurrentHardwarePosition`` (metres);
- ``Acquire/ATL``: ``CurrentRunningBlockID``, ``CurrentRunningSettingID``,
  ``IsPause``, ``PauseState`` and ``CurrentSequenceList`` with
  ``CurrentSequenceBlockID``, ``CurrentSelectedElementID``,
  ``CurrentRunningElementID`` and one ``SequenceBlockInfo`` per job
  (``Name``, ``Type``, ``BlockID``, ``ElementID``).

The tree is built afresh on every call from the callables given, so a test
can move the stage, select a job or reload the experiment between reads.
``reload()`` renumbers the per-job nodes, as LAS X does when an experiment is
loaded. ``node_calls`` counts ``GetNode`` calls, to see whether a reader walks.
"""


class StandInNodes:
    """``GetNode(id) -> (name, rights, parent, children)``; ``RegisterNode(id, out) -> value``."""

    def __init__(
        self,
        jobs,
        *,
        sequence=151,
        selected=None,
        stage=lambda: (0.066, 0.043),
        zdrives=lambda: {1: 0.0, 2: 0.0},
    ):
        self.jobs = jobs  # () -> {name: (block_id, element_id)}
        self.sequence = sequence
        self.selected = selected  # () -> selected job name
        self.stage = stage  # () -> (x_m, y_m)
        self.zdrives = zdrives  # () -> {z_use_mode: metres}
        self.status = {}  # AcquisitionState, CurrentRunningBlockID, ... overriding rest values
        self.generation = 0
        self.reads = 0
        self.node_calls = 0

    def reload(self):
        """Renumber the per-job nodes, as an experiment load does."""
        self.generation += 1

    def tree(self):
        nodes, values = {}, {}

        def add(node_id, name, parent, value=None, children=()):
            nodes[node_id] = (name, parent, tuple(children))
            if value is not None:
                values[node_id] = value

        x_m, y_m = self.stage()
        add(1, "Root", 0, children=(2, 99, 300))
        add(99, "Confocal", 1)
        add(300, "Stage", 1, children=(301, 302))
        add(301, "PositionX", 300, x_m)
        add(302, "PositionY", 300, y_m)
        add(2, "Acquire", 1, children=(8, 3, 310))
        add(8, "AcquisitionState", 2, 0)
        drives = self.zdrives()
        add(310, "ZDriveList", 2, children=(311, 321))
        for base, mode in ((311, 1), (321, 2)):
            add(base, "ZDrive", 310, children=(base + 1, base + 2, base + 3))
            add(base + 1, "ZUseMode", base, mode)
            add(base + 2, "CurrentPosition", base, drives[mode])
            add(base + 3, "CurrentHardwarePosition", base, drives[mode])
        add(3, "ATL", 2, children=(4, 9, 10, 11, 12))
        add(9, "CurrentRunningBlockID", 3, 0)
        add(10, "CurrentRunningSettingID", 3, 0)
        add(11, "IsPause", 3, False)
        add(12, "PauseState", 3, 0)

        selected_name = self.selected() if self.selected else None
        children = [5, 6, 13]
        add(5, "CurrentSequenceBlockID", 4, self.sequence)
        add(6, "CurrentSelectedElementID", 4, 0)
        add(13, "CurrentRunningElementID", 4, 0)
        node = 1000 + 1000 * self.generation
        for name, (block, element) in self.jobs().items():
            info = node
            add(info, "SequenceBlockInfo", 4, children=(info + 1, info + 2, info + 3, info + 4))
            add(info + 1, "Name", info, name)
            add(info + 2, "Type", info, 1)
            add(info + 3, "BlockID", info, block)
            add(info + 4, "ElementID", info, element)
            if name == selected_name:
                values[6] = element
            children.append(info)
            node += 10
        add(4, "CurrentSequenceList", 3, children=children)

        by_name = {
            "AcquisitionState": 8,
            "CurrentRunningBlockID": 9,
            "CurrentRunningSettingID": 10,
            "IsPause": 11,
            "CurrentRunningElementID": 13,
        }
        for name, value in self.status.items():
            values[by_name[name]] = value
        return nodes, values

    def GetNode(self, node_id):
        self.node_calls += 1
        nodes, _ = self.tree()
        name, parent, children = nodes.get(node_id, ("", 0, ()))
        return name, 2, parent, children or None

    def RegisterNode(self, node_id, out):
        self.reads += 1
        _, values = self.tree()
        return values.get(node_id)
