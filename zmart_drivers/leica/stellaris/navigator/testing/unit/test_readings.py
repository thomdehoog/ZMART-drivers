"""The named readings through the dispatcher, against the state tree stand-in.

Every reading asks the one source this driver has, LAS X's hardware server,
by the waiting rule: an answer that counts comes back with when it was seen;
a hardware server that cannot be reached leaves the reading unknown (None)
after the windows, never an exception.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import time

import pytest
from lasx_stand_in import PlainNavigator, attach

from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    profile as direct_profile,
)
from zmart_drivers.leica.stellaris.navigator.procedures import readings


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator(x_um=63500.0, y_um=41500.0, z_galvo_um=-2.5, z_wide_um=4999.5))


@pytest.fixture
def client():
    return hardware_server.Client("test")


def test_the_stage_is_read_in_micrometres_and_metres(lasx, client):
    assert readings.get_xy(client) == {
        "x": pytest.approx(0.0635), "y": pytest.approx(0.0415), "x_um": pytest.approx(63500.0), "y_um": pytest.approx(41500.0)
    }


def test_each_z_drive_is_read_by_its_name(lasx, client):
    assert readings.read_z_um(client, "z-galvo") == pytest.approx(-2.5)
    assert readings.read_zwide_um(client) == pytest.approx(4999.5)


def test_what_las_x_is_set_to_now(lasx, client):
    assert readings.get_scan_status(client) == "eScanIdle"
    assert readings.get_scan_mode(client) == "xyz"
    assert readings.get_zoom(client) == {"current": 1.0, "min": 0.75, "max": 48.0}
    assert readings.get_scan_speed(client)["value"] == 400
    assert readings.get_format(client) == "512 x 512"
    assert readings.get_averaging(client)["lineAverage"] == 1
    assert readings.get_pinhole(client)["airy"]["value"] == pytest.approx(0.9994, abs=1e-3)
    assert readings.get_objective(client)["slotIndex"] == 0
    assert readings.get_fov(client) == (pytest.approx(0.0011625), pytest.approx(0.0011625))
    assert readings.get_bit_depth(client) == 8
    assert readings.get_z_stack(client) is None
    assert readings.get_serial_number(client) == "STELLARIS SIMULATOR"


def test_the_instrument_is_described_whole(lasx, client):
    info = readings.get_hardware_info(client)
    assert info["SerialNumber"] == "STELLARIS SIMULATOR"
    assert [o["slotIndex"] for o in info["Microscope"]["objectives"]] == [0, 1]
    assert [o["slotIndex"] for o in readings.get_objectives(client)] == [0, 1]


def test_the_plain_navigator_lists_no_jobs(lasx, client):
    assert readings.get_jobs(client) == []
    assert readings.shows_plain_navigator(client) is True


def test_navigator_expert_s_jobs_give_it_away(lasx, client):
    lasx.nodes.jobs = lambda: {"HiRes": (152, 3)}
    lasx.nodes.reload()
    assert readings.shows_plain_navigator(client) is False


def test_a_reading_says_when_it_was_seen(lasx, client):
    before = time.time()
    reading = readings.get_xy(client, diagnostics=True)
    assert reading.source == "direct" and reading.error is None
    assert reading.observed_at >= before


def test_a_reading_waits_for_what_the_caller_accepts(lasx, client):
    """A read-back asks again until the value shows, or the window ends."""
    reading = readings.get_xy(client, diagnostics=True, accept=lambda xy: xy["x_um"] > 70_000)
    assert reading is None or reading.value is None
    lasx.x_um = 70_500.0
    assert readings.get_xy(client, accept=lambda xy: xy["x_um"] > 70_000)["x_um"] == pytest.approx(70_500.0)


def test_without_the_hardware_server_a_reading_is_unknown_not_an_error(client):
    reading = readings.get_xy(client, diagnostics=True)
    assert reading is not None and reading.value is None
    assert isinstance(reading.error, hardware_server.HardwareServerUnavailable)
    assert readings.get_xy(client) is None
    assert readings.ping(client) is False
    assert readings.shows_plain_navigator(client) is None


def test_the_profile_knows_every_named_reading():
    for name in ("xy", "z", "scan_status", "zoom", "objective", "jobs", "hardware_info"):
        assert direct_profile.spec(name).name == name
    with pytest.raises(ValueError, match="unknown datum"):
        direct_profile.spec("job_settings")


def test_only_the_direct_source_exists(client):
    reading = direct_profile.ask(
        "log", direct_profile.spec("xy"), client, until=time.monotonic() + 1, answer_counts=lambda r: False, about=None, source_kwargs={}
    )
    assert isinstance(reading.error, direct_profile.UnsupportedSource)
