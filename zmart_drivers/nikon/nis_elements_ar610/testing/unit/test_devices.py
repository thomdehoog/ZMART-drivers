"""The devices beyond the stage, the saved optical configurations, and the small tools, against the mock.

The mock carries the bridge's fake Ti2: a DIA lamp, a SpectraIII light
source with eight laser lines from 405 to 748 nm, two filter turrets, two
shutters, a condenser, an epi-field aperture, a camera port with a spinning
disk, and a Perfect Focus System at offset 3000.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json

import pytest

from zmart_drivers.nikon.nis_elements_ar610.dispatcher.limits import LimitViolation
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.limits import (
    config,
    limit_rules,
)

NIS = {"x": [-57000.0, 57000.0], "y": [-37500.0, 37500.0], "z": [0.0, 10000.0]}


def _rulebook(**changes):
    payload = json.loads(config.defaults_path().read_text(encoding="utf-8"))
    payload.update(changes)
    loaded = {"policy": config.validate_payload(payload), "stage_um": NIS}
    limits, _ = limit_rules.build_limits(
        loaded, NIS, source="machine", path="x", is_fallback=False
    )
    return limits


# =============================================================================
# Reading
# =============================================================================


def test_every_device_is_read_in_the_shape_set_state_takes(scope):
    devices = scope.devices()
    assert devices["lights"] == {"DIA": {"on": False, "iris": 0.5}}
    assert devices["lasers"]["SpectraIII"]["475"] == {"on": False, "power": 0}
    assert devices["filters"] == {"Turret-Up": 1, "Turret-Lo": 1}
    assert devices["shutters"] == {"DIA LED": False, "EPI": False}
    assert devices["light_path"] == {
        "condenser": 1,
        "apertures": {"epi_field": 100.0},
        "analyzer": False,
    }
    assert devices["port"] == {"port": 1, "disk_shutter": False, "disk_speed_rpm": 4000}
    assert devices["camera"]["format"] == "FMT 1x1 16"

    state = scope.state()
    assert state["changeable"]["lights"] == devices["lights"]
    assert state["changeable"]["pfs_offset"] == {"offset": 3000, "dish": "glass"}
    assert set(state["observed"]["lasers"]["SpectraIII"]) == {
        405,
        440,
        475,
        510,
        555,
        575,
        637,
        748,
    }
    assert state["observed"]["filters"]["Turret-Up"]["filters"][2] == "GFP"
    assert state["observed"]["environment"] == {}
    assert state["observed"]["channel_settings"] is None


def test_a_saved_channel_is_read_by_name(scope):
    assert scope.channel_settings("DAPI")["exposure_ms"] == 100.0
    scope.select_channel("DAPI")
    assert scope.state()["observed"]["channel_settings"]["name"] == "DAPI"


# =============================================================================
# Changing, one device at a time
# =============================================================================


def test_each_device_is_changed_and_read_back(scope, mock):
    assert scope.set_light("DIA", on=True)["applied"] == {
        "lights": {"DIA": {"on": True}}
    }
    assert mock.lights["DIA"]["on"] is True

    done = scope.set_laser(475, on=True, power=20)
    assert done["applied"] == {
        "lasers": {"SpectraIII": {"475": {"on": True, "power": 20}}}
    }
    assert scope.lasers()["SpectraIII"][475]["power"] == 20

    assert scope.set_filter("Turret-Up", filter_name="GFP")["applied"] == {
        "filters": {"Turret-Up": 2}
    }
    assert scope.set_shutter("EPI", True)["applied"] == {"shutters": {"EPI": True}}
    assert scope.set_light_path(condenser=2, aperture="epi_field", size=80.0)[
        "applied"
    ] == {"light_path": {"condenser": 2, "apertures": {"epi_field": 80.0}}}
    assert scope.set_port(2, disk_speed_rpm=5000)["applied"] == {
        "port": {"port": 2, "disk_speed_rpm": 5000}
    }
    assert scope.set_camera(format="FMT 2x2 16")["applied"] == {
        "camera": {"format": "FMT 2x2 16"}
    }
    assert scope.set_pfs_offset(3500, dish="plastic")["applied"] == {
        "pfs_offset": {"offset": 3500, "dish": "plastic"}
    }
    assert mock.pfs_offset_value == 3500
    assert scope.state()["observed"]["unconfirmed"] == []


def _count_sends(monkeypatch):
    """Count every change the driver sends, whichever device it is for."""
    from zmart_drivers.nikon.nis_elements_ar610.procedures import changes

    sent = []
    real = changes._run

    def counting(client, change, **kwargs):
        sent.append(change.command)
        return real(client, change, **kwargs)

    monkeypatch.setattr(changes, "_run", counting)
    return sent


def test_a_setting_the_device_already_has_sends_nothing(scope, monkeypatch):
    sent = _count_sends(monkeypatch)
    assert scope.set_shutter("EPI", False)["applied"] == {}
    assert (
        scope.set_state(scope.state()["changeable"] | {"channel": None})["applied"]
        == {}
    )
    assert sent == []


def test_a_whole_state_read_once_is_applied_again_later(scope, mock):
    scope.set_filter("Turret-Up", 3)
    scope.set_laser(637, on=True, power=40)
    saved = scope.state()["changeable"]

    scope.safe_state()
    scope.set_filter("Turret-Up", 1)
    assert (
        mock.lasers["SpectraIII"][6]["on"] is False
    )  # 637 nm, switched off by the safe state

    applied = scope.set_state({k: v for k, v in saved.items() if k != "channel"})[
        "applied"
    ]
    assert applied["filters"] == {"Turret-Up": 3}
    assert applied["lasers"] == {"SpectraIII": {"637": {"on": True}}}
    assert scope.devices()["lasers"]["SpectraIII"]["637"] == {"on": True, "power": 40}


def test_a_wrong_name_is_refused_before_anything_changes(scope, mock, monkeypatch):
    sent = _count_sends(monkeypatch)
    before = len(mock.calls)
    with pytest.raises(ValueError, match="no light 'LED'"):
        scope.set_state({"objective": 2, "lights": {"LED": {"on": True}}})
    with pytest.raises(ValueError, match="no line of SpectraIII '500'"):
        scope.set_state({"lasers": {"SpectraIII": {"500": {"on": True}}}})
    with pytest.raises(ValueError, match="no filter changer 'Wheel'"):
        scope.set_filter("Wheel", 1)
    with pytest.raises(ValueError, match="has positions"):
        scope.set_filter("Turret-Lo", 5)
    with pytest.raises(ValueError, match="one filter of Turret-Up"):
        scope.set_filter("Turret-Up", filter_name="YFP")
    with pytest.raises(ValueError, match="no shutter 'DIA'"):
        scope.set_shutter("DIA", True)
    with pytest.raises(ValueError, match="no changeable setting"):
        scope.set_state({"lamps": {}})
    with pytest.raises(ValueError, match="no light source"):
        scope.set_laser(500, on=True)
    assert sent == [] and len(mock.calls) == before


def test_the_limits_fence_the_new_settings():
    limits = _rulebook(
        laser_wavelength_nm={"allowed": [475, 555]},
        laser_power={"range": [0, 50]},
        light_voltage={"range": [0, 6]},
        pfs_offset={"range": [1000, 5000]},
    )
    limits.check_command(
        "set_laser", {"laser": "SpectraIII", "wavelength_nm": 475, "power": 50}
    )
    with pytest.raises(LimitViolation, match="not allowed"):
        limits.check_command("set_laser", {"wavelength_nm": 405, "power": None})
    with pytest.raises(LimitViolation, match="outside the range"):
        limits.check_command("set_laser", {"wavelength_nm": 475, "power": 80})
    with pytest.raises(LimitViolation, match="outside the range"):
        limits.check_command("set_light", {"voltage": 9.0})
    limits.check_command("set_light", {"voltage": None})
    with pytest.raises(LimitViolation, match="outside the range"):
        limits.check_command("set_pfs_offset", {"offset": 9000, "dish": None})
    with pytest.raises(LimitViolation, match="outside the range"):
        limits.check_command("move_piezo_z", {"distance_um": 80.0})
    limits.check_command("move_piezo_z", {"distance_um": -10.0})
    limits.check_command("safe_state", {})


def test_a_piezo_step_beyond_the_limits_is_refused_and_not_sent(scope, mock):
    mock.piezo = 0.0
    with pytest.raises(RuntimeError, match="piezo_step_um=80.0 is outside the range"):
        scope.move_piezo_z(80.0)
    assert mock.piezo == 0.0


# =============================================================================
# Saved optical configurations, and the small tools
# =============================================================================


def test_a_saved_channel_is_edited_copied_and_deleted(scope, mock):
    assert scope.edit_channel("DAPI", 50)["confirmed"] is True
    assert mock.saved_exposures["DAPI"] == 50.0

    scope.copy_channel("DAPI", "DAPI fast", 20)
    assert "DAPI fast" in scope.channels()["channels"]
    assert scope.channel_settings("DAPI fast")["exposure_ms"] == 20.0

    scope.select_channel("DAPI fast")
    scope.delete_channel("DAPI fast")
    assert "DAPI fast" not in scope.channels()["channels"]
    assert scope.channels()["selected"] is None


def test_live_view_auto_exposure_piezo_escape_and_the_safe_state(scope, mock):
    mock.piezo = 0.0
    scope.live(True)
    assert mock.live_on is True
    assert scope.auto_exposure() == {"exposure_ms": 7.3}

    assert scope.move_piezo_z(2.0)["answered"] == {"moved_um": 2.0}
    assert mock.piezo == 2.0
    scope.center_piezo_z()
    assert mock.piezo == 0.0

    scope.escape()
    assert mock.escaped_state is True
    scope.refocus()
    assert mock.escaped_state is False

    scope.set_light("DIA", on=True)
    scope.set_shutter("EPI", True)
    answered = scope.safe_state()["answered"]
    assert answered["lights_off"] == ["DIA"] and answered["shutters_closed"] == ["EPI"]
    assert scope.shutters() == {"DIA LED": False, "EPI": False}


def test_a_capture_notes_the_laser_line_that_was_on(scope, tmp_path):
    scope.select_channel("FITC")
    scope.set_laser(475, on=True, power=20)
    scope.set_filter("Turret-Up", filter_name="GFP")
    scope.acquire("B2", output_folder=str(tmp_path / "run"))
    scope.save()

    store = tmp_path / "run" / "data"
    (found,) = list(store.glob("*.ome.zarr"))
    xml = (found / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")
    assert (
        'ExcitationWavelength="475"' in xml and 'ExcitationWavelengthUnit="nm"' in xml
    )
    assert "laser_lines_on_nm" in xml and "Turret-Up" in xml
