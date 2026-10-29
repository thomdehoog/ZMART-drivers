"""The driver used by itself: connect, move in the sample's frame, change the objective, capture and save.

``connect()`` hands back a ``Microscope`` on the stand-in ZEN, in a settings
folder set up as an operator would set it up.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from mock_zen_api import FakeGRPCError, FakeScope
from setup_fixtures import TRANSLATIONS, connected, provision

import zmart_drivers.zeiss.light_microscope.zen_api_grpc as zen
from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.store import MACHINE

pylibczirw = pytest.importorskip(
    "pylibCZIrw", reason="pylibCZIrw reads the CZI files the stand-in writes"
)


@pytest.fixture
def scope(monkeypatch, tmp_path):
    provision()
    microscope, fake = connected(monkeypatch, tmp_path)
    microscope.set_origin()
    yield microscope
    microscope.close()


def test_the_package_offers_only_connect_and_the_microscope():
    assert sorted(zen.__all__) == ["Microscope", "__version__", "connect"]


def test_connect_loads_the_setup_and_the_experiment(scope):
    handle = scope.handle

    assert handle.experiment["name"] == "ZMART_Snap"
    assert handle.frame["pixel_size_um"] == [0.65, 0.65]
    assert handle.translations[2] == tuple(TRANSLATIONS["2"])
    assert "password" not in handle.connection


def test_the_origin_is_zero_and_moves_read_back_in_the_frame(scope):
    assert [scope.position()[a]["position"] for a in "xyz"] == pytest.approx(
        [0.0, 0.0, 0.0]
    )

    moved = scope.move_to(120.0, -40.0, 3.0)

    assert [moved[a]["position"] for a in "xyz"] == pytest.approx([120.0, -40.0, 3.0])
    assert moved["x"]["actuator_readings"] == {"motoric": pytest.approx(20120.0)}
    assert moved["z"]["used_actuator"] == "focus"


def test_a_move_outside_the_limits_is_refused_and_the_stage_stays(scope):
    with pytest.raises(RuntimeError, match="refused"):
        scope.move_to(-1e6, 0.0, 0.0)

    assert scope.position()["x"]["position"] == pytest.approx(0.0)


def test_a_wrong_number_is_a_wrong_request(scope):
    with pytest.raises(ValueError, match="number"):
        scope.move_to(float("nan"), 0.0, 0.0)


def test_an_objective_change_keeps_the_sample_point_in_view(scope):
    scope.move_to(50.0, 50.0, 0.0)

    result = scope.change_objective(position=3)

    assert result["correction_um"] == pytest.approx(TRANSLATIONS["3"])
    assert [scope.position()[a]["position"] for a in "xyz"] == pytest.approx(
        [50.0, 50.0, 0.0]
    )
    assert scope.position()["x"]["actuator_readings"]["motoric"] == pytest.approx(
        20000 + 50 + 10
    )


def test_an_objective_pair_that_is_not_calibrated_is_refused(monkeypatch, tmp_path):
    provision(calibration=False)
    microscope, fake = connected(monkeypatch, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="refused"):
            microscope.change_objective(position=2)
        assert fake.objective_position == 1
    finally:
        microscope.close()


def test_the_state_names_the_experiment_the_objective_and_the_frame(scope):
    state = scope.state()

    assert state["changeable"] == {"experiment": "ZMART_Snap", "objective": 1}
    assert state["observed"]["image_size_px"] == [128, 96]
    assert state["observed"]["pixel_size_um"] == [0.65, 0.65]
    assert "ZMART_ZStack" in state["observed"]["experiments"]


def test_the_frame_is_scaled_for_another_objective(scope):
    scope.change_objective(position=2)

    assert scope.state()["observed"]["pixel_size_um"] == pytest.approx([0.325, 0.325])


def test_an_experiment_zen_does_not_have_is_a_wrong_request(scope):
    with pytest.raises(ValueError, match="no experiment"):
        scope.load_experiment("Nope")


def test_a_capture_is_saved_as_zens_czi_with_its_record_and_planes(scope, tmp_path):
    scope.load_experiment("ZMART_ZStack")
    scope.move_to(10.0, 20.0, 0.0)

    taken = scope.acquire("A1", acquisition_type="overview")
    (saved,) = scope.save()

    czi = Path(saved["czi"])
    assert taken == {
        "position_label": "A1",
        "acquisition_type": "overview",
        "experiment": "ZMART_ZStack",
    }
    assert czi.name == "overview_A1.czi" and czi.is_file()
    assert czi.parent == Path(tmp_path / "runs").resolve() / scope.handle.hash6
    assert not (Path(scope.handle.image_output_path) / "overview_A1.czi").exists()
    assert [p["z"] for p in saved["planes"]] == [0, 1, 2, 3, 4]
    assert [p["z_um"] for p in saved["planes"]] == pytest.approx(
        [-10.0, -5.0, 0.0, 5.0, 10.0]
    )
    assert all(
        p["x_um"] == pytest.approx(10.0) and p["y_um"] == pytest.approx(20.0)
        for p in saved["planes"]
    )
    record = json.loads(Path(saved["record"]).read_text(encoding="utf-8"))
    assert record["capture_record"]["position_label"] == "A1"
    assert record["controller_state"]["changeable"]["experiment"] == "ZMART_ZStack"
    assert record["vendor"] == "zeiss-zen"


def test_a_snap_takes_one_plane_per_channel(scope):
    scope.acquire("B2", acquisition_settings={"trigger": "snap"})
    (saved,) = scope.save()

    assert [(p["c"], p["channel"]) for p in saved["planes"]] == [
        (0, "DAPI"),
        (1, "GFP"),
    ]


def test_a_time_series_has_one_plane_per_time_point(scope):
    scope.load_experiment("ZMART_TimeLapse")
    scope.acquire("T1")
    (saved,) = scope.save()

    assert [p["t"] for p in saved["planes"]] == [0, 1, 2]


def test_no_capture_overwrites_another(scope):
    scope.acquire("A1")
    first = scope.save()[0]["czi"]
    scope.acquire("A1")
    second = scope.save()[0]["czi"]

    assert Path(first).name == "acquisition_A1.czi"
    assert Path(second).name == "acquisition_A1_001.czi"


def test_a_capture_zen_refused_raises_and_nothing_waits(scope):
    scope.handle.client.scope.refusals["experiment.run_experiment"] = [
        FakeGRPCError("PERMISSION_DENIED", "no")
    ]

    with pytest.raises(RuntimeError, match="Unsupervised API Mode"):
        scope.acquire("A1")

    assert scope.handle.unsaved == []


def test_a_file_zen_did_not_write_is_a_failed_save_that_says_where_to_look(scope):
    scope.acquire("A1")
    Path(scope.handle.image_output_path, "acquisition_A1.czi").unlink()

    with pytest.raises(RuntimeError, match="zen_images"):
        scope.save()

    assert len(scope.handle.unsaved) == 1


def test_without_an_experiment_a_capture_says_how_to_load_one(monkeypatch, tmp_path):
    provision()
    microscope, _ = connected(monkeypatch, tmp_path, experiment=None)
    try:
        with pytest.raises(RuntimeError, match="no experiment is loaded"):
            microscope.acquire("A1")
    finally:
        microscope.close()


def test_the_z_stack_is_read_and_changed_from_the_origins_focus(scope):
    scope.load_experiment("ZMART_ZStack")

    scope.set_z_stack(first_um=-20.0, interval_um=2.0)

    assert scope.z_stack()["first_um"] == pytest.approx(-20.0)
    assert scope.z_stack()["interval_um"] == pytest.approx(2.0)


def test_the_autofocus_answers_the_focus_from_the_origin(scope):
    result = scope.autofocus()

    assert result["z_um"] == pytest.approx(5.0)


def test_a_closed_microscope_refuses_every_call(scope):
    scope.close()

    with pytest.raises(RuntimeError, match="closed"):
        scope.position()


def test_an_unknown_connection_key_is_refused_before_zen_is_touched():
    with pytest.raises(ValueError, match="unknown connection keys"):
        methods.connect(prot=5002)


def test_a_damaged_origin_stops_the_connection(monkeypatch, tmp_path):
    provision()
    MACHINE.write_origin({"origin": {"x_um": "zero"}})

    with pytest.raises(RuntimeError, match="origin"):
        connected(monkeypatch, tmp_path)


def test_a_zeiss_config_ini_fills_in_the_connection(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[api]\nhost = 10.1.2.3\nport = 5100\ncert_file = c.pem\ncontrol-token = tok\n",
        encoding="utf-8",
    )

    connection = methods.checked_connection({"zen_config_ini": str(ini)})

    assert (connection["host"], connection["port"], connection["password"]) == (
        "10.1.2.3",
        5100,
        "tok",
    )


def test_the_running_log_gets_the_messages_of_every_part(scope):
    driver = logging.getLogger(methods.DRIVER_PACKAGE)

    assert any(
        getattr(h, "baseFilename", "").endswith("running_log.txt")
        for h in driver.handlers
    )


def test_the_stage_moved_in_zen_is_noted_at_the_next_capture(scope):
    scope.position()
    scope.handle.client.scope.x_um += 30.0

    scope.acquire("A1")

    assert any("changed" in note["what"] for note in scope.handle.unconfirmed)
    assert scope.save()[0]["planes"][0]["x_um"] == pytest.approx(30.0)


def test_a_slow_stage_is_still_read_back(monkeypatch, tmp_path):
    provision()
    microscope, fake = connected(monkeypatch, tmp_path, scope=FakeScope(travel_s=0.05))
    try:
        # No origin is saved here, so the frame is ZEN's own coordinates.
        assert microscope.move_to(25000.0, 15000.0, 900.0)["x"][
            "position"
        ] == pytest.approx(25000.0)
        assert microscope.handle.unconfirmed == []
    finally:
        microscope.close()
