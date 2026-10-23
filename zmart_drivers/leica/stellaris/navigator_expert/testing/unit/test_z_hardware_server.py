"""
Unit tests: the stacked drive's Z is read from LAS X's hardware server first.
================================================================================
Once a job's z-stack is on a drive, LAS X stops refreshing that drive's
``zPosition`` in the job settings. LAS X's hardware server reports the
drive's true position (``Hardware.ZPosZGalvo`` / ``ZPosZDrive``, metres;
measured on the simulator 2026-10-12: galvo -2.38e-10 m while the job
settings said 0). So for the stacked drive the reader asks the hardware
server, and when the server cannot be reached, the experiment LAS X saves
(its ``.lrp`` holds each job's ``ZPosition``). It refuses when neither
answers, rather than return the stale number. The free drive is untouched.

The suite's conftest makes the hardware server unreachable for every test;
the tests here hand in a stand-in server instead.
"""

import math
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from z_readback_settings import raw_settings

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import reads as api_reader
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api.parsing import z_um_from_settings
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import client as hardware_server
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct.client import (
    HardwareServerUnavailable,
    read_hardware_z_um,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures import absolute_coordinate_system as _frame
from zmart_drivers.leica.stellaris.navigator_expert.procedures import readings as _readers
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes
from zmart_drivers.leica.stellaris.navigator_expert.procedures import changes


#: What the simulator's hardware server answered on 2026-10-12, at rest.
SIMULATOR_GALVO_M = -2.384185222837308e-10


#: An experiment the simulator saved (2026-10-21), cut to its jobs' z attributes:
#: AF Job, Overview and HiRes, each with ZPosition -2.38e-10 m on z-galvo.
SAVED_LRP = Path(__file__).resolve().parents[1] / "data" / "saved_experiment.lrp"


class SavingClient:
    """A LAS X client whose ``PyApiSaveExperiment`` writes the saved experiment, as LAS X does."""

    def __init__(self, writes=True):
        self.saved = []
        self.writes = writes
        client = self

        class Save:
            Model = SimpleNamespace(ExperimentName=None)

            def UpdateAwaitReceipt(self, _window_s):
                client.saved.append(self.Model.ExperimentName)
                if client.writes:
                    folder = Path(api_reader.SAVED_EXPERIMENT_FOLDER)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / (api_reader.SAVED_EXPERIMENT_NAME + ".lrp")).write_bytes(SAVED_LRP.read_bytes())
                return True

        self.PyApiSaveExperiment = Save()


def saved(client, job_name):
    """The saved experiment of *job_name*, read through *client*, as the driver hands it to the z readers."""
    return partial(api_reader.read_saved_z_um, client, job_name)


class StandInHardware:
    """The ``Hardware`` object of the hardware server: the two z properties, in metres."""

    def __init__(self, galvo_m=0.0, wide_m=0.0):
        self.ZPosZGalvo = galvo_m
        self.ZPosZDrive = wide_m


class StandInServer:
    def __init__(self, galvo_m=0.0, wide_m=0.0):
        self.Hardware = StandInHardware(galvo_m, wide_m)


class BrokenServer:
    """A server whose process went away: every property read fails, as COM does."""

    @property
    def Hardware(self):
        raise OSError("The RPC server is unavailable.")


@pytest.fixture
def server(monkeypatch):
    """Hand in one stand-in server; counts how often the driver attaches to it."""
    stand_in = StandInServer()
    attaches = []

    def attach():
        attaches.append(stand_in)
        return stand_in

    monkeypatch.setattr(hardware_server, "attach", attach)
    hardware_server.forget_connection()
    stand_in.attaches = attaches
    return stand_in


class TestReadHardwareZ:
    def test_galvo_in_micrometres(self, server):
        server.Hardware.ZPosZGalvo = SIMULATOR_GALVO_M
        assert read_hardware_z_um("z-galvo") == pytest.approx(-2.384185222837308e-4)

    def test_wide_in_micrometres(self, server):
        server.Hardware.ZPosZDrive = 4.9995e-3
        assert read_hardware_z_um("z-wide") == pytest.approx(4999.5)

    def test_follows_the_drive(self, server):
        server.Hardware.ZPosZGalvo = 5e-6
        assert read_hardware_z_um("z-galvo") == pytest.approx(5.0)
        server.Hardware.ZPosZGalvo = 0.0
        assert read_hardware_z_um("z-galvo") == 0.0

    def test_attaches_once_and_reuses_the_connection(self, server):
        read_hardware_z_um("z-galvo")
        read_hardware_z_um("z-wide")
        read_hardware_z_um("z-galvo")
        assert len(server.attaches) == 1

    def test_an_unknown_drive_is_a_programming_error(self, server):
        with pytest.raises(ValueError, match="z-tilt"):
            read_hardware_z_um("z-tilt")

    def test_no_server_running_raises(self):
        # The suite's conftest makes attach fail, as on a PC without LAS X.
        with pytest.raises(HardwareServerUnavailable):
            read_hardware_z_um("z-galvo")

    @pytest.mark.parametrize("answer", [None, "0.0", math.nan, math.inf])
    def test_an_answer_that_is_not_a_position_raises(self, server, answer):
        server.Hardware.ZPosZGalvo = answer
        with pytest.raises(HardwareServerUnavailable, match="ZPosZGalvo"):
            read_hardware_z_um("z-galvo")

    def test_a_lost_connection_is_made_again_once(self, monkeypatch):
        # LAS X restarted: the kept connection fails, a fresh one answers.
        answers = [BrokenServer(), StandInServer(galvo_m=3e-6)]
        monkeypatch.setattr(hardware_server, "attach", lambda: answers.pop(0))
        hardware_server.forget_connection()
        assert read_hardware_z_um("z-galvo") == pytest.approx(3.0)
        assert answers == []

    def test_a_connection_lost_for_good_raises(self, monkeypatch):
        monkeypatch.setattr(hardware_server, "attach", lambda: BrokenServer())
        hardware_server.forget_connection()
        with pytest.raises(HardwareServerUnavailable, match="RPC"):
            read_hardware_z_um("z-galvo")


class TestStackedDrive:
    def test_reads_the_hardware_server(self, server, monkeypatch):
        server.Hardware.ZPosZGalvo = 15e-6
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        assert z_um_from_settings(
            settings, "z-galvo", saved=saved(object(), "AF Job 01")
        ) == pytest.approx(15.0)

    def test_the_stacked_z_wide_too(self, server, monkeypatch):
        server.Hardware.ZPosZDrive = 5.0095e-3
        settings = raw_settings(wide_um=4999.5, stack_on="z-wide")
        assert z_um_from_settings(
            settings, "z-wide", saved=saved(object(), "AF Job 01")
        ) == pytest.approx(5009.5)

    def test_needs_no_client_when_the_server_answers(self, server):
        server.Hardware.ZPosZGalvo = 2e-6
        settings = raw_settings(stack_on="z-galvo")
        assert z_um_from_settings(settings, "z-galvo") == pytest.approx(2.0)

    def test_reads_the_saved_experiment_when_the_server_cannot_be_reached(self):
        # The suite's conftest makes the server unreachable, as on a PC without LAS X.
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        lasx = SavingClient()

        z = z_um_from_settings(settings, "z-galvo", saved=saved(lasx, "HiRes"))

        assert z == pytest.approx(SIMULATOR_GALVO_M * 1e6)
        assert lasx.saved == [api_reader.SAVED_EXPERIMENT_NAME + ".xml"]

    def test_refuses_when_neither_the_server_nor_a_saved_experiment_answers(self):
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        with pytest.raises(RuntimeError, match="hardware server could not be reached.*saved experiment"):
            z_um_from_settings(settings, "z-galvo", saved=saved(SavingClient(writes=False), "HiRes"))

    def test_a_saved_experiment_that_holds_the_other_drive_is_refused(self):
        # The simulator's jobs hold their ZPosition for z-galvo; z-wide carries this stack.
        settings = raw_settings(wide_um=4999.5, stack_on="z-wide")
        with pytest.raises(RuntimeError, match="z-galvo, not for 'z-wide'"):
            z_um_from_settings(settings, "z-wide", saved=saved(SavingClient(), "HiRes"))

    def test_a_job_the_saved_experiment_does_not_hold_is_refused(self):
        settings = raw_settings(stack_on="z-galvo")
        with pytest.raises(RuntimeError, match="no job 'Elsewhere'"):
            z_um_from_settings(settings, "z-galvo", saved=saved(SavingClient(), "Elsewhere"))

    def test_without_the_direct_reader_the_saved_experiment_answers_and_the_server_is_not_asked(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            hardware_server, "attach", lambda: pytest.fail("the hardware server is the direct reader's")
        )
        hardware_server.forget_connection()
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")

        z = z_um_from_settings(settings, "z-galvo", saved=saved(SavingClient(), "HiRes"), direct=False)

        assert z == pytest.approx(SIMULATOR_GALVO_M * 1e6)

    def test_a_reader_mode_without_the_direct_reader_reads_the_saved_experiment(self, monkeypatch):
        monkeypatch.setattr(
            hardware_server, "attach", lambda: pytest.fail("api mode does not ask the hardware server")
        )
        hardware_server.forget_connection()
        monkeypatch.setattr(
            _readers, "get_job_settings", lambda *a, **k: raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        )

        z = _readers.read_z_um(SavingClient(), "HiRes", "z-galvo", mode="api")

        assert z == pytest.approx(SIMULATOR_GALVO_M * 1e6)

    def test_without_a_client_the_experiment_cannot_be_saved(self):
        settings = raw_settings(stack_on="z-galvo")
        with pytest.raises(RuntimeError, match="needs the client and the job"):
            z_um_from_settings(settings, "z-galvo")


class TestFreeDrive:
    def test_never_asks_the_hardware_server(self, monkeypatch):
        monkeypatch.setattr(
            hardware_server,
            "attach",
            lambda: pytest.fail("the free drive reads the settings"),
        )
        hardware_server.forget_connection()
        settings = raw_settings(galvo_um=15.0, wide_um=7.0, stack_on="z-wide")
        assert (
            z_um_from_settings(settings, "z-galvo", saved=saved(object(), "J"))
            == 15.0
        )
        assert (
            z_um_from_settings(raw_settings(galvo_um=3.0, wide_um=7.0), "z-wide") == 7.0
        )


class TestCallers:
    def test_a_z_move_is_confirmed_where_the_drive_is(self, server, monkeypatch):
        server.Hardware.ZPosZGalvo = 50e-6
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        assert writes.z_within("z-galvo", 50.0, 1.0)(settings) is True

    def test_a_z_move_is_not_confirmed_on_a_drive_that_stayed(
        self, server, monkeypatch
    ):
        server.Hardware.ZPosZGalvo = 0.0
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        assert writes.z_within("z-galvo", 50.0, 1.0)(settings) is False

    def test_adapter_snapshot_reads_the_stacked_drive_from_the_server(
        self, server, monkeypatch
    ):
        server.Hardware.ZPosZGalvo = 15e-6
        monkeypatch.setattr(_readers, "get_xy", lambda c: {"x_um": 1.0, "y_um": 2.0})
        monkeypatch.setattr(
            _readers, "get_selected_job", lambda c, **k: {"Name": "AF Job 01"}
        )
        monkeypatch.setattr(
            _readers,
            "get_job_settings",
            lambda c, j, **k: raw_settings(galvo_um=-0.01, stack_on="z-galvo"),
        )
        from zmart_drivers.leica.stellaris.navigator_expert.methods import Session

        snap = _frame.hardware_snapshot(Session(client=object(), connection={}, hash6="000abc"))
        assert snap["z_galvo_um"] == pytest.approx(15.0)
        assert snap["z_wide_um"] == 4999.5


class TestMoveZEndToEnd:
    """Through the dispatcher's run(), against the behavioural mock, which
    freezes the stacked drive's zPosition the way LAS X does."""

    def test_stack_drive_move_is_confirmed_in_one_attempt(self, server, monkeypatch):
        from limits_fixtures import install_permissive_limits
        from mock_lasx_api import MockLasxClient

        client = MockLasxClient()
        for job in client._jobs.values():
            if job.get("stack"):
                job["stack"]["mode"] = "z-galvo"
        install_permissive_limits(client, wide_stage=True)
        server.Hardware.ZPosZGalvo = 12e-6
        t0 = time.perf_counter()
        result = changes.move_z(client, "HiRes", 12.0, z_mode="galvo")
        assert (
            result["success"],
            result["confirmed"],
            result["timing"]["attempts"],
        ) == (
            True,
            True,
            1,
        )
        assert time.perf_counter() - t0 < 3.0

    def test_without_the_hardware_server_the_stack_drive_move_is_confirmed_from_the_saved_experiment(self):
        """Neither the direct reader nor the hardware server answers (the suite's conftest), so the
        saved experiment confirms the drive whose job settings LAS X does not refresh."""
        from limits_fixtures import install_permissive_limits
        from mock_lasx_api import MockLasxClient

        client = MockLasxClient()
        for job in client._jobs.values():
            if job.get("stack"):
                job["stack"]["mode"] = "z-galvo"
        install_permissive_limits(client, wide_stage=True)
        saves = []

        class Save:
            """LAS X saving the experiment: HiRes's ZPosition is where the galvo is now."""

            Model = SimpleNamespace(ExperimentName=None)

            def UpdateAwaitReceipt(self, _window_s):
                saves.append(self.Model.ExperimentName)
                galvo_m = client._hardware_z_um["z-galvo"] * 1e-6
                folder = Path(api_reader.SAVED_EXPERIMENT_FOLDER)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / (api_reader.SAVED_EXPERIMENT_NAME + ".lrp")).write_text(
                    "<Experiment><LDM_Block_Sequence_Block>"
                    '<LDM_Block_Sequential BlockName="HiRes"><LDM_Block_Sequential_Master>'
                    f'<ATLConfocalSettingDefinition ZUseModeName="z-galvo" ZPosition="{galvo_m!r}"/>'
                    "</LDM_Block_Sequential_Master></LDM_Block_Sequential>"
                    "</LDM_Block_Sequence_Block></Experiment>",
                    encoding="utf-8",
                )
                return True

        client.PyApiSaveExperiment = Save()

        result = changes.move_z(client, "HiRes", 12.0, z_mode="galvo")

        assert (result["success"], result["confirmed"]) == (True, True)
        assert saves  # the read-back read the saved experiment
