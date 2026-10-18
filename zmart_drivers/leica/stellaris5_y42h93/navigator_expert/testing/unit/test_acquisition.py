"""Tests for the explicit acquire() -> save() workflow.

``acquire`` starts the capture on the microscope and ``save`` writes it to
disk as one OME-Zarr image. These tests use a pretend LAS X cache, so they
run anywhere, and they check what a biologist would care about: the pixels
come back unchanged (or turned the way the rig needs), the descriptions of
the capture are kept, and LAS X is only told to let go of a capture once
our copy has been checked.

Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of
Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
import tifffile
import zarr

import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as drv
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.image_stage_registration import (
    Orientation,
    reorient_array,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.naming import (
    Naming,
    build_image_name,
    parse_image_name,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.product import (
    ExportedAcquisition,
    ExportedPosition,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    acquire as capture,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    ome_canonical,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    save as acquisition,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.primitive_handling import (
    state as readers_router,
)


@pytest.fixture(autouse=True)
def _identity_rig_orientation(monkeypatch):
    monkeypatch.setattr(
        "zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.image_stage_registration.rig_orientation",
        Orientation,
    )


@pytest.fixture
def naming() -> Naming:
    return Naming(stem="overview-scan_000001_000003")


def test_leica_private_naming_round_trips_time_channel_and_z():
    naming = Naming(
        stem="overview_abc123_K00_M000000_G000000_P000000_V00",
        t=12,
        c=2,
        z=34,
    )
    assert build_image_name(naming).endswith("_T000012_C02_Z00034.ome.tiff")
    assert parse_image_name(build_image_name(naming)) == naming


@pytest.mark.parametrize(("field", "value"), [("t", 1_000_000), ("c", 100), ("z", -1)])
def test_leica_private_naming_rejects_unrepresentable_plane_indices(field, value):
    with pytest.raises(ValueError, match=field):
        Naming(
            stem="overview_abc123_P0",
            **{field: value},
        )


def _metadata() -> drv.AcquisitionMetadata:
    return drv.AcquisitionMetadata(
        size_x=16,
        size_y=16,
        size_t=1,
        size_z=1,
        size_c=1,
        pixel_type="uint8",
        physical_size_x_um=1.0,
        physical_size_y_um=1.0,
        channels=(drv.ChannelMetadata(index=0, name="C0"),),
    )


def _grid_metadata() -> drv.AcquisitionMetadata:
    return drv.AcquisitionMetadata(
        size_x=16,
        size_y=16,
        size_t=1,
        size_z=2,
        size_c=2,
        pixel_type="uint8",
        physical_size_x_um=1.0,
        physical_size_y_um=1.0,
        channels=(
            drv.ChannelMetadata(index=0, name="C0"),
            drv.ChannelMetadata(index=1, name="C1"),
        ),
    )


def _job_settings(
    *,
    pixel_size: str = "2.27 um x 2.28 um",
    stack: dict | None = None,
) -> dict:
    return {
        "zoom": {},
        "scanSpeed": {},
        "activeSettings": [],
        "scanMode": "xyz" if stack is not None else "xy",
        "imageSize": "1160 um x 1160 um",
        "pixelSize": pixel_size,
        "format": "512 x 512",
        "xyStage": {},
        "stack": stack,
    }


@pytest.fixture
def fake_export(tmp_path: Path) -> dict:
    """One realistic flat plane grid mapped to a writer-agnostic product."""
    source_root = tmp_path / "media"
    source_dir = source_root / "experiment--demo"
    metadata_dir = source_dir / "metadata"
    source_dir.mkdir(parents=True)
    metadata_dir.mkdir()

    xml_path = metadata_dir / "source.ome.xml"
    xml_path.write_bytes(b"<OME/>")

    plane_paths = {}
    for z in range(2):
        for c in range(2):
            idx = drv.PlaneIndex(t=0, z=z, c=c)
            image_path = source_dir / f"image--Z{z:02d}--C{c:02d}.ome.tif"
            tifffile.imwrite(
                str(image_path),
                np.full((16, 16), 10 * z + c, dtype=np.uint8),
            )
            plane_paths[idx] = image_path

    exported = ExportedAcquisition(
        source_root=source_root,
        source_dir=source_dir,
        positions=[
            ExportedPosition(
                t=0,
                planes={idx: drv.PlaneSource(path=p) for idx, p in plane_paths.items()},
            )
        ],
        metadata=_grid_metadata(),
        method="test",
        source_exporter="lasx_data_container",
        vendor_metadata_sources=(
            drv.VendorMetadataSource(name="source.ome.xml", path=xml_path),
        ),
    )

    return {
        "source_root": source_root,
        "image_path": plane_paths[drv.PlaneIndex(t=0, z=0, c=0)],
        "plane_paths": plane_paths,
        "image_paths": [p for _idx, p in sorted(plane_paths.items())],
        "xml_path": xml_path,
        "exported": exported,
    }


@pytest.fixture
def successful_acq() -> capture.AcquisitionResult:
    return capture.AcquisitionResult(
        job="HiRes",
        started_at=time.time() - 1,
        finished_at=time.time(),
        command_result={"success": True, "message": "ok"},
    )


@pytest.fixture
def patched_export(fake_export, monkeypatch):
    """Pretend LAS X's cache holds the capture; everything after that is real."""
    collect = Mock(return_value=fake_export["exported"])
    monkeypatch.setattr(acquisition, "collect_lasx_data_container", collect)
    return {**fake_export, "collect": collect}


def _placement(_planes):
    """Put every plane at x=0, y=0, with its own z number as its height."""
    return lambda z: {"x_um": 0.0, "y_um": 0.0, "z_um": float(z)}


def _save(acq, output_root, naming, **kwargs):
    """``drv.save`` with a placement, which an OME-Zarr store needs."""
    return drv.save(None, acq, output_root, naming, placement=_placement, **kwargs)


def _store_xml(store: Path) -> str:
    """The OME-XML description stored inside an OME-Zarr image."""
    return (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")


class TestAcquire:
    def test_acquire_returns_save_agnostic_token(self):
        with patch.object(
            capture._commands,
            "acquire",
            return_value={"success": True, "receipt": "ok"},
        ) as command:
            result = drv.acquire("client", "HiRes")

        command.assert_called_once_with(
            "client",
            "HiRes",
            poll_interval=None,
            poll_timeout=None,
            heartbeat_interval=None,
            start_timeout=None,
        )
        assert isinstance(result, drv.AcquisitionResult)
        assert result.job == "HiRes"
        assert result.command_result == {"success": True, "receipt": "ok"}
        assert result.started_at <= result.finished_at

    def test_acquire_raises_on_command_failure(self):
        with (
            patch.object(
                capture._commands,
                "acquire",
                return_value={"success": False, "error": "blocked"},
            ),
            pytest.raises(RuntimeError, match="acquire failed"),
        ):
            drv.acquire("client", "HiRes")

    def test_acquire_has_no_file_or_save_side_effects(self):
        with (
            patch.object(
                capture._commands,
                "acquire",
                return_value={"success": True},
            ),
            patch.object(
                acquisition._files,
                "read_relative_path",
            ) as read_relative_path,
            patch.object(
                acquisition,
                "_persist_store",
            ) as persist,
        ):
            drv.acquire("client", "HiRes")

        read_relative_path.assert_not_called()
        persist.assert_not_called()


class TestCanonicalPhysicalMetadataAuthority:
    def test_job_settings_override_vendor_physical_sizes(self):
        metadata = replace(
            _metadata(),
            physical_size_x_um=9.0,
            physical_size_y_um=9.0,
            physical_size_z_um=1.606305,
        )
        settings = _job_settings(
            stack={"begin": -0.0, "end": 4.82, "sections": 3},
        )

        with patch.object(
            readers_router,
            "get_job_settings",
            return_value=settings,
        ) as read_settings:
            out = ome_canonical.metadata_with_job_physical_sizes(
                metadata,
                "client",
                "Overview",
            )

        assert "mode" not in read_settings.call_args.kwargs
        assert out.physical_size_x_um == pytest.approx(2.27)
        assert out.physical_size_y_um == pytest.approx(2.28)
        assert out.physical_size_z_um == pytest.approx(2.41)

    def test_z_spacing_survives_schema_degraded_settings(self):
        """The stack parser is independent of the full-schema parse: a
        settings dict missing the required top-level keys (which would
        fail make_changeable_copy's schema guard) still yields z spacing
        as long as its stack section is usable."""
        metadata = replace(_metadata(), physical_size_z_um=1.0)
        degraded = {"stack": {"begin": 95.0, "end": 105.0, "sections": 3}}

        with patch.object(
            readers_router,
            "get_job_settings",
            return_value=degraded,
        ):
            out = ome_canonical.metadata_with_job_physical_sizes(
                metadata,
                "client",
                "Overview",
            )

        assert out.physical_size_z_um == pytest.approx(5.0)

    def test_single_section_stack_overrides_vendor_z_to_none(self):
        metadata = replace(_metadata(), physical_size_z_um=1.0)
        settings = _job_settings(
            stack={"begin": 10.0, "end": 10.0, "sections": 1},
        )

        with patch.object(
            readers_router,
            "get_job_settings",
            return_value=settings,
        ):
            out = ome_canonical.metadata_with_job_physical_sizes(
                metadata,
                "client",
                "Overview",
            )

        assert out.physical_size_z_um is None

    def test_job_settings_unavailable_falls_back_to_vendor_metadata(self):
        """The read waits by the one rule; when no settings arrive, vendor values stay."""
        metadata = replace(
            _metadata(),
            physical_size_x_um=9.0,
            physical_size_y_um=9.0,
            physical_size_z_um=9.0,
        )
        with patch.object(readers_router, "get_job_settings", return_value=None):
            out = ome_canonical.metadata_with_job_physical_sizes(
                metadata, "client", "Overview"
            )
        assert out == metadata


class TestSave:
    def test_save_applies_the_active_rig_orientation_by_default(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
        monkeypatch,
    ):
        """The microscope's camera may be turned or mirrored relative to its stage.

        Saving turns every plane the same way, so the picture lines up with
        the stage. The planes read back from the store are the turned ones.
        """
        orientation = Orientation(rotate_deg=90, mirrored=True)
        monkeypatch.setattr(
            "zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.image_stage_registration.rig_orientation",
            lambda: orientation,
        )

        saved = _save(successful_acq, tmp_path / "out", naming)

        for index, source_path in patched_export["plane_paths"].items():
            source = tifffile.imread(source_path)
            assert np.array_equal(
                saved.read_plane(index), reorient_array(source, orientation)
            )

    def test_timepoints_are_kept_apart_inside_one_store(
        self, successful_acq, tmp_path, naming, monkeypatch
    ):
        """A time series is one image: every timepoint is a plane of the same store."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        planes = {}
        positions = []
        for t in (0, 1):
            index = drv.PlaneIndex(t=t, z=0, c=0)
            source = source_root / f"time-{t}.tiff"
            tifffile.imwrite(source, np.full((16, 16), t + 5, dtype=np.uint8))
            plane = drv.PlaneSource(path=source)
            planes[index] = plane
            positions.append(ExportedPosition(t=t, planes={index: plane}))
        exported = ExportedAcquisition(
            source_root=source_root,
            source_dir=source_root,
            positions=positions,
            metadata=replace(_metadata(), size_t=2),
            method="test",
        )
        monkeypatch.setattr(
            acquisition, "collect_lasx_data_container", lambda *a, **k: exported
        )

        saved = _save(successful_acq, tmp_path / "out", naming)

        assert len(saved.image_paths) == 2
        assert len(set(saved.image_paths.values())) == 1
        assert saved.read_plane(drv.PlaneIndex(t=0, z=0, c=0))[0, 0] == 5
        assert saved.read_plane(drv.PlaneIndex(t=1, z=0, c=0))[0, 0] == 6

    def test_save_persists_the_store_and_summary(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """The whole capture is one ``.ome.zarr`` folder in ``data``, listed in summary.json."""
        output_root = tmp_path / "run_000001"

        result = _save(successful_acq, output_root, naming)

        expected_store = output_root / "data" / f"{naming.stem}.ome.zarr"
        assert isinstance(result, drv.SavedAcquisition)
        assert set(result.image_paths) == set(patched_export["plane_paths"])
        assert set(result.image_paths.values()) == {expected_store}
        assert expected_store.is_dir()
        assert result.xml_paths == {}
        assert zarr.open_group(str(expected_store), mode="r")["0"].shape == (
            1,
            2,
            2,
            16,
            16,
        )

        summary = json.loads((output_root / "summary.json").read_text())
        assert len(summary["acquisitions"]) == 1
        rec = summary["acquisitions"][0]
        assert rec["image_path"] == f"data/{naming.stem}.ome.zarr"
        assert rec["format"] == "ome-zarr"
        assert "xml_path" not in rec
        assert rec["source_exporter"] == "lasx_data_container"
        assert rec["naming"]["stem"] == "overview-scan_000001_000003"

    def test_save_without_a_placement_is_refused(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """A store records where the image lies on the sample; guessing would mislead."""
        with pytest.raises(ValueError, match="placed by its canvas position"):
            drv.save(None, successful_acq, tmp_path / "out", naming)

    def test_save_does_not_issue_microscope_acquire(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        with patch.object(capture._commands, "acquire") as command:
            _save(successful_acq, tmp_path / "run_000001", naming)
        command.assert_not_called()

    def test_save_plumbs_export_completion_timeout(
        self,
        successful_acq,
        tmp_path,
        naming,
        monkeypatch,
    ):
        collect = Mock(side_effect=RuntimeError("stop after kwargs"))
        monkeypatch.setattr(acquisition, "collect_lasx_data_container", collect)
        with pytest.raises(RuntimeError, match="stop after kwargs"):
            _save(
                successful_acq,
                tmp_path / "run_000001",
                naming,
                export_completion_timeout_s=12.0,
                export_completion_poll_interval_s=0.25,
            )

        assert collect.call_args.kwargs["timeout_s"] == 12.0
        assert collect.call_args.kwargs["poll_interval_s"] == 0.25

    def test_lineage_passes_through(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        lineage = {"source_tile_rid": 1, "row": 2, "col": 3}
        output_root = tmp_path / "run_000001"
        _save(successful_acq, output_root, naming, lineage=lineage)
        summary = json.loads((output_root / "summary.json").read_text())
        assert summary["acquisitions"][0]["lineage"] == lineage

    def test_a_capture_with_no_way_to_be_let_go_of_is_refused_and_left_alone(
        self,
        patched_export,
        tmp_path,
        successful_acq,
        naming,
    ):
        """Only LAS X lets go of a capture; nothing is ever deleted by the driver itself.

        A source that gives no way for LAS X to let go of it (``release``) is
        refused when releasing is asked for, before anything is saved, and its
        files stay as they are.
        """
        with pytest.raises(RuntimeError, match="cleanup_cache"):
            _save(
                successful_acq,
                tmp_path / "run_000001",
                naming,
                cleanup_cache=True,
            )
        assert all(p.is_file() for p in patched_export["image_paths"])
        assert patched_export["xml_path"].is_file()
        assert not (tmp_path / "run_000001").exists()

    def test_cleanup_lets_go_of_a_source_that_says_how(
        self,
        patched_export,
        tmp_path,
        successful_acq,
        naming,
    ):
        """A source LAS X holds (its cache) is let go of through LAS X, its files left alone."""
        release = Mock()
        patched_export["collect"].return_value = replace(
            patched_export["exported"], release=release
        )
        _save(successful_acq, tmp_path / "run_000001", naming, cleanup_cache=True)
        release.assert_called_once_with()
        assert all(p.is_file() for p in patched_export["image_paths"])

    def test_a_capture_whose_copy_does_not_read_back_is_never_let_go_of(
        self,
        patched_export,
        tmp_path,
        successful_acq,
        naming,
        monkeypatch,
    ):
        """LAS X lets go of a capture only once our copy is on disk and reads back the same.

        If the pixels read back differ, the save stops with an error, and the
        capture stays in LAS X's cache (and its canvas is not cleared).
        """
        # Every read-back comparison says the pixels differ.
        monkeypatch.setattr(np, "array_equal", lambda *_args, **_kwargs: False)
        release = Mock()
        patched_export["collect"].return_value = replace(
            patched_export["exported"], release=release
        )
        with pytest.raises(RuntimeError, match="read back"):
            _save(successful_acq, tmp_path / "run_000001", naming, cleanup_cache=True)
        release.assert_not_called()
        assert all(p.is_file() for p in patched_export["image_paths"])

    def test_save_embeds_state_in_the_stores_ome_xml_when_provided(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """The machine and software state travels inside the image, in its OME-XML."""
        state = {
            "software": {"driver_version": "6.0.0"},
            "hardware": {"Microscope": {"name": "DM Manual-6"}},
            "provenance": {
                "folder": "overview-scan",
                "position_label": "000003",
                "acquisition_hash": "9k2m4p",
                "session_hash6": "000abc",
                "exported_at": "2026-07-07T00:00:00+00:00",
            },
        }

        saved = _save(successful_acq, tmp_path / "run_000001", naming, state=state)

        embedded = _store_xml(next(iter(saved.image_paths.values())))
        assert "StructuredAnnotations" in embedded
        assert 'ID="Annotation:zmart-state-map"' in embedded
        assert 'ID="Annotation:zmart-state-json"' in embedded
        assert 'Namespace="https://zmart-microscopy/state"' in embedded
        # Both the short list of highlights and the full dump are present.
        assert 'K="position_label"' in embedded
        assert '"driver_version": "6.0.0"' in embedded

    def test_save_omits_state_annotations_when_not_provided(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        saved = _save(successful_acq, tmp_path / "run_000001", naming)
        embedded = _store_xml(next(iter(saved.image_paths.values())))
        assert 'Namespace="https://zmart-microscopy/state"' not in embedded

    def test_save_has_no_channel_selector(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        with pytest.raises(TypeError, match="channel"):
            _save(successful_acq, tmp_path / "run_000001", naming, channel=0)

    def test_save_has_no_choice_of_file_format(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """OME-Zarr is the only format, so asking for another one is an error."""
        with pytest.raises(TypeError, match="format"):
            _save(successful_acq, tmp_path / "run_000001", naming, format="ome-tiff")

    def test_every_z_plane_and_channel_is_kept_separately(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        saved = _save(successful_acq, tmp_path / "run_000001", naming)
        for index, source_path in patched_export["plane_paths"].items():
            plane = saved.read_plane(index)
            assert plane.shape == (16, 16)
            assert np.array_equal(plane, tifffile.imread(source_path))

    def test_save_preserves_caller_owned_position_label(
        self,
        patched_export,
        successful_acq,
        tmp_path,
    ):
        naming = Naming(stem="overview-scan_000001_well_B7")

        saved = _save(successful_acq, tmp_path / "run_000001", naming)

        assert {path.name for path in saved.image_paths.values()} == {
            "overview-scan_000001_well_B7.ome.zarr"
        }

    def test_save_leaves_the_lasx_source_files_untouched(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """The driver only reads LAS X's files; it never changes them."""
        before = {p: p.read_bytes() for p in patched_export["image_paths"]}
        _save(successful_acq, tmp_path / "run_000001", naming)
        assert {p: p.read_bytes() for p in before} == before

    def test_the_stores_ome_xml_is_valid_under_ome_types(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """The description inside the store, including the state block, follows the OME schema."""
        # ome-types and lxml are required dependencies of the driver, so this
        # check never silently skips.
        from ome_types import from_xml

        state = {
            "software": {"driver_version": "6.0.0"},
            "provenance": {"folder": "overview-scan", "position_label": "000003"},
        }
        saved = _save(successful_acq, tmp_path / "run_000001", naming, state=state)

        store = next(iter(saved.image_paths.values()))
        from_xml(store / "OME" / "METADATA.ome.xml", validate=True)

    def test_vendor_metadata_is_preserved_as_provenance(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        output_root = tmp_path / "run_000001"
        _save(successful_acq, output_root, naming)
        summary = json.loads((output_root / "summary.json").read_text())
        rec = summary["acquisitions"][0]

        assert rec["canonical_metadata"] is True
        assert rec["vendor_metadata"]
        for item in rec["vendor_metadata"]:
            assert (output_root / item["path"]).is_file()
            assert item["sha256"]

    def test_the_images_have_a_folder_of_their_own_inside_the_acquisition(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """What the microscope captured, and everything describing it, in ``data``.

        What is made from the capture -- its projection (this capture is a
        stack), later a stitched view or an analysis -- is a folder beside
        ``data`` and is never confused with it. Inside, the image store sits
        loose and everything about it is under ``metadata``: ZMART's account
        of the capture, and the vendor's own beneath it.
        """
        output_root = tmp_path / "run_000001"

        _save(successful_acq, output_root, naming, state={"software": {}})

        data = output_root / "data"
        assert {path.name for path in output_root.iterdir()} == {
            "data",
            "projections",
            "summary.json",
        }
        assert {path.name for path in data.iterdir()} == {
            f"{naming.stem}.ome.zarr",
            "metadata",
        }
        metadata = data / "metadata"
        assert {path.name for path in metadata.iterdir()} == {"ZMART_state", "vendor"}
        assert (metadata / "vendor" / "lasx_data_container").is_dir()

    def test_a_stack_also_gets_its_max_projection_as_a_store(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """The brightest value over z is saved beside ``data``, as a one-plane store."""
        output_root = tmp_path / "run_000001"

        saved = _save(successful_acq, output_root, naming)

        projection = output_root / "projections" / f"{naming.stem}.ome.zarr"
        assert saved.projection_paths == (projection,)
        pixels = zarr.open_group(str(projection), mode="r")["0"][...]
        assert pixels.shape == (1, 2, 1, 16, 16)
        assert np.array_equal(pixels[0, :, 0, 0, 0], [10, 11])

    def test_the_state_is_printed_beside_the_images_as_metadata(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """One state file per acquisition, in ``data/metadata``.

        The state is also embedded in the store's OME-XML, but reading it
        there costs opening the image and parsing annotations. Printed once
        beside the image it can simply be read -- by an operator, by a
        workflow, by anything that never wants the pixels. It is named for
        the acquisition it belongs to, so it is one per position and
        timepoint.
        """
        output_root = tmp_path / "run_000001"
        state = {
            "software": {"driver_version": "6.0.0"},
            "provenance": {"folder": "overview-scan"},
        }

        saved = _save(successful_acq, output_root, naming, state=state)

        metadata = output_root / "data" / "metadata" / "ZMART_state"
        printed = list(metadata.iterdir())
        assert [path.name for path in printed] == [
            "overview-scan_000001_000003_T000000_ZMART_state.json"
        ]
        assert saved.state_paths == tuple(printed)
        assert json.loads(printed[0].read_text(encoding="utf-8")) == state

    def test_no_state_is_printed_when_the_caller_captured_none(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """Nothing is invented: no state captured, no state file, no empty folder."""
        output_root = tmp_path / "run_000001"

        _save(successful_acq, output_root, naming)

        assert not (output_root / "data" / "metadata" / "ZMART_state").exists()

    def test_saving_over_an_existing_store_is_refused_and_summary_keeps_one_record(
        self,
        patched_export,
        successful_acq,
        tmp_path,
        naming,
    ):
        """A capture is never written over another one: a second save under the same name stops."""
        output_root = tmp_path / "run_000001"
        _save(successful_acq, output_root, naming)
        with pytest.raises(RuntimeError, match="already exists"):
            _save(successful_acq, output_root, naming)
        summary = json.loads((output_root / "summary.json").read_text())
        assert len(summary["acquisitions"]) == 1


class TestHelpers:
    def test_exported_acquisition_source_files_are_deduplicated(self, tmp_path):
        tiff = tmp_path / "native.ome.tif"
        xml = tmp_path / "source.ome.xml"
        xml.write_bytes(b"<OME/>")
        exported = ExportedAcquisition(
            source_root=tmp_path,
            source_dir=tmp_path,
            positions=[
                ExportedPosition(
                    t=0,
                    planes={
                        drv.PlaneIndex(t=0, z=0, c=0): drv.PlaneSource(
                            path=tiff, page_index=0
                        ),
                        drv.PlaneIndex(t=0, z=0, c=1): drv.PlaneSource(
                            path=tiff, page_index=1
                        ),
                    },
                )
            ],
            metadata=drv.AcquisitionMetadata(
                size_x=8,
                size_y=8,
                size_t=1,
                size_z=1,
                size_c=2,
                pixel_type="uint8",
            ),
            method="test",
            vendor_metadata_sources=(
                drv.VendorMetadataSource(name="source.ome.xml", path=xml),
            ),
        )

        assert exported.image_files == [tiff]
        assert exported.metadata_files == [xml]
        assert exported.source_files == [tiff, xml]
