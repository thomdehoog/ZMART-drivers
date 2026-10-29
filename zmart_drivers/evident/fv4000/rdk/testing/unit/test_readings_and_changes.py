"""The named readings and changes against the pretend FLUOVIEW: confirmed, refused, sent again, or unconfirmed."""

import pytest

from zmart_drivers.evident.fv4000.rdk.dispatcher import limits as gate
from zmart_drivers.evident.fv4000.rdk.primitives.rdk import writes
from zmart_drivers.evident.fv4000.rdk.procedures import changes, readings


def test_readings_come_back_in_the_drivers_units(client):
    assert readings.get_xy(client) == {"x_um": 10000.0, "y_um": 8000.0}
    assert readings.get_z(client) == 2500.0
    assert readings.get_z_piezo(client) == 0.0
    assert readings.get_scan_state(client) == "IDLING"
    assert readings.get_objective(client) == {"slot": 0, "name": "UPLXAPO10X"}
    assert readings.read("laser_intensity", client, about="LASER_488") == 2.0
    assert readings.read("time_lapse_interval_s", client) == 1.0
    assert readings.ping(client)


def test_a_reading_with_diagnostics_names_its_source(client):
    reading = readings.get_xy(client, diagnostics=True)
    assert reading.source == "rdk" and reading.error is None and reading.age_s == 0.0


def test_an_unknown_reading_is_refused_by_name(client):
    with pytest.raises(ValueError, match="unknown reading"):
        readings.read("temperature", client)


def test_a_reading_fluoview_refuses_is_unknown_not_a_crash(client):
    assert readings.read("channel_hv", client, about="CH3") is None  # CH3 is not a PMT
    assert readings.read("channel_hv", client, about="CH1") == 500


def test_a_move_is_sent_once_and_confirmed_when_the_stage_arrives(
    governed_client, mock
):
    result = changes.move_xy(governed_client, 10050.0, 8020.0)
    assert (
        result["success"] is True
        and result["confirmed"] is True
        and result["route"] is None
    )
    assert result["target"] == {"x_um": 10050.0, "y_um": 8020.0}
    sends = [c for c in mock.calls if c[0] == "Parameter.setParameter"]
    assert (
        len(sends) == 1
        and sends[0][1]["x"] == 10_050_000
        and sends[0][1]["escapeEnabled"] is False
    )
    assert readings.get_xy(governed_client) == {"x_um": 10050.0, "y_um": 8020.0}


def test_a_focus_move_is_confirmed_and_escape_is_passed_on(governed_client, mock):
    assert changes.move_z(governed_client, 2600.0)["confirmed"] is True
    assert readings.get_z(governed_client) == 2600.0
    changes.move_xy(governed_client, 10000.0, 8000.0, escape=True)
    assert mock.escape_requests[-1] is True


def test_a_move_refused_by_the_limits_sends_nothing(governed_client, mock):
    result = changes.move_xy(governed_client, 5e6, 0.0)
    assert result["success"] is False and "refused" in result["message"]
    assert not [c for c in mock.calls if c[0] == "Parameter.setParameter"]


def test_without_limits_installed_every_change_is_refused(client, mock):
    result = changes.move_z(client, 2500.0)
    assert result["success"] is False and "no limits are installed" in result["message"]
    assert "set_limits.ipynb" in result["message"]
    assert not [c for c in mock.calls if c[0] == "Parameter.setParameter"]


def test_a_busy_fluoview_is_sent_the_change_again(governed_client, mock):
    mock.busy_answers = 2
    result = changes.change(governed_client, "scanner_type", value="RESONANT")
    assert result["confirmed"] is True
    assert len([c for c in mock.calls if c[0] == "Parameter.setParameter"]) == 3
    assert result["timing"]["attempts"] == 3


def test_a_wrong_setting_or_argument_is_refused_before_anything_is_built(
    governed_client, mock
):
    with pytest.raises(ValueError, match="unknown setting"):
        changes.change(governed_client, "zoom", value=2)
    with pytest.raises(ValueError, match="does not take"):
        changes.change(governed_client, "scanner_type", value="GALVANO", speed=1)
    with pytest.raises(ValueError, match="missing"):
        changes.change(governed_client, "laser_intensity", laser_id="LASER_488")
    assert not [c for c in mock.calls if c[0] == "Parameter.setParameter"]


def test_a_value_fluoview_calls_invalid_is_a_permanent_failure_not_retried(
    governed_client, mock
):
    result = changes.change(governed_client, "scanner_mode", value="SIDEWAYS")
    assert result["success"] is False and "INVALID_PARAMETERS" in result["message"]
    assert len([c for c in mock.calls if c[0] == "Parameter.setParameter"]) == 1


def test_a_laser_power_fluoview_clips_is_recorded_as_unconfirmed(governed_client, mock):
    result = changes.change(
        governed_client, "laser_intensity", laser_id="LASER_488", value=150.0
    )
    assert result["success"] is True and result["confirmed"] is False
    assert mock.lasers["LASER_488"]["intensity"] == 100.0


@pytest.mark.parametrize(
    ("name", "arguments", "check"),
    [
        (
            "channel_enable",
            {"channel_id": "CH3", "enable": True},
            lambda m: m.channels["CH3"].enabled is True,
        ),
        (
            "channel_hv",
            {"channel_id": "CH1", "value": 620},
            lambda m: m.channels["CH1"].hv == 620,
        ),
        (
            "channel_gain",
            {"channel_id": "CH1", "value": 1.5},
            lambda m: m.channels["CH1"].gain == 1.5,
        ),
        (
            "channel_offset",
            {"channel_id": "CH1", "value": 3},
            lambda m: m.channels["CH1"].offset == 3,
        ),
        (
            "channel_laser",
            {"channel_id": "CH1", "laser_id": "LASER_561"},
            lambda m: m.channels["CH1"].lasers == ["LASER_561"],
        ),
        (
            "laser_enable",
            {"laser_id": "LASER_640", "enable": True},
            lambda m: m.lasers["LASER_640"]["enabled"] is True,
        ),
        ("z_stack_enable", {"enable": True}, lambda m: m.z_stack["enabled"] is True),
        ("z_stack_slices", {"value": 11}, lambda m: m.z_stack["slices"] == 11),
        ("z_stack_step", {"value_um": 0.5}, lambda m: m.z_stack["step_nm"] == 500),
        ("z_stack_start", {"value_um": -3.0}, lambda m: m.z_stack["start_nm"] == -3000),
        ("z_stack_end", {"value_um": 3.0}, lambda m: m.z_stack["end_nm"] == 3000),
        ("z_stack_range", {"value_um": 6.0}, lambda m: m.z_stack["range_nm"] == 6000),
        ("z_type", {"method": "RANGE"}, lambda m: m.z_stack["type"] == "RANGE"),
        (
            "time_lapse_enable",
            {"enable": True},
            lambda m: m.time_lapse["enabled"] is True,
        ),
        ("time_lapse_repeat", {"value": 7}, lambda m: m.time_lapse["repeat"] == 7),
        (
            "time_lapse_interval",
            {"value_s": 2.5},
            lambda m: (m.time_lapse["interval"], m.time_lapse["unit"]) == (25, 1),
        ),
        (
            "scanner_mode",
            {"value": "ROUNDTRIP"},
            lambda m: m.scanner_mode == "ROUNDTRIP",
        ),
    ],
)
def test_every_setting_with_a_read_back_is_applied_and_confirmed(
    governed_client, mock, name, arguments, check
):
    result = changes.change(governed_client, name, **arguments)
    assert result["success"] is True and result["confirmed"] is True, result["message"]
    assert check(mock)


def test_the_objective_turns_to_a_slot_or_a_name_and_a_wrong_one_is_refused(
    governed_client, mock
):
    objectives = readings.get_objectives(governed_client)
    assert (
        changes.set_objective(governed_client, objectives, slot=2)["confirmed"] is True
    )
    assert mock.objective_slot == 2
    assert (
        changes.set_objective(governed_client, objectives, name="UPLXAPO20X")[
            "confirmed"
        ]
        is True
    )
    assert mock.objective_slot == 1
    assert (
        "no objective"
        in changes.set_objective(governed_client, objectives, slot=9)["message"]
    )
    assert (
        "exactly one"
        in changes.set_objective(governed_client, objectives, slot=1, name="x")[
            "message"
        ]
    )
    assert (
        "exactly one" in changes.set_objective(governed_client, objectives)["message"]
    )


def test_remote_mode_load_parameter_and_the_live_scan(governed_client, mock):
    assert (
        changes.set_remote_mode(governed_client, True)["success"] is True
        and mock.remote_mode == "REMOTE"
    )
    assert (
        changes.load_parameter(governed_client, "D:/nowhere.oir")["success"] is False
    )  # FILE_NOT_FOUND
    assert changes.load_parameter(governed_client, "  ")["success"] is False
    assert (
        changes.repeat_scan(governed_client, True)["confirmed"] is True
        and mock.state == "RESTING"
    )
    assert (
        changes.repeat_scan(governed_client, False)["confirmed"] is True
        and mock.state == "IDLING"
    )


def test_every_builder_names_a_command_the_limits_know():
    from zmart_drivers.evident.fv4000.rdk.driver_setup.limits.limit_rules import (
        MUTATING_COMMANDS,
    )

    built = [
        writes.move_xy(0, 0),
        writes.move_z(0),
        writes.move_piezo(0),
        writes.set_objective(0),
        writes.acquire(),
        writes.stop(),
        writes.repeat_scan(True),
        writes.repeat_scan(False),
        writes.set_remote_mode(True),
        writes.load_parameter("x"),
    ]
    for name, setting in writes.SETTINGS.items():
        built.append(
            writes.change(name, **{arg: _example(arg) for arg in setting.takes})
        )
    missing = sorted({write.command for write in built} - set(MUTATING_COMMANDS))
    assert missing == [], f"changes the limits do not know: {missing}"


def _example(argument):
    return {
        "channel_id": "CH1",
        "laser_id": "LASER_488",
        "group_id": "g",
        "enable": True,
        "value": 1,
        "value_um": 1.0,
        "value_s": 1.0,
        "method": "RANGE",
        "columns": 1,
        "rows": 1,
        "source_well": "A01",
        "target_well": "A02",
        "column": 0,
        "row": 0,
        "x_um": 0.0,
        "y_um": 0.0,
        "z_um": 0.0,
    }[argument]


def test_the_gate_keys_the_limits_by_the_connection(governed_client, client):
    assert gate.state_of(governed_client) is not None
    gate.uninstall(governed_client)
    assert changes.move_z(client, 2500.0)["success"] is False


def test_the_objective_and_a_channels_lasers_are_found_in_either_reply_shape():
    from zmart_drivers.evident.fv4000.rdk.primitives.rdk import reads

    class Replies:
        def __init__(self, reply):
            self.reply = reply

        def call(self, method, payload=None):
            return {"result": "OK", **self.reply}

    flat = Replies({"revolverIndex": 2, "lensName": "60x"})
    nested = Replies({"value": {"revolverIndex": 2, "lensName": "60x"}})
    listed = Replies({"value": [{"revolverIndex": 2, "lensName": "60x"}]})
    for client in (flat, nested, listed):
        assert reads.get_objective(client) == {"slot": 2, "name": "60x"}
    entry = {
        "imagingDeviceMode": "IMAGING",
        "laserScannerMode": "MAIN",
        "laserId": "LASER_488",
    }
    assert reads.get_channel_laser(Replies({"laser": [entry]}), "CH1") == ["LASER_488"]
    assert reads.get_channel_laser(Replies({"value": [entry]}), "CH1") == ["LASER_488"]


def test_the_objective_slot_is_sent_as_text_as_the_manual_says(governed_client, mock):
    objectives = readings.get_objectives(governed_client)
    assert (
        changes.set_objective(governed_client, objectives, slot=1)["confirmed"] is True
    )
    sent = [c[1] for c in mock.calls if c[0] == "Parameter.setParameter"][-1]
    assert sent["revolverIndex"] == "1"
