"""How the camera's D4 orientation relative to the stage is applied at save time.

Checks quarter-turn and mirror mechanics, config resolution, and the save-time
integration. Every D4 transform is lossless; image dimensions and physical pixel
sizes follow any axis swap. No rig value is hard-coded here.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile
import zarr

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup import orientation as orient
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.acquisition_and_saving import ome_zarr
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.product import AcquisitionMetadata, ChannelMetadata, ExportedAcquisition, ExportedPosition, PlaneIndex, PlaneSource

Orientation = orient.Orientation


# --- schema + config resolution -------------------------------------------


def test_rejects_in_between_angle():
    with pytest.raises(ValueError, match="quarter-turn"):
        Orientation(rotate_deg=45)


def test_identity_and_swaps_axes():
    assert Orientation().is_identity
    assert Orientation(rotate_deg=90).swaps_axes
    assert Orientation(rotate_deg=270).swaps_axes
    assert not Orientation(rotate_deg=180).swaps_axes
    assert not Orientation(mirrored=True).is_identity


def test_load_orientation(tmp_path):
    p = tmp_path / "orientation.json"
    p.write_text('{"schema_version": 1, "rotate_deg": 180}')
    assert orient.load_orientation(p) == Orientation(rotate_deg=180)


def test_current_schema_stores_only_authoritative_values(tmp_path):
    orientation = Orientation(rotate_deg=270, mirrored=True)
    payload = orient.orientation_config(orientation)
    assert payload == {
        "schema_version": 3,
        "measured": True,
        "rotation_deg": 270,
        "reflection": True,
        "sign_convention": {
            "stage_x_from_image": "+Y",
            "stage_y_from_image": "+X",
        },
    }
    p = tmp_path / "orientation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    assert orient.load_orientation(p) == orientation


def test_current_schema_rejects_contradictory_sign_convention(tmp_path):
    payload = orient.orientation_config(Orientation(rotate_deg=90))
    payload["sign_convention"]["stage_x_from_image"] = "+Y"
    p = tmp_path / "orientation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="sign_convention.*contradicts"):
        orient.load_orientation(p)


def test_reflection_axis_names_all_mirrored_cases():
    assert Orientation().reflection_axis is None
    assert Orientation(0, mirrored=True).reflection_axis == "vertical"
    assert Orientation(90, mirrored=True).reflection_axis == "anti_diagonal"
    assert Orientation(180, mirrored=True).reflection_axis == "horizontal"
    assert Orientation(270, mirrored=True).reflection_axis == "main_diagonal"


def _legacy_v2_config(orientation: Orientation) -> dict:
    return {
        "schema_version": 2,
        "measured": True,
        "rotate_deg": orientation.rotate_deg,
        "mirrored": orientation.mirrored,
        "reflection_axis": orientation.reflection_axis,
        "axis_signs": orientation.axis_signs,
        "axis_mapping": orientation.axis_mapping,
        "image_to_stage": [list(row) for row in orientation.image_to_stage],
    }


def test_every_legacy_v2_snapshot_loads_unchanged(tmp_path):
    """Migration guarantee: adopting schema 3 never orphans a measured rig.

    A rig whose ProgramData snapshot was written by the schema-2 driver must
    keep working after an upgrade, without re-measuring. Old files are only
    rewritten as schema 3 when the operator next runs set_orientation.
    """
    for deg in (0, 90, 180, 270):
        for mirrored in (False, True):
            orientation = Orientation(rotate_deg=deg, mirrored=mirrored)
            p = tmp_path / "orientation.json"
            p.write_text(json.dumps(_legacy_v2_config(orientation)), encoding="utf-8")
            assert orient.load_orientation(p) == orientation


def test_legacy_complete_schema_accepts_snapshot_without_reflection_axis(tmp_path):
    orientation = Orientation(rotate_deg=180, mirrored=True)
    payload = _legacy_v2_config(orientation)
    payload.pop("reflection_axis")
    p = tmp_path / "orientation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    assert orient.load_orientation(p) == orientation


def test_legacy_complete_schema_rejects_contradictory_reflection_axis(tmp_path):
    payload = _legacy_v2_config(Orientation(rotate_deg=180, mirrored=True))
    payload["reflection_axis"] = "vertical"
    p = tmp_path / "orientation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="reflection_axis.*contradicts"):
        orient.load_orientation(p)


def test_legacy_complete_schema_rejects_contradictory_signs(tmp_path):
    payload = _legacy_v2_config(Orientation(rotate_deg=90))
    payload["axis_signs"]["stage_x"] = 1
    p = tmp_path / "orientation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="contradicts image_to_stage"):
        orient.load_orientation(p)


def test_load_orientation_absent_key_is_no_turn(tmp_path):
    # A file without rotate_deg means "no turn" -- the shipped placeholder's
    # shape. The reader defaults it rather than failing.
    p = tmp_path / "orientation.json"
    p.write_text('{"schema_version": 1}')
    assert orient.load_orientation(p) == Orientation()


def test_load_orientation_rejects_non_quarter_turn(tmp_path):
    # The core safety promise: a stored angle that is not a whole quarter-turn is
    # refused, never quietly rounded, so the driver cannot save a picture turned
    # onto a fraction of a pixel. This is read on every save, so it must fail
    # loud -- a "be lenient, default to 0" refactor of the reader would silently
    # save mis-rotated images, and this test is what stops that.
    p = tmp_path / "orientation.json"
    p.write_text('{"schema_version": 1, "rotate_deg": 45}')
    with pytest.raises(ValueError, match="quarter-turn"):
        orient.load_orientation(p)


def test_rig_orientation_defaults_to_identity_template(monkeypatch, tmp_path):
    # With no machine snapshot yet, the rig falls back to the shipped identity
    # template -- "no turn." The real value is measured by the set_orientation
    # notebook, never hard-coded. A hermetic ProgramData root (no snapshot)
    # exercises that fallback without touching the machine's real config.
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.store import MachineProfile

    monkeypatch.setattr(
        "zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.store.MACHINE",
        MachineProfile(programdata_root=tmp_path / "programdata"),
    )
    assert orient.rig_orientation() == Orientation()


def test_rig_orientation_reads_the_measured_snapshot(monkeypatch, tmp_path):
    # Once the set_orientation notebook has published a snapshot, the driver
    # reads the measured turn from it -- not the identity template.
    from datetime import datetime, timezone

    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.store import MachineProfile

    machine = MachineProfile(programdata_root=tmp_path / "programdata")
    machine.publish_snapshot(
        datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        orientation={"schema_version": 1, "rotate_deg": 270},
    )
    monkeypatch.setattr(
        "zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.store.MACHINE",
        machine,
    )
    assert orient.rig_orientation() == Orientation(rotate_deg=270)


# --- reorient_array (lossless D4) -----------------------------------------

_A = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)  # (H=2, W=3)


def test_reorient_identity_unchanged():
    assert np.array_equal(orient.reorient_array(_A, Orientation()), _A)


def test_reorient_quarter_turn_clockwise():
    expected = np.array([[4, 1], [5, 2], [6, 3]], dtype=np.uint16)  # (3, 2)
    assert np.array_equal(
        orient.reorient_array(_A, Orientation(rotate_deg=90)), expected
    )


def test_reorient_quarter_turns_are_inverse():
    once = orient.reorient_array(_A, Orientation(rotate_deg=90))
    assert np.array_equal(orient.reorient_array(once, Orientation(rotate_deg=270)), _A)


def test_reorient_mirror_then_quarter_turn_is_lossless():
    orientation = Orientation(rotate_deg=90, mirrored=True)
    expected = np.rot90(np.fliplr(_A), k=-1)
    assert np.array_equal(orient.reorient_array(_A, orientation), expected)


# --- image_to_stage matrix -> Orientation converter -----------------------


def test_converter_anchor_known_90_rig():
    # A measured matrix of [[0,-1],[1,0]] (a rig turned a quarter-turn) is
    # corrected by turning the saved image 90 degrees clockwise.
    assert orient.orientation_from_image_to_stage([[0, -1], [1, 0]]) == Orientation(
        rotate_deg=90
    )


@pytest.mark.parametrize(
    "matrix,expected",
    [
        ([[1, 0], [0, 1]], Orientation(rotate_deg=0)),
        ([[0, -1], [1, 0]], Orientation(rotate_deg=90)),
        ([[-1, 0], [0, -1]], Orientation(rotate_deg=180)),
        ([[0, 1], [-1, 0]], Orientation(rotate_deg=270)),
        ([[-1, 0], [0, 1]], Orientation(rotate_deg=0, mirrored=True)),
        ([[0, -1], [-1, 0]], Orientation(rotate_deg=90, mirrored=True)),
        ([[1, 0], [0, -1]], Orientation(rotate_deg=180, mirrored=True)),
        ([[0, 1], [1, 0]], Orientation(rotate_deg=270, mirrored=True)),
    ],
)
def test_converter_maps_each_rotation(matrix, expected):
    assert orient.orientation_from_image_to_stage(matrix) == expected


def test_reorient_matches_all_converter_d4_matrices():
    # Pin the pixel operation order to all eight matrices trusted by the
    # converter. A convention drift here would silently mislabel a rig.
    for (deg, mirrored), expected in orient._STAGE_FROM_ORIENTATION.items():

        def _where_it_lands(marker_row_col, deg=deg, mirrored=mirrored):
            img = np.zeros((3, 3), int)
            img[marker_row_col] = 1
            turned = orient.reorient_array(
                img,
                Orientation(rotate_deg=deg, mirrored=mirrored),
            )
            r, c = np.argwhere(turned == 1)[0]
            return np.array([c - 1, r - 1])  # (column, row) offset from the centre

        # Where a step east (+1 column) and a step south (+1 row) end up.
        got = np.column_stack([_where_it_lands((1, 2)), _where_it_lands((2, 1))])
        assert np.array_equal(got, np.asarray(expected)), (deg, mirrored)


def test_converter_rejects_non_d4_matrix():
    with pytest.raises(ValueError, match="not a D4 orientation"):
        orient.orientation_from_image_to_stage([[1, 1], [0, 1]])


# --- save-time integration -------------------------------------------------


def _metadata(px_x=0.5, px_y=2.0) -> AcquisitionMetadata:
    # Deliberately anisotropic so a physical-size swap is observable.
    return AcquisitionMetadata(
        size_x=8,
        size_y=4,
        size_t=1,
        size_z=1,
        size_c=1,
        pixel_type="uint16",
        physical_size_x_um=px_x,
        physical_size_y_um=px_y,
        channels=(ChannelMetadata(index=0, name="C0"),),
    )


def _exported(tmp_path, metadata) -> ExportedAcquisition:
    """A one-plane capture, 4 rows by 8 columns, as LAS X would have left it."""
    src = tmp_path / "src.tif"
    arr = np.arange(4 * 8, dtype=np.uint16).reshape(4, 8)
    tifffile.imwrite(src, arr, photometric="minisblack")
    index = PlaneIndex(t=0, z=0, c=0)
    return ExportedAcquisition(
        source_root=tmp_path,
        source_dir=tmp_path,
        positions=[ExportedPosition(t=0, planes={index: PlaneSource(path=src)})],
        metadata=metadata,
        method="test",
    )


def _save_store(tmp_path, orientation, **metadata_kwargs):
    """Save the one-plane capture as an OME-Zarr store.

    Returns the original pixels, the pixels read back from the store, the
    store's own description of itself (OME-XML) and its voxel sizes.
    """
    exported = _exported(tmp_path, _metadata(**metadata_kwargs))
    original = tifffile.imread(tmp_path / "src.tif")
    store = ome_zarr.write_position_store(
        exported,
        tmp_path / "out.ome.zarr",
        orientation=orientation,
        where=lambda _z: {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0},
    )
    group = zarr.open_group(str(store), mode="r")
    pixels = np.asarray(group["0"][0, 0, 0])
    xml = (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")
    scale = group.attrs["ome"]["multiscales"][0]["datasets"][0][
        "coordinateTransformations"
    ][0]["scale"]
    return original, pixels, xml, scale


def test_save_identity_orientation_does_not_rotate(tmp_path):
    original, pixels, xml, _scale = _save_store(tmp_path, Orientation())
    assert np.array_equal(pixels, original)
    assert 'SizeX="8"' in xml and 'SizeY="4"' in xml


def test_save_quarter_turn_rotates_pixels_and_swaps_dims(tmp_path):
    original, pixels, xml, scale = _save_store(
        tmp_path, Orientation(rotate_deg=90), px_x=0.5, px_y=2.0
    )
    # pixels rotated clockwise, grid transposed 4x8 -> 8x4
    assert pixels.shape == (8, 4)
    assert np.array_equal(pixels, np.rot90(original, k=-1))
    # The description follows: SizeX/Y from the rotated shape, and the
    # physical sizes swap with the axes (0.5/2.0 -> 2/0.5).
    assert 'SizeX="4"' in xml and 'SizeY="8"' in xml
    assert 'PhysicalSizeX="2"' in xml and 'PhysicalSizeY="0.5"' in xml
    assert scale[-2:] == [0.5, 2.0]  # (y, x) voxel sizes in the store itself


def test_save_mirror_and_quarter_turn_transform_pixels_and_metadata(tmp_path):
    original, pixels, xml, _scale = _save_store(
        tmp_path, Orientation(rotate_deg=90, mirrored=True), px_x=0.5, px_y=2.0
    )
    assert np.array_equal(pixels, np.rot90(np.fliplr(original), k=-1))
    assert pixels.shape == (8, 4)
    assert 'SizeX="4"' in xml and 'SizeY="8"' in xml
    assert 'PhysicalSizeX="2"' in xml and 'PhysicalSizeY="0.5"' in xml


def test_save_half_turn_keeps_dims(tmp_path):
    original, pixels, xml, _scale = _save_store(tmp_path, Orientation(rotate_deg=180))
    assert pixels.shape == (4, 8)
    assert np.array_equal(pixels, np.rot90(original, k=2))
    assert 'SizeX="8"' in xml and 'SizeY="4"' in xml


def test_save_three_quarter_turn_rotates_and_swaps_dims(tmp_path):
    original, pixels, xml, _scale = _save_store(
        tmp_path, Orientation(rotate_deg=270), px_x=0.5, px_y=2.0
    )
    assert pixels.shape == (8, 4)  # the other axis-swapping turn
    assert np.array_equal(pixels, np.rot90(original, k=-3))
    assert 'SizeX="4"' in xml and 'SizeY="8"' in xml
    assert 'PhysicalSizeX="2"' in xml and 'PhysicalSizeY="0.5"' in xml
