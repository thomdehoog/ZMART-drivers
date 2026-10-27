"""The Microscope, used by itself against the mock: every call of the README's table.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import zarr

from zmart_drivers import metadata_convention as convention
from zmart_drivers.nikon import nis_elements_ar610 as nis_elements
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.optical_calibration import (
    model as calibration,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.store import MACHINE
from zmart_drivers.nikon.nis_elements_ar610.methods import (
    DEFAULT_CONNECTION,
    checked_connection,
)


def test_connect_loads_the_defaults_and_says_what_is_missing(scope):
    state = scope.state()
    assert state["observed"]["limits"]["is_fallback"] is True
    assert not state["observed"]["setup"]["ready"]
    assert any("orientation" in issue for issue in state["observed"]["setup"]["issues"])
    assert scope.handle.translations == {}
    assert scope.handle.orientation.is_identity


def test_a_connection_to_another_computer_or_an_unknown_config_is_refused():
    with pytest.raises(ValueError, match="this computer only"):
        checked_connection({"host": "microscope-pc"})
    with pytest.raises(ValueError, match="no configs"):
        checked_connection({"config": "config_20260101_0000"})
    assert checked_connection({})["port"] == DEFAULT_CONNECTION["port"]


def test_connect_fails_plainly_when_nothing_answers(tmp_path):
    with pytest.raises(RuntimeError, match="start_bridge.mac"):
        nis_elements.connect(host="127.0.0.1", port=1, output_root=str(tmp_path))


def test_the_position_and_a_move_in_the_frame(scope, mock):
    before = scope.position()
    assert before["x"]["position"] == 1000.0 and before["z"]["actuator_readings"] == {
        "focus-drive": 500.0
    }
    assert before["x"]["canvas"] == [-50000.0, 50000.0]

    after = scope.move_to(1020.0, -500.0, 505.0)
    assert after["x"]["position"] == pytest.approx(1020.0)
    assert after["z"]["position"] == pytest.approx(505.0)
    assert mock.position == {"x": 1020.0, "y": -500.0, "z": 505.0}
    assert scope.motors() == {
        "x": {"options": ["stage"], "default": "stage"},
        "y": {"options": ["stage"], "default": "stage"},
        "z": {"options": ["focus-drive"], "default": "focus-drive"},
    }


def test_a_move_outside_the_limits_raises_and_moves_nothing(scope, mock):
    with pytest.raises(RuntimeError, match="outside the limits"):
        scope.move_to(60000.0, 0.0, 500.0)
    assert mock.calls == []


def test_a_move_that_is_never_seen_to_arrive_is_recorded_and_the_work_goes_on(
    scope, mock
):
    mock.never_arrives = True
    scope.move_to(1100.0, -500.0, 500.0)
    assert scope.state()["observed"]["unconfirmed"][0]["what"].startswith("set_xyz")


def test_channels_exposure_objectives_and_focus(scope, mock):
    assert scope.channels() == {
        "selected": None,
        "channels": ["DAPI", "FITC", "TxRed", "Brightfield"],
    }
    assert scope.select_channel("FITC")["applied"] == {"channel": "FITC"}
    assert scope.channels()["selected"] == "FITC"
    with pytest.raises(ValueError, match="not an optical configuration"):
        scope.select_channel("GFP")

    assert scope.set_exposure(20.4)["applied"] == {"exposure_ms": 20}
    assert scope.exposure() == 20.0

    assert scope.objectives()["current"] == 1
    assert scope.lock_focus(True)["pfs"]["on"] is True
    assert scope.autofocus(30.0, 10)["position"]["z"]["position"] == pytest.approx(
        502.0 + 3.0
    )
    assert "autofocus(30,10)" in mock.calls


def test_an_objective_change_keeps_the_sample_in_view_once_the_pair_is_calibrated(
    scope, mock
):
    with pytest.raises(RuntimeError, match="no objective calibration covers"):
        scope.change_objective(2)
    assert mock.nosepiece == 1

    calibration.adopt_calibration(
        {
            1: {"name": "Plan Apo 10x", "translation_um": [0, 0, 0]},
            2: {"name": "Apo 20x WI", "translation_um": [12.5, -3.0, 41.0]},
        }
    )
    scope.handle.translations, scope.handle.calibration_info = (
        calibration.load_objective_calibration()
    )
    result = scope.change_objective(2)

    assert result["confirmed"] is True and result["compensation"]["delta_um"] == [
        12.5,
        -3.0,
        41.0,
    ]
    assert mock.nosepiece == 2
    assert mock.position == {"x": 1012.5, "y": -503.0, "z": 541.0}
    # The pixel size measured under the old objective is forgotten.
    assert scope.handle.pixel_size_um is None
    # Back under the origin's objective the frame reads the same spot.
    scope.set_origin()
    scope.change_objective(1)
    assert scope.position()["x"]["position"] == pytest.approx(0.0, abs=1e-6)


def test_set_state_applies_in_order_and_refuses_a_setting_the_microscope_does_not_have(
    scope, mock
):
    calibration.adopt_calibration(
        {
            1: {"name": "a", "translation_um": [0, 0, 0]},
            2: {"name": "b", "translation_um": [1, 1, 1]},
        }
    )
    scope.handle.translations, _ = calibration.load_objective_calibration()

    applied = scope.set_state(
        {"objective": 2, "channel": "DAPI", "exposure_ms": 50, "pfs": False}
    )

    assert applied["applied"] == {
        "objective": 2,
        "channel": "DAPI",
        "exposure_ms": 50,
        "pfs": False,
    }
    assert [c for c in mock.calls if not c.startswith("move")] == [
        "objective(2)",
        "config(DAPI)",
        "exposure(50)",
        "pfs(off)",
    ]
    with pytest.raises(ValueError, match="zoom"):
        scope.set_state({"zoom": 2})
    state = scope.state()
    assert state["changeable"] == {
        "objective": 2,
        "channel": "DAPI",
        "exposure_ms": 50.0,
        "pfs": False,
    }
    assert state["observed"]["image_size_px"] == [1024, 1024]
    assert state["observed"]["pixel_size_um"] == [0.325, 0.325]


def test_a_capture_is_staged_then_saved_as_an_ome_zarr_store_by_the_convention(
    scope, mock, tmp_path
):
    scope.select_channel("DAPI")
    out = tmp_path / "run"
    taken = scope.acquire("A1", acquisition_type="overview", output_folder=str(out))
    assert Path(taken["staged"]).exists() and mock.captures == 1

    (record,) = scope.save()

    store = out / "data" / "overview_A1.ome.zarr"
    assert record["files"] == [str(store), str(out / "log" / "overview_A1.log.json")]
    assert record["planes"] == [
        {
            "t": 0,
            "z": 0,
            "c": 0,
            "path": str(store),
            "x_um": 1000.0,
            "y_um": -500.0,
            "z_um": 500.0,
        }
    ]
    assert not Path(taken["staged"]).exists() and scope.handle.unsaved == []
    assert not (out / ".staging").exists()  # nothing of the staging is left in the run
    attributes = json.loads((store / "zarr.json").read_text())["attributes"]
    assert attributes["zmart"] == convention.zarr_attributes()
    scale = attributes["ome"]["multiscales"][0]["datasets"][0][
        "coordinateTransformations"
    ][0]["scale"]
    assert scale == [1.0, 1.0, 1.0, 0.65, 0.65]
    pixels = zarr.open_array(str(store / "0"), mode="r")[0, 0, 0]
    assert pixels.shape == (48, 64) and np.array_equal(pixels, mock.picture())
    xml = (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")
    assert (
        convention.CAPTURE_RECORD in xml
        and 'Name="DAPI"' in xml
        and 'PositionX="1000"' in xml
    )
    assert (out / "log" / "overview_A1.log.json").exists()
    try:
        import ome_types  # noqa: F401
        import ome_zarr_models  # noqa: F401
    except ImportError:
        pytest.skip(
            "ome-zarr-models and ome-types are not installed; the store is not checked against the convention"
        )
    assert convention.check_store(store) == []


def test_a_second_capture_of_the_same_label_never_overwrites_the_first(scope, tmp_path):
    out = tmp_path / "run"
    scope.acquire("A1", output_folder=str(out))
    with pytest.raises(ValueError, match="still waiting"):
        scope.acquire("A1", output_folder=str(out))
    scope.save()
    scope.acquire("A1", output_folder=str(out))
    (second,) = scope.save()
    assert second["files"][0].endswith("acquisition_A1_001.ome.zarr")


def test_without_a_data_location_a_capture_without_a_folder_is_refused(port, tmp_path):
    scope = nis_elements.connect(host="127.0.0.1", port=port)
    try:
        with pytest.raises(RuntimeError, match="does not know where to save"):
            scope.acquire("A1")
    finally:
        scope.close()


def test_the_saved_picture_is_turned_by_the_measured_orientation(scope, mock, tmp_path):
    from zmart_drivers.nikon.nis_elements_ar610.driver_setup.orientation import (
        Orientation,
    )

    scope.handle.orientation = Orientation(90, False)
    out = tmp_path / "run"
    scope.acquire("B2", output_folder=str(out))
    (record,) = scope.save()
    pixels = zarr.open_array(str(Path(record["files"][0]) / "0"), mode="r")[0, 0, 0]
    assert pixels.shape == (64, 48)
    assert np.array_equal(pixels, Orientation(90, False).apply(mock.picture()))


def test_the_session_is_closed_once_and_refuses_afterwards(connection):
    scope = nis_elements.connect(**connection)
    scope.close()
    scope.close()
    with pytest.raises(RuntimeError, match="disconnected"):
        scope.position()


def test_the_running_log_records_what_the_driver_did(scope):
    scope.move_to(1001.0, -500.0, 500.0)
    log = MACHINE.snapshot_root() / "logs" / "running_log.txt"
    assert "move to (1001, -500, 500) um" in log.read_text(encoding="utf-8")
