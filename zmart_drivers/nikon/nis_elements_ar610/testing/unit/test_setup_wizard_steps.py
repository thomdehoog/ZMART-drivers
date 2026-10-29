"""The five Nikon steps of the setup wizard, walked through against the mock NIS-Elements.

Each test does what the page does: it calls the wizard's actions by name with
the values the page sends, and reads the answers. The wizard connects through
the real bridge server to the mock, so what it saves is checked by the
driver's own loaders, and the next connection is shown to use it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmart_drivers.nikon import nis_elements_ar610 as nis_elements
from zmart_drivers.nikon.nis_elements_ar610.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.orientation import (
    Orientation,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard.steps import (
    nikon_wizard,
)


@pytest.fixture
def wizard():
    made = nikon_wizard()
    yield made
    made.call("connect", "disconnect")


def ok(answer: dict):
    """The result of an action that should have worked; a failure shows its sentence."""
    assert answer["ok"], answer.get("error")
    return answer["result"]


def _connected(wizard, connection, tmp_path) -> dict:
    (tmp_path / "data").mkdir(exist_ok=True)
    ok(wizard.call("connect", "choose", {"config": ""}))
    return ok(
        wizard.call(
            "connect",
            "connect",
            {
                "host": "127.0.0.1",
                "port": str(connection["port"]),
                "output_root": str(tmp_path / "data"),
            },
        )
    )


def _done(wizard) -> dict[str, bool]:
    return {step["id"]: step["status"]["done"] for step in wizard.outline()["steps"]}


# =============================================================================
# Connect
# =============================================================================


def test_nothing_is_done_on_a_new_microscope_and_every_step_waits_for_connect(wizard):
    assert _done(wizard) == {
        "connect": False,
        "limits": False,
        "origin": False,
        "orientation": False,
        "calibration": False,
    }
    card = ok(wizard.call("connect", "card"))
    assert card["microscope"] == "ti2" and card["port"] == "54470"
    assert card["connected"] is False and card["configs"] == []
    assert (
        wizard.call("limits", "document")["error"] == "Connect to the microscope first."
    )


def test_connect_makes_a_new_config_saves_the_output_folder_and_checks_the_microscope(
    wizard, connection, tmp_path
):
    answer = _connected(wizard, connection, tmp_path)
    checks = dict(answer["checks"])
    assert checks["driver"] == f"nis_elements_ar610 {nis_elements.__version__}"
    assert checks["stage"] == "x 1000 · y -500 · z 500.0 µm"
    assert checks["objective"] == "slot 1 · Plan Apo 10x"
    assert checks["output folder"] == str((tmp_path / "data").resolve())
    assert len(store.MACHINE.configurations()) == 1
    assert _done(wizard)["connect"] is True

    ok(wizard.call("connect", "disconnect"))
    assert _done(wizard)["connect"] is False
    card = ok(wizard.call("connect", "card"))
    assert card["output_root_saved"] is True


def test_a_wrong_port_or_another_computer_is_refused_in_words(wizard, tmp_path):
    ok(wizard.call("connect", "choose", {"config": ""}))
    refused = wizard.call(
        "connect",
        "connect",
        {"host": "127.0.0.1", "port": "fifty", "output_root": str(tmp_path)},
    )
    assert refused == {
        "ok": False,
        "error": "The port is a whole number, such as 54470, not 'fifty'.",
    }
    elsewhere = wizard.call(
        "connect", "connect", {"host": "microscope-pc", "output_root": str(tmp_path)}
    )
    assert elsewhere["ok"] is False and "this computer only" in elsewhere["error"]
    # Nothing was saved for a connection that was refused.
    assert store.MACHINE.configurations() == []


def test_when_nis_elements_does_not_answer_the_operator_is_told_what_to_start(
    wizard, tmp_path
):
    ok(wizard.call("connect", "choose", {"config": ""}))
    answer = wizard.call(
        "connect",
        "connect",
        {"host": "127.0.0.1", "port": "1", "output_root": str(tmp_path)},
    )
    assert answer["ok"] is False and "start_bridge.mac" in answer["error"]


# =============================================================================
# Define limits
# =============================================================================


def test_the_limits_are_drawn_from_the_defaults_cornered_and_adopted(
    wizard, connection, tmp_path, mock
):
    _connected(wizard, connection, tmp_path)
    loaded = ok(wizard.call("limits", "document"))
    assert loaded["source"] == "defaults"
    assert [a["key"] for a in loaded["axes"]] == ["x_um", "y_um", "z_um"]
    assert "laser_power" in loaded["settings"] and "piezo_step_um" in loaded["settings"]

    mock.travel_s = 0.0
    corners = []
    for x, y in ((-20000, -10000), (20000, -10000), (-20000, 10000), (20000, 10000)):
        mock.move_xyz(x, y, 500)
        corners.append(ok(wizard.call("limits", "corner")))
    assert corners[0] == {"x": -20000.0, "y": -10000.0}

    document = loaded["document"]
    document["x_um"] = {"range": [-20000, 20000]}
    document["y_um"] = {"range": [-10000, 10000]}
    document["laser_power"] = {"range": [0, 60]}
    saved = ok(
        wizard.call("limits", "publish", {"document": document, "corners": corners})
    )
    record = Path(saved["saved_to"])
    assert json.loads((record / "limits.json").read_text())["laser_power"] == {
        "range": [0.0, 60.0]
    }
    assert list((record / "notebook").glob("set_limits_wizard_record_*.json"))
    assert _done(wizard)["limits"] is True

    # The wizard connected again, so the new limits fence the next steps' moves.
    loaded_again = ok(wizard.call("limits", "document"))
    assert loaded_again["source"] == "published"


def test_a_malformed_limits_document_is_refused_and_nothing_is_saved(
    wizard, connection, tmp_path
):
    _connected(wizard, connection, tmp_path)
    document = ok(wizard.call("limits", "document"))["document"]
    document["x_um"] = {"range": [10, -10]}
    answer = wizard.call("limits", "publish", {"document": document})
    assert answer["ok"] is False and "minimum above its maximum" in answer["error"]
    assert store.MACHINE.latest_record("limits") is None


# =============================================================================
# Define coordinate system origin
# =============================================================================


def test_the_origin_is_read_and_adopted_and_refused_when_the_stage_moved(
    wizard, connection, tmp_path, mock
):
    _connected(wizard, connection, tmp_path)
    assert ok(wizard.call("origin", "standing")) == {"origin": None}
    assert wizard.call("origin", "publish")["error"] == "Read first."

    read = ok(wizard.call("origin", "read"))
    assert read["actuators"][0] == ["x stage", "1000.00 µm"]

    mock.travel_s = 0.0
    mock.move_xyz(1010, -500, 500)
    moved = wizard.call("origin", "publish")
    assert moved["ok"] is False and "changed since Read" in moved["error"]

    ok(wizard.call("origin", "read"))
    saved = ok(wizard.call("origin", "publish"))
    assert Path(saved["saved_to"]).is_file()
    assert ok(wizard.call("origin", "standing"))["origin"]["x_um"] == 1010.0
    assert _done(wizard)["origin"] is True


# =============================================================================
# Image-to-stage calibration
# =============================================================================


def test_the_orientation_is_measured_checked_and_adopted(
    wizard, connection, tmp_path, mock
):
    mock.turn = (90, True)
    _connected(wizard, connection, tmp_path)
    assert ok(wizard.call("orientation", "standing")) == {"measured": False}
    assert (
        wizard.call("orientation", "check")["error"] == "Measure the orientation first."
    )

    measured = ok(wizard.call("orientation", "measure", {"stage_move_um": 10}))
    assert measured["accepted"] is True
    assert (measured["rotation_deg"], measured["reflection"]) == (90, True)
    assert measured["pixel_size_um"] == pytest.approx(0.65, rel=0.1)

    check = ok(wizard.call("orientation", "check"))
    assert Path(check["image"]).is_file() and check["image"].endswith(".jpg")

    saved = ok(wizard.call("orientation", "publish"))
    assert (Path(saved["saved_to"]) / "set_orientation_wizard_record.json").is_file()
    assert ok(wizard.call("orientation", "standing"))["rotation_deg"] == 90
    assert _done(wizard)["orientation"] is True

    again = nis_elements.connect(**connection)
    try:
        assert again.handle.orientation == Orientation(90, True)
    finally:
        again.close()


def test_a_blank_field_is_answered_with_what_to_do(wizard, connection, tmp_path, mock):
    import numpy as np

    _connected(wizard, connection, tmp_path)
    mock.frame = np.zeros((48, 64))
    answer = ok(wizard.call("orientation", "measure", {"stage_move_um": 10}))
    assert answer["accepted"] is False
    assert "too little texture" in answer["why"] and "Rerun" in answer["why"]
    assert wizard.call("orientation", "publish")["error"] == "Measure first."


# =============================================================================
# Objective calibration
# =============================================================================


def test_a_pair_is_measured_by_eye_and_adopted(wizard, connection, tmp_path, mock):
    _connected(wizard, connection, tmp_path)
    lenses = ok(wizard.call("calibration", "lenses"))
    # Slot 3 is empty on the mock's nosepiece, so it is not offered.
    assert [lens["slot"] for lens in lenses["lenses"]] == [1, 2, 4]
    assert lenses["reference"] is None and lenses["current"] == 1

    pair = {"reference": 1, "target": 2}
    first = ok(wizard.call("calibration", "record", {**pair, "side": "reference"}))
    assert "translation_um" not in first

    wrong = wizard.call("calibration", "record", {**pair, "side": "target"})
    assert wrong["ok"] is False and "Turn to slot 2 first" in wrong["error"]

    ok(wizard.call("calibration", "turn", {"slot": 2}))
    # The nosepiece turned and nothing else moved; the operator re-centres the feature.
    assert mock.get_position() == {"x": 1000.0, "y": -500.0, "z": 500.0}
    mock.travel_s = 0.0
    mock.move_xyz(1012.5, -503.0, 541.0)
    second = ok(wizard.call("calibration", "record", {**pair, "side": "target"}))
    assert second["translation_um"] == {"x": 12.5, "y": -3.0, "z": 41.0}

    saved = ok(wizard.call("calibration", "publish", {"reference": 1}))
    assert (
        Path(saved["saved_to"]) / "calibrate_objective_pair_wizard_record.json"
    ).is_file()
    assert _done(wizard)["calibration"] is True
    held = ok(wizard.call("calibration", "lenses"))
    assert held["reference"] == 1 and held["held"]["2"] == [12.5, -3.0, 41.0]

    again = nis_elements.connect(**connection)
    try:
        assert again.handle.translations == {1: (0.0, 0.0, 0.0), 2: (12.5, -3.0, 41.0)}
    finally:
        again.close()


def test_a_new_reference_counts_the_saved_offsets_again_from_it(
    wizard, connection, tmp_path, mock
):
    from zmart_drivers.nikon.nis_elements_ar610.driver_setup.optical_calibration import (
        model,
    )

    _connected(wizard, connection, tmp_path)
    # A calibration saved earlier in this config, counted from slot 1.
    model.adopt_calibration(
        {
            1: {"name": "Plan Apo 10x", "translation_um": [0, 0, 0]},
            2: {"name": "Apo 20x WI", "translation_um": [10, 0, 40]},
            4: {"name": "Plan Apo 60x WI", "translation_um": [4, 4, 60]},
        }
    )
    mock.travel_s = 0.0
    ok(wizard.call("calibration", "turn", {"slot": 2}))
    pair = {"reference": 2, "target": 1}
    ok(wizard.call("calibration", "record", {**pair, "side": "reference"}))
    ok(wizard.call("calibration", "turn", {"slot": 1}))
    mock.move_xyz(990, -500, 460)
    ok(wizard.call("calibration", "record", {**pair, "side": "target"}))
    ok(wizard.call("calibration", "publish", {"reference": 2}))

    translations, _ = model.load_objective_calibration()
    assert translations == {
        2: (0.0, 0.0, 0.0),
        1: (-10.0, 0.0, -40.0),
        4: (-6.0, 4.0, 20.0),  # the saved 60x offset, counted again from slot 2
    }


def test_nothing_is_saved_before_a_pair_is_recorded_under_both_objectives(
    wizard, connection, tmp_path
):
    _connected(wizard, connection, tmp_path)
    ok(
        wizard.call(
            "calibration", "record", {"reference": 1, "target": 2, "side": "reference"}
        )
    )
    answer = wizard.call("calibration", "publish", {"reference": 1})
    assert answer == {
        "ok": False,
        "error": "Record a preset under both objectives first.",
    }


# =============================================================================
# All five, in the order the rail walks them
# =============================================================================


def test_a_whole_setup_turns_every_step_green_and_the_next_connection_is_ready(
    wizard, connection, tmp_path, mock
):
    mock.turn = (180, False)
    _connected(wizard, connection, tmp_path)
    document = ok(wizard.call("limits", "document"))["document"]
    ok(wizard.call("limits", "publish", {"document": document}))
    ok(wizard.call("origin", "read"))
    ok(wizard.call("origin", "publish"))
    ok(wizard.call("orientation", "measure", {"stage_move_um": 10}))
    ok(wizard.call("orientation", "check"))
    ok(wizard.call("orientation", "publish"))
    pair = {"reference": 1, "target": 2}
    ok(wizard.call("calibration", "record", {**pair, "side": "reference"}))
    ok(wizard.call("calibration", "turn", {"slot": 2}))
    ok(wizard.call("calibration", "record", {**pair, "side": "target"}))
    ok(wizard.call("calibration", "publish", {"reference": 1}))

    assert all(_done(wizard).values())
    ok(wizard.call("connect", "disconnect"))
    ready = nis_elements.connect(**connection)
    try:
        assert ready.state()["observed"]["setup"] == {
            **ready.state()["observed"]["setup"],
            "ready": True,
            "issues": [],
        }
    finally:
        ready.close()
