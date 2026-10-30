"""The hardware settings: the table, and each setting found, read, changed and read back through the driver.

The table in ``primitives/nis_python/devices.py`` is the one place that says
how each hardware setting is read and changed. These tests check that the
table only names functions NIS-Elements AR 7 has, that the bridge and the
pretend NIS agree with it, and that the driver changes each kind of setting
safely: the limits first, guarded settings refused until they are allowed,
and the light switched last.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import csv
import inspect
from pathlib import Path

import mock_nis
import pytest

from zmart_drivers.nikon.nis_elements_ar7.dispatcher import limits as gate
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import config, handshake
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import devices, server
from zmart_drivers.nikon.nis_elements_ar7.procedures import changes, readings

NIS7_FUNCTIONS = Path(__file__).resolve().parents[1] / "data" / "nis7_functions.csv"


# --- the table ----------------------------------------------------------------------------------


def test_every_function_of_the_table_is_one_nis_7_has_and_offers():
    """The names come from NIS-Elements AR 7.01.03's own list (step 0 of the NIS 7 study), so a typo shows here."""
    with NIS7_FUNCTIONS.open(encoding="utf-8") as handle:
        offered = {
            row["function"]: row["reachable_without_hardware"]
            for row in csv.DictReader(handle)
        }
    used = devices.READ_FUNCTIONS | devices.WRITE_FUNCTIONS
    assert sorted(used - set(offered)) == []
    assert sorted(name for name in used if offered[name] != "yes") == []


def test_the_bridge_allows_the_tables_functions_and_sorts_reads_from_changes():
    assert devices.READ_FUNCTIONS <= server.READS
    assert devices.WRITE_FUNCTIONS <= server.WRITES
    assert not (devices.WRITE_FUNCTIONS & server.READS), (
        "a change is never sent as a reading"
    )


def test_the_pretend_nis_takes_the_arguments_the_table_sends():
    """The pretend NIS is written to NIS's conventions; here it must agree with the table, function by function."""
    templates = {}
    for row in devices.TABLE:
        for call in (
            row.read,
            row.write,
            row.present,
            row.count,
            row.name,
            row.positions,
        ):
            if call is not None:
                templates[call.fn] = len(call.args)
    templates["Stg_GetMultiLaserNumLines"] = 1
    for fn, count in templates.items():
        pretend = mock_nis.FUNCTIONS[fn]
        nis = mock_nis.make_nis()
        try:
            getattr(nis.mac, fn)(*([0] * count))
        except RuntimeError as error:
            assert "Bad number of parameters" not in str(error), fn
        except Exception:  # noqa: BLE001, S110 -- only the number of arguments is checked here
            pass
        assert pretend.__name__ == fn


def test_every_row_says_what_it_is_and_is_not_yet_confirmed():
    for row in devices.TABLE:
        assert (
            row.says
            and row.group
            and row.kind in (devices.SWITCH, devices.CHOICE, devices.NUMBER)
        )
        assert row.confirmed is False, (
            f"{row.key} is marked confirmed; only the hardware check may say so"
        )
    guarded = {row.key for row in devices.TABLE if row.guarded}
    assert {
        "laser_power",
        "laser_line",
        "stage_temperature",
        "incubator_gas",
        "tirf_position",
    } <= guarded


def test_values_are_checked_before_anything_is_sent():
    shutter, filter_, voltage = (
        devices.setting("shutter"),
        devices.setting("filter"),
        devices.setting("lamp_voltage"),
    )
    assert devices.checked_value(shutter, True) == 1
    assert devices.checked_value(filter_, 3, positions=6) == 3
    for row, bad, positions in (
        (shutter, 2, None),
        (filter_, 7, 6),
        (filter_, 0, 6),
        (filter_, 1.5, 6),
        (voltage, "3", None),
        (voltage, float("nan"), None),
    ):
        with pytest.raises(ValueError):
            devices.checked_value(row, bad, positions)
    with pytest.raises(ValueError, match="settings are"):
        devices.setting("exposure")


# --- through the driver -------------------------------------------------------------------------------


@pytest.fixture
def governed(client):
    handshake.connect_handshake(client)
    return client


def test_the_inventory_lists_what_the_microscope_has(client):
    found = readings.devices(client)
    assert [d["instance"] for d in found["shutter"]["devices"]] == ["EPI", "DIA"]
    assert found["filter"]["devices"][0]["positions"] == 6
    assert found["laser_power"]["devices"][1]["label"] == "488 nm"
    assert (
        found["zoom"]["devices"] == [] and found["stage_temperature"]["devices"] == []
    )


def test_a_single_setting_is_read_by_its_name(client, stand_in):
    stand_in.scope.light_path = 3
    assert readings.device(client, "light_path") == 3
    assert readings.device(client, "shutter", "EPI") == 0


def test_a_shutter_opens_and_is_read_back(governed, stand_in):
    done = changes.set_device(governed, "shutter/EPI", 1)
    assert done["success"] and done["confirmed"]
    assert stand_in.scope.shutters["EPI"] == 1


def test_a_filter_wheel_turns_and_is_read_back(governed, stand_in):
    done = changes.set_device(governed, "filter/Emission Wheel", 4)
    assert done["success"] and done["confirmed"]
    assert stand_in.scope.filters["Emission Wheel"] == 4


def test_a_lamp_brightness_is_set_within_its_tolerance(governed, stand_in):
    done = changes.set_device(governed, "lamp_voltage/DIA LED", 5.5)
    assert done["success"] and done["confirmed"]
    assert stand_in.scope.lamps["DIA LED"]["voltage"] == 5.5


def test_perfect_focus_is_switched_on_and_its_status_reads_as_on(governed, stand_in):
    done = changes.set_device(governed, "pfs", 1)
    assert done["success"] and done["confirmed"]
    assert readings.device(governed, "pfs") == 1


def test_a_device_that_is_not_there_is_refused_before_anything_is_sent(
    governed, stand_in
):
    with pytest.raises(ValueError, match="no device"):
        changes.set_device(governed, "shutter/LASER", 1)
    with pytest.raises(ValueError, match="no device"):
        changes.set_device(governed, "zoom", 2.0)
    with pytest.raises(ValueError):
        changes.set_device(governed, "filter/Emission Wheel", 9)
    assert not {
        "Stg_SetShutterStateEx",
        "Stg_SetZoomPosition",
        "Stg_Filter_SetPosition",
    } & set(stand_in.scope.calls)


def test_a_guarded_setting_is_refused_until_the_limits_allow_it(governed, stand_in):
    refused = changes.set_device(governed, "laser_power/LU-N4.1", 20.0)
    assert not refused["success"] and "guarded" in refused["message"]
    assert "Stg_SetMultiLaserPower" not in stand_in.scope.calls


def test_published_device_limits_allow_and_bound_a_guarded_setting(client, stand_in):
    payload = config.build_payload(
        {"x_um": [-50000, 50000], "y_um": [-30000, 30000], "z_um": [0, 10000]}
    )
    config.adopt_limits(
        {
            **payload,
            "devices": {
                "laser_power": {"range": [0, 30]},
                "shutter/DIA": {"allowed": [0]},
            },
        }
    )
    handshake.connect_handshake(client)
    assert changes.set_device(client, "laser_power/LU-N4.1", 20.0)["confirmed"]
    over = changes.set_device(client, "laser_power/LU-N4.1", 50.0)
    assert not over["success"] and "outside the limits" in over["message"]
    assert not changes.set_device(client, "shutter/DIA", 1)["success"], (
        "an entry for one device wins over the setting"
    )
    assert changes.set_device(client, "shutter/EPI", 1)["success"]


def test_a_limits_file_naming_an_unknown_setting_is_refused():
    payload = config.build_payload({"x_um": [0, 1], "y_um": [0, 1], "z_um": [0, 1]})
    with pytest.raises(ValueError, match="no hardware setting"):
        config.validate_payload({**payload, "devices": {"exposure": {"range": [0, 1]}}})
    with pytest.raises(ValueError):
        config.validate_payload({**payload, "devices": {"laser_power": {"max": 3}}})


def test_without_limits_no_hardware_setting_is_sent(client, stand_in):
    refused = changes.set_device(client, "shutter/EPI", 1)
    assert not refused["success"]
    assert gate.state_of(client) is None
    assert "Stg_SetShutterStateEx" not in stand_in.scope.calls


# --- through the Microscope and set_state -------------------------------------------------------------


def test_set_state_applies_the_light_last(scope, stand_in):
    scope.set_state(
        {
            "shutter/EPI": 1,
            "lamp/DIA LED": 1,
            "light_path": 2,
            "filter/Filter Turret 1": 3,
        }
    )
    sent = [
        c
        for c in stand_in.scope.calls
        if c.startswith(("Stg_Set", "Stg_Light_Set", "Stg_Filter_Set"))
    ]
    assert sent == [
        "Stg_SetLightPathPosition",
        "Stg_Filter_SetPosition",
        "Stg_SetShutterStateEx",
        "Stg_Light_SetState",
    ]


def test_a_setting_that_already_holds_the_value_is_not_sent(scope, stand_in):
    applied = scope.set_state({"shutter/EPI": 0, "laser_power/LU-N4.0": 0.0})
    assert applied["applied"] == {"shutter/EPI": 0, "laser_power/LU-N4.0": 0.0}
    assert "Stg_SetShutterStateEx" not in stand_in.scope.calls
    assert "Stg_SetMultiLaserPower" not in stand_in.scope.calls


def test_the_microscope_offers_devices_device_and_set_device(scope, stand_in):
    assert scope.devices()["condenser"]["devices"][0]["positions"] == 7
    scope.set_device("condenser", 4)
    assert scope.device("condenser") == 4 and stand_in.scope.condenser == 4
    with pytest.raises(ValueError):
        scope.set_device("condenser", 8)
    with pytest.raises(RuntimeError, match="guarded"):
        scope.set_device("incubator_gas", 6.0)


def test_the_state_lists_each_setting_with_what_it_takes(scope):
    state = scope.state()
    assert state["changeable"]["filter/Filter Turret 1"] == 1
    observed = state["observed"]["devices"]
    assert (
        observed["shutter"]["kind"] == "switch"
        and observed["laser_power"]["guarded"] is True
    )
    assert "zoom" not in observed, (
        "a setting with no device is left out of what is observed"
    )


def test_the_plugin_signature_is_unchanged_for_the_controller():
    from zmart_drivers.nikon.nis_elements_ar7.zmart_controller_plugin.zmart_controller_plugin import (
        ZmartControllerPlugin,
    )

    assert list(inspect.signature(ZmartControllerPlugin.set_state).parameters) == [
        "self",
        "changeable",
    ]


# --- what the review of this table found ------------------------------------------------------------


def test_a_setting_changed_by_the_optical_configuration_is_still_set_as_asked(
    scope, stand_in, monkeypatch
):
    """GFP opens the EPI shutter in NIS; asking GFP with the shutter closed must close it again, not trust an old reading."""
    real = mock_nis.FUNCTIONS["SelectOptConf"]

    def opens_the_shutter(scope_, name):
        scope_.shutters["EPI"] = 1
        return real(scope_, name)

    monkeypatch.setitem(mock_nis.FUNCTIONS, "SelectOptConf", opens_the_shutter)
    scope.set_state({"optical_configuration": "GFP", "shutter/EPI": 0})
    assert stand_in.scope.shutters["EPI"] == 0


def test_a_wrong_value_changes_nothing_at_all(scope, stand_in):
    with pytest.raises(ValueError):
        scope.set_state({"objective_position": 2, "filter/Filter Turret 1": 9})
    with pytest.raises(ValueError):
        scope.set_state({"objective_position": 2, "optical_configuration": "Cy7"})
    assert stand_in.scope.nosepiece == 1


def test_a_refused_guarded_value_changes_nothing_at_all(scope, stand_in):
    with pytest.raises(RuntimeError, match="nothing was changed"):
        scope.set_state(
            {"objective_position": 2, "shutter/EPI": 1, "laser_power/LU-N4.1": 40.0}
        )
    assert stand_in.scope.nosepiece == 1 and stand_in.scope.shutters["EPI"] == 0


def test_a_target_whose_reading_is_its_current_value_is_not_offered_to_hand_back():
    inventory = {
        "stage_temperature": {"devices": [{"instance": "Stage", "value": 25.0}]},
        "shutter": {"devices": [{"instance": "EPI", "value": 0}]},
    }
    assert readings.device_names(inventory, settable_only=True) == {"shutter/EPI": 0}
    assert "stage_temperature/Stage" in readings.device_names(inventory)


def test_a_number_limit_listing_allowed_values_compares_them_as_numbers(client):
    payload = config.build_payload(
        {"x_um": [-50000, 50000], "y_um": [-30000, 30000], "z_um": [0, 10000]}
    )
    config.adopt_limits({**payload, "devices": {"lamp_voltage": {"allowed": [3, 6]}}})
    handshake.connect_handshake(client)
    assert changes.set_device(client, "lamp_voltage/DIA LED", 6.0)["success"]
    assert not changes.set_device(client, "lamp_voltage/DIA LED", 5.0)["success"]


def test_a_limit_naming_a_device_nis_does_not_report_is_warned_about(client, caplog):
    payload = config.build_payload(
        {"x_um": [-50000, 50000], "y_um": [-30000, 30000], "z_um": [0, 10000]}
    )
    config.adopt_limits(
        {**payload, "devices": {"lamp_voltage/dia led": {"range": [0, 5]}}}
    )
    handshake.connect_handshake(client)
    assert "no such device" in caplog.text and "lamp_voltage/DIA LED" in caplog.text


def test_the_bridge_loads_the_table_afresh_each_time(tmp_path):
    first = server._sibling("devices")
    second = server._sibling("devices")
    assert first is not second


#: Each NIS function's arguments as NIS-Elements AR 7.01.03's own help writes them.
SIGNATURES = Path(__file__).resolve().parents[1] / "data" / "nis7_signatures.csv"


def _signatures() -> dict[str, int]:
    with SIGNATURES.open(encoding="utf-8") as handle:
        return {
            row["function"]: int(row["arguments"]) for row in csv.DictReader(handle)
        }


def test_every_call_of_the_table_gives_nis_as_many_arguments_as_its_help_says():
    """NIS refuses a wrong number of arguments ("Bad number of parameters"); the table matches NIS's own help."""
    expected = _signatures()
    calls = [
        call
        for row in devices.TABLE
        for call in (
            row.read,
            row.write,
            row.present,
            row.count,
            row.name,
            row.positions,
        )
        if call is not None
    ] + [devices.LASER_NAME, devices.LASER_LINE_COUNT]
    wrong = {
        call.fn: (len(call.args), expected.get(call.fn))
        for call in calls
        if expected.get(call.fn) != len(call.args)
    }
    assert wrong == {}, "the table's number of arguments, and NIS's help's"
    assert expected["GetOpenedDocumentList"] == len(server.DOCUMENT_COUNT_ARGS)
    assert expected["LoadImageFullDescription"] == 1 + len(server.DESCRIPTION_PARTS)


def test_the_pretend_nis_takes_as_many_arguments_as_the_real_one():
    """The offline tests are only as good as the pretend NIS: each of its functions takes what NIS's help says."""
    expected = _signatures()
    wrong = {}
    for name, function in mock_nis.FUNCTIONS.items():
        if name not in expected:
            continue
        taken = len(inspect.signature(function.__wrapped__).parameters) - 1
        if taken != expected[name]:
            wrong[name] = (taken, expected[name])
    assert wrong == {}
