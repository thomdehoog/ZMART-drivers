"""Bleach points and ROI regions drawn in LAS X's viewer with the mouse, against a stand-in for the viewer.

The stand-in maps the screen onto the picture as LAS X's viewer did on the
simulator (2026-11-03: 1.756 µm a screen pixel, a 512 x 512 picture of
1162.5 µm), and answers the status line's cursor position for a place on the
screen, as LAS X does; the driver must find its target there before it
clicks.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest
from viewer_stand_in import Viewer

from zmart_drivers.leica.stellaris.navigator.procedures import lasx_viewer


def landed(viewer, screen, target_px):
    """How far, in picture pixels, the screen point lies from the centre of the pixel *target_px*."""
    um = viewer.at(*screen)
    step = viewer.size_um / viewer.pixels
    return max(abs(um[i] / step - (target_px[i] + 0.5)) for i in (0, 1))


def test_a_point_is_clicked_where_the_status_line_shows_its_pixel():
    viewer = Viewer()

    result = lasx_viewer.draw_point(100, 300, viewer=viewer)

    assert result["success"] is True and lasx_viewer.TOOLS["point"] in viewer.tools
    (click,) = viewer.clicks
    assert landed(viewer, click, (100, 300)) <= 0.5


def test_it_is_found_across_a_band_where_the_viewer_shows_no_position():
    viewer = Viewer(gap=(600, 700))

    lasx_viewer.draw_point(400, 500, viewer=viewer)

    assert landed(viewer, viewer.clicks[0], (400, 500)) <= 0.5


@pytest.mark.parametrize("x, y", [(-1, 10), (10, 512), (2.5, 3), ("a", 3), (True, 3)])
def test_a_pixel_outside_the_picture_or_not_a_whole_number_is_refused_and_nothing_is_clicked(x, y):
    viewer = Viewer()

    with pytest.raises(ValueError):
        lasx_viewer.draw_point(x, y, viewer=viewer)
    assert viewer.clicks == []


def test_a_picture_shown_too_small_to_place_a_pixel_is_refused():
    viewer = Viewer(scale=3.0)  # 3 µm a screen pixel, 2.27 µm a picture pixel

    with pytest.raises(LookupError, match="zoom"):
        lasx_viewer.draw_point(100, 100, viewer=viewer)
    assert viewer.clicks == []


def test_a_rectangle_is_dragged_from_corner_to_corner():
    viewer = Viewer()

    result = lasx_viewer.draw_region("rectangle", (50, 60), (200, 220), viewer=viewer)

    assert result["success"] is True and result["regions"] is True
    ((start, end),) = viewer.drags
    assert landed(viewer, start, (50, 60)) <= 0.5 and landed(viewer, end, (200, 220)) <= 0.5
    assert lasx_viewer.TOOLS["rectangle"] in viewer.tools


@pytest.mark.parametrize("shape, a, b", [("polygon", (1, 1), (5, 5)), ("ellipse", (5, 5), (5, 9))])
def test_an_unknown_shape_or_one_without_area_is_refused(shape, a, b):
    viewer = Viewer()

    with pytest.raises(ValueError):
        lasx_viewer.draw_region(shape, a, b, viewer=viewer)
    assert viewer.drags == []


def test_the_regions_and_points_are_cleared_with_select_all_and_clear():
    viewer = Viewer(regions=True)

    result = lasx_viewer.clear_regions(viewer=viewer)

    assert viewer.pressed == [lasx_viewer.SELECT_ALL, lasx_viewer.CLEAR_SELECTED]
    assert result["confirmed"] is True


def test_with_nothing_drawn_nothing_is_pressed():
    viewer = Viewer()

    assert lasx_viewer.clear_regions(viewer=viewer)["confirmed"] is True
    assert viewer.pressed == []


@pytest.mark.parametrize("text, found", [
    ("Cursor:  Pos: 584.49 µm, 584.12 µm   Intensity: 254", (584.49, 584.12)),
    ("Image001   x = 512 y = 512  (256 kB)", None),
])
def test_the_status_line_s_cursor_is_read_as_las_x_writes_it(text, found):
    assert lasx_viewer.cursor_in([text]) == found


def test_the_status_line_s_picture_is_read_as_las_x_writes_it():
    texts = ["Image001   x = 512 y = 512  (256 kB)", "Size: 1162.50 µm x 1162.50 µm"]

    assert lasx_viewer.picture_in(texts) == {"name": "Image001", "pixels": (512, 512), "size_um": (1162.5, 1162.5)}


def test_a_polygon_is_a_click_at_each_corner_and_a_right_click_at_the_last():
    viewer = Viewer()

    result = lasx_viewer.draw_polygon([(100, 100), (250, 120), (200, 260)], viewer=viewer)

    assert result["regions"] is True and lasx_viewer.TOOLS["polygon"] in viewer.tools
    assert [round(landed(viewer, c, p), 1) <= 0.5 for c, p in zip(viewer.clicks, [(100, 100), (250, 120), (200, 260)])] == [True] * 3
    assert viewer.right_clicks == [viewer.clicks[-1]]


@pytest.mark.parametrize("vertices", [[(1, 1), (5, 5)], [(1, 1), (5, 5), (600, 5)], "abc"])
def test_a_polygon_of_fewer_than_three_pixels_or_outside_the_picture_is_refused(vertices):
    viewer = Viewer()

    with pytest.raises(ValueError):
        lasx_viewer.draw_polygon(vertices, viewer=viewer)
    assert viewer.clicks == [] and viewer.right_clicks == []


def test_a_range_is_two_clicks_from_one_column_to_the_other():
    viewer = Viewer()

    result = lasx_viewer.draw_range(300, 420, viewer=viewer)

    ((start, end),) = viewer.drags
    assert result["regions"] is True and lasx_viewer.TOOLS["range"] in viewer.tools
    assert landed(viewer, start, (300, 256)) <= 0.5 and landed(viewer, end, (420, 256)) <= 0.5


@pytest.mark.parametrize("seconds, boxes", [
    (3723.5, {"TextDays": "0", "TextHours": "1", "TextMinutes": "2", "TextSeconds": "3", "TextMilliseconds": "500"}),
    (90061.007, {"TextDays": "1", "TextHours": "1", "TextMinutes": "1", "TextSeconds": "1", "TextMilliseconds": "7"}),
])
def test_the_bleaching_time_goes_into_the_panel_s_boxes_and_is_read_back(seconds, boxes):
    viewer = Viewer()

    result = lasx_viewer.set_bleach_time(seconds, viewer=viewer)

    assert viewer.boxes == boxes and result["seconds"] == pytest.approx(seconds)


@pytest.mark.parametrize("seconds", [0, -1, "2 s", True])
def test_a_bleaching_time_that_is_not_a_positive_number_is_refused(seconds):
    viewer = Viewer()

    with pytest.raises(ValueError):
        lasx_viewer.set_bleach_time(seconds, viewer=viewer)
    assert set(viewer.boxes.values()) == {"0"}
