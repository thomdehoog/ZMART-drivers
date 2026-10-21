"""
Unit tests: the job sequence read from the hardware server's state tree.
================================================================================
LAS X keeps its jobs in the hardware server's state tree under
Acquire/ATL/CurrentSequenceList: the sequence's id, the selected element,
and per job its name, live block id and element id: what selecting a job
by its element needs. Read on the simulator,
2026-10-13: HiRes 152/3, Overview 154/2, AF Job 156/1, sequence 151 — while
the CAM API's own job list still said 145/147/149 after an experiment load.
"""

import pytest
from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection import (
    hardware_server,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.direct import (
    state_tree,
)

SIMULATOR = {"HiRes": (152, 3), "Overview": (154, 2), "AF Job": (156, 1)}


class StandInServer:
    def __init__(self, nodes):
        self.ServerNodes = nodes


@pytest.fixture
def nodes(monkeypatch):
    stand_in = StandInNodes(lambda: SIMULATOR, selected=lambda: "HiRes")
    monkeypatch.setattr(hardware_server, "attach", lambda: StandInServer(stand_in))
    hardware_server.forget_connection()
    return stand_in


class TestSequence:
    def test_the_jobs_their_ids_and_the_selection(self, nodes):
        sequence = state_tree.job_sequence()
        assert sequence.block_id == 151
        assert sequence.selected_element == 3
        assert sequence.jobs == {
            "HiRes": state_tree.Job(block_id=152, element_id=3),
            "Overview": state_tree.Job(block_id=154, element_id=2),
            "AF Job": state_tree.Job(block_id=156, element_id=1, autofocus=True),
        }

    def test_the_selected_job_by_name(self, nodes):
        assert state_tree.job_sequence().selected_job == "HiRes"

    def test_a_reloaded_experiment_reads_the_new_ids(self, nodes):
        assert state_tree.job_sequence().jobs["HiRes"].block_id == 152
        nodes.jobs = lambda: {**SIMULATOR, "HiRes": (160, 3)}
        assert state_tree.job_sequence().jobs["HiRes"].block_id == 160

    def test_no_hardware_server_raises(self):
        # The suite's conftest makes the hardware server unreachable.
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            state_tree.job_sequence()

    def test_a_tree_without_the_sequence_raises(self, monkeypatch):
        class Bare:
            def GetNode(self, node_id):
                return ("Root", 2, 0, None) if node_id == 1 else ("", 0, 0, None)

            def RegisterNode(self, node_id, out):
                return None

        monkeypatch.setattr(hardware_server, "attach", lambda: StandInServer(Bare()))
        hardware_server.forget_connection()
        with pytest.raises(hardware_server.HardwareServerUnavailable, match="Acquire"):
            state_tree.job_sequence()


class TestAcquisitionStatus:
    """What LAS X is doing now, from the tree: Acquire/AcquisitionState (idle,
    continuous, single, series, busy — the names the CAM API's scan status
    uses), and under Acquire/ATL the running block, element and setting and
    the pause state."""

    def test_at_rest(self, nodes):
        status = state_tree.acquisition_status()
        assert status.scan_state == "eScanIdle"
        assert status.selected_job == "HiRes"
        assert status.running_job is None
        assert status.paused is False

    def test_a_series_scan_of_overview(self, nodes):
        nodes.status = {
            "AcquisitionState": 3,
            "CurrentRunningBlockID": 154,
            "CurrentRunningElementID": 2,
        }
        status = state_tree.acquisition_status()
        assert status.scan_state == "eScanSeries"
        assert status.running_job == "Overview"

    def test_an_unknown_scan_state_is_named_as_such(self, nodes):
        nodes.status = {"AcquisitionState": 9}
        assert state_tree.acquisition_status().scan_state == "unknown (9)"
