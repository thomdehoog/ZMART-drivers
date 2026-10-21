"""Reading LAS X's description of a picture into OME metadata.

The descriptions under ``testing/data/lasx_description`` are LAS X's own,
taken from the simulator's Data Container cache on 11 October 2026:
HiRes004 (two channels, 12 bit) and AF Job002 (a 17-plane stack, 16 bit).
Beside each lies the OME-XML LAS X's AutoSave wrote for the same picture,
so every value AutoSave gives can be checked against ours.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture import (
    metadata_map as described,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.save_data import (
    ome_canonical as canonical,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture.product import (
    AcquisitionMetadata,
    PlaneIndex,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "lasx_description"
HIRES = "hires004-two-channels-12-bit"
STACK = "af-job002-17-planes-16-bit"
OME = "{http://www.openmicroscopy.org/Schemas/OME/2016-06}"


def metadata_of(name: str) -> AcquisitionMetadata:
    return described.metadata_from_description(
        (DATA / f"{name}.xml").read_text(encoding="utf-8")
    )


def autosave_pixels(name: str) -> ET.Element:
    root = ET.fromstring(
        (DATA / f"{name}.autosave.ome.xml").read_text(encoding="utf-8")
    )
    return root.find(f".//{OME}Pixels")


def in_um(value: str, unit: str) -> float:
    return float(value) * {"m": 1e6, "mm": 1e3, "µm": 1.0, "nm": 1e-3}[unit]


class TestThePicture:
    def test_size_bit_depth_and_exact_pixel_size(self):
        metadata = metadata_of(HIRES)

        assert (metadata.size_x, metadata.size_y, metadata.size_z, metadata.size_c) == (
            512,
            512,
            1,
            2,
        )
        assert metadata.pixel_type == "uint16"
        assert metadata.significant_bits == 12
        # 1.1625 mm over 512 pixels, not the 2.27 the job settings round to.
        assert metadata.physical_size_x_um == 2.2705078125
        assert metadata.physical_size_y_um == 2.2705078125
        assert metadata.physical_size_z_um is None

    def test_the_z_step_is_the_distance_between_planes(self):
        metadata = metadata_of(STACK)

        assert metadata.size_z == 17
        assert metadata.physical_size_z_um == pytest.approx(38.55132 / 16)

    def test_a_stack_taken_top_down_has_a_positive_z_step_and_planes_running_down(self):
        # LAS X writes a top-down stack (begin above end) with the z axis
        # starting at the top and a negative length (STELLARIS, 2026-10-15:
        # Origin 12.25 um, Length -7.23 um). A pixel size is a distance, never
        # negative; the planes' heights keep the direction they were taken in.
        xml = (DATA / f"{STACK}.xml").read_text(encoding="utf-8")
        axis = re.search(r'<DimensionDescription DimID="3"[^>]*/>', xml).group(0)
        origin = float(re.search(r'Origin="([^"]+)"', axis).group(1))
        length = float(re.search(r'Length="([^"]+)"', axis).group(1))
        flipped = re.sub(r'Origin="[^"]+"', f'Origin="{origin + length!r}"', axis)
        flipped = re.sub(r'Length="[^"]+"', f'Length="{-length!r}"', flipped)
        metadata = described.metadata_from_description(xml.replace(axis, flipped))

        assert metadata.physical_size_z_um == pytest.approx(38.55132 / 16)
        heights = [
            metadata.planes[PlaneIndex(0, z, 0)].position_z_um for z in range(17)
        ]
        assert heights[0] == pytest.approx(-19.27566 + 38.55132)
        assert heights == sorted(heights, reverse=True)

    def test_the_time_it_was_taken_and_of_every_plane(self):
        metadata = metadata_of(STACK)

        assert metadata.acquisition_date == "2026-10-11T10:06:32.991000Z"
        delta = [metadata.planes[PlaneIndex(0, z, 0)].delta_t_s for z in range(17)]
        assert delta[:4] == pytest.approx([0.0, 1.291, 2.573, 3.866])
        assert delta == sorted(delta)

    def test_every_plane_says_where_it_was_taken(self):
        metadata = metadata_of(STACK)

        first, last = (
            metadata.planes[PlaneIndex(0, 0, 0)],
            metadata.planes[PlaneIndex(0, 16, 0)],
        )
        assert (first.position_x_um, first.position_y_um) == pytest.approx(
            (63500.0, 41500.0)
        )
        assert first.position_z_um == pytest.approx(-19.27566)
        assert last.position_z_um == pytest.approx(-19.27566 + 38.55132)


class TestTheChannels:
    def test_dye_laser_and_detection_band_of_each_channel(self):
        metadata = metadata_of(HIRES)
        violet, green = metadata.channel(0), metadata.channel(1)

        assert (violet.name, violet.fluor) == ("ALEXA 405", "ALEXA 405")
        assert violet.excitation_wavelength_nm == 405
        assert (green.name, green.excitation_wavelength_nm) == ("ALEXA 488", 497)
        bands = {f.id: (f.cut_in_nm, f.cut_out_nm) for f in metadata.instrument.filters}
        assert [bands[i] for i in violet.emission_filter_ids] == [(410, 487)]
        assert [bands[i] for i in green.emission_filter_ids] == [(502, 779)]

    def test_detector_and_its_settings(self):
        metadata = metadata_of(HIRES)
        detectors = {d.id: d for d in metadata.instrument.detectors}

        violet, green = metadata.channel(0), metadata.channel(1)
        assert detectors[violet.detector_id].model == "HyD 2"
        assert detectors[green.detector_id].model == "HyD S 3"
        assert (violet.detector_gain, violet.detector_offset) == (2.5, 0.0)

    def test_the_laser_and_how_much_of_it_reached_the_sample(self):
        metadata = metadata_of(HIRES)
        lasers = {laser.id: laser for laser in metadata.instrument.lasers}

        violet, green = metadata.channel(0), metadata.channel(1)
        assert lasers[violet.laser_id].model == "Laser 405"
        assert lasers[violet.laser_id].wavelength_nm == 405
        assert lasers[green.laser_id].model == "WLL"
        assert lasers[green.laser_id].tuneable is True
        # An intensity of 1.96 % takes 98.04 % of the light away.
        assert violet.laser_attenuation == pytest.approx(1 - 0.019607843137254901)

    def test_pinhole_colour_and_mode(self):
        violet = metadata_of(HIRES).channel(0)

        assert violet.pinhole_um == pytest.approx(398.0125)
        assert violet.color == 0x0000FFFF
        assert violet.acquisition_mode == "LaserScanningConfocalMicroscopy"


class TestTheInstrument:
    def test_the_microscope_and_its_objective(self):
        instrument = metadata_of(HIRES).instrument

        assert instrument.manufacturer == "Leica Microsystems"
        assert instrument.serial_number == "STELLARIS SIMULATOR"
        assert instrument.type == "Upright"
        objective = instrument.objective
        assert objective.model == "HC PL APO CS 10x/0.40 DRY"
        assert (objective.magnification, objective.lens_na) == (10.0, 0.4)
        assert (objective.immersion, objective.correction) == ("Air", "PlanApo")
        assert objective.refractive_index == 1.0

    def test_only_what_took_part_is_listed(self):
        instrument = metadata_of(HIRES).instrument

        assert sorted(d.model for d in instrument.detectors) == ["HyD 2", "HyD S 3"]
        assert sorted(laser.model for laser in instrument.lasers) == [
            "Laser 405",
            "WLL",
        ]


class TestWhatOmeHasNoFieldFor:
    def test_the_scan_settings_are_kept_by_name(self):
        facts = dict(metadata_of(HIRES).annotations)

        assert facts["job"] == "HiRes"
        assert facts["zoom"] == "1"
        assert facts["scan speed (Hz)"] == "400"
        assert facts["pixel dwell time (s)"] == "1.44125e-06"
        assert facts["line average"] == "1"
        assert facts["frame accumulation"] == "1"
        assert facts["pinhole (Airy units)"] == "0.99996859493750412"
        assert facts["objective order number"] == "506511"
        assert facts["software"] == "LAS X [ BETA ] 4.9.0.30051"
        assert facts["filter wheel Notch FW 2"] == "SMD1 NF 405/470"
        assert facts["channel 0 detector mode"] == "PhotonCounting"
        assert facts["channel 0 laser intensity (%)"] == "1.9607843137254901"
        assert facts["z drive of the stack"] == "z-galvo"


class TestTheOmeWritten:
    @pytest.mark.parametrize("name", [HIRES, STACK])
    def test_every_value_autosave_writes_is_written_the_same(self, name):
        """Parity: nothing LAS X's own OME-TIFF says is lost or different."""
        metadata = metadata_of(name)
        theirs = autosave_pixels(name)

        size_c = int(theirs.get("SizeC"))
        ours = ET.fromstring(canonical.image_xml(metadata, image_name="p"))
        pixels = ours.find(f".//{OME}Pixels")
        for key in ("SizeX", "SizeY", "SizeC", "SizeZ", "Type", "SignificantBits"):
            assert pixels.get(key) == theirs.get(key), key
        for axis in "XY":
            assert math.isclose(
                float(pixels.get(f"PhysicalSize{axis}")),
                in_um(
                    theirs.get(f"PhysicalSize{axis}"),
                    theirs.get(f"PhysicalSize{axis}Unit"),
                ),
                rel_tol=1e-12,
            )
        our_planes = {(p.get("TheZ"), p.get("TheC")): p for p in ours.iter(f"{OME}Plane")}
        for plane in theirs.iter(f"{OME}Plane"):
            index = (plane.get("TheZ"), plane.get("TheC"))
            our_plane = our_planes[index]
            for axis in "XYZ":
                assert in_um(
                    our_plane.get(f"Position{axis}"),
                    our_plane.get(f"Position{axis}Unit"),
                ) == (
                    pytest.approx(
                        in_um(
                            plane.get(f"Position{axis}"),
                            plane.get(f"Position{axis}Unit"),
                        ),
                        rel=1e-6,
                        abs=1e-6,
                    )
                ), (index, axis)
        assert size_c == metadata.size_c

    @pytest.mark.parametrize("name", [HIRES, STACK])
    def test_it_passes_the_ome_2016_06_schema(self, name):
        ome_types = pytest.importorskip("ome_types")
        metadata = metadata_of(name)

        xml = canonical.image_xml(
            metadata, image_name="p", state={"software": {"name": "ZMART"}}
        )
        ome = ome_types.from_xml(xml.decode("ascii"), validate=True)
        image = ome.images[0]
        assert image.acquisition_date is not None
        assert all(channel.fluor for channel in image.pixels.channels)
        assert ome.instruments[0].objectives[0].lens_na == 0.4

    def test_the_file_text_is_plain_ascii(self):
        metadata = metadata_of(HIRES)

        xml = canonical.image_xml(metadata, image_name="p")

        xml.decode("ascii")
        assert b"&#181;m" in xml

    def test_each_channel_carries_its_dye_and_detector_and_the_instrument(self):
        metadata = metadata_of(HIRES)

        root = ET.fromstring(canonical.image_xml(metadata, image_name="p"))

        channel = list(root.iter(f"{OME}Channel"))[1]
        assert channel.get("Fluor") == "ALEXA 488"
        assert channel.get("ExcitationWavelength") == "497"
        detector_ref = channel.find(f"{OME}DetectorSettings").get("ID")
        models = {d.get("ID"): d.get("Model") for d in root.iter(f"{OME}Detector")}
        assert models[detector_ref] == "HyD S 3"
        facts = {m.get("K"): m.text for m in root.iter(f"{OME}M")}
        assert facts["zoom"] == "1"


def with_z_wide_at(
    name: str, metres: str, *, stack_drive: str | None = None
) -> AcquisitionMetadata:
    """A fixture as if the z-wide drive had stood at *metres* (the simulator keeps it at 0)."""
    text = (DATA / f"{name}.xml").read_text(encoding="utf-8")
    text = text.replace(
        'ZUseModeName="z-wide" ZPosition="0"',
        f'ZUseModeName="z-wide" ZPosition="{metres}"',
    )
    if stack_drive is not None:
        text = text.replace(
            'ZUseMode="1" ZUseModeName="z-galvo"',
            f'ZUseMode="2" ZUseModeName="{stack_drive}"',
        )
    return described.metadata_from_description(text)


class TestEveryPlaneSaysWhereEachDriveStood:
    def test_stage_and_both_z_drives_per_plane(self):
        metadata = metadata_of(STACK)

        first = dict(metadata.planes[PlaneIndex(0, 0, 0)].actuators)
        last = dict(metadata.planes[PlaneIndex(0, 16, 0)].actuators)
        assert first["stage x (µm)"] == pytest.approx(63500.0)
        assert first["stage y (µm)"] == pytest.approx(41500.0)
        # The galvo made the stack: it moved from plane to plane...
        assert first["z-galvo (µm)"] == pytest.approx(-19.27566)
        assert last["z-galvo (µm)"] == pytest.approx(19.27566)
        # ...while z-wide stood still.
        assert first["z-wide (µm)"] == last["z-wide (µm)"] == 0.0

    def test_the_plane_height_is_both_z_drives_together(self):
        metadata = with_z_wide_at(STACK, "0.0009")

        first, last = (
            metadata.planes[PlaneIndex(0, 0, 0)],
            metadata.planes[PlaneIndex(0, 16, 0)],
        )
        assert dict(first.actuators)["z-wide (µm)"] == pytest.approx(900.0)
        assert first.position_z_um == pytest.approx(900.0 - 19.27566)
        assert last.position_z_um == pytest.approx(900.0 + 19.27566)

    def test_a_stack_made_by_z_wide_moves_z_wide_and_keeps_the_galvo(self):
        metadata = with_z_wide_at(STACK, "0.0009", stack_drive="z-wide")

        first = dict(metadata.planes[PlaneIndex(0, 0, 0)].actuators)
        last = dict(metadata.planes[PlaneIndex(0, 16, 0)].actuators)
        assert first["z-wide (µm)"] == pytest.approx(-19.27566)
        assert last["z-wide (µm)"] == pytest.approx(19.27566)
        assert (
            first["z-galvo (µm)"]
            == last["z-galvo (µm)"]
            == pytest.approx(19.275422269005238)
        )

    def test_the_written_plane_carries_each_drive(self):
        ome_types = pytest.importorskip("ome_types")
        metadata = with_z_wide_at(HIRES, "0.0012")

        xml = canonical.image_xml(metadata, image_name="p")

        ome = ome_types.from_xml(xml.decode("ascii"), validate=True)
        [plane] = [p for p in ome.images[0].pixels.planes if p.the_z == 0 and p.the_c == 1]
        assert plane.position_z == pytest.approx(1200.0, abs=1e-3)
        [ref] = plane.annotation_refs
        drives = next(
            a for a in ome.structured_annotations.map_annotations if a.id == ref.id
        )
        values = {m.k: m.value for m in drives.value.ms}
        assert float(values["z-wide (µm)"]) == pytest.approx(1200.0)
        assert float(values["stage x (µm)"]) == pytest.approx(63500.0)
        assert "z-galvo (µm)" in values
