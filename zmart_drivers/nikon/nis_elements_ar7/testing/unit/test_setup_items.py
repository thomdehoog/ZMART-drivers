"""What is set up once per microscope: where it is kept, the origin, the orientation, the data location, the pixel sizes.

Each item is saved under this computer's settings folder, as a dated
snapshot, so that an earlier setting is never lost and the newest one is
always the one used. A setting that is damaged is said plainly, never taken
for "not set up".

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from zmart_drivers.nikon import nis_elements_ar7
from zmart_drivers.nikon.nis_elements_ar7.driver_setup import (
    data_location,
    pixel_size,
    store,
)
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
    Orientation,
    load_rig_orientation,
    orientation_from_image_to_stage,
    reorient_array,
    save_orientation,
)
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.origin import saved_origin


def test_settings_are_kept_per_microscope_under_the_settings_folder(
    _hermetic_machine_root,
):
    machine = store.use_microscope("nikon-ti2-zmb")
    path = machine.write("data_location", {"folder": "/data"})
    assert path.is_relative_to(
        _hermetic_machine_root / "nikon" / "nikon-ti2-zmb" / "nis_elements_ar7"
    )
    assert machine.read("data_location") == {"folder": "/data"}


def test_the_newest_snapshot_is_the_one_used():
    machine = store.machine()
    now = datetime.now(UTC)
    machine.write("data_location", {"folder": "/old"}, moment=now)
    machine.write(
        "data_location", {"folder": "/new"}, moment=now + timedelta(seconds=1)
    )
    assert machine.read("data_location") == {"folder": "/new"}
    assert len(machine.snapshots("data_location")) == 2


def test_a_microscope_name_must_be_a_folder_name():
    for bad in ("../elsewhere", "two words", "", "a/b"):
        with pytest.raises(ValueError):
            store.check_microscope_name(bad)


def test_a_damaged_setting_is_said_not_ignored():
    path = store.machine().write("data_location", {"folder": "/x"})
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        data_location.data_location()


# --- the origin ---------------------------------------------------------------------------


def test_the_origin_is_saved_with_its_objective_and_loaded_again(
    scope, stand_in, key_file, output_root
):
    stand_in.scope.place(500.0, 600.0, 3000.0)
    saved = scope.set_origin()
    assert saved["reference"]["objective"] == {"position": 1, "name": "Plan Apo 10x"}
    assert scope.position()["x"]["position"] == pytest.approx(0.0)
    origin = saved_origin.load_saved_origin()
    assert origin["x_um"] == 500.0 and origin["objective"]["position"] == 1


def test_no_saved_origin_means_plain_stage_coordinates():
    assert saved_origin.load_saved_origin() is None


def test_a_damaged_origin_stops_the_connection(stand_in, key_file):
    store.machine().write("origin", {"origin": {"x_um": "here"}})
    with pytest.raises(RuntimeError, match="origin"):
        nis_elements_ar7.connect(port=stand_in.port)


# --- the orientation ----------------------------------------------------------------------


def test_without_a_stated_orientation_pictures_are_kept_as_the_camera_gives_them():
    orientation, info = load_rig_orientation()
    assert orientation.is_identity and not info.get("measured")


def test_a_stated_orientation_is_saved_and_loaded():
    save_orientation(Orientation(rotate_deg=90, mirrored=True))
    orientation, info = load_rig_orientation()
    assert (orientation.rotate_deg, orientation.mirrored) == (90, True) and info[
        "measured"
    ]


def test_every_quarter_turn_and_mirror_round_trips_through_its_matrix():
    for deg in (0, 90, 180, 270):
        for mirrored in (False, True):
            orientation = Orientation(rotate_deg=deg, mirrored=mirrored)
            again = orientation_from_image_to_stage(orientation.image_to_stage)
            assert (again.rotate_deg, again.mirrored) == (deg, mirrored)


def test_a_turn_moves_pixels_and_loses_none():
    picture = np.arange(12).reshape(3, 4)
    turned = reorient_array(picture[np.newaxis], Orientation(rotate_deg=90))
    assert turned.shape == (1, 4, 3)
    assert (
        turned[0, 0, 0] == picture[2, 0]
    )  # a clockwise turn puts the bottom-left corner at the top-left
    assert sorted(turned.ravel()) == sorted(picture.ravel())


# --- the data location --------------------------------------------------------------------


def test_the_data_location_must_be_a_writable_local_folder(tmp_path):
    assert data_location.check(tmp_path) == []
    assert data_location.check(tmp_path / "missing")
    assert data_location.check("relative/folder")
    assert data_location.check("//server/share")
    with pytest.raises(ValueError):
        data_location.set_data_location(tmp_path / "missing")
    data_location.set_data_location(tmp_path)
    assert data_location.data_location() == tmp_path.resolve()


# --- the pixel sizes -----------------------------------------------------------------------


def test_the_pixel_size_record_remembers_each_objective_and_configuration():
    assert pixel_size.lookup("Plan Apo 10x", "GFP") is None
    pixel_size.remember("Plan Apo 10x", "GFP", width=64, height=48, pixel_size_um=0.65)
    first = store.machine().saved_path("pixel_size")
    pixel_size.remember("Plan Apo 10x", "GFP", width=64, height=48, pixel_size_um=0.65)
    assert store.machine().saved_path("pixel_size") == first, (
        "an unchanged entry is not saved again"
    )
    entry = pixel_size.lookup("Plan Apo 10x", "GFP")
    assert entry["image_size_px"] == [64, 48] and entry["calibrated"]
    pixel_size.remember(
        "Plan Apo 60x Oil", "GFP", width=64, height=48, pixel_size_um=None
    )
    assert pixel_size.lookup("Plan Apo 60x Oil", "GFP")["calibrated"] is False
