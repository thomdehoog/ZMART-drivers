"""The setup wizard's Leica steps, run through the wizard on stand-ins for the microscope.

The wizard follows the ZMART driver configuration workflow: Connect, Define
limits, Define coordinate system origin, Image-to-stage calibration and
Objective calibration. Each action is called exactly as the page calls it,
``wizard.call(step, action, values)``, and runs the driver's real setup code.
Only the microscope is replaced (see ``helpers/wizard_standins.py``):

- Connect, the limits and the origin run on the offline LAS X stand-in,
  through the driver's own ``connect()``.
- Image-to-stage calibration runs on the orientation tests' pictures that
  shift as the stage moves, and the real orientation fit.
- Objective calibration runs on the calibration tests' physical simulator: a
  scene seen by two objectives with known offsets, blurred away from focus.
  The wizard has to recover those offsets, as the notebook does.

The checks are what an operator would rely on: the right files are saved
where the driver loads them, a step that is done shows as done, and a mistake
is answered with a sentence instead of a crash.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("scipy")

from wizard_standins import (
    drive_stage_to,
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
    """The offline LAS X stand-in, for Connect, the limits and the origin."""
    return install_lasx(monkeypatch)


@pytest.fixture
def data_folder(tmp_path):
    folder = tmp_path / "ZMART-data"
    folder.mkdir()
    return folder


@pytest.fixture
def connected(lasx, data_folder):
    """A wizard that has been through Connect."""
    wizard = steps.leica_wizard()
    answer = wizard.call("connect", "connect", {"output_root":str(data_folder)})
    assert answer["ok"], answer
    return wizard


def status(wizard, step_id):
    return next(s for s in wizard.outline()["steps"] if s["id"] == step_id)["status"]


def saved_at(snapshot: Path) -> str:
    moment = datetime.strptime(snapshot.name[:26], "%Y-%m-%dT%H-%M-%S-%f")  # noqa: DTZ007 -- the folder name is UTC; set below
    return moment.replace(tzinfo=timezone.utc).isoformat()


def test_the_steps_are_the_driver_configuration_workflows_in_its_order(lasx):
    outline = steps.leica_wizard().outline()

    assert [(s["id"], s["title"]) for s in outline["steps"]] == [
        ("connect", "Connect"),
        ("limits", "Define limits"),
        ("origin", "Define coordinate system origin"),
        ("orientation", "Image-to-stage calibration"),
        ("calibration", "Objective calibration"),
    ]
    calibration = next(s for s in outline["steps"] if s["id"] == "calibration")
    assert calibration["needs"] == ["orientation"]


def test_no_step_reaches_the_microscope_before_connect(lasx):
    wizard = steps.leica_wizard()

    for step, action in (
        ("limits", "document"),
        ("origin", "read"),
        ("orientation", "measure"),
        ("calibration", "lenses"),
    ):
        assert wizard.call(step, action) == {
            "ok": False,
            "error": "Connect to the microscope first.",
        }


# --- 1. Connect ---------------------------------------------------------------------


def test_the_card_suggests_a_data_folder_until_one_is_saved(lasx, data_folder):
    wizard = steps.leica_wizard()

    card = wizard.call("connect", "card")["result"]
    assert card["output_root"] in (r"D:\ZMART-data", r"C:\ZMART-data")
    assert card["output_root_saved"] is False and card["connected"] is False
    wizard.call("connect", "connect", {"output_root":str(data_folder)})
    card = wizard.call("connect", "card")["result"]
    assert card["output_root"] == str(data_folder.resolve())
    assert card["output_root_saved"] is True and card["connected"] is True


def test_connect_saves_the_data_folder_and_lists_what_it_found(lasx, data_folder):
    wizard = steps.leica_wizard()

    answer = wizard.call("connect", "connect", {"output_root":str(data_folder)})

    assert answer["ok"], answer
    checks = dict(answer["result"]["checks"])
    assert checks["driver"].startswith("navigator_expert ")
    assert checks["stage"] == "x 50000 · y 30000 · z-wide 0.0 µm"
    assert checks["objective"].startswith("slot 3 · ")
    assert checks["settings folder"] == str(store.MACHINE.snapshot_root())
    assert checks["output folder"] == str(data_folder.resolve())
    assert status(wizard, "connect")["done"]


@pytest.mark.parametrize(
    "folder, reason",
    # The missing folder is made under the test's own folder, so it is a full
    # path on every system: on Windows "/no/such/folder" has no drive and is
    # refused as "not a full path" instead.
    [("relative/folder", "full path"), ("{tmp}/no/such/folder", "does not exist")],
)
def test_a_data_folder_that_cannot_hold_pictures_stops_connect_with_the_reason(
    lasx, tmp_path, folder, reason
):
    folder = folder.replace("{tmp}", str(tmp_path))
    wizard = steps.leica_wizard()

    answer = wizard.call("connect", "connect", {"output_root":folder})

    assert answer["ok"] is False
    assert answer["error"].startswith("The output folder cannot be used:")
    assert reason in answer["error"]
    assert not status(wizard, "connect")["done"]


def test_lasx_that_does_not_answer_is_reported_as_such(monkeypatch, lasx, data_folder):
    def unreachable(**_kw):
        raise ConnectionError("no CAM server")

    monkeypatch.setattr(lasx_client, "connect_python_client", unreachable)

    answer = steps.leica_wizard().call(
        "connect", "connect", {"output_root":str(data_folder)}
    )

    assert answer["ok"] is False
    assert answer["error"].startswith("Could not reach LAS X.")


def test_the_card_shows_the_seven_connection_words_as_this_driver_connects(lasx):
    card = steps.leica_wizard().call("connect", "card")["result"]

    assert {key: card[key] for key in ("microscope", "api_type", "host", "client", "config")} == {
        "microscope": "stellaris-y42h93",
        "api_type": "LAS X CAM API - Navigator Expert",
        "host": "localhost",
        "client": "PythonClient",
        "config": "",
    }
    assert "password" not in card and "output_root" in card


def test_connect_reaches_lasx_as_the_client_typed(
    monkeypatch, lasx, data_folder
):
    stand_in, asked = lasx_client.connect_python_client, []

    def recorded(**kw):
        asked.append(kw["client_name"])
        return stand_in(**kw)

    monkeypatch.setattr(lasx_client, "connect_python_client", recorded)
    wizard = steps.leica_wizard()

    answer = wizard.call(
        "connect",
        "connect",
        {"output_root": str(data_folder), "client": "ZmartWizard", "password": "typed"},
    )

    assert answer["ok"], answer
    assert asked == ["ZmartWizard"]


def test_the_session_keeps_no_password(lasx):
    from zmart_drivers.leica.stellaris.navigator_expert import methods

    scope = methods.connect(client="ZmartWizard", password="typed")
    try:
        assert scope.handle.connection["client"] == "ZmartWizard"
        assert "password" not in scope.handle.connection
    finally:
        scope.close()


@pytest.mark.parametrize(
    "typed, reason",
    [({"host": "192.168.1.20"}, "answers on this computer only"), ({"config": "Sep 7"}, "named configurations")],
)
def test_a_connection_the_driver_cannot_honour_is_refused_before_anything_is_saved(
    lasx, data_folder, typed, reason
):
    wizard = steps.leica_wizard()

    answer = wizard.call("connect", "connect", {"output_root": str(data_folder), **typed})

    assert answer["ok"] is False and reason in answer["error"]
    assert store.MACHINE.latest_snapshot("data_location") is None
    assert not status(wizard, "connect")["done"]


def test_disconnect_closes_the_connection_and_connect_is_to_do_again(connected):
    assert connected.call("connect", "disconnect")["ok"]

    assert not status(connected, "connect")["done"]
    assert (
        connected.call("origin", "read")["error"] == "Connect to the microscope first."
    )


# --- 2. Define limits ---------------------------------------------------------------


def test_the_limits_document_is_every_entry_of_the_file(connected):
    from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits.limit_rules import (
        SETTER_LIMIT_KEYS,
    )

    doc = connected.call("limits", "document")["result"]

    assert doc["source"] == "published"  # the stand-in's machine holds fixture limits
    assert set(doc["document"]) == {
        "x_um",
        "y_um",
        "z_galvo_um",
        "z_wide_um",
        "objective_slot",
        *SETTER_LIMIT_KEYS,
    }
    assert doc["required"] == ["x_um", "y_um", "z_galvo_um", "z_wide_um"]
    assert doc["measured"] == ["x_um", "y_um"]
    assert doc["actuators"] == {"x": ["motoric"], "y": ["motoric"]}


def test_a_corner_is_the_stage_position_read_from_the_chosen_motors(connected, lasx):
    drive_stage_to(lasx, 21000.0, 16000.0)

    answer = connected.call(
        "limits", "corner", {"x_motor": "motoric", "y_motor": "motoric"}
    )

    assert answer["result"] == pytest.approx({"x": 21000.0, "y": 16000.0})


def test_saved_limits_govern_the_next_moves(connected):
    import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

    doc = connected.call("limits", "document")["result"]["document"]
    doc["x_um"] = {"range": [20000.0, 90000.0]}
    doc["y_um"] = {"range": [15000.0, 70000.0]}
    doc["objective_slot"] = {"allowed": [1, 3]}
    doc["set_zoom"] = {"range": [1.0, 10.0]}
    before = store.MACHINE.latest_snapshot("limits")
    corners = {"top_left": {"x": 20000.0, "y": 15000.0}}

    answer = connected.call("limits", "publish", {"document": doc, "corners": corners})

    assert answer["ok"], answer
    latest = store.MACHINE.latest_snapshot("limits")
    assert latest != before
    written = json.loads((latest / "limits.json").read_text(encoding="utf-8"))
    assert written["x_um"]["range"] == [20000.0, 90000.0]
    assert written["objective_slot"] == {"allowed": [1, 3]}
    assert written["set_zoom"] == {"range": [1.0, 10.0]}
    assert list(latest.rglob("set_limits_wizard_record*.json"))
    assert status(connected, "limits")["saved_at"] == saved_at(latest)
    scope = navigator_expert.connect()
    try:
        # 95 mm was allowed by the old limits; the new ones end at 90 mm.
        with pytest.raises(RuntimeError, match="refused"):
            scope.move_to(95000.0 - scope.handle.origin["x_um"], 0.0, 0.0)
    finally:
        scope.close()


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"x_um": {"range": [90000.0, 20000.0]}}, "x_um"),
        ({"objective_slot": {"range": [0, 2]}}, "objective_slot"),
        ({"set_zoom": {"allowed": []}}, "must not be empty"),
    ],
)
def test_limits_the_driver_cannot_take_are_refused_and_nothing_is_saved(
    connected, change, reason
):
    doc = connected.call("limits", "document")["result"]["document"]
    doc.update(change)
    before = store.MACHINE.latest_snapshot("limits")

    answer = connected.call("limits", "publish", {"document": doc})

    assert answer["ok"] is False and reason in answer["error"]
    assert store.MACHINE.latest_snapshot("limits") == before


# --- 3. Define coordinate system origin ---------------------------------------------


def test_read_shows_every_drive_and_the_objective(connected, lasx):
    drive_stage_to(lasx, 42000.0, 31000.0)

    answer = connected.call("origin", "read")["result"]

    assert dict(answer["actuators"]) == {
        "x motoric": "42000.00 µm",
        "y motoric": "31000.00 µm",
        "z-wide": "0.00 µm",
        "z-galvo": "0.00 µm",
        "objective": "slot 3 · HC PL APO 63x/1.40 OIL CS2",
    }
    assert store.MACHINE.read_origin() is None  # Read writes nothing


def test_the_origin_is_saved_where_the_next_connection_loads_it(connected, lasx):
    import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert

    drive_stage_to(lasx, 42000.0, 31000.0)
    connected.call("origin", "read")

    answer = connected.call("origin", "publish")

    assert answer["ok"], answer
    assert Path(answer["result"]["saved_to"]).is_file()
    standing = connected.call("origin", "standing")["result"]
    assert standing["origin"]["x_um"] == pytest.approx(42000.0)
    assert status(connected, "origin")["done"]
    scope = navigator_expert.connect()
    try:
        assert scope.position()["x"]["position"] == pytest.approx(0.0, abs=1.0)
    finally:
        scope.close()


def test_the_origin_is_not_saved_when_the_stage_moved_after_read(connected, lasx):
    assert connected.call("origin", "publish")["error"] == "Read first."
    drive_stage_to(lasx, 42000.0, 31000.0)
    connected.call("origin", "read")
    drive_stage_to(lasx, 43000.0, 31000.0)

    answer = connected.call("origin", "publish")

    assert answer["ok"] is False and "changed since Read" in answer["error"]
    assert store.MACHINE.read_origin() is None


# --- 4. Image-to-stage calibration ---------------------------------------------------


@pytest.fixture
def orientation_scope(connected, monkeypatch, tmp_path):
    """Pictures that shift as the stage moves, as in the orientation tests."""
    return connected, install_orientation(monkeypatch, tmp_path / "programdata")


def test_orientation_is_measured_checked_and_saved(orientation_scope):
    wizard, machine = orientation_scope

    measured = wizard.call("orientation", "measure", {"stage_move_um": 40})
    assert measured["ok"], measured
    assert measured["result"]["accepted"] is True
    # The driver's own sheet: the detected correction above the eight candidates.
    assert Path(measured["result"]["image"]).is_file()
    assert machine.latest_snapshot("orientation") is None  # not active before saving

    checked = wizard.call("orientation", "check")
    assert checked["ok"], checked
    preview = Path(checked["result"]["image"])
    assert preview.is_file() and "reports" in preview.parts

    assert wizard.call("orientation", "publish")["ok"]
    active = machine.latest_snapshot("orientation")
    assert (active / "orientation.json").is_file()
    standing = wizard.call("orientation", "standing")["result"]
    assert standing["measured"] is True and standing["rotation_deg"] is not None
    assert standing["image"] and Path(standing["image"]).is_file()
    assert status(wizard, "orientation")["done"]


def test_orientation_cannot_be_saved_before_the_check_picture(orientation_scope):
    wizard, machine = orientation_scope
    wizard.call("orientation", "measure", {"stage_move_um": 40})

    answer = wizard.call("orientation", "publish")

    assert answer["ok"] is False and "validation" in answer["error"]
    assert machine.latest_snapshot("orientation") is None


def test_orientation_steps_in_the_wrong_order_are_refused_in_words(orientation_scope):
    wizard, _machine = orientation_scope

    assert (
        wizard.call("orientation", "check")["error"] == "Measure the orientation first."
    )
    assert wizard.call("orientation", "publish")["error"] == "Measure first."


def test_a_field_without_structure_is_explained(connected, monkeypatch, tmp_path):
    install_orientation(monkeypatch, tmp_path / "programdata", trusted=False)

    answer = connected.call("orientation", "measure", {"stage_move_um": 40})

    assert answer["ok"] is True and answer["result"]["accepted"] is False
    assert "recognisable structure" in answer["result"]["why"]


# --- 5. Objective calibration --------------------------------------------------------


@pytest.fixture
def simulated_scope(connected, monkeypatch, tmp_path):
    """The calibration tests' physical simulator, with two objectives of known offset."""
    module, sim, machine = install_calibration(monkeypatch, tmp_path / "programdata")
    return connected, module, sim, machine


PAIR = {"reference": 1, "target": 2}


def measure_the_pair(wizard, sim):
    """What the operator does, in order: the four measurements, turning the turret between them."""
    sim.configure_stack(95.0, 105.0)
    assert wizard.call("calibration", "focus", {**PAIR, "side": "reference"})["ok"]
    sim.turret(2)
    sim.zwide = 104.0
    sim.configure_stack(101.0, 111.0)
    target = wizard.call("calibration", "focus", {**PAIR, "side": "target"})
    assert target["ok"], target
    sim.turret(1)
    assert wizard.call("calibration", "xy", {**PAIR, "side": "reference"})["ok"]
    sim.turret(2)
    xy = wizard.call("calibration", "xy", {**PAIR, "side": "target"})
    assert xy["ok"], xy
    return target["result"], xy["result"]


def test_the_lenses_are_the_objectives_on_the_turret(simulated_scope):
    wizard, _module, _sim, _machine = simulated_scope

    lenses = wizard.call("calibration", "lenses")["result"]

    assert lenses["lenses"] == [{"slot": 1, "name": "10x"}, {"slot": 2, "name": "20x"}]
    assert lenses["reference"] is None and lenses["held"] == {}


def test_the_calibration_recovers_the_true_objective_offsets(simulated_scope):
    wizard, module, sim, machine = simulated_scope

    focus, xy = measure_the_pair(wizard, sim)

    assert focus["translation_z_um"] == pytest.approx(module.TRUE_T[2][2], abs=0.5)
    assert Path(focus["image"]).is_file()  # the focus curve beside the sharpest slice
    assert xy["trusted"] is True
    assert xy["translation_um"]["x"] == pytest.approx(module.TRUE_T[2][0], abs=0.51)
    assert xy["translation_um"]["y"] == pytest.approx(module.TRUE_T[2][1], abs=0.51)
    assert Path(xy["image"]).is_file()  # the two objectives overlaid

    assert wizard.call("calibration", "publish")["ok"]
    published = json.loads(machine.calibration_path().read_text(encoding="utf-8"))
    offsets = published["objectives"]["2"]["translation_um"]
    for measured, true in zip(offsets, module.TRUE_T[2], strict=True):
        assert measured == pytest.approx(true, abs=0.51)
    lenses = wizard.call("calibration", "lenses")["result"]
    assert lenses["reference"] == 1 and set(lenses["held"]) == {"1", "2"}
    assert status(wizard, "calibration")["done"]


def test_a_measurement_with_the_wrong_objective_is_refused_and_can_be_repeated(
    simulated_scope,
):
    wizard, module, sim, _machine = simulated_scope
    sim.configure_stack(95.0, 105.0)
    wizard.call("calibration", "focus", {**PAIR, "side": "reference"})

    wrong = wizard.call(
        "calibration", "focus", {**PAIR, "side": "target"}
    )  # reference still in

    assert wrong["ok"] is False and "reference objective" in wrong["error"]
    sim.turret(2)
    sim.zwide = 104.0
    sim.configure_stack(101.0, 111.0)
    again = wizard.call("calibration", "focus", {**PAIR, "side": "target"})
    assert again["ok"], again
    assert again["result"]["translation_z_um"] == pytest.approx(
        module.TRUE_T[2][2], abs=0.5
    )


def test_calibration_steps_out_of_order_are_refused_in_words(simulated_scope):
    wizard, _module, _sim, _machine = simulated_scope

    assert wizard.call("calibration", "publish")["error"] == "Measure a preset first."
    unknown = wizard.call("calibration", "focus", {**PAIR, "side": "sideways"})
    assert unknown["error"] == "Unknown measurement."
    assert (
        "number" in wizard.call("calibration", "focus", {"side": "reference"})["error"]
    )


# --- what counts as done ---------------------------------------------------------------


def test_defaults_the_driver_fills_in_on_its_first_connect_do_not_count_as_done(
    lasx, tmp_path, monkeypatch, data_folder
):
    """The driver writes an unmeasured orientation and an empty calibration when it
    first connects; on a new microscope those steps must still show as to do."""
    fresh = store.MachineProfile(programdata_root=tmp_path / "fresh")
    limits_dir = store.MACHINE.latest_snapshot("limits")
    monkeypatch.setattr(store, "MACHINE", fresh)
    fresh.publish_snapshot(
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        limits=json.loads((limits_dir / "limits.json").read_text(encoding="utf-8")),
    )
    wizard = steps.leica_wizard()
    assert wizard.call("connect", "connect", {"output_root":str(data_folder)})["ok"]
    assert (
        fresh.latest_snapshot("orientation") is not None
    )  # the defaults were filled in
    assert fresh.latest_snapshot("calibration") is not None

    done = {s["id"]: s["status"]["done"] for s in wizard.outline()["steps"]}

    assert done == {
        "connect": True,
        "limits": True,
        "origin": False,
        "orientation": False,
        "calibration": False,
    }


def test_looking_at_the_steps_writes_nothing(tmp_path, monkeypatch):
    fresh = store.MachineProfile(programdata_root=tmp_path / "fresh")
    monkeypatch.setattr(store, "MACHINE", fresh)

    steps.leica_wizard().outline()

    assert not (tmp_path / "fresh").exists() or not any(
        (tmp_path / "fresh").rglob("*.json")
    )


def test_a_status_says_when_it_was_saved_in_words(lasx):
    limits = status(steps.leica_wizard(), "limits")

    assert limits["note"] == "Saved on 1 January 2026 at 00:00 (UTC)."
    assert limits["saved_at"] == "2026-01-01T00:00:00+00:00"
    assert status(steps.leica_wizard(), "origin") == {
        "done": False,
        "note": None,
        "saved_at": None,
    }
