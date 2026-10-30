"""Taking a picture and saving it: from NIS's TIFF to one OME-Zarr store that follows the ZMART convention.

Each test takes a real picture of the pretend microscope, whose pattern
depends on where the stage stood, so the saved pixels can be checked against
what the camera saw: the right place, the right turn, every channel, and no
value changed on the way.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from zmart_drivers import metadata_convention as convention
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
    Orientation,
    reorient_array,
)
from zmart_drivers.nikon.nis_elements_ar7.procedures.save_data import (
    naming,
    ome_zarr,
    save,
)


def transformations(store: str) -> dict:
    """The scale and translation of the store's finest level."""
    attributes = json.loads((Path(store) / "zarr.json").read_text(encoding="utf-8"))[
        "attributes"
    ]
    found = attributes["ome"]["multiscales"][0]["datasets"][0][
        "coordinateTransformations"
    ]
    return {entry["type"]: entry[entry["type"]] for entry in found}


def test_a_capture_is_saved_as_one_store_that_follows_the_convention(
    scope, output_root
):
    record = scope.acquire("A1", acquisition_type="overview")
    store = Path(record["store"])
    assert store == output_root / "data" / "overview_A1.ome.zarr"
    assert record["files"] == [
        str(store),
        str(output_root / "log" / "overview_A1.log.json"),
    ]
    assert convention.check_store(store) == []
    assert record["planes"] == [
        {
            "path": str(store),
            "c": 0,
            "z": 0,
            "t": 0,
            "x_um": 1000.0,
            "y_um": 2000.0,
            "z_um": 3000.0,
        }
    ]
    assert not (output_root / ".staging").exists(), (
        "the TIFF and its note are removed once the store is written"
    )


def test_the_saved_pixels_are_what_the_camera_saw(scope, stand_in, output_root):
    expected = stand_in.scope.picture()
    record = scope.acquire("A1")
    assert np.array_equal(
        ome_zarr.read_plane(Path(record["store"]), (0, 0, 0)), expected
    )


def test_the_store_says_where_the_picture_lies(scope, output_root):
    record = scope.acquire("A1")
    found = transformations(record["store"])
    assert found["scale"][-2:] == pytest.approx([0.65, 0.65])
    assert found["translation"] == pytest.approx(
        ome_zarr.translation(
            1000.0, 2000.0, 3000.0, nx=64, ny=48, pixel_x_um=0.65, pixel_y_um=0.65
        )
    )
    assert found["translation"][-1] == pytest.approx(1000.0 - 63 * 0.65 / 2)


def test_the_capture_record_and_log_hold_real_values(scope, output_root):
    record = scope.acquire("A1")
    log = json.loads(Path(record["log"]).read_text(encoding="utf-8"))
    assert log["position_label"] == "A1" and log["used"] == {"trigger": "snap"}
    assert log["capture"]["nis"]["image"]["width"] == 64
    assert log["stage before (um)"] == {"x": 1000.0, "y": 2000.0, "z": 3000.0}
    xml = (Path(record["store"]) / "OME" / "METADATA.ome.xml").read_text(
        encoding="ascii"
    )
    assert "Plan Apo 10x" in xml and 'PhysicalSizeX="0.65"' in xml


@pytest.mark.parametrize(
    "deg,mirrored", [(90, False), (180, False), (270, True), (0, True)]
)
def test_a_stated_orientation_turns_the_saved_picture(scope, stand_in, deg, mirrored):
    scope.handle.orientation = Orientation(rotate_deg=deg, mirrored=mirrored)
    expected = reorient_array(stand_in.scope.picture(), scope.handle.orientation)
    record = scope.acquire("A1")
    assert np.array_equal(
        ome_zarr.read_plane(Path(record["store"]), (0, 0, 0)), expected
    )


def test_a_colour_picture_becomes_three_channels(stand_in, scope):
    stand_in.scope.channels = "rgb"
    stand_in.scope.bits = 8
    record = scope.acquire("A1")
    assert [p["c"] for p in record["planes"]] == [0, 1, 2]
    assert convention.check_store(record["store"]) == []


def test_a_picture_of_pages_becomes_one_channel_per_page(stand_in, scope):
    stand_in.scope.channels = "pages"
    record = scope.acquire("A1")
    assert len(record["planes"]) == 3
    assert convention.check_store(record["store"]) == []


def test_a_picture_saved_as_displayed_is_refused_and_kept(stand_in, scope, output_root):
    stand_in.scope.saves_as_displayed = True
    with pytest.raises(RuntimeError, match="kept at"):
        scope.acquire("A1")
    staged = save.staged(output_root)
    assert [p.name for p in staged] == ["acquisition_A1.tif"]
    assert not naming.store_path(output_root, "acquisition_A1").exists()


def test_a_kept_capture_is_saved_later_by_save_data(
    stand_in, scope, output_root, monkeypatch
):
    real = save.read_staged
    monkeypatch.setattr(
        save,
        "read_staged",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError, match="disk full"):
        scope.acquire("A1")
    monkeypatch.setattr(save, "read_staged", real)
    done = scope.save_staged()
    assert done["failed"] == {} and len(done["saved"]) == 1
    assert convention.check_store(done["saved"][0]["store"]) == []
    assert save.staged(output_root) == []


def test_no_capture_is_ever_saved_over_another(scope, output_root):
    first = scope.acquire("A1")
    second = scope.acquire("A1")
    assert Path(second["store"]).name == "acquisition_A1_001.ome.zarr"
    assert Path(first["store"]).is_dir() and Path(second["store"]).is_dir()


def test_without_a_label_the_session_counts(scope):
    assert Path(scope.acquire()["store"]).name == "acquisition_000000.ome.zarr"
    assert Path(scope.acquire()["store"]).name == "acquisition_000001.ome.zarr"


def test_an_uncalibrated_objective_is_refused_before_a_second_picture(
    stand_in, scope, output_root
):
    stand_in.scope.calibrated = False
    with pytest.raises(RuntimeError, match="pixel size"):
        scope.acquire("A1")  # the first time, NIS's answer shows it
    captures = stand_in.scope.captures
    with pytest.raises(RuntimeError, match="not calibrated"):
        scope.acquire("A2")  # from then on, the record refuses before capturing
    assert stand_in.scope.captures == captures


def test_wrong_requests_are_refused(scope):
    with pytest.raises(ValueError):
        scope.acquire("A1", acquisition_settings={"trigger": "full"})
    with pytest.raises(ValueError):
        scope.acquire("A1", acquisition_settings={"exposure": 10})
    with pytest.raises(ValueError):
        scope.acquire("A1", acquisition_type=" ")
    with pytest.raises(ValueError):
        scope.acquire("x" * 100)


def test_an_output_folder_given_to_acquire_is_used(scope, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    record = scope.acquire("A1", output_folder=str(elsewhere))
    assert Path(record["store"]).is_relative_to(elsewhere)


def test_nis_own_description_of_the_picture_goes_into_the_store(scope, output_root):
    """The vendor's layer: NIS's whole description (camera, exposure...) is kept in the OME-XML, and its file removed."""
    record = scope.acquire("A1")
    xml = (Path(record["store"]) / "OME" / "METADATA.ome.xml").read_text(
        encoding="ascii"
    )
    assert "Exposure: 100 ms" in xml and "Pretend sCMOS" in xml
    assert "Optics: " in xml  # each part of the description, by its name
    assert not list(output_root.rglob("*.nis_description.txt"))


def test_a_picture_is_saved_even_when_nis_writes_no_description(
    scope, stand_in, output_root
):
    stand_in.scope.answer_next["LoadImageFullDescription"] = 0
    record = scope.acquire("A1")
    assert convention.check_store(record["store"]) == []
    log = json.loads(Path(record["log"]).read_text(encoding="utf-8"))
    assert log["capture"]["nis"]["description"]["path"] is None
