"""The setup wizard's ZEISS steps, run through the wizard on the stand-in ZEN.

The wizard has five steps: Connect, Define limits, Define coordinate system
origin, Image-to-stage calibration and Objective calibration. Each action is
called exactly as the page calls it, ``wizard.call(step, action, values)``,
and runs the driver's real setup code through the driver's own
``connect()``. Only ZEN is replaced, by the pretend ZEN of
``helpers/mock_zen_api.py``: its stage, focus and objective changer, and its
snaps, which are real CZI files of a sample that shifts as the stage moves.

The checks are what an operator would rely on: the right files are saved
where the driver loads them, a step that is done shows as done, the next
connection uses what was saved, and a mistake is answered with a sentence
instead of a crash.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest
from mock_zen_api import FakeScope, build_fake_client

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup import store
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard import (
    picture,
    steps,
)


@pytest.fixture
def zen(monkeypatch, tmp_path):
    """The pretend ZEN, reached by every connection the wizard opens; the connections it was asked for are kept."""
    scope = FakeScope(image_output_folder=str(tmp_path / "zen_images"))
    Path(scope.image_output_folder).mkdir()
    scope.asked = []

    def stand_in(connection):
        scope.asked.append(dict(connection))
        client, _ = build_fake_client(scope)
        return client

    monkeypatch.setattr(methods, "open_connection", stand_in)
    return scope


@pytest.fixture
def data_folder(tmp_path):
    folder = tmp_path / "ZMART-data"
    folder.mkdir()
    return folder


@pytest.fixture
def connected(zen, data_folder):
    """A wizard that has been through Connect."""
    wizard = steps.zeiss_wizard()
    answer = wizard.call("connect", "connect", {"output_root": str(data_folder)})
    assert answer["ok"], answer
    return wizard


def status(wizard, step_id):
    return next(s for s in wizard.outline()["steps"] if s["id"] == step_id)["status"]


def ok(answer):
    assert answer["ok"], answer
    return answer["result"]


def test_the_steps_are_the_five_setup_steps_in_their_order(zen):
    outline = steps.zeiss_wizard().outline()

    assert outline["title"] == "ZEISS ZEN setup"
    assert [(s["id"], s["title"]) for s in outline["steps"]] == [
        ("connect", "Connect"),
        ("limits", "Define limits"),
        ("origin", "Define coordinate system origin"),
        ("orientation", "Image-to-stage calibration"),
        ("calibration", "Objective calibration"),
    ]
    # The offsets are read by eye in ZEN's numbers, so they hang on no other step.
    assert all(s["needs"] == [] for s in outline["steps"])


def test_no_step_reaches_the_microscope_before_connect(zen):
    wizard = steps.zeiss_wizard()

    for step, action in (
        ("limits", "document"),
        ("limits", "corner"),
        ("origin", "read"),
        ("orientation", "experiments"),
        ("orientation", "measure"),
        ("calibration", "lenses"),
    ):
        assert wizard.call(step, action, {"experiment": "ZMART_Snap"}) == {
            "ok": False,
            "error": "Connect to the microscope first.",
        }
    assert zen.asked == []


# --- 1. Connect ---------------------------------------------------------------------


def test_the_card_shows_the_gateway_words_and_suggests_a_data_folder(zen, data_folder):
    wizard = steps.zeiss_wizard()

    card = ok(wizard.call("connect", "card"))
    assert card["microscope"] == "zeiss-zen"
    assert "ZEN API" in card["api_type"]
    assert (card["host"], card["port"]) == ("127.0.0.1", "5002")
    assert card["output_root"] in (r"D:\ZMART-data", r"C:\ZMART-data")
    assert card["output_root_saved"] is False and card["connected"] is False
    ok(wizard.call("connect", "connect", {"output_root": str(data_folder)}))
    card = ok(wizard.call("connect", "card"))
    assert card["output_root"] == str(data_folder.resolve())
    assert card["output_root_saved"] is True and card["connected"] is True


def test_connect_saves_the_data_folder_and_lists_what_it_found(zen, data_folder):
    wizard = steps.zeiss_wizard()

    checks = dict(
        ok(wizard.call("connect", "connect", {"output_root": str(data_folder)}))[
            "checks"
        ]
    )

    assert checks["driver"].startswith("zen_api_grpc ")
    assert checks["ZEN API"] == "zen_api pretend"
    assert checks["stage"] == "x 20000 · y 15000 µm · focus 1000.0 µm"
    assert checks["objective"] == "position 1 · Plan-Apochromat 10x/0.45"
    assert checks["experiment"] == "none loaded"
    assert checks["ZEN image folder"] == zen.image_output_folder
    assert checks["settings folder"] == str(store.MACHINE.snapshot_root())
    assert checks["output folder"] == str(data_folder.resolve())
    assert status(wizard, "connect")["done"]


def test_connect_reaches_the_gateway_as_typed_and_keeps_the_token_out_of_the_card(
    zen, data_folder
):
    wizard = steps.zeiss_wizard()

    ok(
        wizard.call(
            "connect",
            "connect",
            {
                "host": "10.0.0.7",
                "port": "5003",
                "password": "the-token",
                "client": "lab-pc",
                "output_root": str(data_folder),
            },
        )
    )

    asked = zen.asked[-1]
    assert (asked["host"], asked["port"], asked["password"], asked["client"]) == (
        "10.0.0.7",
        5003,
        "the-token",
        "lab-pc",
    )
    card = ok(wizard.call("connect", "card"))
    assert (card["host"], card["port"]) == ("10.0.0.7", "5003")
    assert "the-token" not in json.dumps(card)


def test_a_port_that_is_not_a_number_is_refused_before_anything_is_saved(
    zen, data_folder
):
    wizard = steps.zeiss_wizard()

    answer = wizard.call(
        "connect", "connect", {"port": "fifty", "output_root": str(data_folder)}
    )

    assert answer["ok"] is False
    assert "port must be a whole number" in answer["error"]
    assert zen.asked == []
    assert store.MACHINE.read_data_location() is None


def test_a_data_folder_that_cannot_hold_pictures_stops_connect_with_the_reason(
    zen, tmp_path
):
    wizard = steps.zeiss_wizard()

    answer = wizard.call(
        "connect", "connect", {"output_root": str(tmp_path / "missing")}
    )

    assert answer["ok"] is False
    assert answer["error"].startswith("The output folder cannot be used:")
    assert zen.asked == []


def test_a_gateway_that_does_not_answer_is_reported_in_words(
    monkeypatch, zen, data_folder
):
    def unreachable(_connection):
        raise OSError("connection refused")

    monkeypatch.setattr(methods, "open_connection", unreachable)
    wizard = steps.zeiss_wizard()

    answer = wizard.call("connect", "connect", {"output_root": str(data_folder)})

    assert answer["ok"] is False
    assert (
        "ZEN API Gateway" in answer["error"] and "connection refused" in answer["error"]
    )
    assert not status(wizard, "connect")["done"]


def test_a_new_config_is_made_on_connect_and_another_is_chosen_only_when_disconnected(
    zen, data_folder
):
    wizard = steps.zeiss_wizard()
    name = ok(wizard.call("connect", "choose", {"config": ""}))["config"]
    assert store.MACHINE.configurations() == []

    ok(wizard.call("connect", "connect", {"output_root": str(data_folder)}))

    assert store.MACHINE.configurations() == [name]
    assert zen.asked[-1]["config"] == name
    assert wizard.call("connect", "choose", {"config": name}) == {
        "ok": False,
        "error": "Disconnect first to choose another config.",
    }
    ok(wizard.call("connect", "disconnect"))
    assert not status(wizard, "connect")["done"]
    assert wizard.call("connect", "choose", {"config": "config_19990101_0000"})[
        "error"
    ] == ("There is no config 'config_19990101_0000' on this computer.")


# --- 2. Define limits ---------------------------------------------------------------


def test_the_limits_document_is_every_entry_of_the_file_with_its_words(connected):
    doc = ok(connected.call("limits", "document"))

    assert doc["source"] == "defaults"
    assert set(doc["document"]) == {
        "x_um",
        "y_um",
        "z_um",
        "objective_position",
        "experiment",
        "set_z_stack",
    }
    assert (
        [a["key"] for a in doc["axes"]] == doc["required"] == ["x_um", "y_um", "z_um"]
    )
    assert set(doc["settings"]) == {"objective_position", "experiment", "set_z_stack"}
    assert all(s["label"] and s["note"] for s in doc["settings"].values())
    assert doc["words"] == ["experiment"]


def test_a_corner_and_a_focus_end_are_read_in_zens_own_numbers(connected, zen):
    zen.x_um, zen.y_um, zen.z_um = 12345.5, 6789.25, 432.0

    assert ok(connected.call("limits", "corner")) == {"x": 12345.5, "y": 6789.25}
    assert ok(connected.call("limits", "focus_end", {"end": "high"})) == {
        "end": "high",
        "z": 432.0,
    }
    assert connected.call("limits", "focus_end", {"end": "middle"})["ok"] is False


def test_saved_limits_govern_the_next_moves(connected, zen):
    doc = ok(connected.call("limits", "document"))["document"]
    doc["x_um"] = {"range": [10000.0, 30000.0]}
    doc["y_um"] = {"range": [10000.0, 20000.0]}
    doc["z_um"] = {"range": [500.0, 1500.0]}
    doc["objective_position"] = {"allowed": [1, 2]}

    saved = ok(connected.call("limits", "publish", {"document": doc}))

    snapshot = Path(saved["saved_to"])
    assert json.loads((snapshot / "limits.json").read_text())["x_um"] == {
        "range": [10000.0, 30000.0]
    }
    assert list((snapshot / "notebook").glob("set_limits_wizard_record*.json"))
    assert status(connected, "limits")["done"]
    assert ok(connected.call("limits", "document"))["source"] == "published"
    # The wizard connected again, so the new limits fence the next change.
    refused = connected.call("calibration", "turn", {"position": 3})
    assert refused["ok"] is False and "not allowed" in refused["error"]
    assert zen.objective_position == 1


def test_limits_the_driver_cannot_take_are_refused_and_nothing_is_saved(connected):
    doc = ok(connected.call("limits", "document"))["document"]
    doc["x_um"] = {"range": [-1e9, 1e9]}

    answer = connected.call("limits", "publish", {"document": doc})

    assert answer["ok"] is False
    assert store.MACHINE.latest_snapshot("limits") is None


# --- 3. Define coordinate system origin ---------------------------------------------


def test_read_shows_every_drive_and_the_objective(connected):
    held = ok(connected.call("origin", "read"))

    assert held["actuators"] == [
        ["x (stage)", "20000.00 µm"],
        ["y (stage)", "15000.00 µm"],
        ["z (focus)", "1000.00 µm"],
        ["objective", "position 1 · Plan-Apochromat 10x/0.45"],
    ]
    assert ok(connected.call("origin", "standing")) == {"origin": None}


def test_the_origin_is_saved_where_the_next_connection_loads_it(connected, zen):
    ok(connected.call("origin", "read"))
    saved = ok(connected.call("origin", "publish"))

    assert Path(saved["saved_to"]).is_file()
    assert status(connected, "origin")["done"]
    standing = ok(connected.call("origin", "standing"))["origin"]
    assert (standing["x_um"], standing["y_um"], standing["z_um"]) == (
        20000.0,
        15000.0,
        1000.0,
    )
    again = methods.connect(password="pretend")
    try:
        assert again.handle.origin["x_um"] == 20000.0
        assert again.position()["x"]["position"] == pytest.approx(0.0)
    finally:
        again.close()


def test_the_origin_is_not_saved_when_the_stage_moved_after_read(connected, zen):
    assert connected.call("origin", "publish")["error"] == "Read first."
    ok(connected.call("origin", "read"))
    zen.x_um += 50.0

    answer = connected.call("origin", "publish")

    assert answer["ok"] is False and "changed since Read" in answer["error"]
    assert store.MACHINE.read_origin() is None


# --- 4. Image-to-stage calibration ---------------------------------------------------


@pytest.mark.parametrize(
    "camera", [((1, 0), (0, 1)), ((0, -1), (1, 0)), ((-1, 0), (0, 1))]
)
def test_the_orientation_is_measured_shown_and_saved(connected, zen, camera):
    pytest.importorskip("pylibCZIrw")
    zen.camera_image_to_stage = camera
    listed = ok(connected.call("orientation", "experiments"))
    assert "ZMART_Snap" in listed["experiments"] and listed["loaded"] is None
    assert not status(connected, "orientation")["done"]

    measured = ok(
        connected.call("orientation", "measure", {"experiment": "ZMART_Snap"})
    )

    assert measured["accepted"] is True
    assert measured["pixel_size_um"] == pytest.approx([0.65, 0.65], rel=0.02)
    assert measured["image_size_px"] == [128, 96]
    assert "The picture is turned" in measured["description"]
    sheet = Path(measured["image"])
    assert sheet.read_bytes().startswith(b"\x89PNG")
    assert ok(connected.call("orientation", "experiments"))["loaded"] == "ZMART_Snap"

    saved = ok(connected.call("orientation", "publish"))

    snapshot = Path(saved["saved_to"])
    document = json.loads((snapshot / "orientation.json").read_text())
    assert document["measured"] is True
    assert (snapshot / "data" / "notebook" / steps.SHEET_NAME).is_file()
    assert list((snapshot / "data" / "notebook").glob("set_orientation_wizard_record*"))
    assert status(connected, "orientation")["done"]
    standing = ok(connected.call("orientation", "standing"))
    assert standing["measured"] is True
    assert standing["rotation_deg"] == measured["rotation_deg"]
    assert standing["image"] is not None


def test_a_field_without_structure_is_explained_and_nothing_can_be_saved(
    connected, monkeypatch
):
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.orientation import (
        measure_orientation as mo,
    )

    def blank(*_args, **_kwargs):
        raise RuntimeError("the picture did not shift when the stage moved")

    monkeypatch.setattr(mo, "measure_orientation", blank)

    answer = ok(connected.call("orientation", "measure", {"experiment": "ZMART_Snap"}))

    assert answer["accepted"] is False
    assert "did not shift" in answer["why"] and "Rerun" in answer["why"]
    assert connected.call("orientation", "publish")["error"] == "Measure first."


def test_the_orientation_needs_an_experiment_zen_can_load(connected):
    assert connected.call("orientation", "measure", {})["error"] == (
        "Choose the experiment the snaps should use."
    )
    answer = connected.call("orientation", "measure", {"experiment": "Nope"})
    assert answer["ok"] is False
    assert answer["error"].startswith("The experiment could not be loaded:")


# --- 5. Objective calibration --------------------------------------------------------


def test_the_lenses_are_the_objectives_on_the_changer(connected):
    lenses = ok(connected.call("calibration", "lenses"))

    assert [(l["position"], l["magnification"]) for l in lenses["lenses"]] == [
        (1, 10.0),
        (2, 20.0),
        (3, 63.0),
    ]
    assert lenses["in_place"] == 1
    # The driver's first connection filled in an empty calibration.
    assert lenses["reference"] is None and lenses["held"] == {}


def _measure_pair(wizard, zen, target, offset):
    """Record the reference where the stage stands, turn, 'find the feature' and record the target."""
    pair = {"reference": 1, "target": target}
    ok(wizard.call("calibration", "turn", {"position": 1}))
    ok(wizard.call("calibration", "record", {**pair, "side": "reference"}))
    assert (
        ok(wizard.call("calibration", "turn", {"position": target}))["in_place"]
        == target
    )
    zen.x_um += offset[0]
    zen.y_um += offset[1]
    zen.z_um += offset[2]
    got = ok(wizard.call("calibration", "record", {**pair, "side": "target"}))
    zen.x_um -= offset[0]
    zen.y_um -= offset[1]
    zen.z_um -= offset[2]
    return got


def test_the_calibration_saves_every_pair_measured_and_the_next_connection_uses_it(
    connected, zen
):
    first = _measure_pair(connected, zen, 2, (4.0, -2.0, 3.0))
    _measure_pair(connected, zen, 3, (10.0, 6.0, -5.0))
    assert first["translation_um"] == pytest.approx({"x": 4.0, "y": -2.0, "z": 3.0})
    assert not status(connected, "calibration")["done"]

    saved = ok(connected.call("calibration", "publish"))

    assert saved["positions"] == [1, 2, 3] and saved["replaced"] is False
    held = ok(connected.call("calibration", "lenses"))
    assert held["reference"] == 1
    assert held["held"]["2"] == pytest.approx([4.0, -2.0, 3.0])
    assert held["held"]["3"] == pytest.approx([10.0, 6.0, -5.0])
    assert "positions 1, 2, 3" in status(connected, "calibration")["note"]
    again = methods.connect(password="pretend")
    try:
        assert sorted(again.handle.calibration_info["measured_positions"]) == [1, 2, 3]
    finally:
        again.close()


def test_a_new_reference_starts_a_new_calibration(connected, zen):
    _measure_pair(connected, zen, 2, (4.0, -2.0, 3.0))
    ok(connected.call("calibration", "publish"))
    pair = {"reference": 2, "target": 3}
    ok(connected.call("calibration", "turn", {"position": 2}))
    ok(connected.call("calibration", "record", {**pair, "side": "reference"}))
    ok(connected.call("calibration", "turn", {"position": 3}))
    zen.x_um += 1.0
    ok(connected.call("calibration", "record", {**pair, "side": "target"}))

    saved = ok(connected.call("calibration", "publish"))

    assert saved["replaced"] is True and saved["positions"] == [2, 3]
    assert ok(connected.call("calibration", "lenses"))["reference"] == 2


def test_a_record_with_the_wrong_objective_is_refused_and_can_be_repeated(
    connected, zen
):
    pair = {"reference": 1, "target": 2}

    early = connected.call("calibration", "record", {**pair, "side": "target"})
    assert early["ok"] is False
    assert "Turn to the target objective first" in early["error"]
    ok(connected.call("calibration", "record", {**pair, "side": "reference"}))
    ok(connected.call("calibration", "turn", {"position": 3}))
    wrong = connected.call("calibration", "record", {**pair, "side": "target"})
    assert wrong["ok"] is False and "position 3" in wrong["error"]
    ok(connected.call("calibration", "turn", {"position": 2}))
    assert "translation_um" in ok(
        connected.call("calibration", "record", {**pair, "side": "target"})
    )


def test_calibration_steps_out_of_order_are_refused_in_words(connected):
    assert connected.call("calibration", "publish")["error"].startswith(
        "Measure an objective first"
    )
    same = connected.call(
        "calibration", "record", {"reference": 1, "target": 1, "side": "reference"}
    )
    assert same["error"] == "The target must be another objective than the reference."
    assert connected.call(
        "calibration", "record", {"reference": 1, "target": 2, "side": "sideways"}
    )["error"] == ("Unknown measurement.")


# --- the rail ---------------------------------------------------------------------


def test_defaults_the_driver_fills_in_on_its_first_connect_do_not_count_as_done(
    connected,
):
    rail = {s["id"]: s["status"]["done"] for s in connected.outline()["steps"]}

    assert rail == {
        "connect": True,
        "limits": False,
        "origin": False,
        "orientation": False,
        "calibration": False,
    }


def test_a_status_says_when_it_was_saved_in_words(connected):
    doc = ok(connected.call("limits", "document"))["document"]
    ok(connected.call("limits", "publish", {"document": doc}))

    limits = status(connected, "limits")

    assert limits["note"].startswith("Saved on ") and limits["note"].endswith("(UTC).")
    assert limits["saved_at"].endswith("+00:00")


def test_looking_at_the_steps_writes_nothing(zen, tmp_path):
    root = store.MACHINE.root()
    before = sorted(root.rglob("*")) if root.exists() else []

    wizard = steps.zeiss_wizard()
    wizard.outline()
    wizard.call("connect", "card")

    after = sorted(root.rglob("*")) if root.exists() else []
    assert after == before


# --- the orientation sheet ------------------------------------------------------------


def test_the_sheet_is_a_png_any_viewer_can_read(tmp_path):
    np = pytest.importorskip("numpy")
    planes = [np.arange(12 * 10, dtype=float).reshape(10, 12) * k for k in (1, 2, 3)]

    grey = picture.sheet(planes)
    path = picture.write_png(tmp_path / "sheet.png", grey)

    assert grey.shape == (10, 3 * 12 + 2 * picture.GAP_PX)
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )
    assert (width, height) == (grey.shape[1], grey.shape[0])
    start = data.index(b"IDAT") + 4
    length = int.from_bytes(data[start - 8 : start - 4], "big")
    rows = zlib.decompress(data[start : start + length])
    assert rows == b"".join(b"\x00" + bytes(r) for r in grey)


def test_a_wide_sheet_is_drawn_smaller_and_a_flat_plane_is_black(tmp_path):
    np = pytest.importorskip("numpy")

    grey = picture.sheet([np.ones((50, 1000))] * 3, width_px=1200)

    assert grey.shape[1] <= 1200
    assert grey[:, :10].max() == 0
