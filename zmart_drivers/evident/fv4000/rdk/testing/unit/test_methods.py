"""The Microscope used by itself: connecting, closing, and what each call answers or refuses."""

import pytest
from mock_fluoview import MockFluoview, MockRdkClient

from zmart_drivers.evident.fv4000.rdk import methods
from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client


def test_connect_puts_fluoview_in_remote_mode_and_close_hands_it_back(scope, mock):
    assert (
        mock.remote_mode == "REMOTE"
        and scope.handle.fluoview_version == MockFluoview.VERSION
    )
    scope.close()
    assert mock.remote_mode == "LOCAL" and scope.handle.closed
    scope.close()  # a second close does nothing
    with pytest.raises(RuntimeError, match="disconnected"):
        scope.position()


def test_a_fluoview_that_does_not_answer_stops_the_connection(
    mock, monkeypatch, published_limits
):
    mock.connected = False
    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    with pytest.raises(RuntimeError, match="does not answer"):
        methods.connect()


def test_the_connection_words_are_checked_before_fluoview_is_touched():
    with pytest.raises(ValueError, match="port"):
        methods.checked_connection({"port": "eighty"})
    with pytest.raises(ValueError, match="no configs are kept"):
        methods.checked_connection({"config": "config_20261026_1915"})
    checked = methods.checked_connection({"host": " 10.0.0.5 ", "remote_mode": False})
    assert (
        checked["host"] == "10.0.0.5"
        and checked["port"] == 8080
        and checked["remote_mode"] is False
    )
    assert set(methods.DEFAULT_CONNECTION) >= {
        "microscope",
        "api_type",
        "host",
        "password",
        "config",
        "output_root",
    }


def test_connecting_without_remote_mode_leaves_fluoview_local(
    mock, monkeypatch, published_limits, tmp_path
):
    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    scope = methods.connect(remote_mode=False, output_root=str(tmp_path))
    assert mock.remote_mode == "LOCAL"
    scope.close()


def test_a_damaged_data_location_stops_the_connection(
    mock, monkeypatch, published_limits
):
    from zmart_drivers.evident.fv4000.rdk.driver_setup.store import MACHINE

    MACHINE.write_data_location({"folder": ""})
    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    with pytest.raises(RuntimeError, match="data location could not be read"):
        methods.connect()


def test_without_a_data_location_or_output_root_a_capture_says_how_to_set_one_up(
    mock, monkeypatch, published_limits
):
    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    scope = methods.connect()
    with pytest.raises(RuntimeError, match="set_data_location.ipynb"):
        scope.acquire("A1")
    scope.close()


def test_change_setting_checks_the_name_and_arguments_and_records_an_unconfirmed_change(
    scope, mock
):
    with pytest.raises(ValueError, match="unknown setting"):
        scope.change_setting("zoom", value=1)
    with pytest.raises(ValueError, match="takes"):
        scope.change_setting("scanner_type")
    assert scope.change_setting("scanner_type", value="RESONANT")["confirmed"] is True
    assert (
        scope.change_setting("laser_intensity", laser_id="LASER_488", value=500.0)[
            "confirmed"
        ]
        is False
    )
    assert scope.handle.unconfirmed[-1]["what"] == "change_setting('laser_intensity')"
    with pytest.raises(RuntimeError, match="failed"):
        scope.change_setting("scanner_type", value="SIDEWAYS")
    assert "laser_intensity" in scope.settings() and scope.settings()[
        "laser_intensity"
    ] == ["laser_id", "value"]


def test_the_objectives_change_objective_and_the_piezo(scope, mock):
    assert (
        scope.objectives()["current"] == {"slot": 0, "name": "UPLXAPO10X"}
        and len(scope.objectives()["objectives"]) == 3
    )
    with pytest.raises(RuntimeError, match="no calibration translation covers"):
        scope.change_objective(slot=2)
    scope.handle.translations = {0: (0.0, 0.0, 0.0), 2: (10.0, -6.0, 2.0)}
    result = scope.change_objective(slot=2)
    assert result["confirmed"] is True and result["compensation_um"] == [
        10.0,
        -6.0,
        2.0,
    ]
    assert (mock.objective_slot, mock.at_rest()) == (
        2,
        (10_010_000, 7_994_000, 2_502_000),
    )
    assert scope.move_piezo(20.0)["confirmed"] is True and mock.piezo_nm == 20_000
    with pytest.raises(RuntimeError, match="refused"):
        scope.move_piezo(500.0)


def test_load_settings_from_live_and_stop(scope, mock):
    mock.scan_s = 0.0
    taken = scope.acquire("A1")
    scope.save()
    assert scope.load_settings_from(taken["fluoview_file"])["success"] is True
    assert (
        mock.loaded_parameters == [taken["fluoview_file"]]
        and scope.state()["changeable"]["load_settings_from"] == taken["fluoview_file"]
    )
    with pytest.raises(RuntimeError):
        scope.load_settings_from("D:/nowhere.oir")
    assert scope.live(True)["confirmed"] is True and mock.state == "SCANNING"
    assert scope.live(False)["confirmed"] is True
    with pytest.raises(RuntimeError, match="failed"):
        scope.stop()  # nothing runs


def test_the_state_reports_everything_changeable_and_set_state_applies_it_in_order(
    scope, mock
):
    state = scope.state()
    changeable, observed = state["changeable"], state["observed"]
    assert (
        changeable["objective"] == 0
        and changeable["scanner_type"] == "GALVANO"
        and changeable["z_stack_slices"] == 5
    )
    assert changeable["channels"]["CH1"] == {
        "enabled": True,
        "hv": 500,
        "gain": 1.0,
        "offset": 0,
        "digital_gain": 1.0,
        "lasers": ["LASER_488"],
    }
    assert changeable["channels"]["CH3"]["hv"] is None, (
        "a channel that is not a PMT has no HV"
    )
    assert changeable["lasers"]["LASER_561"] == {"enabled": True, "intensity": 1.5}
    assert (
        observed["fluoview_version"] == MockFluoview.VERSION
        and observed["scan_state"] == "IDLING"
    )
    assert observed["limits"]["source"] == "machine" and not observed["setup"]["ready"]
    assert observed["objectives"][2] == {"slot": 2, "name": "UPLXAPO60XO"}

    applied = scope.set_state(
        {
            "scanner_type": "RESONANT",
            "channels": {"CH2": {"hv": 610, "lasers": ["LASER_640"]}},
            "lasers": {"LASER_640": {"enabled": True, "intensity": 3.0}},
            "time_lapse_interval_s": 0.5,
        }
    )
    assert applied["applied"] == {
        "scanner_type": "RESONANT",
        "channels": {"CH2": {"hv": 610, "lasers": ["LASER_640"]}},
        "lasers": {"LASER_640": {"enabled": True, "intensity": 3.0}},
        "time_lapse_interval_s": 0.5,
    }
    assert (
        mock.channels["CH2"].hv == 610
        and mock.channels["CH2"].lasers == ["LASER_640"]
        and mock.lasers["LASER_640"]["intensity"] == 3.0
    )
    with pytest.raises(ValueError, match="no changeable setting"):
        scope.set_state({"zoom": 2})
    with pytest.raises(RuntimeError, match="failed"):
        scope.set_state({"scanner_mode": "SIDEWAYS"})


def test_set_state_changes_the_objective_with_the_calibration_and_leaves_it_when_it_is_in(
    scope, mock
):
    scope.handle.translations = {0: (0.0, 0.0, 0.0), 1: (1.0, 1.0, 1.0)}
    assert scope.set_state({"objective": 0})["applied"] == {"objective": 0}
    assert (
        scope.set_state({"objective": 1})["applied"] == {"objective": 1}
        and mock.objective_slot == 1
    )
    assert mock.at_rest()[0] == 10_001_000


def test_the_running_log_is_started_in_the_settings_folder(scope):
    from zmart_drivers.evident.fv4000.rdk.driver_setup.store import MACHINE

    assert (MACHINE.snapshot_root() / "logs" / methods.FILE_NAME).exists()
