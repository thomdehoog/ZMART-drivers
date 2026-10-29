"""Collecting pictures from LAS X's Data Container cache: a capture, and a tile scan one field at a time.

The cache files are written here in the layout LAS X uses (measured on the
LAS X simulator, 2026-10-11): a description ``V2LMSDC_OBJ<n>_<pid>.tmp`` made
of records, and the raw pixels ``V2LMSDC_MMF<n>_0_<pid>.tmp`` beside it. The
tile scan tests are ported from the private branch (commits 5f26f67 and
55c8bb9): one picture, its fields one after the other, each placed where its
tile was taken, saved as one store per field and let go of once.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import numpy as np
import pytest

from zmart_drivers.leica.stellaris.navigator.driver_setup.orientation import Orientation
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import (
    collect as container,
)
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import (
    metadata as description,
)
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import product
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import save as saving
from zmart_drivers.leica.stellaris.navigator.procedures.save_data.naming import Naming
from zmart_drivers.leica.stellaris.navigator.procedures.save_data.product import (
    PlaneIndex,
    RawPlaneSource,
)

PID = "669c"


def record(payload: bytes) -> bytes:
    return b"p\0\0\0" + struct.pack("<I", len(payload)) + payload


def text_field(text: str) -> bytes:
    return b"*" + struct.pack("<i", len(text)) + text.encode("utf-16-le")


def description_xml(*, channels, dims, time_stamps) -> str:
    channel_xml = "".join(
        f'<ChannelDescription DataType="0" ChannelTag="0" Resolution="{bits}" LUTName="{lut}" BytesInc="{start}" BitInc="0"/>'
        for bits, lut, start in channels
    )
    dim_xml = "".join(
        f'<DimensionDescription DimID="{dim_id}" NumberOfElements="{n}" Origin="0" Length="{length_m:e}" Unit="m" BitInc="0" BytesInc="{step}"/>'
        for dim_id, n, length_m, step in dims
    )
    stamps = " ".join("1dd59653d1099d0" for _ in range(time_stamps))
    return (
        '<Data><Image TextDescription=""><ImageDescription>'
        f"<Channels>{channel_xml}</Channels><Dimensions>{dim_xml}</Dimensions>"
        f'</ImageDescription><TimeStampList NumberOfTimeStamps="{time_stamps}">{stamps}'
        '</TimeStampList><Attachment Name="HardwareSetting" SystemTypeName="SIMULATOR">'
        '<ATLConfocalSettingDefinition StagePosX="0.0635" StagePosY="0.0415"/>'
        "</Attachment></Image></Data>"
    )


def write_description(cache: Path, number: int, name: str, xml: str) -> Path:
    path = cache / f"V2LMSDC_OBJ{number}_{PID}.tmp"
    body = (
        record(b"*" + struct.pack("<i", 2))
        + record(text_field(xml))
        + record(text_field(name) + text_field("LAS AF"))
        + record(b"*" + struct.pack("<i", 2))
    )
    path.write_bytes(body)
    return path


def write_pixels(cache: Path, number: int, pixels: np.ndarray) -> Path:
    path = cache / f"V2LMSDC_MMF{number}_0_{PID}.tmp"
    raw = pixels.astype(pixels.dtype.newbyteorder("<")).tobytes()
    path.write_bytes(raw + b"\0" * (-len(raw) % 65536))
    return path


def two_channel_12_bit(cache: Path, number: int = 746, name: str = "Image002", stamps: int = 2):
    """A 2-channel, 1-plane, 12-bit picture, 4 x 3 pixels, as a Capture Image makes it."""
    pixels = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4) * 100 + 7
    write_pixels(cache, number, pixels)
    xml = description_xml(
        channels=[(12, "Blue", 0), (12, "Green", 24)],
        dims=[(1, 4, 4 * 2.5e-6, 2), (2, 3, 3 * 2.5e-6, 8), (3, 1, 0.0, 48)],
        time_stamps=stamps,
    )
    write_description(cache, number, name, xml)
    return pixels


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    root = tmp_path / "Temp"
    root.mkdir()
    return root


def acquisition_of(picture: str, *, started: float | None = None) -> product.AcquisitionResult:
    return product.AcquisitionResult(
        picture=picture,
        started_at=time.time() - 1 if started is None else started,
        finished_at=time.time(),
        command_result={"success": True},
    )


def plane(source: RawPlaneSource) -> np.ndarray:
    from zmart_drivers.leica.stellaris.navigator.procedures.save_data import (
        ome_canonical,
    )

    return ome_canonical._read_source_plane(source)


def one_place(field, stage_um, depths):
    return lambda depth: {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0}


class TestCollectingACapture:
    def test_the_capture_is_found_by_its_name_and_read_plane_by_plane(self, cache):
        pixels = two_channel_12_bit(cache)

        (exported,) = container.collect_picture(acquisition_of("capture"), cache_root=cache)

        planes = exported.positions[0].planes
        assert sorted(planes) == [PlaneIndex(0, 0, 0), PlaneIndex(0, 0, 1)]
        for c in (0, 1):
            np.testing.assert_array_equal(plane(planes[PlaneIndex(0, 0, c)]), pixels[c])
        assert exported.release_target == (746, "Image002")
        assert exported.metadata.pixel_type == "uint16" and exported.metadata.significant_bits == 12

    def test_a_tile_scan_s_picture_is_not_taken_for_a_capture(self, cache):
        two_channel_12_bit(cache, name="Mosaic001")
        with pytest.raises(RuntimeError, match="no new picture named Image"):
            container.collect_picture(acquisition_of("capture"), cache_root=cache, timeout_s=0.3)

    def test_a_picture_from_before_the_capture_is_not_taken(self, cache):
        two_channel_12_bit(cache)
        with pytest.raises(RuntimeError, match="no new picture"):
            container.collect_picture(acquisition_of("capture", started=time.time() + 10), cache_root=cache, timeout_s=0.3)

    def test_a_picture_collected_once_is_never_new_again(self, cache):
        two_channel_12_bit(cache)
        container.collect_picture(acquisition_of("capture"), cache_root=cache)
        with pytest.raises(RuntimeError, match="no new picture"):
            container.collect_picture(acquisition_of("capture"), cache_root=cache, timeout_s=0.3)

    def test_an_unfinished_picture_is_waited_for_then_given_up_on(self, cache):
        two_channel_12_bit(cache, stamps=1)
        with pytest.raises(RuntimeError, match="time stamp for 1 of 2 planes"):
            container.collect_picture(acquisition_of("capture"), cache_root=cache, timeout_s=0.3)

    def test_an_unknown_kind_of_picture_is_refused(self, cache):
        with pytest.raises(ValueError, match="kind"):
            container.collect_picture(acquisition_of("movie"), cache_root=cache)

    def test_a_single_capture_is_saved_as_one_store(self, cache, tmp_path):
        import zarr

        pixels = two_channel_12_bit(cache, number=10762, name="Image001")

        saved = saving.save(
            acquisition_of("capture"), tmp_path / "out", [Naming(stem="capture_000001_one")],
            place=one_place, cache_root=cache, orientation=Orientation(),
        )

        assert len(saved) == 1 and saved[0].release_target == (10762, "Image001")
        level = zarr.open_group(str(saved[0].image_paths[PlaneIndex(0, 0, 0)]), mode="r")["0"][:]
        for c in (0, 1):
            np.testing.assert_array_equal(level[0, c, 0], pixels[c])


#: Three fields of a Navigator tile scan, as LAS X lists them (metres).
FIELDS_M = ((0.0635, 0.0415, 0.0), (0.0640, 0.0415, 0.0), (0.0645, 0.0415, 0.0))


def mosaic_xml(*, fields=FIELDS_M, listed=FIELDS_M, times=1) -> str:
    """A Navigator tile scan as LAS X describes it (simulator, 2026-10-15): 2 channels, 4 x 3 pixels
    at 8 bit, one field after the other (DimID 10), one time point (DimID 4), the fields' positions
    in the TileScanInfo attachment; the setting's own position is the last field's."""
    tiles = "".join(f'<Tile FieldX="-1" FieldY="-1" PosX="{x}" PosY="{y}" PosZ="{z}"/>' for x, y, z in listed)
    stamps = " ".join(format(0x1DD59653D1099D0 + i * 10_000_000, "x") for i in range(len(fields) * 2 * times))
    return (
        '<Data><Image TextDescription=""><ImageDescription><Channels>'
        '<ChannelDescription DataType="0" ChannelTag="0" Resolution="8" LUTName="Blue" BytesInc="0" BitInc="0"/>'
        '<ChannelDescription DataType="0" ChannelTag="0" Resolution="8" LUTName="Green" BytesInc="12" BitInc="0"/>'
        "</Channels><Dimensions>"
        '<DimensionDescription DimID="1" NumberOfElements="4" Origin="0" Length="1e-05" Unit="m" BitInc="0" BytesInc="1"/>'
        '<DimensionDescription DimID="2" NumberOfElements="3" Origin="0" Length="1e-05" Unit="m" BitInc="0" BytesInc="4"/>'
        f'<DimensionDescription DimID="10" NumberOfElements="{len(fields)}" Origin="0" Length="0" Unit="" BitInc="0" BytesInc="24"/>'
        f'<DimensionDescription DimID="4" NumberOfElements="{times}" Origin="0" Length="0" Unit="s" BitInc="0" BytesInc="{24 * len(fields)}"/>'
        f'</Dimensions></ImageDescription><TimeStampList NumberOfTimeStamps="{len(fields) * 2 * times}">{stamps}'
        '</TimeStampList><Attachment Name="HardwareSetting" SystemTypeName="SIMULATOR">'
        f'<ATLConfocalSettingDefinition StagePosX="{fields[-1][0]}" StagePosY="{fields[-1][1]}"/>'
        "</Attachment>"
        f'<Attachment Name="TileScanInfo" Application="LAS AF">{tiles}</Attachment></Image></Data>'
    )


class TestTileScan:
    """The Navigator's Preview over several positions: one picture, its fields one after the other."""

    def pixels(self):
        return np.arange(3 * 2 * 3 * 4, dtype=np.uint8).reshape(3, 2, 3, 4)  # field, channel, y, x

    def test_a_tile_scan_has_as_many_fields_as_positions(self):
        assert container.fields(mosaic_xml()) == 3

    def test_a_single_picture_is_one_field(self):
        xml = description_xml(channels=[(8, "Gray", 0)], dims=[(1, 4, 1e-5, 1), (2, 3, 1e-5, 4)], time_stamps=1)
        assert container.fields(xml) == 1

    @pytest.mark.parametrize("field", [0, 1, 2])
    def test_each_field_is_read_from_its_own_place(self, cache, field):
        pixels = self.pixels()
        path = write_pixels(cache, 10755, pixels)
        _metadata, planes = container.layout(mosaic_xml(), path, field=field)
        assert sorted(planes) == [PlaneIndex(t=0, z=0, c=0), PlaneIndex(t=0, z=0, c=1)]
        for c in (0, 1):
            np.testing.assert_array_equal(plane(planes[PlaneIndex(t=0, z=0, c=c)]), pixels[field, c])

    @pytest.mark.parametrize("field", [0, 1, 2])
    def test_each_field_is_placed_where_its_tile_was_taken(self, field):
        metadata = description.metadata_from_description(mosaic_xml(), field=field)
        x_m, y_m, _ = FIELDS_M[field]
        for c in (0, 1):
            placed = metadata.planes[PlaneIndex(t=0, z=0, c=c)]
            assert placed.position_x_um == pytest.approx(x_m * 1e6)
            assert placed.position_y_um == pytest.approx(y_m * 1e6)
        # Time is counted from the field's own first plane: its second channel is 1 s later.
        assert metadata.planes[PlaneIndex(t=0, z=0, c=1)].delta_t_s == pytest.approx(1.0)

    def test_a_field_that_is_not_there_is_refused(self, cache):
        path = write_pixels(cache, 10755, self.pixels())
        with pytest.raises(RuntimeError, match="field 3"):
            container.layout(mosaic_xml(), path, field=3)

    def test_positions_that_do_not_match_the_fields_are_refused(self):
        with pytest.raises(RuntimeError, match="2 positions for 3 fields"):
            description.metadata_from_description(mosaic_xml(listed=FIELDS_M[:2]), field=0)

    def test_a_time_series_of_tile_scans_is_still_refused(self):
        with pytest.raises(RuntimeError, match="dimension 4"):
            description.metadata_from_description(mosaic_xml(times=2), field=0)


class TestSavingATileScan:
    """The Navigator's Preview over several positions, saved as one OME-Zarr store per field, then let go of."""

    def mosaic(self, cache):
        pixels = np.arange(3 * 2 * 3 * 4, dtype=np.uint8).reshape(3, 2, 3, 4)  # field, channel, y, x
        write_pixels(cache, 10755, pixels)
        write_description(cache, 10755, "Mosaic001", mosaic_xml())
        return pixels

    def test_the_tile_scan_is_collected_one_capture_per_field(self, cache):
        pixels = self.mosaic(cache)

        fields = container.collect_picture(acquisition_of("tile_scan"), cache_root=cache)

        assert len(fields) == 3
        for f, exported in enumerate(fields):
            planes = exported.positions[0].planes
            for c in (0, 1):
                np.testing.assert_array_equal(plane(planes[PlaneIndex(t=0, z=0, c=c)]), pixels[f, c])
            assert exported.release_target == (10755, "Mosaic001")  # the one picture, let go of once

    def test_a_capture_s_picture_is_not_taken_for_a_tile_scan(self, cache):
        two_channel_12_bit(cache, number=746, name="Image002")
        with pytest.raises(RuntimeError, match="no new picture named Mosaic"):
            container.collect_picture(acquisition_of("tile_scan"), cache_root=cache, timeout_s=0.3)

    def test_every_field_becomes_its_own_store_at_its_own_place(self, cache, tmp_path):
        import zarr

        pixels = self.mosaic(cache)
        placed = []

        def place(field, stage_um, depths):
            placed.append(stage_um)
            return lambda depth: {"x_um": stage_um[0] - 63500.0, "y_um": stage_um[1] - 41500.0, "z_um": 0.0}

        namings = [Naming(stem=f"overview_000001_field{f}") for f in range(3)]
        saved = saving.save(
            acquisition_of("tile_scan"), tmp_path / "out", namings, place=place, cache_root=cache, orientation=Orientation()
        )

        assert [s.naming.stem for s in saved] == [f"overview_000001_field{f}" for f in range(3)]
        stores = [s.image_paths[PlaneIndex(0, 0, 0)] for s in saved]
        assert len(set(stores)) == 3
        for f, store in enumerate(stores):
            level = zarr.open_group(str(store), mode="r")["0"][:]
            for c in (0, 1):
                np.testing.assert_array_equal(level[0, c, 0], pixels[f, c])
        # The placement is asked once for the canvas and once more when the store is written.
        assert sorted({round(x) for x, _y, _z in placed}) == [63500, 64000, 64500]
        assert {s.release_target for s in saved} == {(10755, "Mosaic001")}

    def test_a_field_that_is_not_saved_stops_the_save(self, cache, tmp_path, monkeypatch):
        self.mosaic(cache)
        real_persist = saving._persist_store
        stored = []

        def persist(exported, *args, **kwargs):
            if len(stored) == 2:
                raise RuntimeError("the pixels read back from disk are not those written; not saved")
            stored.append(1)
            return real_persist(exported, *args, **kwargs)

        monkeypatch.setattr(saving, "_persist_store", persist)
        namings = [Naming(stem=f"overview_000001_field{f}") for f in range(3)]
        with pytest.raises(RuntimeError, match="not saved"):
            saving.save(acquisition_of("tile_scan"), tmp_path / "out", namings, place=one_place, cache_root=cache, orientation=Orientation())

    def test_a_name_for_every_field_is_needed(self, cache, tmp_path):
        self.mosaic(cache)
        with pytest.raises(ValueError, match="3 fields, 2 names"):
            saving.save(
                acquisition_of("tile_scan"), tmp_path / "out", [Naming(stem="o_000001_a")] * 2,
                place=one_place, cache_root=cache, orientation=Orientation(),
            )
