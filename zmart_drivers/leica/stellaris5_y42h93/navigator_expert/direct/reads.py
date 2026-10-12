"""Reads over the direct route, answered by LAS X's hardware server.

The driver's CAM readers (in ``vendor_interface``) read through the
CAM API's data objects: they clear a field, name the read on the
``PyApiCommand`` channel, and wait for the field to be filled. The route
offers the same objects, and fills the field from the hardware server
instead. Three reads have a hardware-server form, seen in ``lcsCommand.log``
and compared with the CAM API's answers on the simulator (2026-10-12):

- job settings: ``<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute=<job>/>``,
  the same JSON for all three jobs;
- hardware info: ``<Scanner Command="GetConfocalHardwareInfoAsJson"/>``, the same JSON;
- stage XY: ``<Scanner Command="GetStageHwPosition"/>``, metres, equal to the last digit.

Any other read on the channel (the job list) goes on to the CAM API. A read
LAS X refuses leaves the field empty: to the reader, no answer.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from .command import send
from .translations import NoTranslation

BODY = re.compile(r'<Result Error="">(.*)</Result>', re.DOTALL)
STAGE = re.compile(r'<Result HwStagePosX="([^"]+)" HwStagePosY="([^"]+)" Unit="m"/>')


class ReadObject:
    """A CAM read object: its ``Model`` holds the parameters and the answer."""

    def __init__(self, **fields):
        self.Model = SimpleNamespace(**fields)

    def UpdateAwaitReceipt(self, timeout=None):
        """Hand over the parameters: nothing to send until the read is named."""
        return True


class ReadChannel:
    """The ``PyApiCommand`` channel: naming a read fetches it from the hardware server."""

    def __init__(self, route):
        self.route = route
        self.Model = SimpleNamespace(Command="")

    def UpdateAwaitReceipt(self, timeout=None):
        fetch = {
            "GetJobSettingsByName": self.job_settings,
            "GetConfocalHardwareInfo": self.hardware_info,
            "GetXY": self.stage_xy,
        }.get(self.Model.Command)
        if fetch is None:
            cam = self.route.cam_client.PyApiCommand
            cam.Model.Command = ""
            cam.Model.Command = self.Model.Command
            return cam.UpdateAwaitReceipt(timeout)
        fetch()
        return True

    def job_settings(self):
        """The job's settings, by the id last seen for it; a fresh id when the answer names another job."""
        model = self.route.PyApiGetJobSettingsByName.Model
        try:
            for block in self.route.read_ids(model.JobName):
                body = body_of(
                    send(
                        f'<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="{block}"/>'
                    )
                )
                if (
                    body is not None
                    and json.loads(body).get("jobName") == model.JobName
                ):
                    model.Settings = body
                    return
        except NoTranslation:
            return

    def hardware_info(self):
        self.route.PyApiGetConfocalHardwareInfo.Model.HWInfo = body_of(
            send('<Scanner Command="GetConfocalHardwareInfoAsJson"/>')
        )

    def stage_xy(self):
        match = STAGE.fullmatch(send('<Scanner Command="GetStageHwPosition"/>'))
        if match:
            model = self.route.PyApiGetXY.Model
            model.XPosition, model.YPosition = float(match[1]), float(match[2])


def body_of(answer: str):
    """The JSON text of an accepted read, or None when LAS X refused it."""
    match = BODY.fullmatch(answer or "")
    return match[1] if match else None


def read_objects(route) -> dict:
    """The read objects the route offers in place of the CAM API's."""
    return {
        "PyApiCommand": ReadChannel(route),
        "PyApiGetJobSettingsByName": ReadObject(JobName=None, Settings=None),
        "PyApiGetConfocalHardwareInfo": ReadObject(HWInfo=None),
        "PyApiGetXY": ReadObject(XPosition=float("nan"), YPosition=float("nan")),
    }
