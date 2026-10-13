"""
Unit tests: the direct reader, beside the API reader and the log reader.
================================================================================
The direct reader reads LAS X's live state from its hardware server's state
tree. It is a reader like the other two: the same names, and answers of the
same shape, so a caller cannot tell which source answered except by asking.
Leica agreed (2026-10-13) to the driver reading the stage, the selected job
and the z drives this way; the direct reader reads nothing else and never
writes.

Parity is checked against the API reader on the behavioural mock, whose
state a stand-in tree mirrors.
"""

import pytest
from limits_fixtures import install_permissive_limits
from mock_lasx_api import MockLasxClient
from state_tree_stand_in import StandInNodes

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    api_reader,
    direct_reader,
    hardware_server,
    state_tree,
)

#: The jobs' block and element ids as on the simulator (2026-10-13).
JOBS = {"AF Job": (156, 1), "Overview": (154, 2), "HiRes": (152, 3)}


def selection(jobs):
    """Each job's name and whether it is selected: what every reader's job list has."""
    return {job["Name"]: job["IsSelected"] for job in jobs}


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


class TestParityWithTheApiReader:
    def test_xy_is_the_api_readers_answer(self, client, tree):
        assert direct_reader.get_xy() == api_reader.get_xy(client)

    def test_xy_after_a_stage_move(self, client, tree):
        client._stage_x, client._stage_y = 0.0612, 0.0405
        assert direct_reader.get_xy() == api_reader.get_xy(client)
        assert direct_reader.get_xy()["x_um"] == pytest.approx(61200.0)

    @pytest.mark.parametrize("job", ["HiRes", "Overview", "AF Job"])
    def test_the_selected_job_is_the_api_readers(self, client, tree, job):
        client._selected_job = job
        assert direct_reader.get_selected_job()["Name"] == api_reader.get_selected_job(client)["Name"]

    def test_the_selected_job_carries_its_live_block_id(self, client, tree):
        assert direct_reader.get_selected_job() == {"Name": "HiRes", "ID": 152, "IsSelected": True}

    @pytest.mark.parametrize("job", ["HiRes", "Overview", "AF Job"])
    def test_the_jobs_and_their_selection_are_the_api_readers(self, client, tree, job):
        client._selected_job = job
        assert selection(direct_reader.get_jobs()) == selection(api_reader.get_jobs(client))


class TestJobs:
    def test_every_job_in_sequence_order_with_its_live_block_id(self, client, tree):
        assert direct_reader.get_jobs() == [
            {"Name": "AF Job", "ID": 156, "IsSelected": False},
            {"Name": "Overview", "ID": 154, "IsSelected": False},
            {"Name": "HiRes", "ID": 152, "IsSelected": True},
        ]

    def test_a_reloaded_experiment_gives_the_new_ids(self, client, tree):
        tree.jobs = lambda: {"AF Job": (166, 1), "Overview": (164, 2), "HiRes": (162, 3)}
        tree.reload()
        assert [j["ID"] for j in direct_reader.get_jobs()] == [166, 164, 162]
        assert direct_reader.get_selected_job()["ID"] == 162

    def test_get_job_by_name_as_the_other_readers_name_it(self, client, tree):
        assert direct_reader.get_job_by_name("Overview") == {
            "Name": "Overview",
            "ID": 154,
            "IsSelected": False,
        }
        assert direct_reader.get_job_by_name("No such job") is None


class TestZ:
    def test_each_drive_in_micrometres(self, client, tree):
        client._hardware_z_um.update({"z-galvo": -2.5, "z-wide": 4999.5})
        assert direct_reader.read_z_um("z-galvo") == pytest.approx(-2.5)
        assert direct_reader.read_z_um("z-wide") == pytest.approx(4999.5)

    def test_read_zwide_um_as_the_log_reader_names_it(self, client, tree):
        client._hardware_z_um["z-wide"] = 123.0
        assert direct_reader.read_zwide_um() == pytest.approx(123.0)

    def test_an_unknown_drive_is_refused(self, tree):
        with pytest.raises(ValueError, match="z-piezo"):
            direct_reader.read_z_um("z-piezo")


class TestWhatItReads:
    def test_only_what_leica_agreed_to(self):
        assert direct_reader.READS == ("xy", "z", "selected_job", "jobs")

    def test_without_the_hardware_server_it_raises(self):
        # The suite's conftest makes the hardware server unreachable.
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_xy()
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_selected_job()
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.read_z_um("z-wide")
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_jobs()

    def test_it_never_writes(self, client, tree):
        direct_reader.get_xy()
        direct_reader.get_selected_job()
        direct_reader.read_z_um("z-galvo")
        assert not hasattr(tree, "ChangeNodes")
        assert tree.reads > 0
