"""Unit tests for collecting pictures from LAS X's Data Container cache.

The cache files are written here in the layout LAS X uses (measured on the
LAS X simulator, 2026-10-11): a description ``V2LMSDC_OBJ<n>_<pid>.tmp`` made
of records, and the raw pixels ``V2LMSDC_MMF<n>_0_<pid>.tmp`` beside it.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.orientation import (
    Orientation,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.naming import (
    Naming,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.product import (
    PlaneIndex,
    RawPlaneSource,
)

from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    save as acquisition,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import collect as container
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import product

PID = "669c"


def record(payload: bytes) -> bytes:
    return b"p\0\0\0" + struct.pack("<I", len(payload)) + payload


def text_field(text: str) -> bytes:
    return b"*" + struct.pack("<i", len(text)) + text.encode("utf-16-le")


def description_xml(*, channels, dims, time_stamps, job=None) -> str:
    channel_xml = "".join(
        f'<ChannelDescription DataType="0" ChannelTag="0" Resolution="{bits}" '
        f'LUTName="{lut}" BytesInc="{start}" BitInc="0"/>'
        for bits, lut, start in channels
    )
    dim_xml = "".join(
        f'<DimensionDescription DimID="{dim_id}" NumberOfElements="{n}" Origin="0" '
        f'Length="{length_m:e}" Unit="m" BitInc="0" BytesInc="{step}"/>'
        for dim_id, n, length_m, step in dims
    )
    stamps = " ".join("1dd59653d1099d0" for _ in range(time_stamps))
    return (
        '<Data><Image TextDescription=""><ImageDescription>'
        f"<Channels>{channel_xml}</Channels><Dimensions>{dim_xml}</Dimensions>"
        f'</ImageDescription><TimeStampList NumberOfTimeStamps="{time_stamps}">{stamps}'
        '</TimeStampList><Attachment Name="HardwareSetting" SystemTypeName="SIMULATOR">'
        '<ATLConfocalSettingDefinition StagePosX="0.0635" StagePosY="0.0415"/>'
        + (f'<LDM_Block_Sequential BlockName="{job}"/>' if job else "")
        + "</Attachment></Image></Data>"
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
    padded = raw + b"\0" * (-len(raw) % 65536)
    path.write_bytes(padded)
    return path


def two_channel_12_bit(
    cache: Path, number: int = 746, name: str = "HiRes002", stamps: int = 2
):
    """A 2-channel, 1-plane, 12-bit picture, 4 x 3 pixels, as HiRes002 was."""
    pixels = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4) * 100 + 7
    write_pixels(cache, number, pixels)
    xml = description_xml(
        channels=[(12, "Blue", 0), (12, "Green", 24)],
        dims=[(1, 4, 4 * 2.5e-6, 2), (2, 3, 3 * 2.5e-6, 8), (3, 1, 0.0, 48)],
        time_stamps=stamps,
    )
    write_description(cache, number, name, xml)
    return pixels


def three_plane_8_bit(cache: Path, number: int = 702, name: str = "AF Job001"):
    """A 1-channel stack of 3 planes 2 um apart, 8-bit, 4 x 3 pixels."""
    pixels = np.arange(3 * 3 * 4, dtype=np.uint8).reshape(3, 3, 4)
    write_pixels(cache, number, pixels)
    xml = description_xml(
        channels=[(8, "Gray", 0)],
        dims=[(1, 4, 4 * 2.5e-6, 1), (2, 3, 3 * 2.5e-6, 4), (3, 3, 4.0e-6, 12)],
        time_stamps=3,
    )
    write_description(cache, number, name, xml)
    return pixels


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    root = tmp_path / "Temp"
    root.mkdir()
    return root


def acquisition_of(
    job: str, *, started: float | None = None
) -> product.AcquisitionResult:
    started = time.time() - 1 if started is None else started
    return product.AcquisitionResult(
        job=job,
        started_at=started,
        finished_at=time.time(),
        command_result={"success": True},
    )


def plane(source: RawPlaneSource) -> np.ndarray:
    from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import ome_canonical

    return ome_canonical._read_source_plane(source)


class TestCollecting:
    def test_two_channels_are_read_where_the_description_puts_them(self, cache):
        pixels = two_channel_12_bit(cache)

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        planes = exported.positions[0].planes
        assert sorted(planes) == [PlaneIndex(t=0, z=0, c=0), PlaneIndex(t=0, z=0, c=1)]
        np.testing.assert_array_equal(plane(planes[PlaneIndex(0, 0, 0)]), pixels[0])
        np.testing.assert_array_equal(plane(planes[PlaneIndex(0, 0, 1)]), pixels[1])
        assert plane(planes[PlaneIndex(0, 0, 1)]).dtype == np.uint16

    def test_the_description_gives_size_bit_depth_and_pixel_size(self, cache):
        two_channel_12_bit(cache)

        metadata = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        ).metadata

        assert (metadata.size_x, metadata.size_y, metadata.size_z, metadata.size_c) == (
            4,
            3,
            1,
            2,
        )
        assert metadata.size_t == 1
        assert metadata.pixel_type == "uint16"
        assert metadata.significant_bits == 12
        assert metadata.physical_size_x_um == pytest.approx(2.5)
        assert metadata.physical_size_y_um == pytest.approx(2.5)
        assert metadata.physical_size_z_um is None

    def test_a_stack_has_one_plane_per_height_and_the_step_between_them(self, cache):
        pixels = three_plane_8_bit(cache)

        exported = container.collect_lasx_data_container(
            None, acquisition_of("AF Job"), cache_root=cache
        )

        planes = exported.positions[0].planes
        assert sorted(planes) == [PlaneIndex(0, z, 0) for z in range(3)]
        for z in range(3):
            np.testing.assert_array_equal(plane(planes[PlaneIndex(0, z, 0)]), pixels[z])
        assert exported.metadata.pixel_type == "uint8"
        assert exported.metadata.significant_bits == 8
        # Three planes over 4 um are 2 um apart: the length spans the steps
        # between the planes, not the planes themselves.
        assert exported.metadata.physical_size_z_um == pytest.approx(2.0)

    def test_the_description_is_kept_as_the_vendors_own_account(self, cache):
        two_channel_12_bit(cache)

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        assert exported.source_exporter == "lasx_data_container"
        [kept] = exported.vendor_metadata_sources
        assert kept.name == "HiRes002.lasx-description.xml"
        assert b'Resolution="12"' in kept.data


class TestFindingThePicture:
    def test_the_picture_is_the_one_named_after_the_job(self, cache):
        three_plane_8_bit(cache, number=700, name="TileScan 4")
        two_channel_12_bit(cache, number=746, name="HiRes002")
        two_channel_12_bit(cache, number=748, name="HiRes002_pmd_0")

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        assert exported.relative_path == f"V2LMSDC_MMF746_0_{PID}.tmp"

    def test_a_job_whose_name_starts_the_same_is_not_mistaken_for_it(self, cache):
        """LAS X names a capture job + number: "HiRes2" makes "HiRes2001", which "HiRes" + digits matches too."""
        pixels = np.zeros((3, 4), np.uint8)
        for number, name, job in (
            (746, "HiRes2001", "HiRes2"),
            (790, "HiRes003", "HiRes"),
        ):
            write_pixels(cache, number, pixels)
            xml = description_xml(
                channels=[(8, "Gray", 0)],
                dims=[(1, 4, 1e-5, 1), (2, 3, 1e-5, 4)],
                time_stamps=1,
                job=job,
            )
            write_description(cache, number, name, xml)

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        assert exported.relative_path == f"V2LMSDC_MMF790_0_{PID}.tmp"

    def test_a_picture_from_before_the_acquisition_is_never_taken(self, cache):
        two_channel_12_bit(cache)
        old = time.time() - 3600
        for path in cache.iterdir():
            os.utime(path, (old, old))

        with pytest.raises(RuntimeError, match="HiRes"):
            container.collect_lasx_data_container(
                None, acquisition_of("HiRes"), cache_root=cache, timeout_s=0.3
            )

    def test_a_picture_let_go_of_is_passed_over_even_when_it_has_the_same_name(
        self, cache
    ):
        """LAS X keeps a released picture's description, rewritten, without its pixels (simulator, 2026-10-14).

        It names the next capture as the released one was ("HiRes009" twice),
        and rewrites the released description after the next capture began.
        """
        two_channel_12_bit(cache, number=6642, name="HiRes009")
        (cache / f"V2LMSDC_MMF6642_0_{PID}.tmp").unlink()
        two_channel_12_bit(cache, number=6662, name="HiRes009")

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        assert exported.relative_path == f"V2LMSDC_MMF6662_0_{PID}.tmp"

    def test_an_older_picture_whose_description_is_written_again_is_passed_over(
        self, cache
    ):
        """LAS X writes a picture's description again about 4 s after the capture (simulator, 2026-10-15).

        With two captures of one job close together, the first one's description
        was written again after the second began; its pixels were not.
        """
        two_channel_12_bit(cache, number=10119, name="AF Job011")
        old = time.time() - 30
        os.utime(cache / f"V2LMSDC_MMF10119_0_{PID}.tmp", (old, old))
        two_channel_12_bit(cache, number=10133, name="AF Job012")

        exported = container.collect_lasx_data_container(
            None, acquisition_of("AF Job"), cache_root=cache
        )

        assert exported.relative_path == f"V2LMSDC_MMF10133_0_{PID}.tmp"

    def test_two_new_pictures_of_the_job_are_refused_not_guessed(self, cache):
        two_channel_12_bit(cache, number=746, name="HiRes002")
        two_channel_12_bit(cache, number=790, name="HiRes003")

        with pytest.raises(RuntimeError, match="refusing to guess"):
            container.collect_lasx_data_container(
                None, acquisition_of("HiRes"), cache_root=cache
            )


class TestWaitingUntilFinished:
    def test_it_waits_until_every_plane_has_its_time_stamp(self, cache):
        two_channel_12_bit(cache, stamps=1)

        def finish():
            time.sleep(0.3)
            two_channel_12_bit(cache, stamps=2)

        threading.Thread(target=finish).start()
        started = time.perf_counter()
        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache, poll_interval_s=0.05
        )

        assert time.perf_counter() - started >= 0.25
        assert len(exported.positions[0].planes) == 2

    def test_a_picture_that_never_finishes_is_reported(self, cache):
        two_channel_12_bit(cache, stamps=1)

        with pytest.raises(RuntimeError, match="1 of 2 planes"):
            container.collect_lasx_data_container(
                None,
                acquisition_of("HiRes"),
                cache_root=cache,
                timeout_s=0.3,
                poll_interval_s=0.05,
            )


class TestLettingGo:
    """The collector names the picture to let go of; letting go is a procedure (``clear_cache``)."""

    def test_the_picture_read_is_the_one_named_to_be_let_go_of(self, cache):
        two_channel_12_bit(cache)

        exported = container.collect_lasx_data_container(
            None, acquisition_of("HiRes"), cache_root=cache
        )

        assert exported.release_target == (746, "HiRes002")


class TestRefusing:
    def test_a_dimension_other_than_x_y_z_is_refused(self, cache):
        write_pixels(cache, 746, np.zeros((2, 3, 4), np.uint8))
        xml = description_xml(
            channels=[(8, "Gray", 0)],
            dims=[(1, 4, 1e-5, 1), (2, 3, 1e-5, 4), (4, 2, 1.0, 12)],
            time_stamps=2,
        )
        write_description(cache, 746, "HiRes002", xml)

        with pytest.raises(RuntimeError, match="dimension 4"):
            container.collect_lasx_data_container(
                None, acquisition_of("HiRes"), cache_root=cache
            )

    def test_a_bit_depth_above_16_is_refused(self, cache):
        write_pixels(cache, 746, np.zeros((3, 4), np.uint32))
        xml = description_xml(
            channels=[(32, "Gray", 0)],
            dims=[(1, 4, 1e-5, 4), (2, 3, 1e-5, 16)],
            time_stamps=1,
        )
        write_description(cache, 746, "HiRes002", xml)

        with pytest.raises(RuntimeError, match="32-bit"):
            container.collect_lasx_data_container(
                None, acquisition_of("HiRes"), cache_root=cache
            )

    def test_rows_that_are_not_packed_are_refused(self, cache):
        write_pixels(cache, 746, np.zeros((3, 8), np.uint8))
        xml = description_xml(
            channels=[(8, "Gray", 0)],
            dims=[(1, 4, 1e-5, 1), (2, 3, 1e-5, 8)],
            time_stamps=1,
        )
        write_description(cache, 746, "HiRes002", xml)

        with pytest.raises(RuntimeError, match="not packed"):
            container.collect_lasx_data_container(
                None, acquisition_of("HiRes"), cache_root=cache
            )


class TestSaving:
    @pytest.fixture(autouse=True)
    def identity_orientation(self, monkeypatch):
        monkeypatch.setattr(
            "zmart_drivers.leica.stellaris.navigator_expert.driver_setup."
            "orientation.rig_orientation",
            Orientation,
        )

    def test_save_writes_one_ome_zarr_store_from_the_cache(self, cache, tmp_path):
        """Both channels land in one store, with the pixels exactly as LAS X held them."""
        pixels = two_channel_12_bit(cache)
        naming = Naming(stem="high-res_000001_000003")

        saved = acquisition.save(
            None,
            acquisition_of("HiRes"),
            tmp_path / "out",
            naming,
            cache_root=cache,
            placement=lambda _planes: (
                lambda z: {"x_um": 0.0, "y_um": 0.0, "z_um": float(z)}
            ),
        )

        assert len(saved.image_paths) == 2
        assert len(set(saved.image_paths.values())) == 1
        for c in range(2):
            np.testing.assert_array_equal(
                saved.read_plane(PlaneIndex(0, 0, c)), pixels[c]
            )
        store = next(iter(saved.image_paths.values()))
        ome = (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")
        assert "http://www.openmicroscopy.org/Schemas/OME/2016-06" in ome
        assert 'SignificantBits="12"' in ome
        assert 'Type="uint16"' in ome
        assert 'PhysicalSizeX="2.5"' in ome
