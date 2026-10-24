"""The setup wizard's five Leica steps, run through the wizard on stand-ins for the microscope.

Each step is called exactly as the page calls it, ``wizard.call(step, action,
values)``, and runs the driver's real setup code. Only the microscope is
replaced:

- Data location, travel limits and zero point run on the offline LAS X stand-in
  (``helpers/mock_lasx_api.py``), through the driver's own ``connect()``.
- Image orientation runs on the stand-in the orientation tests use
  (``test_orientation_measure.py``): synthetic pictures that shift as the stage
  moves, and the real orientation fit.
- Objective calibration runs on the small physical simulator of the
  calibration tests (``test_notebook_on_simulated_scope.py``): a scene seen by
  two objectives with known offsets, blurred away from focus. The wizard has to
  recover those offsets, as the notebook does.

The checks are what an operator would rely on: the right files are saved where
the driver loads them, a step that is done shows as done, and a mistake is
answered with a sentence instead of a crash.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("scipy")

from wizard_standins import (
    drive_stage_to as _drive_stage_to,
)
from wizard_standins import (
    install_calibration,
    install_lasx,
    install_orientation,
)

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard import steps
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import (
    client as lasx_client,
)


@pytest.fixture
def lasx(monkeypatch):
    """The offline LAS X stand-in, for data location, limits and zero point."""
    return install_lasx(monkeypatch)


def test_the_wizard_lists_five_steps_in_the_setup_order(lasx):
    outline = steps.leica_wizard().outline()

    assert [s["id"] for s in outline["steps"]] == [
        "data_location",
        "limits",
        "orientation",
        "calibration",
        "origin",
    ]
    assert all(s["purpose"].endswith(".") for s in outline["steps"])
    # When a step was done is said in words, not as the folder's raw name.
    assert (
        outline["steps"][1]["status"]["note"]
        == "Saved on 1 January 2026 at 00:00 (UTC)."
    )


def test_defaults_the_driver_fills_in_on_its_first_connect_do_not_count_as_done(
    lasx, tmp_path, monkeypatch
):
    """The driver writes an unmeasured orientation and an empty calibration when it
    first connects; on a new microscope those steps must still show as to do."""
    from zmart_drivers.leica.stellaris import navigator_expert

    fresh = store.MachineProfile(programdata_root=tmp_path / "fresh")
    limits_dir = store.MACHINE.latest_snapshot("limits")
    monkeypatch.setattr(store, "MACHINE", fresh)
    # Only the limits, as an operator who has done that step would have.
    fresh.publish_snapshot(
        __import__("datetime").datetime(
            2026, 2, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        limits=json.loads((limits_dir / "limits.json").read_text(encoding="utf-8")),
    )
    navigator_expert.connect().close()
    assert (
        fresh.latest_snapshot("orientation") is not None
    )  # the defaults were filled in
    assert fresh.latest_snapshot("calibration") is not None

    status = {
        s["id"]: s["status"]["done"] for s in steps.leica_wizard().outline()["steps"]
    }

    assert status == {
        "data_location": False,
        "limits": True,
        "orientation": False,
        "calibration": False,
        "origin": False,
    }


def test_looking_at_the_steps_writes_nothing(tmp_path, monkeypatch):
    fresh = store.MachineProfile(programdata_root=tmp_path / "fresh")
    monkeypatch.setattr(store, "MACHINE", fresh)

    steps.leica_wizard().outline()

    assert not (tmp_path / "fresh").exists() or not any(
        (tmp_path / "fresh").rglob("*.json")
    )


# --- 1. data location -----------------------------------------------------------------


def test_a_good_folder_is_saved_and_the_step_shows_done(lasx, tmp_path):
    wizard = steps.leica_wizard()
    before = {s["id"]: s["status"] for s in wizard.outline()["steps"]}
    folder = tmp_path / "ZMART-data"
    folder.mkdir()

    answer = wizard.call("data_location", "save", {"folder": str(folder)})

    assert answer["ok"], answer
    after = {s["id"]: s["status"] for s in wizard.outline()["steps"]}
    assert before["data_location"]["done"] is False
    assert after["data_location"]["done"] is True
    assert str(folder) in after["data_location"]["note"]


@pytest.mark.parametrize(
    "folder, reason",
    [
        ("relative/folder", "full path"),
        ("/no/such/folder/anywhere", "does not exist"),
        ("", "full path"),
    ],
)
def test_a_folder_that_cannot_hold_data_is_refused_with_the_reason(
    lasx, folder, reason
):
    wizard = steps.leica_wizard()

    answer = wizard.call("data_location", "save", {"folder": folder})

    assert answer["ok"] is False
    assert reason in answer["error"]
    assert not any(
        s["status"]["done"]
        for s in wizard.outline()["steps"]
        if s["id"] == "data_location"
    )


def test_check_lists_the_problems_without_saving(lasx, tmp_path):
    wizard = steps.leica_wizard()

    assert wizard.call("data_location", "check", {"folder": str(tmp_path)})[
        "result"
    ] == {"problems": []}
    assert wizard.call("data_location", "check", {"folder": "nope"})["result"][
        "problems"
    ]
    assert wizard.outline()["steps"][0]["status"]["done"] is False


# --- 2. travel limits -----------------------------------------------------------------


CORNERS_UM = [
    (20000.0, 15000.0),
    (90000.0, 15000.0),
    (90000.0, 70000.0),
    (20000.0, 70000.0),
]


def test_four_recorded_corners_are_saved_as_the_limits_the_driver_loads(lasx):
    wizard = steps.leica_wizard()
    before = store.MACHINE.latest_snapshot("limits")

    for number, (x, y) in enumerate(CORNERS_UM, start=1):
        _drive_stage_to(lasx, x, y)
        answer = wizard.call("limits", "corner", {"corner": number})
        assert answer["ok"], answer
        assert answer["result"]["position"] == pytest.approx({"x_um": x, "y_um": y})
    saved = wizard.call("limits", "save")

    assert saved["ok"], saved
    assert saved["result"]["x_um"] == [20000.0, 90000.0]
    assert saved["result"]["y_um"] == [15000.0, 70000.0]
    latest = store.MACHINE.latest_snapshot("limits")
    assert latest != before
    written = json.loads((latest / "limits.json").read_text(encoding="utf-8"))
    assert written["x_um"]["range"] == [20000.0, 90000.0]
    assert written["y_um"]["range"] == [15000.0, 70000.0]
    # The same untouched ranges the notebook starts from.
    assert written["z_galvo_um"]["range"] == [-250.0, 250.0]
    assert written["z_wide_um"]["range"] == [0.0, 8000.0]
    # The wizard's record of the corners is kept with the limits, as the notebook would be.
    records = list(latest.rglob("set_limits_wizard_record*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["corners_um"][0] == {
        "x_um": 20000.0,
        "y_um": 15000.0,
    }


def test_the_saved_limits_are_the_ones_a_new_connection_uses(lasx):
    from zmart_drivers.leica.stellaris import navigator_expert

    wizard = steps.leica_wizard()
    for number, (x, y) in enumerate(CORNERS_UM, start=1):
        _drive_stage_to(lasx, x, y)
        wizard.call("limits", "corner", {"corner": number})
    assert wizard.call("limits", "save")["ok"]

    scope = navigator_expert.connect()
    try:
        with pytest.raises(RuntimeError, match="refused"):
            scope.move_to(95000.0 - scope.handle.origin["x_um"], 0.0, 0.0)
    finally:
        scope.close()


def test_saving_before_all_corners_are_recorded_is_refused_in_words(lasx):
    wizard = steps.leica_wizard()

    assert wizard.call("limits", "save") == {
        "ok": False,
        "error": "Record the four corners first.",
    }
    _drive_stage_to(lasx, *CORNERS_UM[0])
    wizard.call("limits", "corner", {"corner": 1})
    answer = wizard.call("limits", "save")

    assert answer["ok"] is False
    assert "corner 2 is not recorded" in answer["error"]


def test_four_corners_on_one_line_are_refused(lasx):
    wizard = steps.leica_wizard()
    for number in range(1, 5):
        _drive_stage_to(lasx, 20000.0 + number * 1000.0, 15000.0)
        wizard.call("limits", "corner", {"corner": number})

    answer = wizard.call("limits", "save")

    assert answer["ok"] is False
    assert "rectangle" in answer["error"]


@pytest.mark.parametrize("corner", [0, 5, "two", None])
def test_a_corner_that_does_not_exist_is_refused(lasx, corner):
    answer = steps.leica_wizard().call("limits", "corner", {"corner": corner})

    assert answer["ok"] is False
    assert "Traceback" not in answer["error"]


def test_recording_a_corner_again_replaces_it(lasx):
    wizard = steps.leica_wizard()
    for number, (x, y) in enumerate(CORNERS_UM, start=1):
        _drive_stage_to(lasx, x, y)
        wizard.call("limits", "corner", {"corner": number})
    _drive_stage_to(lasx, 10000.0, 15000.0)
    wizard.call("limits", "corner", {"corner": 1})

    assert wizard.call("limits", "save")["result"]["x_um"] == [10000.0, 90000.0]


def test_lasx_that_does_not_answer_is_reported_as_such(monkeypatch, lasx):
    def unreachable(**_kw):
        raise ConnectionError("no CAM server")

    monkeypatch.setattr(lasx_client, "connect_python_client", unreachable)

    answer = steps.leica_wizard().call("limits", "corner", {"corner": 1})

    assert answer["ok"] is False
    assert answer["error"].startswith("Could not reach LAS X.")


# --- 5. zero point --------------------------------------------------------------------


def test_the_zero_point_is_saved_where_the_next_connection_loads_it(lasx):
    from zmart_drivers.leica.stellaris import navigator_expert

    wizard = steps.leica_wizard()
    _drive_stage_to(lasx, 42000.0, 31000.0)

    answer = wizard.call("origin", "set")

    assert answer["ok"], answer
    assert answer["result"]["reference"]["x_um"] == pytest.approx(42000.0)
    assert Path(answer["result"]["saved_to"]).is_file()
    assert next(s for s in wizard.outline()["steps"] if s["id"] == "origin")["status"][
        "done"
    ]
    scope = navigator_expert.connect()
    try:
        here = scope.position()
        assert here["x"]["position"] == pytest.approx(0.0, abs=1.0)
        assert here["y"]["position"] == pytest.approx(0.0, abs=1.0)
    finally:
        scope.close()


def test_setting_the_zero_point_leaves_no_connection_behind(lasx, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator_expert import methods

    closed = []
    real_close = methods.Microscope.close
    monkeypatch.setattr(
        methods.Microscope,
        "close",
        lambda self: (closed.append(self), real_close(self)),
    )

    steps.leica_wizard().call("origin", "set")

    assert len(closed) == 1


# --- 3. image orientation -------------------------------------------------------------


@pytest.fixture
def orientation_scope(monkeypatch, tmp_path):
    """Pictures that shift as the stage moves, as in the orientation tests."""
    return install_orientation(monkeypatch, tmp_path / "programdata")


def test_orientation_is_measured_checked_and_saved(orientation_scope):
    wizard = steps.leica_wizard()

    measured = wizard.call("orientation", "measure", {"move_um": 30})
    assert measured["ok"], measured
    assert measured["result"]["label"]
    assert measured["result"]["image"] and Path(measured["result"]["image"]).is_file()
    assert (
        orientation_scope.latest_snapshot("orientation") is None
    )  # not active before saving

    checked = wizard.call("orientation", "check")
    assert checked["ok"], checked
    preview = Path(checked["result"]["image"])
    assert preview.is_file() and preview.suffix == ".jpg"
    assert "reports" in preview.parts and not any(
        p.endswith(".ome.zarr") for p in preview.parts
    )

    saved = wizard.call("orientation", "save")
    assert saved["ok"], saved
    active = orientation_scope.latest_snapshot("orientation")
    assert active is not None and (active / "orientation.json").is_file()
    assert list(active.rglob("set_orientation_wizard_record.json"))


def test_orientation_cannot_be_saved_before_it_is_checked(orientation_scope):
    wizard = steps.leica_wizard()
    wizard.call("orientation", "measure", {"move_um": 30})

    answer = wizard.call("orientation", "save")

    assert answer["ok"] is False
    assert "validation" in answer["error"]
    assert orientation_scope.latest_snapshot("orientation") is None


def test_orientation_steps_in_the_wrong_order_are_refused_in_words(orientation_scope):
    wizard = steps.leica_wizard()

    assert (
        wizard.call("orientation", "check")["error"] == "Measure the orientation first."
    )
    assert (
        wizard.call("orientation", "save")["error"] == "Measure the orientation first."
    )


def test_a_field_without_structure_is_explained(monkeypatch, tmp_path):
    install_orientation(monkeypatch, tmp_path / "programdata", trusted=False)

    answer = steps.leica_wizard().call("orientation", "measure", {"move_um": 30})

    assert answer["ok"] is False
    assert "recognisable structure" in answer["error"]


# --- 4. objective calibration ---------------------------------------------------------


@pytest.fixture
def simulated_scope(monkeypatch, tmp_path):
    """The calibration tests' physical simulator, with two objectives of known offset."""
    return install_calibration(monkeypatch, tmp_path / "programdata")


def test_the_calibration_recovers_the_true_objective_offsets(simulated_scope):
    module, sim, machine = simulated_scope
    wizard = steps.leica_wizard()

    started = wizard.call(
        "calibration", "start", {"pair_name": "10x-20x", "reference_slot": 1}
    )
    assert started["ok"], started
    sim.configure_stack(95.0, 105.0)
    assert wizard.call("calibration", "measure", {"part": "focus_reference"})["ok"]
    sim.turret(2)
    sim.zwide = 104.0
    sim.configure_stack(101.0, 111.0)
    target = wizard.call("calibration", "measure", {"part": "focus_target"})
    assert target["ok"], target
    assert target["result"]["translation_z_um"] == pytest.approx(
        module.TRUE_T[2][2], abs=0.5
    )
    sim.turret(1)
    assert wizard.call("calibration", "measure", {"part": "xy_reference"})["ok"]
    sim.turret(2)
    last = wizard.call("calibration", "measure", {"part": "xy_target"})
    assert last["ok"], last
    tx, ty = last["result"]["translation_xy_um"]
    assert tx == pytest.approx(module.TRUE_T[2][0], abs=0.51)
    assert ty == pytest.approx(module.TRUE_T[2][1], abs=0.51)

    saved = wizard.call("calibration", "save")

    assert saved["ok"], saved
    published = json.loads(machine.calibration_path().read_text(encoding="utf-8"))
    for measured, true in zip(
        published["objectives"]["2"]["translation_um"], module.TRUE_T[2], strict=True
    ):
        assert measured == pytest.approx(true, abs=0.51)
    assert next(s for s in wizard.outline()["steps"] if s["id"] == "calibration")[
        "status"
    ]["done"]


def test_a_measurement_with_the_wrong_objective_is_refused_and_can_be_repeated(
    simulated_scope,
):
    module, sim, _machine = simulated_scope
    wizard = steps.leica_wizard()
    wizard.call("calibration", "start", {"pair_name": "10x-20x", "reference_slot": 1})
    sim.configure_stack(95.0, 105.0)
    wizard.call("calibration", "measure", {"part": "focus_reference"})

    wrong = wizard.call(
        "calibration", "measure", {"part": "focus_target"}
    )  # reference still in

    assert wrong["ok"] is False
    assert "reference objective" in wrong["error"]
    sim.turret(2)
    sim.zwide = 104.0
    sim.configure_stack(101.0, 111.0)
    again = wizard.call("calibration", "measure", {"part": "focus_target"})
    assert again["ok"], again
    assert again["result"]["translation_z_um"] == pytest.approx(
        module.TRUE_T[2][2], abs=0.5
    )


def test_calibration_steps_out_of_order_are_refused_in_words(simulated_scope):
    wizard = steps.leica_wizard()

    assert (
        wizard.call("calibration", "measure", {"part": "focus_reference"})["error"]
        == "Start the calibration first."
    )
    assert (
        wizard.call("calibration", "save")["error"]
        == "Finish all four measurements first."
    )
    assert (
        "name"
        in wizard.call(
            "calibration", "start", {"pair_name": "  ", "reference_slot": 1}
        )["error"]
    )
    wizard.call("calibration", "start", {"pair_name": "10x-20x", "reference_slot": 1})
    assert (
        wizard.call("calibration", "measure", {"part": "everything"})["error"]
        == "Unknown measurement."
    )
    assert (
        wizard.call("calibration", "save")["error"]
        == "Finish all four measurements first."
    )


def test_each_status_says_when_it_was_saved_and_calibration_needs_the_orientation(
    lasx, tmp_path
):
    wizard = steps.leica_wizard()
    folder = tmp_path / "data"
    folder.mkdir()
    wizard.call("data_location", "save", {"folder": str(folder)})

    outline = {s["id"]: s for s in wizard.outline()["steps"]}

    assert outline["limits"]["status"]["saved_at"] == "2026-01-01T00:00:00+00:00"
    assert outline["data_location"]["status"]["saved_at"].endswith("+00:00")
    assert (
        outline["data_location"]["status"]["note"]
        == f"Pictures are saved under {folder.resolve()}."
    )
    # Not done means no time: nothing to compare against.
    assert outline["origin"]["status"] == {
        "done": False,
        "note": None,
        "saved_at": None,
    }
    assert outline["calibration"]["needs"] == ["orientation"]
    assert all(
        outline[i]["needs"] == []
        for i in ("data_location", "limits", "orientation", "origin")
    )


def test_an_orientation_saved_after_the_calibration_is_seen_as_newer(
    orientation_scope, tmp_path, monkeypatch
):
    """The page turns the calibration orange from these two times; here they are checked to be right."""
    from datetime import datetime, timezone

    machine = orientation_scope
    machine.publish_snapshot(
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        calibration={
            "objectives": {
                "1": {"translation_um": [0, 0, 0]},
                "2": {"translation_um": [1, 2, 3]},
            },
            "schema_version": 13,
        },
    )
    wizard = steps.leica_wizard()
    wizard.call("orientation", "measure", {"move_um": 30})
    wizard.call("orientation", "check")
    assert wizard.call("orientation", "save")["ok"]

    outline = {s["id"]: s["status"] for s in wizard.outline()["steps"]}

    assert outline["calibration"]["done"] and outline["orientation"]["done"]
    assert outline["orientation"]["saved_at"] > outline["calibration"]["saved_at"]
