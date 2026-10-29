"""A stand-in for LAS X's hardware server with its plain Navigator shown: the state tree, and the commands it takes.

The offline suite runs the whole driver against this: the reads walk the
state tree stand-in (``state_tree_stand_in.py``, as on the simulator), and
the writes are the elements the plain Navigator route sends, applied to
the same tree as LAS X applies them (simulator and STELLARIS, 2026-10-15):

- ``SetZoom``, ``SetScanSpeed``, ``SetConfocalScanFieldRotation``,
  ``SetLineAverage`` and ``SetPinholeAU`` change the current setting's node;
- ``SetZPosition`` with ``RelativeCoordinates="1"`` steps the named z drive;
- ``SetStagePositionInHwOnly`` puts the stage at the position given;
- ``SetSlotPosition`` turns the turret;
- ``CaptureSingleFrame`` and ``StartAcquiring`` run a scan: the scan status
  reads ``eScanSingle`` for a few reads, then idle again;
- the other elements of a tile scan (``SetStageUseMode``, ``ScanMode``...)
  are taken and remembered.

A value a limit of the stand-in refuses (zoom above ``ZoomMax``) is
answered as LAS X answers it, ``Error="1"`` with a reason, and nothing
changes. Every element sent is kept in ``sent``, in order.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET

from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    state_tree,
    transfer,
)

SCANNING = ("Confocal", "ScanningParameter")
HARDWARE = ("Confocal", "HWParameter")

#: The hardware info the stand-in answers, as LAS X's JSON names it.
HARDWARE_INFO = {
    "SerialNumber": "STELLARIS SIMULATOR",
    "SystemType": "SIMULATOR",
    "Microscope": {
        "name": "DM Manual-6",
        "objectives": [
            {"slotIndex": 0, "objectiveNumber": 506511, "name": "HC PL APO  CS    10x/0.40 DRY ", "magnification": 10.0},
            {"slotIndex": 1, "objectiveNumber": 506513, "name": "HC PL APO CS  20.0x0.70 UV", "magnification": 20.0},
        ],
    },
}


class PlainNavigator:
    """LAS X with its plain Navigator shown: no jobs, a stage, two z drives, the current settings."""

    def __init__(self, *, x_um=63500.0, y_um=41500.0, z_galvo_um=0.0, z_wide_um=100.0, scan_s=0.15):
        self.x_um, self.y_um = x_um, y_um
        self.z_um = {1: z_galvo_um, 2: z_wide_um}
        self.sent: list[str] = []
        self.answers: dict[str, str] = {}  # a command name -> the answer to give instead
        self.refuse_all: str | None = None  # a reason: every element is refused with it
        self.scanning_until = 0.0
        self.scan_s = scan_s  # how long a scan lasts
        self.captures = 0
        self.nodes = StandInNodes(
            dict,
            stage=lambda: (self.x_um * 1e-6, self.y_um * 1e-6),
            zdrives=lambda: {1: self.z_um[1] * 1e-6, 2: self.z_um[2] * 1e-6},
        )
        self.nodes.live[("Acquire", "AcquisitionState")] = self.scan_state
        self.ServerNodes = self.nodes

    # --- what the reads see ----------------------------------------------------------

    def scan_state(self):
        return 2 if time.monotonic() < self.scanning_until else 0  # eScanSingle, or idle

    @property
    def live(self):
        return self.nodes.live

    def setting(self, path):
        return self.live[path]

    # --- what the hardware server answers ------------------------------------------------

    def SendCommand2(self, xml, _):
        self.sent.append(xml)
        if xml == '<Scanner Command="GetConfocalHardwareInfoAsJson"/>':
            return f'<Result Error="">{json.dumps(HARDWARE_INFO)}</Result>'
        element = ET.fromstring(xml)
        command = element.get("Command")
        if self.refuse_all:
            return f'<Result Error="1">{self.refuse_all}</Result>'
        if command in self.answers:
            return self.answers[command]
        handler = getattr(self, f"take_{command}", None)
        if handler is None:
            return '<Result Error="0"/>'
        return handler(element) or '<Result Error="0"/>'

    def take_SetZoom(self, e):
        zoom = float(e.get("Zoom"))
        if e.get("BlockId") != "0":
            return '<Result Error="1">invalid block identifier</Result>'
        if not self.live[(*SCANNING, "ZoomMin")] <= zoom <= self.live[(*SCANNING, "ZoomMax")]:
            return f'<Result Error="1">Zoom {zoom:g} is out of range</Result>'
        self.live[(*SCANNING, "Zoom")] = zoom

    def take_SetScanSpeed(self, e):
        self.live[(*SCANNING, "Speed")] = int(float(e.get("Speed")))

    def take_SetConfocalScanFieldRotation(self, e):
        self.live[(*HARDWARE, "ScanRotationAngle")] = float(e.get("Rotation"))

    def take_SetLineAverage(self, e):
        self.live[(*SCANNING, "LineAverage")] = int(float(e.get("LineAverage")))

    def take_SetPinholeAU(self, e):
        value = float(e.get("PinholeAU"))
        top = self.live[(*HARDWARE, "PinholeAUMax")]
        if value > top:
            self.live[(*HARDWARE, "PinholeAU")] = top
            return f'<Result Error="2">Pinhole set to {top:.3f}</Result>'
        self.live[(*HARDWARE, "PinholeAU")] = value

    def take_SetZPosition(self, e):
        drive = int(e.get("ZUseMode"))
        step_um = float(e.get("PosZ")) * 1e6
        if e.get("RelativeCoordinates") == "1":
            self.z_um[drive] += step_um
        else:
            self.z_um[drive] = step_um

    def take_SetStagePositionInHwOnly(self, e):
        x, y = float(e.get("StagePosX")) * 1e6, float(e.get("StagePosY")) * 1e6
        if e.get("RelativeCoordinates") == "1":
            self.x_um, self.y_um = self.x_um + x, self.y_um + y
        else:
            self.x_um, self.y_um = x, y

    def take_StageMoveToCoordinates(self, e):
        self.x_um, self.y_um = float(e.get("PosX")) * 1e6, float(e.get("PosY")) * 1e6

    def take_SetSlotPosition(self, e):
        slot = int(e.get("SlotPosition"))
        if not 0 <= slot < len(self.nodes.objectives):
            return f'<Result Error="1">slot {slot} is invalid</Result>'
        self.live[("Microscope", "SlotPosition")] = slot

    def take_CaptureSingleFrame(self, e):
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s

    def take_StartAcquiring(self, e):
        self.captures += 1
        self.scanning_until = time.monotonic() + self.scan_s

    def take_ScanMode(self, e):
        self.live[("Acquire", "ScanDimensionList", "ScanModeName")] = e.get("ScanMode")

    # --- what was sent -------------------------------------------------------------------

    def commands(self) -> list[str]:
        """The ``Command`` of every element sent, in order, the reads left out."""
        found = []
        for xml in self.sent:
            match = re.search(r'Command="([^"]+)"', xml)
            if match and match[1] != "GetConfocalHardwareInfoAsJson":
                found.append(match[1])
        return found


def attach(monkeypatch, navigator: PlainNavigator | None = None) -> PlainNavigator:
    """Stand *navigator* in for LAS X's hardware server for the test, in every thread."""
    navigator = navigator or PlainNavigator()
    monkeypatch.setattr(hardware_server, "attach", lambda: navigator)
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    monkeypatch.setitem(transfer.senders, "current", None)
    return navigator
