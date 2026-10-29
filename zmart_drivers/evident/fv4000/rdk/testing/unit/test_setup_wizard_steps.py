"""The setup wizard's FV4000 steps, run through the wizard on the pretend FLUOVIEW.

The wizard follows the ZMART driver configuration workflow: Connect, Define
limits, Define coordinate system origin, Image-to-stage calibration and
Objective calibration. Each action is called exactly as the page calls it,
``wizard.call(step, action, values)``, and runs the driver's real setup code
against the pretend FLUOVIEW (``helpers/wizard_standins.py``), whose stage moves
the field of view and whose objectives look and focus at offsets the test sets.
The wizard has to recover those offsets, as the notebook does.

The checks are what an operator would rely on: the right files are saved
where the driver loads them, a step that is done shows as done, and a mistake
is answered with a sentence instead of a crash. They follow the Leica driver's
wizard tests, case for case.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("scipy")
pytest.importorskip("matplotlib")

from wizard_standins import (  # noqa: E402
    TRUE_OFFSETS,
    drive_stage_to,
    give_objectives_offsets,
    install_fluoview,
    set_up_z_stack,
    switch_z_stack_off,
    turn_nosepiece,
)

from zmart_drivers.evident.fv4000.rdk import methods  # noqa: E402
from zmart_drivers.evident.fv4000.rdk.driver_setup import store  # noqa: E402
from zmart_drivers.evident.fv4000.rdk.driver_setup.wizard import steps  # noqa: E402
from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet_figures(monkeypatch):
    import matplotlib

    matplotlib.use("Agg")


@pytest.fixture
def fluoview(monkeypatch, published_limits):
    """The pretend FLUOVIEW, on a microscope whose limits were published on 1 January 2026."""
    return install_fluoview(monkeypatch)


@pytest.fixture
def data_folder(tmp_path):
    folder = tmp_path / "ZMART-data"
    folder.mkdir()
    return folder


@pytest.fixture
def connected(fluoview, data_folder):
    """A wizard that has been through Connect."""
    wizard = steps.evident_wizard()
    answer = wizard.call("connect", "connect", {"output_root": str(data_folder)})
    assert answer["ok"], answer
    yield wizard
    wizard.call("connect", "disconnect")


def status(wizard, step_id):
    return next(s for s in wizard.outline()["steps"] if s["id"] == step_id)["status"]


def saved_at(snapshot: Path) -> str:
    moment = datetime.strptime(snapshot.name[:26], "%Y-%m-%dT%H-%M-%S-%f")  # noqa: DTZ007 -- the folder name is UTC; set below
    return moment.replace(tzinfo=UTC).isoformat()


def test_the_steps_are_the_driver_configuration_workflows_in_its_order(fluoview):
    outline = steps.evident_wizard().outline()

    assert outline["title"] == "Evident FLUOVIEW FV4000 setup"
    assert [(s["id"], s["title"]) for s in outline["steps"]] == [
        ("connect", "Connect"),
        ("limits", "Define limits"),
        ("origin", "Define coordinate system origin"),
        ("orientation", "Image-to-stage calibration"),
        ("calibration", "Objective calibration"),
    ]
    calibration = next(s for s in outline["steps"] if s["id"] == "calibration")
    assert calibration["needs"] == ["orientation"]


def test_no_step_reaches_the_microscope_before_connect(fluoview):
    wizard = steps.evident_wizard()

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


def test_the_card_suggests_a_data_folder_until_one_is_saved(fluoview, data_folder):
    wizard = steps.evident_wizard()

    card = wizard.call("connect", "card")["result"]
    assert card["output_root"] in (r"D:\ZMART-data", r"C:\ZMART-data")
    assert card["output_root_saved"] is False and card["connected"] is False
    wizard.call("connect", "connect", {"output_root": str(data_folder)})
    card = wizard.call("connect", "card")["result"]
    assert card["output_root"] == str(data_folder.resolve())
    assert card["output_root_saved"] is True and card["connected"] is True
    wizard.call("connect", "disconnect")


def test_connect_saves_the_data_folder_and_lists_what_it_found(fluoview, data_folder):
    wizard = steps.evident_wizard()

    answer = wizard.call("connect", "connect", {"output_root": str(data_folder)})

    assert answer["ok"], answer
    checks = dict(answer["result"]["checks"])
    assert checks["driver"].startswith("fv4000 rdk ")
    assert checks["FLUOVIEW"] == fluoview.VERSION
    assert checks["stage"] == "x 10000 · y 8000 · z 2500.0 µm"
    assert checks["objective"] == "slot 0 · UPLXAPO10X"
    assert checks["settings folder"] == str(store.MACHINE.snapshot_root())
    assert checks["output folder"] == str(data_folder.resolve())
    assert status(wizard, "connect")["done"]
    assert fluoview.remote_mode == "REMOTE"
    wizard.call("connect", "disconnect")
    assert fluoview.remote_mode == "LOCAL", "Disconnect hands FLUOVIEW back"


@pytest.mark.parametrize(
    "folder, reason",
    [("relative/folder", "full path"), ("{tmp}/no/such/folder", "does not exist")],
)
def test_a_data_folder_that_cannot_hold_pictures_stops_connect_with_the_reason(
    fluoview, tmp_path, folder, reason
):
    folder = folder.replace("{tmp}", str(tmp_path))
    wizard = steps.evident_wizard()

    answer = wizard.call("connect", "connect", {"output_root": folder})

    assert answer["ok"] is False
    assert answer["error"].startswith("The output folder cannot be used:")
    assert reason in answer["error"]
    assert not status(wizard, "connect")["done"]


def test_fluoview_that_does_not_answer_is_reported_as_such(fluoview, data_folder):
    fluoview.connected = False

    answer = steps.evident_wizard().call(
        "connect", "connect", {"output_root": str(data_folder)}
    )

    assert answer["ok"] is False
    assert answer["error"].startswith("Could not reach FLUOVIEW.")


def test_the_card_shows_the_connection_words_as_this_driver_connects(fluoview):
    card = steps.evident_wizard().call("connect", "card")["result"]

    assert {
        key: card[key] for key in ("microscope", "api_type", "host", "port", "config")
    } == {
        "microscope": "fv4000",
        "api_type": "FLUOVIEW RDK (XML-RPC)",
        "host": "127.0.0.1",
        "port": "8080",
        "config": "",
    }
    assert "password" not in card and "output_root" in card


def test_connect_reaches_fluoview_at_the_host_and_port_typed(
    monkeypatch, fluoview, data_folder
):
    stand_in, asked = rdk_client.connect_client, []

    def recorded(host="127.0.0.1", port=8080, **kw):
        asked.append((host, port))
        return stand_in(host, port, **kw)

    monkeypatch.setattr(rdk_client, "connect_client", recorded)
    wizard = steps.evident_wizard()

    answer = wizard.call(
        "connect",
        "connect",
        {"output_root": str(data_folder), "host": "10.100.100.100", "port": "8081"},
    )

    assert answer["ok"], answer
    assert asked == [("10.100.100.100", 8081)]
    wizard.call("connect", "disconnect")


def test_a_connection_the_driver_cannot_honour_is_refused_before_anything_is_saved(
    fluoview, data_folder
):
    wizard = steps.evident_wizard()
    wizard.call("connect", "choose", {"config": ""})

    answer = wizard.call(
        "connect", "connect", {"output_root": str(data_folder), "port": "eighty"}
    )

    assert answer["ok"] is False and "whole number" in answer["error"]
    assert store.MACHINE.latest_snapshot("data_location") is None
    assert store.MACHINE.configurations() == []  # the new config was not made either
    assert not status(wizard, "connect")["done"]


# --- configs ---------------------------------------------------------------------------------


def make_config(name: str, output_root: Path) -> None:
    """A config kept on this computer, with its output folder saved in it."""
    store.use_configuration(name)
    store.MACHINE.write_data_location({"folder": str(output_root)})
    store.use_configuration(None)


def test_the_card_lists_the_configs_kept_here_most_recent_first_and_opens_on_the_newest(
    data_folder,
):
    make_config("config_20260907_0714", data_folder)
    make_config("config_20261026_1915", data_folder)
    (store.MACHINE.api_root() / "limits").mkdir(
        parents=True, exist_ok=True
    )  # not a config

    card = steps.evident_wizard().call("connect", "card")["result"]

    assert card["configs"] == ["config_20261026_1915", "config_20260907_0714"]
    assert card["config"] == "config_20261026_1915"
    assert card["output_root"] == str(data_folder)
    store.use_configuration(None)


def test_a_new_config_has_nothing_done_and_is_made_on_connect(
    fluoview, data_folder, tmp_path
):
    make_config("config_20260907_0714", tmp_path)
    wizard = steps.evident_wizard()

    chosen = wizard.call("connect", "choose", {"config": ""})
    name = chosen["result"]["config"]

    assert chosen["ok"] and store.is_configuration_name(name)
    assert not (store.MACHINE.api_root() / name).exists()  # nothing made before Connect
    assert not status(wizard, "limits")["done"]
    assert wizard.call("connect", "card")["result"]["output_root"] == str(tmp_path)

    assert wizard.call("connect", "connect", {"output_root": str(data_folder)})["ok"]
    assert store.MACHINE.configurations() == [name, "config_20260907_0714"]
    assert store.MACHINE.snapshot_root() == store.MACHINE.api_root() / name
    assert wizard.call("connect", "card")["result"]["output_root"] == str(data_folder)
    wizard.call("connect", "disconnect")
    store.use_configuration(None)


def test_choosing_a_kept_config_shows_what_is_done_in_it(connected, data_folder):
    connected.call("connect", "disconnect")
    made = connected.call("connect", "choose", {"config": ""})["result"]["config"]
    assert connected.call("connect", "connect", {"output_root": str(data_folder)})["ok"]
    doc = connected.call("limits", "document")["result"]["document"]
    assert connected.call("limits", "publish", {"document": doc})["ok"]
    connected.call("connect", "disconnect")

    assert (
        connected.call("connect", "choose", {"config": ""})["result"]["config"] != made
    )
    assert not status(connected, "limits")["done"]
    assert (
        connected.call("connect", "choose", {"config": made})["result"]["config"]
        == made
    )
    assert status(connected, "limits")["done"]
    store.use_configuration(None)


@pytest.mark.parametrize("name", ["Sep 7", "config_20990101_0000", "../limits"])
def test_a_config_not_kept_here_is_refused(name):
    answer = steps.evident_wizard().call("connect", "choose", {"config": name})

    assert answer["ok"] is False and "no config" in answer["error"]
    store.use_configuration(None)


def test_another_config_is_chosen_only_when_disconnected(connected):
    answer = connected.call("connect", "choose", {"config": ""})

    assert answer["ok"] is False and "Disconnect first" in answer["error"]


def test_disconnect_closes_the_connection_and_connect_is_to_do_again(connected):
    assert connected.call("connect", "disconnect")["ok"]

    assert not status(connected, "connect")["done"]
    assert (
        connected.call("origin", "read")["error"] == "Connect to the microscope first."
    )


# --- 2. Define limits ---------------------------------------------------------------


def test_the_limits_document_is_every_entry_of_the_file(connected):
    from zmart_drivers.evident.fv4000.rdk.driver_setup.limits.limit_rules import (
        SETTER_LIMIT_KEYS,
    )

    doc = connected.call("limits", "document")["result"]

    assert doc["source"] == "published"
    assert set(doc["document"]) == {
        "x_um",
        "y_um",
        "z_um",
        "z_piezo_um",
        "objective_slot",
        *SETTER_LIMIT_KEYS,
    }
    assert doc["required"] == ["x_um", "y_um", "z_um", "z_piezo_um"]
    assert doc["measured"] == ["x_um", "y_um"]
    assert doc["actuators"] == {"x": ["stage"], "y": ["stage"]}


def test_a_corner_is_the_stage_position_read_from_the_chosen_motors(
    connected, fluoview
):
    drive_stage_to(fluoview, 21000.0, 16000.0)

    answer = connected.call(
        "limits", "corner", {"x_motor": "stage", "y_motor": "stage"}
    )

    assert answer["result"] == pytest.approx({"x": 21000.0, "y": 16000.0})


def test_saved_limits_govern_the_next_moves(connected, fluoview):
    doc = connected.call("limits", "document")["result"]["document"]
    doc["x_um"] = {"range": [5000.0, 20000.0]}
    doc["y_um"] = {"range": [4000.0, 15000.0]}
    doc["objective_slot"] = {"allowed": [0, 2]}
    doc["set_laser_intensity"] = {"range": [0.0, 10.0]}
    before = store.MACHINE.latest_snapshot("limits")
    corners = {"top_left": {"x": 5000.0, "y": 4000.0}}

    answer = connected.call("limits", "publish", {"document": doc, "corners": corners})

    assert answer["ok"], answer
    latest = store.MACHINE.latest_snapshot("limits")
    assert latest != before
    written = json.loads((latest / "limits.json").read_text(encoding="utf-8"))
    assert written["x_um"]["range"] == [5000.0, 20000.0]
    assert written["objective_slot"] == {"allowed": [0, 2]}
    assert written["set_laser_intensity"] == {"range": [0.0, 10.0]}
    assert list(latest.rglob("set_limits_wizard_record*.json"))
    assert status(connected, "limits")["saved_at"] == saved_at(latest)
    scope = methods.connect()
    try:
        # 25 mm was allowed by the old limits; the new ones end at 20 mm.
        with pytest.raises(RuntimeError, match="refused"):
            scope.move_to(25000.0 - scope.handle.origin["x_um"], 8000.0, 2500.0)
    finally:
        scope.close()


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"x_um": {"range": [90000.0, 20000.0]}}, "x_um"),
        ({"objective_slot": {"range": [0, 2]}}, "objective_slot"),
        ({"set_scanner_type": {"allowed": []}}, "must not be empty"),
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


def test_read_shows_every_drive_and_the_objective(connected, fluoview):
    drive_stage_to(fluoview, 12000.0, 9000.0)

    answer = connected.call("origin", "read")["result"]

    assert dict(answer["actuators"]) == {
        "x stage": "12000.00 µm",
        "y stage": "9000.00 µm",
        "z focus motor": "2500.00 µm",
        "z piezo": "0.00 µm",
        "objective": "slot 0 · UPLXAPO10X",
    }
    assert store.MACHINE.read_origin() is None  # Read writes nothing


def test_the_origin_is_saved_where_the_next_connection_loads_it(connected, fluoview):
    drive_stage_to(fluoview, 12000.0, 9000.0)
    connected.call("origin", "read")

    answer = connected.call("origin", "publish")

    assert answer["ok"], answer
    assert Path(answer["result"]["saved_to"]).is_file()
    standing = connected.call("origin", "standing")["result"]
    assert standing["origin"]["x_um"] == pytest.approx(12000.0)
    assert status(connected, "origin")["done"]
    scope = methods.connect()
    try:
        assert scope.position()["x"]["position"] == pytest.approx(0.0, abs=1.0)
    finally:
        scope.close()


def test_the_origin_is_not_saved_when_the_stage_moved_after_read(connected, fluoview):
    assert connected.call("origin", "publish")["error"] == "Read first."
    drive_stage_to(fluoview, 12000.0, 9000.0)
    connected.call("origin", "read")
    drive_stage_to(fluoview, 13000.0, 9000.0)

    answer = connected.call("origin", "publish")

    assert answer["ok"] is False and "changed since Read" in answer["error"]
    assert store.MACHINE.read_origin() is None


# --- 4. Image-to-stage calibration ---------------------------------------------------


def test_orientation_is_measured_checked_and_saved(connected, fluoview):
    fluoview.orientation = (90, False)

    measured = connected.call("orientation", "measure", {"stage_move_um": 40})
    assert measured["ok"], measured
    assert measured["result"]["accepted"] is True
    # The driver's own sheet: the detected correction above the eight candidates.
    assert Path(measured["result"]["image"]).is_file()
    assert store.MACHINE.latest_snapshot("orientation").name.startswith("1970"), (
        "not active before saving"
    )

    checked = connected.call("orientation", "check")
    assert checked["ok"], checked
    preview = Path(checked["result"]["image"])
    assert preview.is_file() and "reports" in preview.parts

    assert connected.call("orientation", "publish")["ok"]
    active = store.MACHINE.latest_snapshot("orientation")
    assert (active / "orientation.json").is_file()
    standing = connected.call("orientation", "standing")["result"]
    assert standing["measured"] is True and standing["rotation_deg"] == 90
    assert standing["image"] and Path(standing["image"]).is_file()
    assert status(connected, "orientation")["done"]


def test_orientation_cannot_be_saved_before_the_check_picture(connected):
    connected.call("orientation", "measure", {"stage_move_um": 40})

    answer = connected.call("orientation", "publish")

    assert answer["ok"] is False and "validation" in answer["error"]
    assert not status(connected, "orientation")["done"]


def test_orientation_steps_in_the_wrong_order_are_refused_in_words(connected):
    assert (
        connected.call("orientation", "check")["error"]
        == "Measure the orientation first."
    )
    assert connected.call("orientation", "publish")["error"] == "Measure first."


def test_a_field_without_structure_is_explained(connected, fluoview):
    fluoview.field = np.full_like(fluoview.field, 70)

    answer = connected.call("orientation", "measure", {"stage_move_um": 40})

    assert answer["ok"] is True and answer["result"]["accepted"] is False
    assert "recognisable structure" in answer["result"]["why"]


# --- 5. Objective calibration --------------------------------------------------------


@pytest.fixture
def optics(connected, fluoview):
    """Objectives with known offsets, and the zoom matched so they give the same pixel size."""
    give_objectives_offsets(fluoview)
    return connected, fluoview


PAIR = {"reference": 0, "target": 2}


def measure_the_pair(wizard, mock):
    """What the operator does, in order: the four measurements, turning the nosepiece between them."""
    set_up_z_stack(mock)
    assert wizard.call("calibration", "focus", {**PAIR, "side": "reference"})["ok"]
    turn_nosepiece(mock, 2)
    target = wizard.call("calibration", "focus", {**PAIR, "side": "target"})
    assert target["ok"], target
    switch_z_stack_off(mock)
    turn_nosepiece(mock, 0)
    assert wizard.call("calibration", "xy", {**PAIR, "side": "reference"})["ok"]
    turn_nosepiece(mock, 2)
    xy = wizard.call("calibration", "xy", {**PAIR, "side": "target"})
    assert xy["ok"], xy
    return target["result"], xy["result"]


def test_the_lenses_are_the_objectives_on_the_nosepiece(optics):
    wizard, _mock = optics

    lenses = wizard.call("calibration", "lenses")["result"]

    assert lenses["lenses"] == [
        {"slot": 0, "name": "UPLXAPO10X"},
        {"slot": 1, "name": "UPLXAPO20X"},
        {"slot": 2, "name": "UPLXAPO60XO"},
    ]
    assert lenses["reference"] is None and lenses["held"] == {}


def test_the_calibration_recovers_the_true_objective_offsets(optics):
    wizard, mock = optics
    true = TRUE_OFFSETS[2]

    focus, xy = measure_the_pair(wizard, mock)

    assert focus["translation_z_um"] == pytest.approx(true[2], abs=0.2)
    assert Path(focus["image"]).is_file()  # the focus curve beside the sharpest slice
    assert xy["trusted"] is True
    assert xy["translation_um"]["x"] == pytest.approx(true[0], abs=0.2)
    assert xy["translation_um"]["y"] == pytest.approx(true[1], abs=0.2)
    assert Path(xy["image"]).is_file()  # the two objectives overlaid

    assert wizard.call("calibration", "publish")["ok"]
    published = json.loads(store.MACHINE.calibration_path().read_text(encoding="utf-8"))
    offsets = published["objectives"]["2"]["translation_um"]
    for measured, expected in zip(offsets, true, strict=True):
        assert measured == pytest.approx(expected, abs=0.2)
    lenses = wizard.call("calibration", "lenses")["result"]
    assert lenses["reference"] == 0 and set(lenses["held"]) == {"0", "2"}
    assert status(wizard, "calibration")["done"]


def test_a_measurement_with_the_wrong_objective_is_refused_and_can_be_repeated(optics):
    wizard, mock = optics
    set_up_z_stack(mock)
    wizard.call("calibration", "focus", {**PAIR, "side": "reference"})

    wrong = wizard.call(
        "calibration", "focus", {**PAIR, "side": "target"}
    )  # reference still in

    assert wrong["ok"] is False and "reference objective" in wrong["error"]
    turn_nosepiece(mock, 2)
    again = wizard.call("calibration", "focus", {**PAIR, "side": "target"})
    assert again["ok"], again
    assert again["result"]["translation_z_um"] == pytest.approx(
        TRUE_OFFSETS[2][2], abs=0.2
    )


def test_calibration_steps_out_of_order_are_refused_in_words(optics):
    wizard, _mock = optics

    assert wizard.call("calibration", "publish")["error"] == "Measure a preset first."
    unknown = wizard.call("calibration", "focus", {**PAIR, "side": "sideways"})
    assert unknown["error"] == "Unknown measurement."
    assert (
        "number" in wizard.call("calibration", "focus", {"side": "reference"})["error"]
    )


# --- what counts as done ---------------------------------------------------------------


def test_defaults_the_driver_fills_in_on_its_first_connect_do_not_count_as_done(
    fluoview, data_folder
):
    """The driver writes an unmeasured orientation and an empty calibration when it
    first connects; on a new microscope those steps must still show as to do."""
    wizard = steps.evident_wizard()
    assert wizard.call("connect", "connect", {"output_root": str(data_folder)})["ok"]
    assert (
        store.MACHINE.latest_snapshot("orientation") is not None
    )  # the defaults were filled in
    assert store.MACHINE.latest_snapshot("calibration") is not None

    done = {s["id"]: s["status"]["done"] for s in wizard.outline()["steps"]}

    assert done == {
        "connect": True,
        "limits": True,
        "origin": False,
        "orientation": False,
        "calibration": False,
    }
    wizard.call("connect", "disconnect")


def test_looking_at_the_steps_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "fresh"))

    steps.evident_wizard().outline()

    assert not (tmp_path / "fresh").exists() or not any(
        (tmp_path / "fresh").rglob("*.json")
    )


def test_a_status_says_when_it_was_saved_in_words(fluoview):
    limits = status(steps.evident_wizard(), "limits")

    assert limits["note"] == "Saved on 1 January 2026 at 00:00 (UTC)."
    assert limits["saved_at"] == "2026-01-01T00:00:00+00:00"
    assert status(steps.evident_wizard(), "origin") == {
        "done": False,
        "note": None,
        "saved_at": None,
    }
