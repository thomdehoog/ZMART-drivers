"""A picture from the capture to the files on disk: ``acquire`` and ``tile_scan``, then ``save_data``.

The driver is connected to the stand-in for LAS X's plain Navigator. The
capture is a real send through the dispatcher; LAS X's cache is a folder
here, with a picture written into it the way LAS X writes one (the layout
``test_lasx_data_container.py`` uses). What is checked is what a workflow
sees: the note kept for ``save_data``, then the stores and the record.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import numpy as np
import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import hermetic_mock_machine_root
from test_lasx_data_container import (
    mosaic_xml,
    two_channel_12_bit,
    write_description,
    write_pixels,
)

from zmart_drivers.leica.stellaris import navigator
from zmart_drivers.leica.stellaris.navigator.procedures import acquire as acquiring
from zmart_drivers.leica.stellaris.navigator.procedures.clear_cache import (
    data_container,
)
from zmart_drivers.leica.stellaris.navigator.procedures.save_data import collect


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator(z_wide_um=100.0))


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """LAS X's Data Container cache, a folder here."""
    root = tmp_path / "Temp2"
    root.mkdir()
    monkeypatch.setattr(collect, "data_container_root", lambda: root)
    return root


@pytest.fixture
def scope(lasx, cache, tmp_path, monkeypatch):
    monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)
    hermetic_mock_machine_root()
    scope = navigator.connect(output_root=str(tmp_path / "out"))
    yield scope
    scope.close()


class TestACapture:
    def test_the_capture_is_noted_for_save_data_with_where_it_was_taken(self, scope, lasx):
        answer = scope.acquire("A1", acquisition_type="overview")

        assert answer == {"position_label": "A1", "acquisition_type": "overview", "picture": "capture"}
        assert lasx.captures == 1
        (pending,) = scope.handle.unsaved
        assert pending.label == "A1" and [n.stem for n in pending.namings] == ["overview_A1"]
        # No origin is saved here, so the frame is the stage's own coordinates.
        assert pending.fields[0]["x"] == pytest.approx(63500.0) and pending.fields[0]["z_wide_um"] == pytest.approx(100.0)
        assert pending.settings == {"trigger": "full"}

    def test_a_label_still_waiting_to_be_saved_cannot_be_used_again(self, scope):
        scope.acquire("A1", acquisition_type="overview")
        with pytest.raises(ValueError, match="A1"):
            scope.acquire("A1", acquisition_type="overview")

    def test_without_a_label_the_session_counts(self, scope):
        first = scope.acquire(acquisition_type="overview")["position_label"]
        second = scope.acquire(acquisition_type="overview")["position_label"]
        assert (first, second) == ("000000", "000001")

    def test_an_unknown_acquisition_setting_is_refused_before_anything_is_sent(self, scope, lasx):
        with pytest.raises(ValueError, match="trigger"):
            scope.acquire("A1", acquisition_settings={"trigger": "partial"}, acquisition_type="overview")
        assert lasx.captures == 0

    def test_the_capture_is_saved_as_a_store_and_remembered_for_clear_cache(self, scope, cache, tmp_path):
        import zarr

        scope.acquire("A1", acquisition_type="overview")
        pixels = two_channel_12_bit(cache, number=746, name="Image002")

        (record,) = scope.save("A1")

        assert scope.handle.unsaved == []
        assert scope.handle.in_cache == [(746, "Image002")]
        assert record["position_label"] == "A1" and record["acquisition_type"] == "overview"
        assert [p["c"] for p in record["planes"]] == [0, 1]
        assert all(p["x_um"] == pytest.approx(63500.0) and p["z_um"] == pytest.approx(100.0) for p in record["planes"])
        store = record["planes"][0]["path"]
        assert store.startswith(str(tmp_path / "out"))
        level = zarr.open_group(store, mode="r")["0"][:]
        np.testing.assert_array_equal(level[0, 1, 0], pixels[1])
        assert all((tmp_path / "out") in __import__("pathlib").Path(f).parents for f in record["files"])

    def test_a_picture_las_x_never_wrote_stays_waiting(self, scope, cache, monkeypatch):
        monkeypatch.setattr(collect, "DEFAULT_EXPORT_COMPLETION_TIMEOUT_S", 0.3)
        from zmart_drivers.leica.stellaris.navigator.procedures.save_data import (
            save as saving,
        )

        monkeypatch.setattr(saving, "DEFAULT_EXPORT_COMPLETION_TIMEOUT_S", 0.3)
        scope.acquire("A1", acquisition_type="overview")

        with pytest.raises(RuntimeError, match="no new picture"):
            scope.save("A1")
        assert len(scope.handle.unsaved) == 1

    def test_saving_a_label_that_is_not_waiting_is_refused(self, scope):
        with pytest.raises(ValueError, match="B2"):
            scope.save("B2")


class TestATileScan:
    def test_the_scan_is_sent_with_every_field_on_the_stage_and_noted_per_field(self, scope, lasx):
        row = scope.row_of_fields(3)

        answer = scope.tile_scan(row, "row")

        assert answer == {"position_label": "row", "acquisition_type": "tile_scan", "picture": "tile_scan", "fields": 3}
        assert lasx.captures == 1
        (pending,) = scope.handle.unsaved
        assert [n.stem for n in pending.namings] == [f"tile_scan_row_field{n}" for n in range(3)]
        assert [round(f["x"], 1) for f in pending.fields] == [63500.0, 64778.8, 66057.5]
        tiles = next(x for x in lasx.sent if "SetStageIrregularTileScanPositions" in x)
        assert tiles.count("<Tile ") == 3 and 'XPos="0.0647787500"' in tiles
        # The stage is back at the first field afterwards.
        assert (lasx.x_um, lasx.y_um) == (pytest.approx(63500.0), pytest.approx(41500.0))

    def test_no_positions_is_refused(self, scope):
        with pytest.raises(ValueError, match="at least one"):
            scope.tile_scan([], "row")

    def test_a_field_outside_the_limits_refuses_the_whole_scan_and_nothing_moves(self, scope, lasx):
        with pytest.raises(RuntimeError, match="refused"):
            scope.tile_scan([(63500.0, 41500.0), (500_000.0, 41500.0)], "row")
        assert lasx.captures == 0 and lasx.commands() == []

    def test_every_field_is_saved_as_its_own_store_where_it_was_taken(self, scope, cache, lasx):
        import zarr

        scope.tile_scan([(63500.0, 41500.0), (64000.0, 41500.0), (64500.0, 41500.0)], "row")
        pixels = np.arange(3 * 2 * 3 * 4, dtype=np.uint8).reshape(3, 2, 3, 4)
        write_pixels(cache, 10755, pixels)
        write_description(cache, 10755, "Mosaic001", mosaic_xml())

        (record,) = scope.save("row")

        planes = record["planes"]
        assert [(p["field"], p["c"]) for p in planes] == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
        assert [p["x_um"] for p in planes[::2]] == [pytest.approx(63500.0), pytest.approx(64000.0), pytest.approx(64500.0)]
        for field in range(3):
            level = zarr.open_group(planes[2 * field]["path"], mode="r")["0"][:]
            np.testing.assert_array_equal(level[0, 0, 0], pixels[field, 0])
        assert scope.handle.in_cache == [(10755, "Mosaic001")]
        assert len({p["path"] for p in planes}) == 3


def test_the_row_turns_round_at_the_edge_of_the_canvas(scope, lasx):
    """A row that would leave the stage's limits runs the other way instead."""
    here = scope.position()
    x_max = here["x"]["canvas"][1]
    scope.move_to(x_max - 100.0, here["y"]["position"], here["z"]["position"])

    row = acquiring.row_of_fields(scope.handle, 3)

    assert all(x <= x_max for x, _y in row)
    assert row[1][0] < row[0][0]
