"""Saving a capture as one OME-Zarr 0.5 image instead of OME-TIFF planes.

The cache is written in LAS X's layout with LAS X's own descriptions of
HiRes004 (two channels, 12 bit) and AF Job002 (17 planes, 16 bit), so the
store carries the real metadata.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
import zarr

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    acquire as capture,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.image_stage_registration import (
    Orientation,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import save as saving
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output.naming import Naming
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output.product import PlaneIndex

DATA = Path(__file__).resolve().parents[1] / "data" / "lasx_description"
PID = "669c"


def record(payload: bytes) -> bytes:
    return b"p\0\0\0" + struct.pack("<I", len(payload)) + payload


def text_field(text: str) -> bytes:
    return b"*" + struct.pack("<i", len(text)) + text.encode("utf-16-le")


def cache_holding(root: Path, name: str, description: str, pixels: np.ndarray) -> None:
    """One picture in LAS X's cache: its description and its raw pixels (c inside z)."""
    (root / f"V2LMSDC_OBJ900_{PID}.tmp").write_bytes(
        record(b"*" + struct.pack("<i", 2)) + record(text_field(description)) + record(text_field(name))
    )
    raw = pixels.astype(pixels.dtype.newbyteorder("<")).tobytes()
    (root / f"V2LMSDC_MMF900_0_{PID}.tmp").write_bytes(raw + b"\0" * (-len(raw) % 65536))


@pytest.fixture
def hires(tmp_path):
    """HiRes004 in the cache: pixels[c] for channels 0 and 1, 512 x 512, 12 bit."""
    root = tmp_path / "Temp"
    root.mkdir()
    rng = np.random.default_rng(4)
    pixels = rng.integers(0, 4096, size=(2, 512, 512), dtype=np.uint16)
    cache_holding(root, "HiRes004", (DATA / "hires004-two-channels-12-bit.xml").read_text(encoding="utf-8"), pixels)
    return root, pixels


@pytest.fixture
def stack(tmp_path):
    """AF Job002 in the cache: pixels[z] for 17 planes, 512 x 512, 16 bit."""
    root = tmp_path / "Temp"
    root.mkdir()
    rng = np.random.default_rng(17)
    pixels = rng.integers(0, 65536, size=(17, 512, 512), dtype=np.uint16)
    cache_holding(root, "AF Job002", (DATA / "af-job002-17-planes-16-bit.xml").read_text(encoding="utf-8"), pixels)
    return root, pixels


def acquisition_of(job: str) -> capture.AcquisitionResult:
    return capture.AcquisitionResult(
        job=job, started_at=time.time() - 1, finished_at=time.time(), command_result={"success": True}
    )


def at(x_um: float, y_um: float, z_of: dict[int, float]):
    """A placement: plane z number -> where on the sample, as the driver's adapter answers it."""

    def placement(plane_z):
        return lambda z: {"x_um": x_um, "y_um": y_um, "z_um": z_of[z]}

    return placement


def save_as_zarr(root, tmp_path, job, *, placement=None, orientation=Orientation()):
    return saving.save(
        None,
        acquisition_of(job),
        tmp_path / "out",
        Naming(folder="high-res", hash6="000001", position_label="A1"),
        source="cache",
        cache_root=root,
        format="ome-zarr",
        placement=placement,
        orientation=orientation,
    )


def attributes(store: Path) -> dict:
    return json.loads((store / "zarr.json").read_text(encoding="utf-8"))["attributes"]


class TestOneCopy:
    def test_one_store_and_no_tiff(self, hires, tmp_path):
        root, _ = hires

        saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(1000.0, 2000.0, {0: 50.0}))

        data = tmp_path / "out" / "high-res" / "data"
        assert sorted(p.name for p in data.iterdir() if p.name != "metadata") == ["high-res_000001_A1.ome.zarr"]
        assert not list((tmp_path / "out").rglob("*.ome.tif*"))
        store = data / "high-res_000001_A1.ome.zarr"
        assert saved.image_paths == {PlaneIndex(0, 0, 0): store, PlaneIndex(0, 0, 1): store}

    def test_nothing_half_written_is_left_behind(self, hires, tmp_path):
        root, _ = hires

        save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

        data = tmp_path / "out" / "high-res" / "data"
        assert [p.name for p in data.iterdir() if p.name not in ("metadata",)] == ["high-res_000001_A1.ome.zarr"]


class TestTheStore:
    def test_it_is_a_valid_ome_zarr_0_5_image(self, hires, tmp_path):
        models = pytest.importorskip("ome_zarr_models.v05.image")
        root, _ = hires

        saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

        store = saved.image_paths[PlaneIndex(0, 0, 0)]
        image = models.Image.from_zarr(zarr.open_group(str(store), mode="r"))
        assert image.attributes.ome.version == "0.5"

    def test_the_pixels_are_the_captured_ones(self, hires, tmp_path):
        root, pixels = hires

        saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

        level = zarr.open_group(str(saved.image_paths[PlaneIndex(0, 0, 0)]), mode="r")["0"]
        assert level.shape == (1, 2, 1, 512, 512)
        assert level.dtype == np.uint16
        np.testing.assert_array_equal(level[0, 0, 0], pixels[0])
        np.testing.assert_array_equal(level[0, 1, 0], pixels[1])

    def test_axes_scale_and_where_it_lies_on_the_sample(self, hires, tmp_path):
        root, _ = hires

        saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(1000.0, 2000.0, {0: 50.0}))

        multiscale = attributes(saved.image_paths[PlaneIndex(0, 0, 0)])["ome"]["multiscales"][0]
        assert [a["name"] for a in multiscale["axes"]] == ["t", "c", "z", "y", "x"]
        [level] = multiscale["datasets"]
        transforms = {t["type"]: t for t in level["coordinateTransformations"]}
        assert transforms["scale"]["scale"][3:] == pytest.approx([2.2705078125, 2.2705078125])
        # The stage point is the middle of the frame; the store starts at its corner.
        half = 512 * 2.2705078125 / 2
        assert transforms["translation"]["translation"][2:] == pytest.approx([50.0, 2000.0 - half, 1000.0 - half])
        # The viewer builds the coarser levels itself from this one.
        assert multiscale["type"] == "mean"

    def test_channels_are_named_and_coloured_as_in_las_x(self, hires, tmp_path):
        root, _ = hires

        saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

        channels = attributes(saved.image_paths[PlaneIndex(0, 0, 0)])["ome"]["omero"]["channels"]
        assert [c["label"] for c in channels] == ["ALEXA 405", "ALEXA 488"]
        assert [c["color"].upper() for c in channels] == ["0000FF", "00FF00"]

    def test_a_stack_lies_from_its_lowest_plane_up(self, stack, tmp_path):
        root, pixels = stack
        # Swept downwards, one step of the stack (2.41 um, as LAS X describes it) a plane.
        step = 38.55132 / 16
        heights = {z: 100.0 - step * z for z in range(17)}

        saved = save_as_zarr(root, tmp_path, "AF Job", placement=at(0.0, 0.0, heights))

        store = saved.image_paths[PlaneIndex(0, 0, 0)]
        multiscale = attributes(store)["ome"]["multiscales"][0]
        transforms = {t["type"]: t for t in multiscale["datasets"][0]["coordinateTransformations"]}
        assert transforms["scale"]["scale"][2] == pytest.approx(step)
        assert transforms["translation"]["translation"][2] == pytest.approx(100.0 - 16 * step)
        level = zarr.open_group(str(store), mode="r")["0"]
        # Plane 16 is the lowest, so it comes first.
        np.testing.assert_array_equal(level[0, 0, 0], pixels[16])
        np.testing.assert_array_equal(level[0, 0, 16], pixels[0])

    def test_the_orientation_is_applied_to_every_plane(self, hires, tmp_path):
        root, pixels = hires

        saved = save_as_zarr(
            root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}), orientation=Orientation(mirrored=True)
        )

        level = zarr.open_group(str(saved.image_paths[PlaneIndex(0, 0, 0)]), mode="r")["0"]
        np.testing.assert_array_equal(level[0, 0, 0], pixels[0][:, ::-1])


class TestTheFullMetadata:
    def test_the_ome_xml_of_the_whole_capture_is_in_the_store(self, stack, tmp_path):
        ome_types = pytest.importorskip("ome_types")
        root, _ = stack

        saved = save_as_zarr(root, tmp_path, "AF Job", placement=at(0.0, 0.0, {z: float(z) for z in range(17)}))

        xml = saved.image_paths[PlaneIndex(0, 0, 0)] / "OME" / "METADATA.ome.xml"
        ome = ome_types.from_xml(xml.read_text(encoding="utf-8"), validate=True)
        pixels = ome.images[0].pixels
        assert (pixels.size_z, pixels.size_c, pixels.significant_bits) == (17, 1, 16)
        assert len(pixels.planes) == 17
        assert ome.instruments[0].objectives[0].lens_na == 0.4
        assert pixels.channels[0].fluor == "ALEXA 405"


def test_a_store_is_written_with_opencv_already_loaded(hires, tmp_path):
    """OpenCV (which the driver uses) loads its own HDF5; the writer must not need another."""
    pytest.importorskip("cv2")
    root, pixels = hires

    saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

    level = zarr.open_group(str(saved.image_paths[PlaneIndex(0, 0, 0)]), mode="r")["0"]
    np.testing.assert_array_equal(level[0, 1, 0], pixels[1])


class TestEveryFileSaysTheCanvasPosition:
    """The plane position in every file is its canvas position; the actuators stand beside it."""

    def test_the_tiff_planes_carry_the_canvas_position(self, hires, tmp_path):
        ome_types = pytest.importorskip("ome_types")
        import tifffile

        root, _ = hires
        saved = saving.save(
            None, acquisition_of("HiRes"), tmp_path / "out",
            Naming(folder="high-res", hash6="000001", position_label="A1"),
            source="cache", cache_root=root, format="ome-tiff", orientation=Orientation(),
            placement=at(1000.0, 2000.0, {0: 50.0}),
        )

        with tifffile.TiffFile(saved.image_paths[PlaneIndex(0, 0, 1)]) as tif:
            ome = ome_types.from_xml(tif.pages[0].description, validate=True)
        [plane] = ome.images[0].pixels.planes
        assert (plane.position_x, plane.position_y, plane.position_z) == (1000.0, 2000.0, 50.0)
        drives = {m.k: float(m.value) for a in ome.structured_annotations.map_annotations
                  if a.id == plane.annotation_refs[0].id for m in a.value.ms}
        assert drives["stage x (µm)"] == pytest.approx(63500.0)

    def test_the_stores_ome_xml_agrees_with_its_translation(self, stack, tmp_path):
        ome_types = pytest.importorskip("ome_types")
        root, _ = stack
        step = 38.55132 / 16
        heights = {z: 10.0 + step * z for z in range(17)}

        saved = save_as_zarr(root, tmp_path, "AF Job", placement=at(1000.0, 2000.0, heights))

        store = saved.image_paths[PlaneIndex(0, 0, 0)]
        ome = ome_types.from_xml((store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8"))
        planes = sorted(ome.images[0].pixels.planes, key=lambda p: p.the_z)
        assert [p.position_z for p in planes] == pytest.approx([heights[z] for z in range(17)])
        assert {(p.position_x, p.position_y) for p in planes} == {(1000.0, 2000.0)}
        translation = {t["type"]: t for t in attributes(store)["ome"]["multiscales"][0]["datasets"][0]
                       ["coordinateTransformations"]}["translation"]["translation"]
        assert translation[2] == pytest.approx(planes[0].position_z)

    def test_without_a_canvas_position_no_plane_position_is_claimed(self, hires, tmp_path):
        ome_types = pytest.importorskip("ome_types")
        import tifffile

        root, _ = hires
        saved = saving.save(
            None, acquisition_of("HiRes"), tmp_path / "out",
            Naming(folder="high-res", hash6="000001", position_label="A1"),
            source="cache", cache_root=root, format="ome-tiff", orientation=Orientation(),
        )

        with tifffile.TiffFile(saved.image_paths[PlaneIndex(0, 0, 0)]) as tif:
            [plane] = ome_types.from_xml(tif.pages[0].description).images[0].pixels.planes
        assert plane.position_x is None and plane.position_z is None
        assert plane.annotation_refs, "the actuators are still there"

    def test_a_store_without_a_canvas_position_is_refused(self, hires, tmp_path):
        root, _ = hires

        with pytest.raises(ValueError, match="canvas"):
            save_as_zarr(root, tmp_path, "HiRes", placement=None)


def save_with_projection(root, tmp_path, job, *, format, placement, projection="max"):
    return saving.save(
        None, acquisition_of(job), tmp_path / "out",
        Naming(folder="high-res", hash6="000001", position_label="A1"),
        source="cache", cache_root=root, format=format, orientation=Orientation(),
        placement=placement, projection=projection,
    )


class TestTheProjection:
    """A stack's max projection, beside the data in ``projections/``, made as the capture lands."""

    STEP = 38.55132 / 16

    def heights(self):
        return {z: 10.0 + self.STEP * z for z in range(17)}

    def test_a_stack_gets_one_projection_store(self, stack, tmp_path):
        root, pixels = stack

        saved = save_with_projection(root, tmp_path, "AF Job", format="ome-zarr",
                                     placement=at(1000.0, 2000.0, self.heights()))

        projections = tmp_path / "out" / "high-res" / "projections"
        assert [p.name for p in projections.iterdir()] == ["high-res_000001_A1.ome.zarr"]
        assert saved.projection_paths == (projections / "high-res_000001_A1.ome.zarr",)
        level = zarr.open_group(str(saved.projection_paths[0]), mode="r")["0"]
        assert level.shape == (1, 1, 1, 512, 512)
        np.testing.assert_array_equal(level[0, 0, 0], pixels.max(axis=0))

    def test_the_projection_lies_where_the_stack_starts(self, stack, tmp_path):
        root, _ = stack

        saved = save_with_projection(root, tmp_path, "AF Job", format="ome-zarr",
                                     placement=at(1000.0, 2000.0, self.heights()))

        data = attributes(saved.image_paths[PlaneIndex(0, 0, 0)])["ome"]["multiscales"][0]["datasets"][0]
        flat = attributes(saved.projection_paths[0])["ome"]["multiscales"][0]["datasets"][0]
        moved = {t["type"]: t["translation"] for t in data["coordinateTransformations"] if t["type"] == "translation"}
        flat_moved = {t["type"]: t["translation"] for t in flat["coordinateTransformations"] if t["type"] == "translation"}
        assert flat_moved["translation"] == pytest.approx(moved["translation"])
        assert flat_moved["translation"][2] == pytest.approx(10.0)

    def test_the_projection_says_what_it_is(self, stack, tmp_path):
        ome_types = pytest.importorskip("ome_types")
        root, _ = stack

        saved = save_with_projection(root, tmp_path, "AF Job", format="ome-zarr",
                                     placement=at(0.0, 0.0, self.heights()))

        ome = ome_types.from_xml(
            (saved.projection_paths[0] / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8"), validate=True
        )
        assert ome.images[0].pixels.size_z == 1
        facts = {m.k: m.value for a in ome.structured_annotations.map_annotations for m in a.value.ms}
        assert facts["projection"] == "max over z"
        assert facts["projected planes"] == "17"
        assert float(facts["lowest plane z (µm)"]) == pytest.approx(10.0)
        assert float(facts["highest plane z (µm)"]) == pytest.approx(10.0 + 16 * self.STEP)

    def test_a_single_plane_gets_no_projection(self, hires, tmp_path):
        root, _ = hires

        saved = save_with_projection(root, tmp_path, "HiRes", format="ome-zarr", placement=at(0.0, 0.0, {0: 0.0}))

        assert saved.projection_paths == ()
        assert not (tmp_path / "out" / "high-res" / "projections").exists()

    def test_no_projection_when_none_is_asked_for(self, stack, tmp_path):
        root, _ = stack

        saved = save_with_projection(root, tmp_path, "AF Job", format="ome-zarr",
                                     placement=at(0.0, 0.0, self.heights()), projection="none")

        assert saved.projection_paths == ()

    def test_a_tiff_stack_gets_one_projection_file_a_channel(self, stack, tmp_path):
        import tifffile

        root, pixels = stack

        saved = save_with_projection(root, tmp_path, "AF Job", format="ome-tiff",
                                     placement=at(0.0, 0.0, self.heights()))

        [path] = saved.projection_paths
        assert path.parent == tmp_path / "out" / "high-res" / "projections"
        assert path.name == "high-res_000001_A1_T000000_C00_max.ome.tiff"
        np.testing.assert_array_equal(tifffile.imread(path), pixels.max(axis=0))


def test_the_log_is_written_beside_data(tmp_path):
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import command_log

    naming = Naming(folder="high-res", hash6="000001", position_label="A1")
    path = command_log.write(tmp_path / "out", naming, {"job": "HiRes", "µ": "µm"})

    assert path == tmp_path / "out" / "high-res" / "log" / "high-res_000001_A1.log.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"job": "HiRes", "µ": "µm"}


def test_the_description_holds_no_empty_values(hires, tmp_path):
    """A browser viewer refuses an axis whose unit is null ("Unsupported unit: null"); an absent unit is left out."""
    root, _ = hires

    saved = save_as_zarr(root, tmp_path, "HiRes", placement=at(0.0, 0.0, {0: 0.0}))

    text = (saved.image_paths[PlaneIndex(0, 0, 0)] / "zarr.json").read_text(encoding="utf-8")
    assert "null" not in text
    axes = json.loads(text)["attributes"]["ome"]["multiscales"][0]["axes"]
    assert axes[1] == {"name": "c", "type": "channel"}
