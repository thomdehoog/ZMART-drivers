"""The Microscope that connect() hands back: its state, its settings, and how it fails.

These are the methods a biologist calls from a notebook. Each either does
what it says or raises: ``ValueError`` for a request that is wrong, and
``RuntimeError`` for something the microscope could not do.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

import pytest
from mock_nis import open_operator_image

from zmart_drivers.nikon import nis_elements_ar7
from zmart_drivers.nikon.nis_elements_ar7 import methods


def test_connect_reads_the_version_and_installs_the_limits(scope):
    assert scope.handle.nis_version.startswith("7.")
    assert scope.state()["observed"]["limits"]["is_fallback"] is True


def test_the_state_can_be_read_with_no_image_open(scope):
    state = scope.state()
    assert state["changeable"] == {
        "objective_position": 1,
        "optical_configuration": None,
    }
    observed = state["observed"]
    assert (
        observed["image_size_px"] is None
        and "capture one picture" in observed["image_size_source"]
    )
    assert observed["objectives"] == {
        "1": "Plan Apo 10x",
        "2": "Plan Apo 20x",
        "3": "Plan Apo 60x Oil",
    }
    assert observed["setup"]["ready"] is False and observed["setup"]["issues"]


def test_the_state_reads_an_uncalibrated_image_without_failing(scope, stand_in):
    stand_in.scope.calibrated = False
    open_operator_image(stand_in.scope)
    observed = scope.state()["observed"]
    assert observed["image_size_px"] == [64, 48] and observed["pixel_size_um"] is None
    assert "pixel size is unknown" in observed["image_size_source"]


def test_after_a_capture_the_state_knows_the_picture_size(scope):
    scope.acquire("A1")
    observed = scope.state()["observed"]
    assert observed["image_size_px"] == [64, 48] and observed["pixel_size_um"] == [
        0.65,
        0.65,
    ]


def test_a_quarter_turn_swaps_the_picture_size(scope):
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
        Orientation,
    )

    scope.acquire("A1")
    scope.handle.orientation = Orientation(rotate_deg=90)
    assert scope.state()["observed"]["image_size_px"] == [48, 64]


def test_set_state_round_trips_get_state(scope):
    scope.set_state({"objective_position": 2, "optical_configuration": "GFP"})
    changeable = scope.state()["changeable"]
    assert changeable == {"objective_position": 2, "optical_configuration": "GFP"}
    assert scope.set_state(changeable)["applied"] == changeable


def test_set_state_refuses_a_setting_it_does_not_have(scope):
    with pytest.raises(ValueError):
        scope.set_state({"zoom": 2})


def test_the_objectives_and_configurations_are_listed(scope):
    assert scope.objectives()["position"] == 1
    assert scope.optical_configurations()["configurations"] == [
        "DAPI",
        "GFP",
        "Brightfield",
    ]
    assert scope.change_objective(3)["applied"] == {"objective_position": 3}
    assert scope.select_optical_configuration("DAPI")["applied"] == {
        "optical_configuration": "DAPI"
    }


def test_an_unconfirmed_change_is_listed_in_the_state(scope, stand_in, monkeypatch):
    monkeypatch.setattr(stand_in.scope, "_start_move", lambda **_: None)
    scope.move_to(1050.0, 2000.0, 3000.0)
    unconfirmed = scope.state()["observed"]["unconfirmed"]
    assert unconfirmed and "set_xyz" in unconfirmed[-1]["what"]


def test_close_refuses_every_later_call(scope):
    scope.close()
    for call in (
        scope.position,
        scope.state,
        scope.objectives,
        lambda: scope.acquire("A1"),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            call()
    scope.close()  # a second close does nothing


def test_close_warns_about_captures_still_waiting(scope, output_root, caplog):
    staging = output_root / ".staging"
    staging.mkdir()
    (staging / "x.tif").write_bytes(b"")
    (staging / "x.json").write_text("{}")
    with caplog.at_level(logging.WARNING):
        scope.close()
    assert "still waiting" in caplog.text


def test_stop_bridge_asks_the_macro_loop_to_end(scope, stand_in):
    scope.stop_bridge()
    assert stand_in.bridge.stop_requested and scope.handle.closed


def test_the_running_log_is_written(scope):
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store

    scope.acquire("A1")
    for handler in logging.getLogger(methods.DRIVER_PACKAGE).handlers:
        handler.flush()
    text = (store.machine().logs_folder() / methods.RUNNING_LOG).read_text(
        encoding="utf-8"
    )
    assert "connected to NIS-Elements" in text


def test_a_connection_that_cannot_be_honoured_is_refused_before_nis_is_reached(
    stand_in, key_file
):
    with pytest.raises(ValueError, match="this computer"):
        nis_elements_ar7.connect(port=stand_in.port, host="microscope-pc.example.org")
    with pytest.raises(ValueError, match="unknown connection"):
        nis_elements_ar7.connect(port=stand_in.port, api_delay_ms=5)
    with pytest.raises(ValueError, match="config"):
        nis_elements_ar7.connect(port=stand_in.port, config="config_20260101_0900")
    assert stand_in.scope.calls == []


def test_without_the_bridge_connect_says_what_to_start(key_file):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(RuntimeError, match="start_bridge.mac"):
        nis_elements_ar7.connect(port=port)


def test_without_a_data_location_acquire_says_how_to_set_one(stand_in, key_file):
    microscope = nis_elements_ar7.connect(port=stand_in.port)
    try:
        with pytest.raises(RuntimeError, match="set_data_location"):
            microscope.acquire("A1")
        assert stand_in.scope.captures == 0
    finally:
        microscope.close()


def test_the_data_location_is_used_when_no_output_root_is_given(
    stand_in, key_file, tmp_path
):
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup import data_location

    data_location.set_data_location(tmp_path)
    microscope = nis_elements_ar7.connect(port=stand_in.port)
    try:
        assert microscope.acquire("A1")["store"].startswith(str(tmp_path.resolve()))
    finally:
        microscope.close()


# --- the microscope switched off ---------------------------------------------------------


@pytest.fixture
def switched_off(stand_in, key_file, output_root):
    stand_in.scope.hardware_on = False
    microscope = nis_elements_ar7.connect(
        port=stand_in.port, output_root=str(output_root)
    )
    yield microscope
    microscope.close()


def test_with_the_microscope_off_connect_still_works(switched_off):
    assert switched_off.handle.nis_version.startswith("7.")


def test_with_the_microscope_off_the_position_fails_with_a_sentence(switched_off):
    with pytest.raises(RuntimeError, match="DR_NOTAVAILABLE"):
        switched_off.position()


def test_with_the_microscope_off_acquire_refuses_before_capturing(
    switched_off, stand_in
):
    with pytest.raises(RuntimeError, match="nothing was captured"):
        switched_off.acquire("A1")
    assert "Capture" not in stand_in.scope.calls


def test_with_the_microscope_off_the_state_is_still_readable(switched_off):
    assert "observed" in switched_off.state()
