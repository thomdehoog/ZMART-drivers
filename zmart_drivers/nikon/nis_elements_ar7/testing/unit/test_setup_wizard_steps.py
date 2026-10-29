"""The setup wizard's five steps, run through the wizard as the page calls them, on the pretend NIS.

Each test presses the buttons the page offers (``wizard.call(step, action,
values)``) and checks what the microscope's settings folder holds afterwards.
The page itself is tested in ``test_setup_wizard_page.py``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard.steps import nikon_wizard


@pytest.fixture
def wizard(stand_in, key_file):
    made = nikon_wizard({"port": stand_in.port})
    yield made
    made.call("connect", "disconnect", {})


def ok(answer: dict) -> dict:
    assert answer["ok"], answer.get("error")
    return answer["result"]


@pytest.fixture
def connected(wizard, stand_in, tmp_path):
    ok(wizard.call("connect", "choose", {"config": ""}))
    ok(
        wizard.call(
            "connect", "connect", {"output_root": str(tmp_path), "port": stand_in.port}
        )
    )
    return wizard


def status(wizard, step: str) -> dict:
    return next(s["status"] for s in wizard.outline()["steps"] if s["id"] == step)


def test_a_new_microscope_starts_with_every_step_to_do(wizard):
    outline = wizard.outline()
    assert [s["id"] for s in outline["steps"]] == [
        "connect",
        "limits",
        "origin",
        "orientation",
        "hardware",
    ]
    assert not any(s["status"]["done"] for s in outline["steps"])


def test_nothing_after_connect_works_before_connect(wizard):
    answer = wizard.call("origin", "read", {})
    assert not answer["ok"] and "Connect" in answer["error"]


def test_connect_writes_the_macro_makes_a_config_and_saves_the_data_folder(
    wizard, stand_in, tmp_path
):
    written = ok(wizard.call("connect", "macro", {}))
    assert Path(written["bridge_macro"]).is_file()
    config = ok(wizard.call("connect", "choose", {"config": ""}))["config"]
    checks = ok(
        wizard.call(
            "connect", "connect", {"output_root": str(tmp_path), "port": stand_in.port}
        )
    )["checks"]
    assert dict(checks)["NIS-Elements"].startswith("7.")
    assert config in store.machine().configurations()
    assert store.machine().read("data_location")["folder"] == str(tmp_path.resolve())
    assert status(wizard, "connect")["done"]


def test_connect_refuses_a_folder_that_cannot_hold_the_data(wizard, stand_in, tmp_path):
    ok(wizard.call("connect", "choose", {"config": ""}))
    answer = wizard.call(
        "connect",
        "connect",
        {"output_root": str(tmp_path / "missing"), "port": stand_in.port},
    )
    assert not answer["ok"] and "output folder" in answer["error"]
    assert store.machine().configurations() == [], (
        "a refused Connect leaves no half-made config behind"
    )


def test_the_limits_are_measured_saved_and_govern_at_once(connected, stand_in):
    document = ok(connected.call("limits", "document", {}))
    assert document["source"] == "defaults" and "laser_power" in [
        g["key"] for g in document["guarded"]
    ]
    corners = []
    for x, y in ((-100, -200), (300, 400)):
        stand_in.scope.place(x, y, 3000.0)
        corners.append(ok(connected.call("limits", "corner", {})))
    stand_in.scope.place(0, 0, 2000.0)
    low = ok(connected.call("limits", "focus", {}))["z"]
    stand_in.scope.place(0, 0, 4000.0)
    high = ok(connected.call("limits", "focus", {}))["z"]
    limits = {
        **document["document"],
        "x_um": {"range": [-100.0, 300.0]},
        "y_um": {"range": [-200.0, 400.0]},
        "z_um": {"range": [low, high]},
        "devices": {"laser_power": {"range": [0, 20]}},
    }
    saved = ok(
        connected.call("limits", "publish", {"document": limits, "corners": corners})
    )
    assert Path(saved["saved_to"]).is_file()
    assert status(connected, "limits")["done"]
    refused = connected.call(
        "hardware", "set", {"name": "laser_power/0.1", "value": 30.0}
    )
    assert not refused["ok"] and "outside the limits" in refused["error"]
    assert (
        ok(
            connected.call(
                "hardware", "set", {"name": "laser_power/0.1", "value": 10.0}
            )
        )["value"]
        == 10.0
    )


def test_a_broken_limits_document_is_refused_with_the_reason(connected):
    answer = connected.call("limits", "publish", {"document": {"x_um": "everywhere"}})
    assert not answer["ok"] and "missing" in answer["error"]


def test_the_origin_is_read_and_adopted(connected, stand_in):
    stand_in.scope.place(500.0, 600.0, 3000.0)
    rows = ok(connected.call("origin", "read", {}))
    assert rows["x_um"] == 500.0
    ok(connected.call("origin", "publish", {}))
    assert store.machine().read("origin")["origin"]["x_um"] == 500.0
    assert status(connected, "origin")["done"]


def test_the_origin_is_not_adopted_when_the_stage_moved_after_read(connected, stand_in):
    ok(connected.call("origin", "read", {}))
    stand_in.scope.place(1500.0, 2000.0, 3000.0)
    answer = connected.call("origin", "publish", {})
    assert not answer["ok"] and "Read again" in answer["error"]


def test_the_orientation_is_measured_from_three_pictures_and_adopted(
    connected, stand_in
):
    stand_in.scope.camera_mount = (90, True)
    found = ok(connected.call("orientation", "measure", {"step_um": 6}))
    assert found["accepted"] and (found["rotate_deg"], found["mirrored"]) == (90, True)
    assert found["image"].endswith(".png") and Path(
        found["image"]
    ).read_bytes().startswith(b"\x89PNG")
    ok(connected.call("orientation", "publish", {}))
    standing = ok(connected.call("orientation", "standing", {}))
    assert (
        standing["measured"]
        and standing["label"] == "turned 90 degrees clockwise, mirrored"
    )
    record = (
        store.machine()
        .saved_path("orientation")
        .with_name("set_orientation_wizard_record.json")
    )
    assert json.loads(record.read_text())["how"] == "measured from three pictures"


def test_the_orientation_can_be_chosen_by_hand(connected):
    ok(
        connected.call(
            "orientation",
            "publish",
            {"by_hand": True, "rotate_deg": 180, "mirrored": False},
        )
    )
    assert (
        ok(connected.call("orientation", "standing", {}))["label"]
        == "turned 180 degrees clockwise, not mirrored"
    )
    assert status(connected, "orientation")["done"]


def test_the_hardware_is_listed_tried_and_noted(connected, stand_in):
    settings = ok(connected.call("hardware", "inventory", {}))["settings"]
    names = {s["name"] for s in settings}
    assert {"shutter/EPI", "filter/Emission Wheel", "light_path", "pfs"} <= names
    assert (
        ok(connected.call("hardware", "set", {"name": "shutter/EPI", "value": 1}))[
            "value"
        ]
        == 1
    )
    assert stand_in.scope.shutters["EPI"] == 1
    refused = connected.call(
        "hardware", "set", {"name": "laser_power/0.1", "value": 5.0}
    )
    assert not refused["ok"] and "guarded" in refused["error"]
    ok(connected.call("hardware", "publish", {}))
    assert status(connected, "hardware")["done"]


def test_two_presses_at_once_wait_for_each_other(connected):
    assert connected._busy.acquire(blocking=False)
    try:
        answer = connected.call("origin", "read", {})
        assert not answer["ok"] and "busy" in answer["error"]
    finally:
        connected._busy.release()
