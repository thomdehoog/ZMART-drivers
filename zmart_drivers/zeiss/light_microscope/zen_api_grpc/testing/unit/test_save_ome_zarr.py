"""A ZEN capture saved as one OME-Zarr store, as every ZMART driver saves its captures.

The pretend ZEN writes real CZI files (with ZEISS's ``pylibCZIrw``) of a
sample that shifts as the stage moves, seen through a camera that can be
turned against the stage. Saving turns each CZI into ``data/<name>.ome.zarr``.
These tests check what a reader of the store relies on: that it follows the
ZMART metadata convention, that its pixels are exactly ZEN's, turned onto the
stage's axes, that it lies where it was taken, and that a save that fails
loses nothing.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pylibCZIrw")
pytest.importorskip("zarr")
pytest.importorskip("ome_zarr_models")
np = pytest.importorskip("numpy")

from mock_zen_api import FakeExperiment, FakeScope, write_czi
from setup_fixtures import connected, provision

from zmart_drivers import metadata_convention as convention
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.orientation import (
    STAGE_FROM_ORIENTATION,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.orientation.measure_orientation import (
    phase_shift,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.procedures.save_data import (
    ome_zarr,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.procedures.save_data import (
    save as save_step,
)


def _zarr_json(store) -> dict:
    return json.loads((Path(store) / "zarr.json").read_text(encoding="utf-8"))[
        "attributes"
    ]


def _open(store):
    import zarr

    return zarr.open_group(str(store), mode="r")


@pytest.fixture
def scope(monkeypatch, tmp_path):
    provision()
    microscope, _ = connected(monkeypatch, tmp_path)
    yield microscope
    microscope.close()


def test_a_saved_store_follows_the_zmart_metadata_convention(scope):
    pytest.importorskip("ome_types")
    scope.load_experiment("ZMART_ZStack")
    scope.acquire("A1")
    (saved,) = scope.save()

    assert convention.check_store(saved["store"]) == []
    assert convention.check_store(saved["projection"]) == []


def test_the_store_holds_zens_pixels_lowest_plane_first_with_their_channels(
    scope,
):
    scope.load_experiment("ZMART_ZStack")
    scope.acquire("A1")
    (saved,) = scope.save()

    group = _open(saved["store"])
    full = np.asarray(group["0"])
    czi = ome_zarr.read_czi(saved["czi"])["volume"]
    assert full.shape == czi.shape == (1, 1, 5, 96, 128)
    assert full.dtype == czi.dtype
    # The pretend ZEN stacks upwards, so ZEN's order is already lowest first.
    assert np.array_equal(full, czi)
    ome = _zarr_json(saved["store"])["ome"]
    assert ome["version"] == "0.5"
    assert [c["label"] for c in ome["omero"]["channels"]] == ["DAPI"]
    multiscale = ome["multiscales"][0]
    assert multiscale["type"] == "mean" and len(multiscale["datasets"]) == 2
    scale = multiscale["datasets"][0]["coordinateTransformations"][0]["scale"]
    assert scale == pytest.approx([1.0, 1.0, 5.0, 0.65, 0.65], rel=1e-3)


def test_the_store_lies_where_the_capture_was_taken(scope):
    scope.set_origin()  # positions from here, in micrometres
    scope.load_experiment("ZMART_ZStack")
    scope.move_to(10.0, 20.0, 0.0)
    scope.acquire("A1")
    (saved,) = scope.save()

    multiscale = _zarr_json(saved["store"])["ome"]["multiscales"][0]
    _t, _c, z, y, x = multiscale["datasets"][0]["coordinateTransformations"][1][
        "translation"
    ]
    # The centre of the first pixel is half a pixel in from the picture's corner.
    assert x == pytest.approx(10.0 - (128 - 1) * 0.65 / 2, abs=0.01)
    assert y == pytest.approx(20.0 - (96 - 1) * 0.65 / 2, abs=0.01)
    assert z == pytest.approx(-10.0)  # the lowest slice of the stack


def test_the_xml_holds_zens_description_and_each_planes_drive_readings(scope):
    scope.load_experiment("ZMART_ZStack")
    scope.acquire("A1")
    (saved,) = scope.save()

    xml = (Path(saved["store"]) / "OME" / "METADATA.ome.xml").read_text(
        encoding="utf-8"
    )
    assert convention.vendor_namespace("zeiss-zen") in xml
    assert "&lt;ImageDocument&gt;" in xml  # ZEN's own metadata, byte for byte, as text
    assert xml.count(convention.ACTUATOR_READINGS) == 5  # one per slice
    assert 'K="z/focus"' in xml and 'K="x/motoric"' in xml
    assert 'Model="Plan-Apochromat 10x/0.45"' in xml
    assert '<StageLabel Name="A1"' in xml


@pytest.mark.parametrize("turn", sorted(STAGE_FROM_ORIENTATION))
def test_the_pixels_are_turned_onto_the_stages_axes(tmp_path, turn):
    """Whichever way the camera is turned, the stored picture looks like the one an upright camera takes."""
    matrix = STAGE_FROM_ORIENTATION[turn]
    experiment = FakeExperiment(channels=["DAPI"], frame_px=(96, 96))
    upright, turned = tmp_path / "upright.czi", tmp_path / "turned.czi"
    write_czi(upright, experiment, pixel_um=0.65, at_um=(500.0, 700.0))
    write_czi(
        turned, experiment, pixel_um=0.65, at_um=(500.0, 700.0), image_to_stage=matrix
    )

    expected = ome_zarr.read_czi(upright)["volume"][0, 0, 0].astype(float)
    stored = ome_zarr.to_stage_axes(ome_zarr.read_czi(turned)["volume"], matrix)[
        0, 0, 0
    ].astype(float)

    # The same field, at most one pixel apart (the pretend camera's centre
    # sits half a pixel off the middle, so a mirror moves it by one).
    dx, dy, clarity = phase_shift(expected, stored)
    assert abs(dx) <= 1 and abs(dy) <= 1 and clarity > 20


def test_a_turned_camera_is_saved_turned_and_its_frame_says_so(monkeypatch, tmp_path):
    provision()
    matrix = ((0, -1), (1, 0))
    microscope, _ = connected(
        monkeypatch, tmp_path, scope=FakeScope(camera_image_to_stage=matrix)
    )
    try:
        microscope.handle.orientation = type(microscope.handle.orientation)(
            rotate_deg=90
        )
        microscope.acquire("A1", acquisition_settings={"trigger": "snap"})
        (saved,) = microscope.save()
    finally:
        microscope.close()

    group = _open(saved["store"])
    assert group["0"].shape == (1, 2, 1, 128, 96)  # width and height swapped
    xml = (Path(saved["store"]) / "OME" / "METADATA.ome.xml").read_text(
        encoding="utf-8"
    )
    assert json.dumps([list(row) for row in matrix]) in xml.replace("&quot;", '"')


def test_a_single_snap_gets_no_projection(scope):
    scope.acquire("A1", acquisition_settings={"trigger": "snap"})
    (saved,) = scope.save()

    assert saved["projection"] is None
    assert not (Path(saved["store"]).parent.parent / "projections").exists()


def test_the_projection_is_the_brightest_pixel_over_z_and_says_what_it_came_from(
    scope,
):
    scope.load_experiment("ZMART_ZStack")
    scope.acquire("A1")
    (saved,) = scope.save()

    full = np.asarray(_open(saved["store"])["0"])
    projected = np.asarray(_open(saved["projection"])["0"])
    assert np.array_equal(projected, full.max(axis=2, keepdims=True))
    xml = (Path(saved["projection"]) / "OME" / "METADATA.ome.xml").read_text(
        encoding="utf-8"
    )
    assert "derived_from" in xml and "max over z" in xml


def test_zens_czi_can_be_let_go_once_the_store_is_written(scope, monkeypatch):
    monkeypatch.setattr(save_step, "KEEP_CZI", False)
    scope.acquire("A1", acquisition_settings={"trigger": "snap"})
    zen_copy = Path(scope.handle.image_output_path) / "acquisition_A1.czi"
    assert zen_copy.is_file()

    (saved,) = scope.save()

    assert saved["czi"] is None and not zen_copy.exists()
    assert Path(saved["store"]).is_dir()


def test_a_store_that_cannot_be_written_leaves_zens_file_and_the_capture_waiting(
    scope, monkeypatch
):
    def full_disk(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(ome_zarr, "write_store", full_disk)
    scope.acquire("A1", acquisition_settings={"trigger": "snap"})

    with pytest.raises(OSError, match="no space"):
        scope.save()

    assert (Path(scope.handle.image_output_path) / "acquisition_A1.czi").is_file()
    assert len(scope.handle.unsaved) == 1


def test_a_colour_camera_becomes_three_channels_red_first(tmp_path):
    from pylibCZIrw import czi as pyczi

    path = tmp_path / "colour.czi"
    bgr = np.zeros((8, 10, 3), dtype=np.uint8)
    bgr[..., 0], bgr[..., 1], bgr[..., 2] = 10, 20, 30  # blue, green, red
    with pyczi.create_czi(str(path), exist_ok=True) as writer:
        writer.write(data=bgr, plane={"C": 0, "Z": 0, "T": 0})
        writer.write_metadata(
            document_name="colour",
            channel_names={0: "TL Brightfield"},
            scale_x=1e-6,
            scale_y=1e-6,
        )

    czi = ome_zarr.read_czi(path)

    assert [c.name for c in czi["channels"]] == [
        "TL Brightfield red",
        "TL Brightfield green",
        "TL Brightfield blue",
    ]
    assert [int(czi["volume"][0, c, 0, 0, 0]) for c in range(3)] == [30, 20, 10]


def test_a_store_is_never_written_over_another(tmp_path):
    czi = tmp_path / "a.czi"
    write_czi(czi, FakeExperiment(channels=["DAPI"]), pixel_um=0.65)
    capture = ome_zarr.gather(
        ome_zarr.read_czi(czi),
        image_to_stage=((1, 0), (0, 1)),
        heights_um=[0.0],
        centre_um=(0.0, 0.0),
    )
    store = ome_zarr.write_store(capture, tmp_path / "data" / "a.ome.zarr")

    with pytest.raises(RuntimeError, match="never written over another"):
        ome_zarr.write_store(capture, store)
    assert not list(store.parent.glob(".*.writing"))
