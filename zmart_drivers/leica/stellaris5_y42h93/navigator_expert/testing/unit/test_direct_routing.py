"""
Unit tests: X, Y, z and the selected job, read by the direct reader first.
================================================================================
LAS X's hardware server keeps its live state as a tree of named nodes, which
the direct reader reads. The routed readers ask it first (the profile's
``direct`` source) and fall back to the
hybrid reader (CAM API and LAS X log) when the hardware server cannot be
reached. Leica agreed to these reads (2026-10-13); the scan status is not
among them and stays on the CAM API and the log. Measured on the simulator,
2026-10-13: XY from the tree in 1.1 ms (CAM API 20 ms, worst 57 ms); after
a stage move the tree had the new
position in 3.3-4.2 ms, the CAM API in 22-58 ms. The command confirmations
read through these readers, so they confirm from the tree too.

A stand-in tree mirrors the behavioural mock: its stage, the true position
of each z drive (the mock's ``_hardware_z_um``, which moves even for the
drive whose job settings freeze) and the selected job.
"""

import pytest
from limits_fixtures import install_permissive_limits
from mock_lasx_api import MockLasxClient
from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    set as commands,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    read as readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    hardware_server,
    state_tree,
)

#: The jobs' block and element ids as on the simulator (2026-10-13).
JOBS = {"AF Job": (156, 1), "Overview": (154, 2), "HiRes": (152, 3)}


class StandInServer:
    def __init__(self, nodes):
        self.ServerNodes = nodes


@pytest.fixture
def client():
    return install_permissive_limits(MockLasxClient(), wide_stage=True)


@pytest.fixture
def tree(client, monkeypatch):
    nodes = StandInNodes(
        lambda: JOBS,
        selected=lambda: client._selected_job,
        stage=lambda: (client._stage_x, client._stage_y),
        zdrives=lambda: {
            1: client._hardware_z_um["z-galvo"] * 1e-6,
            2: client._hardware_z_um["z-wide"] * 1e-6,
        },
    )
    monkeypatch.setattr(hardware_server, "attach", lambda: StandInServer(nodes))
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    return nodes


class TestFromTheTree:
    def test_xy(self, client, tree):
        reading = readers.get_xy(client, diagnostics=True)
        assert reading.source == "direct"
        assert reading.value == {"x": 0.05, "y": 0.03, "x_um": 50000.0, "y_um": 30000.0}

    def test_the_scan_status_is_not_read_directly(self, client, tree):
        reading = readers.get_scan_status(client, diagnostics=True)
        assert reading.source in ("api", "log")
        assert reading.value == "eScanIdle"

    def test_selected_job(self, client, tree):
        reading = readers.get_selected_job(client, diagnostics=True)
        assert reading.source == "direct"
        assert reading.value == {"Name": "HiRes", "ID": 152, "IsSelected": True, "IsAutofocus": False}

    def test_both_z_drives_in_micrometres(self, client, tree):
        client._hardware_z_um.update({"z-galvo": 2.5, "z-wide": 4999.5})
        assert readers.read_z_um(client, "HiRes", "z-galvo") == pytest.approx(2.5)
        assert readers.read_z_um(client, "HiRes", "z-wide") == pytest.approx(4999.5)
        assert readers.read_zwide_um(client, "HiRes") == pytest.approx(4999.5)

    def test_the_z_of_a_stacked_drive_is_the_true_one(self, client, tree):
        # The job settings freeze the drive carrying the stack; the tree does not.
        client._jobs["HiRes"]["stack"] = {"begin": -10.0, "end": 10.0, "sections": 21, "mode": "z-galvo"}
        client._hardware_z_um["z-galvo"] = 7.0
        assert readers.read_z_um(client, "HiRes", "z-galvo") == pytest.approx(7.0)


class TestFallback:
    """Without the hardware server (the suite's conftest), the hybrid reader answers."""

    def test_xy(self, client):
        reading = readers.get_xy(client, diagnostics=True)
        assert reading.source in ("api", "log")
        assert reading.value["x_um"] == pytest.approx(50000.0)

    def test_selected_job(self, client):
        assert readers.get_selected_job(client)["Name"] == "HiRes"

    def test_z_from_the_job_settings(self, client):
        client._jobs["HiRes"]["zPosition"]["z-wide"]["position"] = 123.0
        assert readers.read_z_um(client, "HiRes", "z-wide") == pytest.approx(123.0)


class TestRememberedNodes:
    def test_a_second_read_does_not_walk_the_tree(self, client, tree):
        readers.get_xy(client)
        walked = tree.node_calls
        readers.get_xy(client)
        assert tree.node_calls - walked <= 2  # a check of each remembered node, no walk

    def test_a_reloaded_experiment_is_followed(self, client, tree):
        assert readers.get_selected_job(client)["ID"] == 152
        tree.jobs = lambda: {"AF Job": (166, 1), "Overview": (164, 2), "HiRes": (162, 3)}
        tree.reload()
        assert readers.get_selected_job(client)["ID"] == 162


class TestSuccessChecks:
    """move_xy, move_z and select_job confirm from the direct reader."""

    def test_move_xy(self, client, tree):
        result = commands.move_xy(client, 50025.0, 30000.0)
        assert (result["success"], result["confirmed"]) == (True, True)
        assert readers.get_xy(client)["x_um"] == pytest.approx(50025.0)

    def test_move_z_on_the_stacked_drive(self, client, tree):
        client._jobs["HiRes"]["stack"] = {"begin": -10.0, "end": 10.0, "sections": 21, "mode": "z-galvo"}
        result = commands.move_z(client, "HiRes", 12.0, z_mode="galvo")
        assert (result["success"], result["confirmed"], result["timing"]["attempts"]) == (True, True, 1)

    def test_select_job(self, client, tree):
        result = commands.select_job(client, "Overview")
        assert (result["success"], result["confirmed"]) == (True, True)
        assert client._selected_job == "Overview"

    def test_selecting_the_selected_job_sends_nothing(self, client, tree):
        result = commands.select_job(client, "HiRes")
        assert result["success"] is True
        assert "already selected" in result["message"]


class TestEveryDatumDirectly:
    """Asked for the direct reader, every routed datum answers from it, or falls back when it cannot."""

    def test_scan_status(self, client, tree):
        reading = readers.get_scan_status(client, mode="direct", diagnostics=True)
        assert (reading.source, reading.value) == ("direct", "eScanIdle")

    def test_jobs(self, client, tree):
        reading = readers.get_jobs(client, mode="direct", diagnostics=True)
        assert reading.source == "direct"
        assert [j["Name"] for j in reading.value] == ["AF Job", "Overview", "HiRes"]

    def test_hardware_info(self, client, tree):
        reading = readers.get_hardware_info(client, mode="direct", diagnostics=True)
        assert reading.source == "direct"
        assert reading.value["SerialNumber"] == "STELLARIS SIMULATOR"

    def test_the_selected_jobs_settings(self, client, tree):
        reading = readers.get_job_settings(client, "HiRes", mode="direct", diagnostics=True)
        assert (reading.source, reading.value["jobName"]) == ("direct", "HiRes")

    def test_the_field_of_view_through_the_settings(self, client, tree):
        assert readers.get_fov(client, "HiRes", mode="direct") == pytest.approx((0.0011625, 0.0011625))

    def test_another_jobs_settings_fall_back(self, client, tree):
        reading = readers.get_job_settings(client, "Overview", mode="direct", diagnostics=True)
        assert reading.source in ("api", "log")
        assert reading.value["objective"]["slotIndex"] is not None
