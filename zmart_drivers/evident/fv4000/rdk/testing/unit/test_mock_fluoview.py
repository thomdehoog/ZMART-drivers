"""The pretend FLUOVIEW behaves as the manual describes, in-process and over a real XML-RPC connection."""

import time

import pytest
from mock_fluoview import MockFluoview, MockRdkClient, serve

from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client
from zmart_drivers.evident.fv4000.rdk.primitives.rdk import image_data, reads


def test_every_answer_carries_a_result_code(mock):
    assert mock.call("Maintenance.getVersion")["result"] == "OK"
    assert mock.call("Parameter.getParameter", {"settingId": "NO_SUCH_SETTING"}) == {
        "result": "INVALID_PARAMETERS"
    }
    assert mock.call(
        "Parameter.setParameter",
        {"settingId": "XY_STAGE_POSITION_SETTING", "x": "1", "y": 2},
    ) == {"result": "INVALID_PARAMETERS"}


def test_a_scan_is_scanning_then_idling_and_then_its_file_opens(mock):
    mock.scan_s = 0.1
    assert (
        mock.call("IDA.open", {"filename": "D:/Images/image_0001.oir"})["result"]
        == "FILE_NOT_FOUND"
    )
    started = mock.call(
        "Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
    )
    path = started["targetName"][0]["name"]
    assert (
        mock.call(
            "Protocol.getProtocolProgress",
            {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"},
        )["state"]
        == "SCANNING"
    )
    # Not allowed while scanning (the manual's section 3.6).
    assert (
        mock.call(
            "Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
        )["result"]
        == "UNAVAILABLE"
    )
    assert (
        mock.call(
            "Parameter.setParameter",
            {"settingId": "LSM_SCANNER_TYPE_SETTING", "scannerType": "RESONANT"},
        )["result"]
        == "UNAVAILABLE"
    )
    assert mock.call("IDA.open", {"filename": path})["result"] == "FILE_NOT_FOUND"
    time.sleep(0.15)
    assert (
        mock.call(
            "Protocol.getProtocolProgress",
            {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"},
        )["state"]
        == "IDLING"
    )
    assert mock.call("IDA.open", {"filename": path})["result"] == "OK"


def test_stop_is_refused_when_nothing_runs_and_stops_a_scan(mock):
    assert (
        mock.call(
            "Protocol.stopProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
        )["result"]
        == "UNAVAILABLE"
    )
    mock.scan_s = 10
    mock.call("Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"})
    assert (
        mock.call(
            "Protocol.stopProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
        )["result"]
        == "OK"
    )
    assert mock.state == "IDLING"


def test_the_stage_takes_a_moment_to_arrive(mock):
    mock.travel_s = 0.1
    mock.call(
        "Parameter.setParameter",
        {
            "settingId": "XY_STAGE_POSITION_SETTING",
            "x": 10_100_000,
            "y": 8_000_000,
            "escapeEnabled": False,
        },
    )
    on_the_way = mock.call(
        "Parameter.getParameter", {"settingId": "XY_STAGE_POSITION_SETTING"}
    )["x"]
    time.sleep(0.12)
    arrived = mock.call(
        "Parameter.getParameter", {"settingId": "XY_STAGE_POSITION_SETTING"}
    )["x"]
    assert 10_000_000 <= on_the_way < 10_100_000 and arrived == 10_100_000


def test_laser_power_is_clipped_to_the_lasers_range_as_the_manual_says(mock):
    mock.call(
        "Parameter.setParameter",
        {
            "settingId": "LSM_LASER_INTENSITY_SETTING",
            "laserId": "LASER_488",
            "laserIntensity": 150.0,
        },
    )
    assert mock.lasers["LASER_488"]["intensity"] == 100.0
    mock.call(
        "Parameter.setParameter",
        {
            "settingId": "LSM_LASER_INTENSITY_SETTING",
            "laserId": "LASER_488",
            "laserIntensity": 0.01,
        },
    )
    assert mock.lasers["LASER_488"]["intensity"] == 0.0


def test_a_picture_is_read_back_whole_through_the_ida_calls(client, mock):
    mock.scan_s = 0.0
    mock.body_piece = (
        10_000  # several pieces per plane, so getNextImageBody is exercised
    )
    path = mock.call(
        "Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
    )["targetName"][0]["name"]
    with image_data.OpenedImage(client, path) as image:
        description = image.description
        assert (description.width, description.height) == (256, 256)
        assert [c.id for c in description.channels] == ["CH1", "CH2"]
        assert description.pixel_length_um == (1.242, 1.242)
        plane = image.read_plane("CH1")
    assert plane.shape == (256, 256) and plane.dtype == "uint16" and plane.max() > 1000
    assert mock.handles == {}, "every id is released again"


def test_the_mock_is_served_over_real_xml_rpc_to_the_real_client(mock, monkeypatch):
    mock.scan_s = 0.0
    server, _thread = serve(mock)
    try:
        real = rdk_client.RdkClient(
            "127.0.0.1", server.server_address[1], min_gap_s=0.0
        )
        assert reads.get_version(real) == MockFluoview.VERSION
        assert reads.get_xy(real) == {"x_um": 10000.0, "y_um": 8000.0}
        assert reads.get_objectives(real)[0] == {"slot": 0, "name": "UPLXAPO10X"}
        path = real.call(
            "Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
        )["targetName"][0]["name"]
        with image_data.OpenedImage(real, path) as image:
            plane = image.read_plane("CH1")
        assert plane.shape == (256, 256), "binary pixels travel over the wire"
        assert real.calls_made > 5 and real.last_reply["result"] == "OK"
    finally:
        server.shutdown()
        server.server_close()


def test_a_closed_port_is_a_lost_connection_in_words():
    real = rdk_client.RdkClient("127.0.0.1", 1, min_gap_s=0.0, timeout_s=1.0)
    with pytest.raises(rdk_client.ConnectionLost) as caught:
        real.call("Maintenance.getVersion")
    assert "FLUOVIEW" in str(caught.value)


def test_the_client_keeps_a_gap_between_calls(mock):
    real = MockRdkClient(mock)
    real.call("Maintenance.getVersion")
    assert real.last_reply["version"] == MockFluoview.VERSION
    paced = rdk_client.RdkClient("127.0.0.1", 1, min_gap_s=0.05)
    paced._last_call_at = time.monotonic()
    started = time.monotonic()
    paced._pace()
    assert time.monotonic() - started >= 0.04


# --- one try per call -------------------------------------------------------------------------


class _OneShotServer:
    """A server that reads each request and then misbehaves: closes the connection, or never answers."""

    def __init__(self, behaviour):
        import socket
        import threading

        self.behaviour = behaviour
        self.requests = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(65536)
                if data:
                    self.requests += 1
                if self.behaviour == "silent":
                    time.sleep(1.5)

    def close(self):
        self.sock.close()


@pytest.mark.parametrize("behaviour", ["reset", "silent"])
def test_a_call_broken_after_it_was_sent_is_sent_once_and_said_to_be_uncertain(
    behaviour,
):
    server = _OneShotServer(behaviour)
    try:
        real = rdk_client.RdkClient(
            "127.0.0.1", server.port, min_gap_s=0.0, timeout_s=0.5
        )
        with pytest.raises(
            rdk_client.CallInterrupted, match="may or may not have carried it out"
        ):
            real.call(
                "Protocol.startProtocol",
                {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"},
            )
        time.sleep(0.2)
        assert server.requests == 1, (
            "Python's transport must not send the capture a second time"
        )
    finally:
        server.close()


def test_each_call_opens_and_closes_its_own_connection(mock):
    server, _thread = serve(mock)
    try:
        real = rdk_client.RdkClient(
            "127.0.0.1", server.server_address[1], min_gap_s=0.0
        )
        for _ in range(3):
            assert reads.get_version(real) == MockFluoview.VERSION
        assert real._proxy("transport")._connection == (None, None)
    finally:
        server.shutdown()
        server.server_close()


def test_channel_info_is_read_from_struct_value_as_the_manual_says(client, mock):
    mock.scan_s = 0.0
    path = mock.call(
        "Protocol.startProtocol", {"executionType": "EXECUTION_TYPE_MANUAL_MAIN"}
    )["targetName"][0]["name"]
    with image_data.OpenedImage(client, path) as image:
        first = image.description.channels[0]
        assert (first.name, first.bit_count, first.device_name) == (
            "CH1",
            12,
            "PMT CH1",
        )
        with pytest.raises(ValueError, match="no ZSTACK axis"):
            image.read_plane("CH1", z=3)


def test_a_comma_joined_list_is_split_and_extra_image_ids_are_released():
    assert image_data._as_list(["ZSTACK,TIMELAPSE"]) == ["ZSTACK", "TIMELAPSE"]
    assert image_data._as_list([["CH1", "CH2"]]) == ["CH1", "CH2"]
    assert image_data._as_list("CH1, CH2") == ["CH1", "CH2"]

    released = []

    class TwoIds:
        def call(self, method, payload=None):
            if method == "IDA.getImage":
                return {"result": "OK", "imageId": ["image-1", "image-2"]}
            if method == "IDA.releaseImage":
                released.append(payload["imageId"])
            return {"result": "OK"}

    opened = image_data.OpenedImage(TwoIds(), "x.oir")
    opened.area_id = "area-1"
    assert opened._image_id("CH1", {}) == "image-1"
    assert released == ["image-2"]
