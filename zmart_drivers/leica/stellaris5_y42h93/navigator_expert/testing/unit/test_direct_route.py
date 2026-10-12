"""
Unit tests: the direct route sends exactly what the CAM API sends.
================================================================================
``direct.route(client)`` is a client on which every driver command runs
unchanged: its own checks, limits gate, confirmation and re-fire. Only the
CAM API's command objects are replaced, by ones that send the hardware-server
command the CAM API itself ends in. Each expected XML below is what the CAM
API sent for the same call on the LAS X simulator (``lcsCommand.log``,
2026-10-12), without its ``Origin`` label. A command with no verified
translation is refused and nothing is sent.

The stand-in server answers "refused" unless told otherwise, so each command
is sent once and stops: what was sent can be compared exactly. Reads go to
the hardware server too (job settings, hardware info, stage XY), and the
stand-in answers them from the mock, as LAS X answers from its own state.
"""

import json
import re

import pytest
from limits_fixtures import install_permissive_limits
from mock_lasx_api import JOB_IDS, MockLasxClient
from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import direct
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    set as cam,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.direct import (
    client as direct_client,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    read as readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    api_reader,
    hardware_server,
)

#: The job sequence's block id and each job's element, as on the simulator (2026-10-13).
SEQUENCE = 151
ELEMENTS = {"AF Job": 1, "Overview": 2, "HiRes": 3}
REFUSED = '<Result Error="1">stand-in: not applied</Result>'
ACCEPTED = '<Result Error="0"/>'


def beam(*positions):
    levels = "".join(
        f'<BeamPosition BeamPositionLevel="{level}" BeamPosition="{p}"/>'
        for level, p in enumerate(positions)
    )
    return f'<BeamRoute Version="0">{levels}</BeamRoute>'


class StandInServer:
    """LAS X's hardware server as the direct route meets it: one method, SendCommand2.

    Writes are kept in ``sent`` and answered with ``answer``; reads are kept
    in ``reads`` and answered from the mock's own state.
    """

    def __init__(self, client, answer=REFUSED):
        self.client = client
        self.answer = answer
        self.sent = []
        self.reads = []
        self.ServerNodes = StandInNodes(
            lambda: {name: (block, ELEMENTS[name]) for name, block in JOB_IDS.items()},
            sequence=SEQUENCE,
            selected=lambda: client._selected_job,
        )

    def SendCommand2(self, xml, result):
        read = self.read(xml)
        if read is not None:
            self.reads.append(xml)
            return read
        self.sent.append(xml)
        zoom = re.fullmatch(
            r'<Scanner Command="SetZoom" Zoom="([^"]+)" BlockId="(\d+)"/>', xml
        )
        selection = re.fullmatch(
            rf'<ATL Command="ATL_Sequence" BlockIDOfSequence="{SEQUENCE}" '
            r'SubCommand="SetCurrentSelectedElementID" ElementID="(\d+)" DoNotActivateBlockFromElement="0"/>',
            xml,
        )
        if self.answer == ACCEPTED and selection:
            self.client._selected_job = {e: n for n, e in ELEMENTS.items()}[
                int(selection[1])
            ]
        if self.answer == ACCEPTED and zoom:
            job = {block: name for name, block in JOB_IDS.items()}[int(zoom[2])]
            self.client._jobs[job]["zoom"]["current"] = float(zoom[1])
        return self.answer

    def read(self, xml):
        settings = re.fullmatch(
            r'<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="(\d+)"/>', xml
        )
        if settings:
            names = {block: name for name, block in JOB_IDS.items()}
            if int(settings[1]) not in names:
                return '<Result Error="invalid block identifier"/>'
            name = names[int(settings[1])]
            # LAS X's answer names the job and its id, as seen in lcsCommand.log.
            body = json.dumps(
                {
                    **api_reader.get_job_settings(self.client, name),
                    "jobName": name,
                    "id": int(settings[1]),
                }
            )
            return f'<Result Error="">{body}</Result>'
        if xml == '<Scanner Command="GetConfocalHardwareInfoAsJson"/>':
            return f'<Result Error="">{json.dumps(api_reader.get_hardware_info(self.client))}</Result>'
        if xml == '<Scanner Command="GetStageHwPosition"/>':
            return (
                f'<Result HwStagePosX="{self.client._stage_x!r}" '
                f'HwStagePosY="{self.client._stage_y!r}" Unit="m"/>'
            )
        return None


@pytest.fixture
def client():
    return install_permissive_limits(MockLasxClient(), wide_stage=True)


@pytest.fixture
def server(client, monkeypatch):
    stand_in = StandInServer(client)
    monkeypatch.setattr(hardware_server, "attach", lambda: stand_in)
    hardware_server.forget_connection()
    return stand_in


def objectives(client):
    return readers.get_hardware_info(client)


GOLDEN = {
    "set_zoom": (
        lambda c: cam.set_zoom(c, "HiRes", 5.0),
        '<Scanner Command="SetZoom" Zoom="5" BlockId="142"/>',
    ),
    "set_scan_speed": (
        lambda c: cam.set_scan_speed(c, "HiRes", 600),
        '<Scanner Command="SetScanSpeed" Speed="600" BlockId="142"/>',
    ),
    "set_scan_resonant": (
        lambda c: cam.set_scan_resonant(c, "HiRes", True),
        '<Scanner Command="SetScannerToResonant" Enable="1" BlockId="142"/>',
    ),
    "set_sequential_mode": (
        lambda c: cam.set_sequential_mode(c, "HiRes", "Frame"),
        '<Scanner Command="SetConfocalSequentialMode" SequentialMode="1" IsApiCommand="1" BlockId="142"/>',
    ),
    "set_sequential_mode Stack": (
        lambda c: cam.set_sequential_mode(c, "HiRes", "Stack"),
        '<Scanner Command="SetConfocalSequentialMode" SequentialMode="2" IsApiCommand="1" BlockId="142"/>',
    ),
    "set_scan_mode": (
        lambda c: cam.set_scan_mode(c, "HiRes", "xyz"),
        '<Scanner Command="ScanMode" ScanMode="xyz" BlockId="142"/>',
    ),
    "set_scan_field_rotation": (
        lambda c: cam.set_scan_field_rotation(c, "HiRes", 5.0),
        '<Scanner Command="SetConfocalScanFieldRotation" Rotation="5" BlockId="142"/>',
    ),
    "set_image_format": (
        lambda c: cam.set_image_format(c, "HiRes", "1024 x 1024"),
        '<Scanner Command="SetFormat" FormatX="1024" FormatY="1024" BlockId="142" IsAutoFocusDefinition="0"/>',
    ),
    "set_frame_accumulation": (
        lambda c: cam.set_frame_accumulation(c, "HiRes", 0, 2),
        '<Scanner Command="SetFrameAccu" FrameAccu="2" SetMode="2" SingleSettingIndex="0" BlockId="142"/>',
    ),
    "set_frame_average": (
        lambda c: cam.set_frame_average(c, "HiRes", 0, 2),
        '<Scanner Command="SetFrameAverage" FrameAverage="2" SetMode="2" SingleSettingIndex="0" BlockId="142"/>',
    ),
    "set_line_accumulation": (
        lambda c: cam.set_line_accumulation(c, "HiRes", 0, 2),
        '<Scanner Command="SetLineAccu" LineAccu="2" SetMode="2" SingleSettingIndex="0" BlockId="142"/>',
    ),
    "set_line_average": (
        lambda c: cam.set_line_average(c, "HiRes", 0, 2),
        '<Scanner Command="SetLineAverage" LineAverage="2" SetMode="2" SingleSettingIndex="0" BlockId="142"/>',
    ),
    "set_pinhole_airy": (
        lambda c: cam.set_pinhole_airy(c, "HiRes", 0, 1.2),
        '<Scanner Command="SetPinholeAU" PinholeAU="1.2" SetMode="2" SingleSettingIndex="0" BlockId="142"/>',
    ),
    "set_detector_gain": (
        lambda c: cam.set_detector_gain(c, "HiRes", 0, "40;2", 2.5),
        '<Scanner Command="SetDetectorGain" DetectorGain="2.5" SetMode="2" SingleSettingIndex="0" BlockId="142">'
        + beam(40, 2)
        + "</Scanner>",
    ),
    "set_laser_intensity": (
        lambda c: cam.set_laser_intensity(c, "HiRes", 0, "30", 0, 0.03),
        '<Scanner Command="SetLaserLineIntensity" LineIndex="0" LineIntensity="0.03" SetMode="2" '
        'SingleSettingIndex="0" BlockId="142" IsRoiBackground="0">'
        + beam(30)
        + "</Scanner>",
    ),
    "set_laser_shutter": (
        lambda c: cam.set_laser_shutter(c, "HiRes", 0, "30", True),
        '<Scanner Command="ActivateShutter" Activate="1" SetMode="2" SingleSettingIndex="0" BlockId="142">'
        + beam(30)
        + "</Scanner>",
    ),
    "set_filter_wheel_slot": (
        lambda c: cam.set_filter_wheel_slot(c, "HiRes", 0, "20", 180, 1),
        '<Scanner Command="FilterWheel_ApiSetSlot" FilterWheelType="180" SlotIndex="1" SetMode="2" '
        'SingleSettingIndex="0" BlockId="142">' + beam(20) + "</Scanner>",
    ),
    "set_filter_wheel_spectrum": (
        lambda c: cam.set_filter_wheel_spectrum(c, "HiRes", 0, "", 140, 10.0),
        (
            '<Scanner Command="FilterWheel_SetSpectrumPosition" FilterWheelType="140" FilterSpectrumPosition="10" '
            'SetMode="2" SingleSettingIndex="0" BlockId="142" IsApiCommand="1"><BeamRoute Version="0"/></Scanner>'
        ),
    ),
    "select_job": (
        lambda c: cam.select_job(c, "Overview"),
        (
            '<ATL Command="ATL_Sequence" BlockIDOfSequence="151" SubCommand="SetCurrentSelectedElementID" '
            'ElementID="2" DoNotActivateBlockFromElement="0"/>'
        ),
    ),
    "set_z_stack_step_size": (
        lambda c: cam.set_z_stack_step_size(c, "AF Job", 2.409),
        '<Scanner Command="SetStackStepSize" StepSizeInMeter="2.409E-06" BlockId="140"/>',
    ),
    "set_z_stack_size": (
        lambda c: cam.set_z_stack_size(c, "AF Job", 38.55),
        '<Scanner Command="SetZStackSize" ZStackSize="3.855E-05" BlockId="140"/>',
    ),
    "set_z_stack_definition": (
        lambda c: cam.set_z_stack_definition(c, "AF Job", -19.28, 19.27),
        (
            '<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="1" BeginValue="-1.928E-05" '
            'EndValue="1.927E-05" BlockId="140"/>'
        ),
    ),
    "set_objective": (
        lambda c: cam.set_objective(
            c, "HiRes", objectives(c), slot_index=1, compensate=False
        ),
        '<Microscope Command="SetObjectiveSlotIndex" SlotIndex="1" BlockId="142"/>',
    ),
    "move_xy": (
        lambda c: cam.move_xy(c, 66025.0, 43000.0),
        (
            '<Scanner Command="SetStagePositionInHwOnly" StagePosX="0.066025" StagePosY="0.043" '
            'RelativeCoordinates="0" MoveStageX="1" MoveStageY="1"/>'
        ),
    ),
    "move_z galvo": (
        lambda c: cam.move_z(c, "HiRes", 2.0, z_mode="galvo"),
        '<Scanner Command="SetZPosition" PosZ="2E-06" RelativeCoordinates="0" ZUseMode="1" BlockId="142"/>',
    ),
    "move_z z-wide": (
        lambda c: cam.move_z(c, "HiRes", 5.0, z_mode="zwide"),
        '<Scanner Command="SetZPosition" PosZ="5E-06" RelativeCoordinates="0" ZUseMode="2" BlockId="142"/>',
    ),
}


@pytest.mark.parametrize("name", list(GOLDEN))
def test_sends_what_the_cam_api_sends(name, client, server):
    call, expected = GOLDEN[name]
    result = call(direct.route(client))
    assert server.sent[0] == expected
    if name.startswith("move_"):
        # Moves fire without an error check and are re-sent while unconfirmed,
        # on the CAM route too: every send is the same element.
        assert set(server.sent) == {expected}
    else:
        assert server.sent == [
            expected
        ]  # the stand-in refused it: one send, no re-fire
        assert result["success"] is False


NOT_TRANSLATED = {
    "acquire": lambda c: cam.acquire(c, "HiRes"),
}


@pytest.mark.parametrize("name", list(NOT_TRANSLATED))
def test_a_command_without_a_verified_translation_sends_nothing(name, client, server):
    result = NOT_TRANSLATED[name](direct.route(client))
    assert result["success"] is False
    assert "no direct translation" in str(result.get("message")) + str(
        result.get("logs")
    )
    assert server.sent == []


class TestAcceptedEndToEnd:
    def test_zoom_confirms_in_one_attempt_like_the_cam_route(self, client, server):
        server.answer = ACCEPTED
        by_cam = install_permissive_limits(MockLasxClient())
        cam_result = cam.set_zoom(by_cam, "HiRes", 1.5)
        direct_result = cam.set_zoom(direct.route(client), "HiRes", 1.5)
        outcome = lambda r: (r["success"], r["confirmed"], r["timing"]["attempts"])
        assert outcome(direct_result) == outcome(cam_result) == (True, True, 1)
        assert server.sent == ['<Scanner Command="SetZoom" Zoom="1.5" BlockId="142"/>']

    def test_las_x_refusal_text_reaches_the_result(self, client, server):
        server.answer = (
            '<Result Error="1">The zoom parameter (100.000) is out of range. '
            "Value has to be between 0.750 and 48.000</Result>"
        )
        result = cam.set_zoom(direct.route(client), "HiRes", 100.0)
        assert result["success"] is False
        assert "out of range" in result["message"]
        assert len(server.sent) == 1


class TestTheRouteIsTheSameSession:
    def test_without_limits_both_routes_refuse_and_nothing_is_sent(self, monkeypatch):
        bare = MockLasxClient()
        stand_in = StandInServer(bare)
        monkeypatch.setattr(hardware_server, "attach", lambda: stand_in)
        hardware_server.forget_connection()
        cam_result = cam.set_zoom(bare, "HiRes", 1.5)
        direct_result = cam.set_zoom(direct.route(bare), "HiRes", 1.5)
        assert cam_result["success"] is direct_result["success"] is False
        assert direct_result["message"] == cam_result["message"]
        assert stand_in.sent == []

    def test_an_unknown_job_sends_nothing(self, client, server):
        result = cam.set_zoom(direct.route(client), "No Such Job", 1.5)
        assert result["success"] is False
        assert "No Such Job" in str(result.get("message")) + str(result.get("logs"))
        assert server.sent == []

    def test_no_hardware_server_fails_without_sending(self, client):
        # The suite's conftest makes the hardware server unreachable.
        result = cam.set_zoom(direct.route(client), "HiRes", 1.5)
        assert result["success"] is False
        assert (
            readers.get_job_settings(client, "HiRes", mode="api")["zoom"]["current"]
            == 10.0
        )


class TestReads:
    """Job settings, hardware info and stage XY are read from the hardware server.

    On the simulator its answers equal the CAM API's (2026-10-12): the job
    settings of all three jobs and the hardware info JSON identical, XY to the
    last digit. The job list and the scan status stay on the CAM API: no
    hardware-server form of them was seen.
    """

    def test_job_settings(self, client, server):
        assert readers.get_job_settings(direct.route(client), "HiRes", mode="api") == {
            **api_reader.get_job_settings(client, "HiRes"),
            "jobName": "HiRes",
            "id": 142,
        }
        assert server.reads == [
            '<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="142"/>'
        ]

    def test_hardware_info(self, client, server):
        assert readers.get_hardware_info(direct.route(client), mode="api") == (
            api_reader.get_hardware_info(client)
        )
        assert server.reads == ['<Scanner Command="GetConfocalHardwareInfoAsJson"/>']

    def test_stage_xy(self, client, server):
        client._stage_x, client._stage_y = 0.066025, 0.043
        assert readers.get_xy(direct.route(client), mode="api") == {
            "x": 0.066025,
            "y": 0.043,
            "x_um": pytest.approx(66025.0),
            "y_um": pytest.approx(43000.0),
        }
        assert server.reads == ['<Scanner Command="GetStageHwPosition"/>']

    def test_the_job_list_stays_on_the_cam_api(self, client, server):
        assert readers.get_jobs(
            direct.route(client), mode="api"
        ) == api_reader.get_jobs(client)
        assert server.reads == []

    def test_a_read_las_x_refuses_is_no_answer(self, client, server, monkeypatch):
        monkeypatch.setattr(
            server, "read", lambda xml: '<Result Error="invalid block identifier"/>'
        )
        assert api_reader.get_job_settings(direct.route(client), "HiRes") is None

    def test_confirmation_reads_the_server_too(self, client, server):
        server.answer = ACCEPTED
        result = cam.set_zoom(direct.route(client), "HiRes", 1.5)
        assert (result["success"], result["confirmed"]) == (True, True)
        assert (
            '<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="142"/>'
            in server.reads
        )


class TestAdjusted:
    """LAS X answers ``Error="2"`` when it applied a value other than the one
    asked for (pinhole 100 airy was set to 2.512 on the simulator). The CAM
    API reports that as accepted; the read-back then shows the difference."""

    def test_an_adjusted_value_is_accepted_and_left_to_the_read_back(
        self, client, server
    ):
        server.answer = '<Result Error="2">The requested pinhole airy (100.000) has been adjusted to 2.512</Result>'
        result = cam.set_pinhole_airy(direct.route(client), "HiRes", 0, 100.0)
        assert (result["success"], result["confirmed"]) == (True, False)


class TestJobIds:
    """Job ids come from LAS X's state tree. Reads reuse a job's id, checked
    against the job name in LAS X's answer; writes always look it up. Job ids
    change when LAS X reloads the experiment (HiRes 142 -> 145 -> 152 on the
    simulator), and the CAM API's own list can keep the old ones."""

    def tree_lookups(self, monkeypatch):
        calls = []
        original = direct_client.job_sequence
        monkeypatch.setattr(
            direct_client, "job_sequence", lambda: (calls.append(1), original())[1]
        )
        return calls

    def test_repeated_reads_look_the_id_up_once(self, client, server, monkeypatch):
        calls = self.tree_lookups(monkeypatch)
        route = direct.route(client)
        for _ in range(3):
            assert api_reader.get_job_settings(route, "HiRes")["jobName"] == "HiRes"
        assert len(calls) == 1

    def test_every_write_looks_the_id_up(self, client, server, monkeypatch):
        route = direct.route(client)
        api_reader.get_job_settings(route, "HiRes")
        calls = self.tree_lookups(monkeypatch)
        cam.set_zoom(route, "HiRes", 1.5)
        cam.set_zoom(route, "HiRes", 2.0)
        assert len(calls) == 2

    def test_the_tree_wins_over_a_stale_cam_job_list(self, client, server):
        # The CAM API still lists HiRes as 142; LAS X has moved it to 152.
        server.ServerNodes.jobs = lambda: {
            "HiRes": (152, 3),
            "Overview": (154, 2),
            "AF Job": (156, 1),
        }
        cam.set_zoom(direct.route(client), "HiRes", 1.5)
        assert server.sent == ['<Scanner Command="SetZoom" Zoom="1.5" BlockId="152"/>']

    def test_a_reloaded_experiment_is_followed(self, client, server, monkeypatch):
        route = direct.route(client)
        assert api_reader.get_job_settings(route, "HiRes")["id"] == 142
        monkeypatch.setitem(JOB_IDS, "HiRes", 145)
        monkeypatch.setitem(
            JOB_IDS, "Overview", 142
        )  # the old id now names another job
        settings = api_reader.get_job_settings(route, "HiRes")
        assert (settings["jobName"], settings["id"]) == ("HiRes", 145)


class TestSelectJob:
    """select_job by the job's element in LAS X's sequence, ids from the state tree."""

    def test_an_accepted_selection_is_confirmed_like_the_cam_route(
        self, client, server
    ):
        server.answer = ACCEPTED
        by_cam = install_permissive_limits(MockLasxClient())
        cam_result = cam.select_job(by_cam, "Overview")
        direct_result = cam.select_job(direct.route(client), "Overview")
        assert (
            (direct_result["success"], direct_result["confirmed"])
            == (
                cam_result["success"],
                cam_result["confirmed"],
            )
            == (True, True)
        )
        assert client._selected_job == "Overview"
        assert server.sent == [
            (
                '<ATL Command="ATL_Sequence" BlockIDOfSequence="151" SubCommand="SetCurrentSelectedElementID" '
                'ElementID="2" DoNotActivateBlockFromElement="0"/>'
            )
        ]
