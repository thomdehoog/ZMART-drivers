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
  (``Name``, ``Type``, ``BlockID``, ``ElementID``, ``IsAutoFocusOnStart``;
  the last as on the STELLARIS, 2026-10-15, where it marks Navigator
  Expert's autofocus job).

The tree is built afresh on every call from the callables given, so a test
can move the stage, select a job or reload the experiment between reads.
``reload()`` renumbers the per-job nodes, as LAS X does when an experiment is
loaded. ``node_calls`` counts ``GetNode`` calls, to see whether a reader walks.
"""

import copy
import json
import re


#: The current setting's nodes as read on the LAS X simulator, 2026-10-13
#: (HiRes selected, no z-stack), by path from the root.
SIMULATOR_SETTINGS = {
    ("Acquire", "ScanDimensionList", "ScanModeName"): "xyz",
    ("Acquire", "AcquireImageSizeInfo", "ImageSizeX"): 0.0011625,
    ("Acquire", "AcquireImageSizeInfo", "ImageSizeY"): 0.0011625,
    ("Acquire", "AcquireZStackInfo", "BeginValue"): -2.384185222837308e-10,
    ("Acquire", "AcquireZStackInfo", "EndValue"): -2.384185222837308e-10,
    ("Acquire", "AcquireZStackInfo", "SectionsCount"): 1,
    ("Acquire", "AcquireZStackInfo", "StepSize"): 0.0,
    ("Acquire", "ZStackUseMode"): 1,
    ("Confocal", "ScanningParameter", "IsResonantConfocalScanner"): False,
    ("Confocal", "ScanningParameter", "Speed"): 400,
    ("Confocal", "ScanningParameter", "SpeedUnit"): "Hz",
    ("Confocal", "ScanningParameter", "CurrentFormat"): "512 x 512",
    ("Confocal", "ScanningParameter", "FormatX"): 512,
    ("Confocal", "ScanningParameter", "FormatY"): 512,
    ("Confocal", "ScanningParameter", "Zoom"): 1.0,
    ("Confocal", "ScanningParameter", "ZoomMin"): 0.75,
    ("Confocal", "ScanningParameter", "ZoomMax"): 48.0,
    ("Confocal", "ScanningParameter", "LineAverage"): 1,
    ("Confocal", "ScanningParameter", "LineAccu"): 1,
    ("Confocal", "ScanningParameter", "FrameAverage"): 1,
    ("Confocal", "ScanningParameter", "FrameAccumulation"): 1,
    ("Confocal", "ScanningParameter", "LineAverageSequentialList"): (1,),
    ("Confocal", "HWParameter", "Pinhole"): 0.0003977875,
    ("Confocal", "HWParameter", "PinholeAU"): 0.9994033038125747,
    ("Confocal", "HWParameter", "PinholeAUMin"): 0.251240499968595,
    ("Confocal", "HWParameter", "PinholeAUMax"): 2.5124049996859497,
    ("Microscope", "SlotPosition"): 0,
    ("Microscope", "CurrentMicStandName"): "DM Manual-6",
    ("Confocal", "ScanningParameter", "Resolution"): 8,
    ("Confocal", "ScanningParameter", "SpeedMin"): 1,
    ("Confocal", "ScanningParameter", "SpeedMax"): 2600,
    ("Confocal", "ScanningParameter", "IsSequentialEnabled"): True,
    ("Confocal", "ScanningParameter", "SequentialMode"): 0,
    ("Confocal", "HWParameter", "ScanRotationAngle"): 0.0,
    ("Confocal", "HWParameter", "ScanRotationAngleMin"): -100.0,
    ("Confocal", "HWParameter", "ScanRotationAngleMax"): 100.0,
    ("Acquire", "ActiveDataSource", "CurrentSerialNumber"): "STELLARIS SIMULATOR",
    ("Confocal", "HWParameter", "CurrentMultiFunctionPortPos"): 1,
    ("Confocal", "HWParameter", "MultiFunctionPortNameList"): ("--- ", "Substrate ", "SD 470", "DD 470/640", "--- "),
    ("Confocal", "FastLiveParametersResonant", "ScanSpeedMax"): 12000,
    ("Confocal", "ServiceOnlyParameter", "CanDoAFC"): False,
    ("Microscope", "CurrentMotCorrPos"): 0.0,
    ("Stage", "DynamicLimitXmin"): 0.0,
    ("Stage", "DynamicLimitXmax"): 0.127,
    ("Stage", "DynamicLimitYmin"): 0.0,
    ("Stage", "DynamicLimitYmax"): 0.083,
}


def aotf_line(wavelength, index, intensity):
    return {"WaveLength": wavelength, "IsLineChecked": True, "LineIndex": index, "Intensity": intensity}


#: The simulator's light sources (Confocal/LightSourceList), 2026-10-13: the UV
#: laser's 405 at 1.96 % and the white-light laser's 497 at 2.0 % are on.
def laser(name, low, high):
    return {"LaserName": name, "WavelengthMin": low, "WavelengthMax": high}


SIMULATOR_LIGHT_SOURCES = [
    {
        "LightSourceName": "UV",
        "BeamRoute": (30,),
        "LaserList": {"Laser": [laser("Laser 405", 405.0, 405.0)]},
        "Shutter": {"IsShutterActive": True},
        "AOTF": {"AotfLine": [aotf_line(405.0, 0, 1.9607843137254901)]},
    },
    {
        "LightSourceName": "Visible Light",
        "BeamRoute": (10, 30),
        "LaserList": {
            "Laser": [
                laser("Laser 488", 488.0, 488.0),
                laser("Laser 638", 638.0, 638.0),
                laser("Laser 730", 730.0, 730.0),
            ]
        },
        "Shutter": {"IsShutterActive": True},
        "AOTF": {
            "AotfLine": [aotf_line(488.0, 0, 0.0), aotf_line(638.0, 1, 0.0), aotf_line(730.0, 2, 0.0)]
        },
    },
    {
        "LightSourceName": "SuperContVisible Light",
        "BeamRoute": (10, 10),
        "LaserList": {"Laser": [laser("WLL", 485.0, 685.0)]},
        "Shutter": {"IsShutterActive": True},
        "AOTF": {"AotfLine": [aotf_line(497.0, 0, 2.000135222048726), aotf_line(681.0, 1, 0.0)]},
    },
]


def detector(route, name, kind, begin, end, active, dye, gain, *, photon_counting=True):
    return {
        "BeamRoute": route,
        "Name": name,
        "WaveLengthBegin": begin,
        "WaveLengthEnd": end,
        "WaveLengthRangeMin": 5.0,
        "Type": kind,
        "IsActive": active,
        "DyeName": dye,
        "HighVoltage": gain,
        "HighVoltageMin": 2.5,
        "HighVoltageMax": 500.0,
        "Offset": 0.0,
        "CanDoPhotonCounting": photon_counting,
    }


#: The simulator's detectors (Confocal/SpectralDetection), 2026-10-13: HyD 2 and
#: HyD S 3 are in use. Two of the internal five, and the transmitted-light PMT.
SIMULATOR_DETECTION = {
    "Spectrophotometer": [
        {
            "DetectionUnitList": {
                "DetectorHWInfo": [
                    detector((40, 2), "HyD 2", 4, 410.0, 487.0, True, "Leica/ALEXA 405", 2.5),
                    detector((40, 3), "HyD S 3", 6, 502.0, 779.0, True, "Leica/ALEXA 488", 2.5),
                ]
            }
        },
        {
            "DetectionUnitList": {
                "DetectorHWInfo": [
                    detector((10, 5), "Trans PMT", 3, 350.0, 800.0, False, "None", 0.0, photon_counting=False)
                ]
            }
        },
    ]
}


def wheel(kind, slot, names):
    return {
        "CurrentSlotPosition": slot,
        "FilterWheelType": kind,
        "Filter": [{"FilterName": name} for name in names],
    }


#: Three of the simulator's wheels (Confocal/HWParameter/FilterWheelList), 2026-10-13,
#: the polarization wheel under its own node name, as LAS X has it.
SIMULATOR_FILTER_WHEELS = {
    "FilterWheel": [
        wheel(200, 0, ["Galvo X Pan Center ", "Galvo X Pan Mirror "]),
        wheel(140, 2, ["--- ", "Substrate ", "Mirror "]),
    ],
    "PolarizationFW": [wheel(130, 0, ["Empty", "NF 448/514/561"])],
}

#: The first two of the simulator's six objectives (Microscope/ObjectiveList), in slot order.
SIMULATOR_OBJECTIVES = [
    {
        "Name": "HC PL APO  CS    10x/0.40 DRY ",
        "Magnification": 10.0,
        "NumericalAperture": 0.4,
        "Immersion": "DRY",
        "OrderNumber": 506511,
        "IsMotCorrApplicable": True,
    },
    {
        "Name": "HC PL APO CS  20.0x0.70 UV",
        "Magnification": 20.0,
        "NumericalAperture": 0.7,
        "Immersion": "DRY",
        "OrderNumber": 506513,
        "IsMotCorrApplicable": True,
    },
]


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
        autofocus=("AF Job",),
    ):
        self.jobs = jobs  # () -> {name: (block_id, element_id)}
        self.autofocus = autofocus  # the jobs whose IsAutoFocusOnStart is set
        self.sequence = sequence
        self.selected = selected  # () -> selected job name
        self.stage = stage  # () -> (x_m, y_m)
        self.zdrives = zdrives  # () -> {z_use_mode: metres}
        self.status = {}  # AcquisitionState, CurrentRunningBlockID, ... overriding rest values
        self.live = dict(SIMULATOR_SETTINGS)  # path -> value (or a function giving it), the current setting's nodes
        self.objectives = [dict(o) for o in SIMULATOR_OBJECTIVES]  # Microscope/ObjectiveList, by slot
        self.light_sources = copy.deepcopy(SIMULATOR_LIGHT_SOURCES)  # Confocal/LightSourceList
        self.detection = copy.deepcopy(SIMULATOR_DETECTION)  # Confocal/SpectralDetection
        self.filter_wheels = copy.deepcopy(SIMULATOR_FILTER_WHEELS)  # Confocal/HWParameter/FilterWheelList
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
            add(info, "SequenceBlockInfo", 4, children=(info + 1, info + 2, info + 3, info + 4, info + 5))
            add(info + 1, "Name", info, name)
            add(info + 2, "Type", info, 1)
            add(info + 3, "BlockID", info, block)
            add(info + 4, "ElementID", info, element)
            add(info + 5, "IsAutoFocusOnStart", info, name in self.autofocus)
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

        fresh = iter(range(20000, 30000))

        def child(parent, name):
            found = next((c for c in nodes[parent][2] if nodes[c][0] == name), None)
            if found is None:
                found = next(fresh)
                add(found, name, parent)
                pname, pparent, pchildren = nodes[parent]
                nodes[parent] = (pname, pparent, (*pchildren, found))
            return found

        for path, value in self.live.items():
            node = 1
            for name in path:
                node = child(node, name)
            values[node] = value() if callable(value) else value

        def graft(parent, name, spec):
            """A dict is a node with children, a list repeats the node, anything else is a value."""
            if isinstance(spec, list):
                for each in spec:
                    graft(parent, name, each)
                return
            node = next(fresh)
            add(node, name, parent)
            pname, pparent, pchildren = nodes[parent]
            nodes[parent] = (pname, pparent, (*pchildren, node))
            if isinstance(spec, dict):
                for key, inner in spec.items():
                    graft(node, key, inner)
            else:
                values[node] = spec

        confocal = child(1, "Confocal")
        graft(confocal, "LightSourceList", {"LightSource": self.light_sources})
        graft(confocal, "SpectralDetection", self.detection)
        graft(child(confocal, "HWParameter"), "FilterWheelList", self.filter_wheels)
        objective_list = child(child(1, "Microscope"), "ObjectiveList")
        for objective in self.objectives:
            info = next(fresh)
            add(info, "ObjectiveInfo", objective_list)
            name, parent, kids = nodes[objective_list]
            nodes[objective_list] = (name, parent, (*kids, info))
            for key, value in objective.items():
                values[child(info, key)] = value
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


JOB_SETTINGS_READ = re.compile(r'<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="(\d+)"/>')
HARDWARE_INFO_READ = '<Scanner Command="GetConfocalHardwareInfoAsJson"/>'


def answer_json_read(xml, client, nodes):
    """The hardware server's answer to one of the two JSON reads, from the mock; None for anything else.

    ``ATL_GetBlockApiInfoAsJsonString`` (a job's settings, by block id) and
    ``GetConfocalHardwareInfoAsJson`` are what the CAM API asks the hardware
    server; the mock's settings and hardware info stand for its answers, as
    they stand for the CAM API's. A block id the tree does not have is
    refused, as LAS X refuses it.
    """
    settings = JOB_SETTINGS_READ.fullmatch(xml)
    if settings:
        by_block = {block: name for name, (block, _) in nodes.jobs().items()}
        job = by_block.get(int(settings[1]))
        if job is None or job not in client._jobs:
            return '<Result Error="1">invalid block identifier</Result>'
        return f'<Result Error="">{json.dumps(client._jobs[job])}</Result>'
    if xml == HARDWARE_INFO_READ:
        return f'<Result Error="">{json.dumps(client._hardware)}</Result>'
    return None
