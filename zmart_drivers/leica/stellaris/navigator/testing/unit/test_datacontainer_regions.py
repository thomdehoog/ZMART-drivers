"""ROI regions and bleach points as LAS X's data container holds them, against sets LAS X wrote (simulator, 2026-11-03):
four shapes drawn at known pixels, and one bleach point with 2.5 s."""

from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris.navigator.primitives.direct import data_container as regions

DATA = Path(__file__).resolve().parents[1] / "data" / "datacontainer"
PIXEL_UM = 1162.5 / 512  # the simulator's field at zoom 1


def at_pixel(px, py):
    """A pixel's place in micrometres from the field's centre, x right and y up (the 512 x 512 picture, zoom 1)."""
    return (px - 256) * PIXEL_UM, (256 - py) * PIXEL_UM


class TestWhatLasXWrote:
    """Four shapes drawn in LAS X's viewer: a rectangle at pixels (100, 100) to (250, 220), an ellipse at (300, 300) to
    (420, 380), a polygon at (60, 400), (160, 420), (110, 480), and a range over columns 330 to 430."""

    @pytest.fixture(scope="class")
    def shapes(self):
        return regions.read_set((DATA / "regions_four_shapes.xml").read_text(encoding="utf-8"))

    def test_each_shape_is_read_with_its_name(self, shapes):
        assert [(s["name"], s["shape"]) for s in shapes] == [
            ("ROI 1", "rectangle"), ("ROI 2", "ellipse"), ("ROI 3", "polygon"), ("ROI 4", "range")]

    def test_the_turned_rectangle_lands_on_its_pixels(self, shapes):
        xs = [x for x, _ in shapes[0]["points_um"]]
        ys = [y for _, y in shapes[0]["points_um"]]
        left, top = at_pixel(100, 100)
        right, bottom = at_pixel(250, 220)
        assert min(xs) == pytest.approx(left, abs=2 * PIXEL_UM) and max(xs) == pytest.approx(right, abs=2 * PIXEL_UM)
        assert max(ys) == pytest.approx(top, abs=2 * PIXEL_UM) and min(ys) == pytest.approx(bottom, abs=2 * PIXEL_UM)

    def test_the_ellipse_s_outline_spans_its_box(self, shapes):
        xs = [x for x, _ in shapes[1]["points_um"]]
        ys = [y for _, y in shapes[1]["points_um"]]
        assert len(xs) == regions.ELLIPSE_POINTS
        assert max(xs) - min(xs) == pytest.approx(120 * PIXEL_UM, abs=3 * PIXEL_UM)
        assert max(ys) - min(ys) == pytest.approx(80 * PIXEL_UM, abs=3 * PIXEL_UM)

    def test_the_polygon_s_corners_land_on_their_pixels(self, shapes):
        for got, pixel in zip(shapes[2]["points_um"], [(60, 400), (160, 420), (110, 480)]):
            assert got == (pytest.approx(at_pixel(*pixel)[0], abs=2 * PIXEL_UM), pytest.approx(at_pixel(*pixel)[1], abs=2 * PIXEL_UM))

    def test_the_range_spans_its_columns(self, shapes):
        (left, _), (right, _) = shapes[3]["points_um"]
        assert left == pytest.approx(at_pixel(330, 0)[0], abs=2 * PIXEL_UM)
        assert right == pytest.approx(at_pixel(430, 0)[0], abs=2 * PIXEL_UM)

    def test_each_region_s_laser_lines_are_read(self, shapes):
        assert (499.0, True) in shapes[0]["laser_lines"] and (405.0, False) in shapes[0]["laser_lines"]


def test_a_bleach_point_is_read_with_its_place_and_time():
    (point,) = regions.read_set((DATA / "bleach_one_point.xml").read_text(encoding="utf-8"))

    assert point["shape"] == "point" and point["seconds"] == 2.5
    assert point["centre_um"] == (pytest.approx(at_pixel(150, 150)[0], abs=2 * PIXEL_UM), pytest.approx(at_pixel(150, 150)[1], abs=2 * PIXEL_UM))


class TestWhatTheDriverWrites:
    REGIONS = [
        {"shape": "rectangle", "centre_um": (-50.0, 30.0), "size_um": (120.0, 80.0)},
        {"shape": "ellipse", "centre_um": (100.0, -40.0), "size_um": (60.0, 40.0)},
        {"shape": "polygon", "vertices_um": [(0.0, 0.0), (40.0, 10.0), (20.0, 50.0)]},
        {"shape": "range", "from_um": 20.0, "to_um": -100.0},
    ]

    def test_regions_are_read_back_where_they_were_given(self):
        shapes = regions.read_set(regions.regions_xml(self.REGIONS, first=3))

        assert [(s["name"], s["shape"]) for s in shapes] == [("ROI 3", "rectangle"), ("ROI 4", "ellipse"), ("ROI 5", "polygon"), ("ROI 6", "range")]
        assert shapes[0]["points_um"] == [pytest.approx(p) for p in [(-110.0, 70.0), (10.0, 70.0), (10.0, -10.0), (-110.0, -10.0)]]
        assert shapes[1]["centre_um"] == pytest.approx((100.0, -40.0))
        assert shapes[2]["points_um"] == [pytest.approx(p) for p in [(0.0, 0.0), (40.0, 10.0), (20.0, 50.0)]]
        assert [x for x, _ in shapes[3]["points_um"]] == [pytest.approx(-100.0), pytest.approx(20.0)]

    def test_bleach_points_are_read_back_with_their_times(self):
        xml = regions.bleach_points_xml([{"at_um": (10.0, -20.0), "seconds": 3.0}, {"at_um": (-5.0, 5.0), "seconds": 0.25}], z_m=1e-6)

        points = regions.read_set(xml)

        assert [(p["shape"], p["seconds"]) for p in points] == [("point", 3.0), ("point", 0.25)]
        assert points[0]["centre_um"] == pytest.approx((10.0, -20.0))

    def test_the_extent_is_the_region_s_box(self):
        assert regions.extent(self.REGIONS[0]) == pytest.approx((-110.0, 10.0, 70.0, -10.0))

    @pytest.mark.parametrize("region", [
        {"shape": "star"}, {"shape": "rectangle", "centre_um": (0, 0), "size_um": (0, 5)},
        {"shape": "rectangle", "centre_um": "middle", "size_um": (5, 5)}, {"shape": "polygon", "vertices_um": [(0, 0), (1, 1)]},
        {"shape": "range", "from_um": 3.0, "to_um": 3.0}, {"shape": "ellipse", "centre_um": (0, 0), "size_um": (True, 4)},
    ])
    def test_what_is_not_a_region_is_refused(self, region):
        with pytest.raises(ValueError):
            regions.regions_xml([region])

    @pytest.mark.parametrize("point", [{"at_um": (0, 0), "seconds": 0}, {"at_um": (0, 0)}, {"at_um": None, "seconds": 1}])
    def test_what_is_not_a_bleach_point_is_refused(self, point):
        with pytest.raises(ValueError):
            regions.bleach_points_xml([point], z_m=0.0)
