"""A capture read back from FLUOVIEW and saved as one OME-Zarr store that follows the ZMART metadata convention."""

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("zarr", reason="saving needs zarr")
pytest.importorskip("ome_zarr_models", reason="saving needs ome-zarr-models")

from zmart_drivers import metadata_convention as convention
from zmart_drivers.evident.fv4000.rdk.procedures.save_data import (
    naming,
    ome_zarr,
)


def test_a_single_plane_capture_becomes_a_store_with_no_projection(
    scope, tmp_path, mock
):
    taken = scope.acquire(
        "A1", acquisition_type="overview", output_folder=str(tmp_path / "run")
    )
    assert taken["fluoview_file"].endswith(".oir") and len(scope.handle.unsaved) == 1
    (record,) = scope.save()
    store = Path(record["files"][0])
    assert (
        store == tmp_path / "run" / "data" / "overview_A1.ome.zarr" and store.is_dir()
    )
    assert (
        record["projections"] == [] and len(record["files"]) == 2
    )  # the store and its log
    assert {(p["t"], p["c"], p["z"]) for p in record["planes"]} == {
        (0, 0, 0),
        (0, 1, 0),
    }
    assert (
        record["planes"][0]["x_um"] == 10000.0 and record["planes"][0]["z_um"] == 2500.0
    )
    assert record["pixel_size_um"] == [1.242, 1.242] and record["image_size_px"] == [
        256,
        256,
    ]
    assert scope.state()["observed"]["pixel_size_um"] == [1.242, 1.242]
    assert convention.check_store(store) == []
    assert scope.handle.unsaved == []
    log = json.loads(Path(record["log"][0]).read_text())
    assert (
        log["capture"]["fluoview_file"] == taken["fluoview_file"]
        and log["files"][0] == "data/overview_A1.ome.zarr"
    )


def test_a_z_stack_is_stored_lowest_plane_first_with_a_max_projection_beside_it(
    scope, tmp_path, mock
):
    scope.set_state(
        {
            "z_stack_enabled": True,
            "z_stack_slices": 4,
            "z_stack_step_um": -1.0,
            "z_stack_start_um": 3.0,
        }
    )
    scope.acquire("S1", acquisition_type="stack", output_folder=str(tmp_path / "run"))
    (record,) = scope.save()
    store, projection = Path(record["files"][0]), Path(record["projections"][0])
    heights = sorted({(p["z"], p["z_um"]) for p in record["planes"]})
    assert heights == [(0, 2500.0), (1, 2501.0), (2, 2502.0), (3, 2503.0)], (
        "depth 0 is the lowest slice, although FLUOVIEW swept downwards"
    )
    assert (
        convention.check_store(store) == [] and convention.check_store(projection) == []
    )
    plane0 = ome_zarr.read_plane(store, (0, 0, 0))
    assert np.array_equal(
        plane0, mock.files[record["fluoview_file"]].planes[(0, "CH1", 3, 0)]
    ), "the lowest slice is FLUOVIEW's last"
    projected = ome_zarr.read_plane(projection, (0, 0, 0))
    assert projected.max() >= plane0.max()
    xml = (store / "OME" / "METADATA.ome.xml").read_text()
    assert 'PhysicalSizeZ="1"' in xml and "actuator-readings" in xml


def test_the_measured_orientation_is_applied_to_the_saved_pixels(scope, tmp_path, mock):
    from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation import Orientation

    mock.orientation = (90, True)
    scope.handle.orientation = Orientation(90, True)
    scope.acquire("O1", output_folder=str(tmp_path / "run"))
    (record,) = scope.save()
    stored = ome_zarr.read_plane(Path(record["files"][0]), (0, 0, 0))
    assert np.array_equal(stored, mock.view(0)), (
        "the stored picture is the stage-aligned view"
    )


def test_a_second_capture_of_the_same_label_never_overwrites_the_first(scope, tmp_path):
    for _ in range(2):
        scope.acquire("A1", output_folder=str(tmp_path / "run"))
        scope.save()
    assert (tmp_path / "run" / "data" / "acquisition_A1.ome.zarr").is_dir()
    assert (tmp_path / "run" / "data" / "acquisition_A1_001.ome.zarr").is_dir()
    assert naming.free_stem(tmp_path / "run", "acquisition_A1") == "acquisition_A1_002"


def test_a_label_still_waiting_to_be_saved_cannot_be_captured_again(
    scope, tmp_path, mock
):
    scope.acquire("A1", output_folder=str(tmp_path / "run"))
    starts = len([c for c in mock.calls if c[0] == "Protocol.startProtocol"])
    with pytest.raises(ValueError, match="still waiting"):
        scope.acquire("A1", output_folder=str(tmp_path / "run"))
    assert len([c for c in mock.calls if c[0] == "Protocol.startProtocol"]) == starts, (
        "refused before any scan"
    )
    with pytest.raises(ValueError, match="no capture waiting"):
        scope.save("B2")


def test_an_unlabelled_capture_counts_and_the_default_folder_is_under_the_data_location(
    scope, tmp_path
):
    first = scope.acquire()
    second = scope.acquire()
    assert (first["position_label"], second["position_label"]) == ("000000", "000001")
    assert scope.handle.position_counter == 2, (
        "the counter moves only for pictures taken"
    )
    records = scope.save()
    assert all(str(tmp_path / "out" / ".staging") in r["files"][0] for r in records)


def test_the_vendor_file_is_copied_only_when_asked_and_reachable(
    scope, tmp_path, monkeypatch
):
    from zmart_drivers.evident.fv4000.rdk.procedures.save_data import save

    monkeypatch.setattr(
        save, "IMAGE_SAVE", save.ImageSaveProfile(copy_vendor_file=True)
    )
    scope.acquire("V1", output_folder=str(tmp_path / "run"))
    (record,) = scope.save()
    assert len(record["files"]) == 2, (
        "FLUOVIEW's D:/Images is not on this computer, so nothing is copied"
    )


def test_a_store_is_never_written_over_another(tmp_path):
    target = tmp_path / "x.ome.zarr"
    target.mkdir()
    with pytest.raises(RuntimeError, match="never written over"):
        ome_zarr.write_store(None, target)


def test_the_mean_pyramid_halves_down_to_one_chunk():
    volume = np.arange(1 * 1 * 1 * 300 * 300, dtype=np.uint16).reshape(
        1, 1, 1, 300, 300
    )
    levels = ome_zarr.mean_pyramid(volume)
    assert [level.shape[-2:] for level in levels] == [(300, 300), (150, 150), (75, 75)]
    assert (
        ome_zarr.halve_xy(np.array([[[[[1, 3], [5, 7]]]]], dtype=np.uint16))[
            0, 0, 0, 0, 0
        ]
        == 4
    )


def test_the_ome_model_gets_each_channels_laser_and_detector_from_the_state_at_the_capture(
    scope, tmp_path, mock
):
    mock.channels["CH1"].hv = 610
    mock.channels["CH1"].offset = 3
    scope.acquire("L1", output_folder=str(tmp_path / "run"))
    (record,) = scope.save()
    store = Path(record["files"][0])
    xml = (store / "OME" / "METADATA.ome.xml").read_text()
    assert convention.check_store(store) == [], "still valid against the OME schema"
    assert 'Laser ID="LightSource:LASER_488"' in xml and 'Wavelength="488"' in xml
    assert 'ExcitationWavelength="488"' in xml
    assert 'LightSourceSettings ID="LightSource:LASER_488" Attenuation="0.98"' in xml, (
        "2 % power"
    )
    assert 'Voltage="610"' in xml and 'Offset="3"' in xml and 'Gain="1"' in xml


def test_fluoviews_own_answers_about_the_picture_are_kept_whole_in_the_vendor_layer(
    scope, tmp_path
):
    import xml.etree.ElementTree as ET

    scope.acquire("V2", output_folder=str(tmp_path / "run"))
    (record,) = scope.save()
    root = ET.parse(Path(record["files"][0]) / "OME" / "METADATA.ome.xml").getroot()
    texts = [
        "".join(el.itertext())
        for el in root.iter()
        if el.tag.endswith("CommentAnnotation")
        and "/vendor/evident-fluoview" in (el.get("Namespace") or "")
    ]
    described = [json.loads(t) for t in texts if '"IDA.getProperty"' in t]
    assert len(described) == 1
    keys = [entry["key"] for entry in described[0]["IDA.getProperty"]]
    assert {"EnabledChannelIdList", "ChannelInfo", "Axes", "PixelLength"} <= set(keys)
    info = next(e for e in described[0]["IDA.getProperty"] if e["key"] == "ChannelInfo")
    assert info["answer"]["structValue"][0]["bitCount"] == 12, (
        "the answer is kept as FLUOVIEW gave it"
    )
