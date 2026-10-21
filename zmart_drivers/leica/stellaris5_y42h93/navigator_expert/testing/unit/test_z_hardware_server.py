"""
Unit tests: the stacked drive's Z is read from LAS X's hardware server first.
================================================================================
Once a job's z-stack is on a drive, LAS X stops refreshing that drive's
``zPosition`` in the job settings. LAS X's hardware server reports the
drive's true position (``Hardware.ZPosZGalvo`` / ``ZPosZDrive``, metres;
measured on the simulator 2026-10-12: galvo -2.38e-10 m while the job
settings said 0). So for the stacked drive the reader asks the hardware
server, and refuses when the server cannot be reached, rather than return
the stale number. The free drive is untouched.

The suite's conftest makes the hardware server unreachable for every test;
the tests here hand in a stand-in server instead.
"""

import math
import time
from unittest.mock import patch

import pytest
from z_readback_settings import raw_settings

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import confirm_changes as confirmations
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import changes as commands
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.primitives.values.derived import (
    z_um_from_settings,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.connection import (
    hardware_server,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.connection.hardware_server import (
    HardwareServerUnavailable,
    read_hardware_z_um,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.primitives.values.parsing import (
    make_changeable_copy,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.absolute_coordinate_system import (
    frame as _frame,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.readings import named_readings as _readers


#: What the simulator's hardware server answered on 2026-10-12, at rest.
SIMULATOR_GALVO_M = -2.384185222837308e-10


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
            settings, "z-galvo", client=object(), job_name="AF Job 01"
        ) == pytest.approx(15.0)

    def test_the_stacked_z_wide_too(self, server, monkeypatch):
        server.Hardware.ZPosZDrive = 5.0095e-3
        settings = raw_settings(wide_um=4999.5, stack_on="z-wide")
        assert z_um_from_settings(
            settings, "z-wide", client=object(), job_name="AF Job 01"
        ) == pytest.approx(5009.5)

    def test_needs_no_client_when_the_server_answers(self, server):
        server.Hardware.ZPosZGalvo = 2e-6
        settings = raw_settings(stack_on="z-galvo")
        assert z_um_from_settings(settings, "z-galvo") == pytest.approx(2.0)

    def test_refuses_when_the_server_cannot_be_reached(self):
        # The suite's conftest makes the server unreachable, as on a PC without LAS X.
        settings = raw_settings(galvo_um=-0.01, stack_on="z-galvo")
        with pytest.raises(RuntimeError, match="hardware server could not be reached"):
            z_um_from_settings(
                settings, "z-galvo", client=object(), job_name="AF Job 01"
            )


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
            z_um_from_settings(settings, "z-galvo", client=object(), job_name="J")
            == 15.0
        )
        assert (
            z_um_from_settings(raw_settings(galvo_um=3.0, wide_um=7.0), "z-wide") == 7.0
        )


class TestCallers:
    def test_confirm_move_z_confirms_where_the_drive_is(self, server, monkeypatch):
        server.Hardware.ZPosZGalvo = 50e-6
        ch = make_changeable_copy(raw_settings(galvo_um=-0.01, stack_on="z-galvo"))
        with patch.object(confirmations, "_readback", return_value=ch):
            result = confirmations.confirm_move_z(
                object(), job_name="J", z_mode="galvo", target_um=50.0, poll_window=1.0
            )
        assert result["success"] is True

    def test_confirm_move_z_does_not_confirm_a_drive_that_stayed(
        self, server, monkeypatch
    ):
        server.Hardware.ZPosZGalvo = 0.0
        ch = make_changeable_copy(raw_settings(galvo_um=-0.01, stack_on="z-galvo"))
        with patch.object(confirmations, "_readback", return_value=ch):
            result = confirmations.confirm_move_z(
                object(), job_name="J", z_mode="galvo", target_um=50.0, poll_window=0.3
            )
        assert result["success"] is False

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
        snap = _frame.hardware_snapshot(type("H", (), {"client": object()})())
        assert snap["z_galvo_um"] == pytest.approx(15.0)
        assert snap["z_wide_um"] == 4999.5


class TestMoveZEndToEnd:
    """Through the command backbone, against the behavioural mock, which
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
        result = commands.move_z(client, "HiRes", 12.0, z_mode="galvo")
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
