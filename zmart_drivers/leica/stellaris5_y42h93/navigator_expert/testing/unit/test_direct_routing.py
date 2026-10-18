"""
Unit tests: every reading asks the direct reader first.
================================================================================
LAS X's hardware server keeps its live state as a tree of named nodes, which
the direct reader reads. The routed readers ask it first (the profile's
``direct`` source) and fall back to the hybrid reader (CAM API and LAS X
log) when it fails. Measured on the simulator, 2026-10-13: XY from the tree
in 1.1 ms (CAM API 20 ms, worst 57 ms); after a stage move the tree had the
new position in 3.3-4.2 ms, the CAM API in 22-58 ms. The command confirmations
read through these readers, so they confirm from the tree too.

A stand-in tree mirrors the behavioural mock: its stage, the true position
of each z drive (the mock's ``_hardware_z_um``, which moves even for the
drive whose job settings freeze) and the selected job.
"""

import dataclasses
import time

import pytest
from limits_fixtures import install_permissive_limits
from mock_lasx_api import MockLasxClient
from state_tree_stand_in import StandInNodes, answer_json_read

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import readings as capabilities
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import changes as commands
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.commands import (
    compensated_changes as commands_comp,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import prechecks
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import state as readers
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.direct import direct_reader, state_tree
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection import hardware_server

#: The jobs' block and element ids as on the simulator (2026-10-13).
JOBS = {"AF Job": (156, 1), "Overview": (154, 2), "HiRes": (152, 3)}


class StandInServer:
    """LAS X's hardware server: its state tree, and its answers to the two JSON reads."""

    def __init__(self, nodes, client=None):
        self.ServerNodes = nodes
        self.client = client
        self.sent = []

    def SendCommand2(self, xml, result):
        self.sent.append(xml)
        answer = answer_json_read(xml, self.client, self.ServerNodes)
        return (
            answer
            if answer is not None
            else '<Result Error="1">not a read the stand-in answers</Result>'
        )


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
    monkeypatch.setattr(hardware_server, "attach", lambda: StandInServer(nodes, client))
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    return nodes


class TestFromTheTree:
    def test_xy(self, client, tree):
        reading = readers.get_xy(client, diagnostics=True)
        assert reading.source == "direct"
        assert reading.value == {"x": 0.05, "y": 0.03, "x_um": 50000.0, "y_um": 30000.0}

    def test_selected_job(self, client, tree):
        reading = readers.get_selected_job(client, diagnostics=True)
        assert reading.source == "direct"
        assert reading.value == {
            "Name": "HiRes",
            "ID": 152,
            "IsSelected": True,
            "IsAutofocus": False,
        }

    def test_both_z_drives_in_micrometres(self, client, tree):
        client._hardware_z_um.update({"z-galvo": 2.5, "z-wide": 4999.5})
        assert readers.read_z_um(client, "HiRes", "z-galvo") == pytest.approx(2.5)
        assert readers.read_z_um(client, "HiRes", "z-wide") == pytest.approx(4999.5)
        assert readers.read_zwide_um(client, "HiRes") == pytest.approx(4999.5)

    def test_the_z_of_a_stacked_drive_is_the_true_one(self, client, tree):
        # The job settings freeze the drive carrying the stack; the tree does not.
        client._jobs["HiRes"]["stack"] = {
            "begin": -10.0,
            "end": 10.0,
            "sections": 21,
            "mode": "z-galvo",
        }
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
        tree.jobs = lambda: {
            "AF Job": (166, 1),
            "Overview": (164, 2),
            "HiRes": (162, 3),
        }
        tree.reload()
        assert readers.get_selected_job(client)["ID"] == 162


class TestSuccessChecks:
    """move_xy, move_z and select_job confirm from the direct reader."""

    def test_move_xy(self, client, tree):
        result = commands.move_xy(client, 50025.0, 30000.0)
        assert (result["success"], result["confirmed"]) == (True, True)
        assert readers.get_xy(client)["x_um"] == pytest.approx(50025.0)

    def test_move_z_on_the_stacked_drive(self, client, tree):
        client._jobs["HiRes"]["stack"] = {
            "begin": -10.0,
            "end": 10.0,
            "sections": 21,
            "mode": "z-galvo",
        }
        result = commands.move_z(client, "HiRes", 12.0, z_mode="galvo")
        assert (
            result["success"],
            result["confirmed"],
            result["timing"]["attempts"],
        ) == (True, True, 1)

    def test_select_job(self, client, tree):
        result = commands_comp.select_job(client, "Overview")
        assert (result["success"], result["confirmed"]) == (True, True)
        assert client._selected_job == "Overview"

    def test_selecting_the_selected_job_sends_nothing(self, client, tree):
        result = commands_comp.select_job(client, "HiRes")
        assert result["success"] is True
        assert "already selected" in result["message"]


class TestEveryDatumDirectly:
    """Asked for the direct reader, every routed datum answers from it, or falls back when it cannot."""

    def test_scan_status(self, client, tree):
        reading = readers.get_scan_status(client, mode="direct", diagnostics=True)
        assert (reading.source, reading.value) == ("direct", "eScanIdle")

    def test_the_jobs_and_which_is_the_autofocus_job(self, client, tree):
        reading = readers.get_jobs(client, mode="direct", diagnostics=True)
        assert reading.source == "direct"
        assert [j["Name"] for j in reading.value if j["IsAutofocus"]] == ["AF Job"]

    def test_hardware_info(self, client, tree):
        reading = readers.get_hardware_info(client, mode="direct", diagnostics=True)
        assert reading.source == "direct"
        assert reading.value == readers.get_hardware_info(client, mode="api")

    def test_the_selected_jobs_settings(self, client, tree):
        reading = readers.get_job_settings(
            client, "HiRes", mode="direct", diagnostics=True
        )
        assert reading.source == "direct"
        assert (
            reading.value["zoom"]
            == readers.get_job_settings(client, "HiRes", mode="api")["zoom"]
        )

    def test_the_field_of_view_through_the_settings(self, client, tree):
        assert readers.get_fov(client, "HiRes", mode="direct") == readers.get_fov(
            client, "HiRes", mode="api"
        )

    def test_another_jobs_settings_come_directly_too(self, client, tree):
        reading = readers.get_job_settings(
            client, "Overview", mode="direct", diagnostics=True
        )
        assert reading.source == "direct"
        assert reading.value == readers.get_job_settings(client, "Overview", mode="api")


class TestDirectByDefault:
    """The direct reader answers every reading by default; the API and the log when it fails."""

    def test_every_reading_asks_the_direct_reader_first(self, client, tree):
        assert readers.get_scan_status(client, diagnostics=True).source == "direct"
        assert readers.get_jobs(client, diagnostics=True).source == "direct"
        assert readers.get_hardware_info(client, diagnostics=True).source == "direct"
        assert (
            readers.get_job_settings(client, "HiRes", diagnostics=True).source
            == "direct"
        )
        assert readers.get_xy(client, diagnostics=True).source == "direct"
        assert readers.get_selected_job(client, diagnostics=True).source == "direct"

    def test_an_unexpected_error_falls_back(self, client, tree, monkeypatch):
        # Another LAS X version could lay the tree out differently: any error is a fallback, not a crash.
        def changed_tree():
            raise KeyError("Stage")

        monkeypatch.setattr(direct_reader, "get_xy", changed_tree)
        monkeypatch.setitem(
            capabilities.DATUMS,
            "xy",
            dataclasses.replace(capabilities.DATUMS["xy"], direct_fn=changed_tree),
        )
        reading = readers.get_xy(client, diagnostics=True)
        assert reading.source in ("api", "log")
        assert reading.value["x_um"] == pytest.approx(50000.0)

    def test_an_answer_that_does_not_count_falls_back_at_once(self, client, tree):
        tree.status = {"AcquisitionState": 9}  # a scan state this reader does not know
        started = time.monotonic()
        reading = readers.get_scan_status(client, diagnostics=True)
        assert reading.source in ("api", "log")
        assert reading.value == "eScanIdle"
        assert time.monotonic() - started < 1.0

    def test_z_falls_back_on_an_unexpected_error(self, client, tree, monkeypatch):
        def changed_tree(drive):
            raise TypeError("ZDriveList")

        monkeypatch.setattr(direct_reader, "read_z_um", changed_tree)
        client._jobs["HiRes"]["zPosition"]["z-wide"]["position"] = 123.0
        assert readers.read_z_um(client, "HiRes", "z-wide") == pytest.approx(123.0)

    def test_the_idle_check_before_a_command_asks_the_direct_reader(
        self, client, tree, monkeypatch
    ):
        asked = []
        monkeypatch.setattr(
            readers.api_reader,
            "get_scan_status",
            lambda c: asked.append(c) or "eScanIdle",
        )
        assert prechecks.check_idle(client)["success"] is True
        assert asked == []

    def test_the_end_of_a_capture_is_watched_through_the_direct_reader(
        self, client, tree, monkeypatch
    ):
        # The scan runs for three readings, then LAS X is idle again.
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import confirmations

        real, asked = readers.get_scan_status, []

        def watched(*args, **kwargs):
            asked.append((kwargs.get("mode"), kwargs.get("fallback")))
            tree.status = {
                "AcquisitionState": 2 if len(asked) <= 3 else 0
            }  # eScanSingle, then idle
            reading = real(*args, **kwargs)
            asked[-1] += (reading.source if reading is not None else None,)
            return reading

        monkeypatch.setattr(readers, "get_scan_status", watched)
        result = confirmations.confirm_acquire(
            client, start_timeout=5.0, timeout=10.0, poll_interval=0.001
        )
        assert result["success"] is True
        assert {call for call in asked} == {("direct", "api", "direct")}

    def test_a_job_switch_asks_neither_the_cam_api_nor_the_log_when_the_direct_reader_answers(
        self, client, tree, monkeypatch
    ):
        # The direct reader says HiRes is selected; a stale log line claims the
        # target, Overview, already is. The switch must still be made, and the
        # CAM API's job list is never read for it.
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import confirm_select_job

        real_jobs, api_job_lists = readers.get_jobs, []

        def jobs(*args, **kwargs):
            if kwargs.get("mode") == "api":
                api_job_lists.append(1)
            return real_jobs(*args, **kwargs)

        monkeypatch.setattr(readers, "get_jobs", jobs)
        monkeypatch.setattr(
            confirm_select_job,
            "_selected_job_name_from_log",
            lambda _profile: "Overview",
        )
        result = commands_comp.select_job(client, "Overview")
        assert (result["success"], client._selected_job) == (True, "Overview")
        assert api_job_lists == []

    def test_the_idle_check_falls_back_to_the_api_alone(self, client, monkeypatch):
        # No hardware server (the suite's conftest): the API decides, never the log, whose idle goes stale.
        monkeypatch.setattr(
            readers.log_reader, "parse_log", lambda: pytest.fail("the log was asked")
        )
        assert prechecks.check_idle(client)["success"] is True

    def test_the_selected_job_check_falls_back_on_an_unexpected_error(
        self, client, tree, monkeypatch
    ):
        def changed_tree():
            raise ValueError("CurrentSequenceList")

        monkeypatch.setattr(direct_reader, "get_selected_job", changed_tree)
        result = commands_comp.select_job(client, "Overview")
        assert (result["success"], client._selected_job) == (True, "Overview")
