"""The pan, against the stand-in for LAS X's plain Navigator: read from a saved setting, and set back to the centre.

LAS X's window moves the pan with no command LCS logs, and holds it only in
its own setting, where a save shows it (``PanFirstDim``, ``PanSecondDim``).
A loaded setting does not set it, but the smallest zoom pulls it to the
centre and it stays there when the zoom goes back (simulator, 2026-11-01).
These tests check the reading in micrometres and that the centring puts the
zoom back where it was.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import install_permissive_limits

from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.procedures import pan

SCANNING = ("Confocal", "ScanningParameter")


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator())


@pytest.fixture
def client(lasx):
    return install_permissive_limits(hardware_server.Client("test"), wide_stage=True)


def test_one_click_at_zoom_2_is_17_micrometres_with_the_10x(lasx, client, tmp_path):
    # LAS X's numbers on the simulator: one click 0.00017437, the 10x's field 1162.5 um at zoom 1.
    lasx.pan = [0.0001743713507108436, 0.0]

    shown = pan.read(client, tmp_path)

    assert shown["x"] == pytest.approx(0.0001743713507108436)
    assert shown["x_um"] == pytest.approx(17.44, abs=0.01)
    assert shown["y_um"] == 0.0


def test_the_reading_does_not_change_with_the_zoom(lasx, client, tmp_path):
    lasx.pan = [0.0001, 0.0]
    first = pan.read(client, tmp_path)["x_um"]
    lasx.live[(*SCANNING, "Zoom")] = 2.0
    lasx.live[("Acquire", "AcquireImageSizeInfo", "ImageSizeX")] = 0.0011625 / 2

    assert pan.read(client, tmp_path)["x_um"] == pytest.approx(first)


def test_centring_goes_through_the_smallest_zoom_and_back(lasx, client, tmp_path):
    lasx.pan = [-0.000754, -0.000096]
    lasx.live[(*SCANNING, "Zoom")] = 4.177

    result = pan.center(client, tmp_path)

    assert result["success"] is True and result["confirmed"] is True
    assert lasx.live[(*SCANNING, "Zoom")] == pytest.approx(4.177)
    assert result["pan"]["x"] == pytest.approx(0.0, abs=pan.CENTRED)
    zooms = [e for e in lasx.sent if 'Command="SetZoom"' in e]
    assert len(zooms) == 2 and 'Zoom="0.75"' in zooms[0]


def test_a_pan_the_smallest_zoom_does_not_centre_is_not_confirmed(lasx, client, tmp_path):
    lasx.pan = [0.0005, 0.0]
    lasx.keeps_pan = True

    result = pan.center(client, tmp_path)

    assert result["confirmed"] is False and "centre" in result["message"]
    assert lasx.live[(*SCANNING, "Zoom")] == pytest.approx(1.0)


def test_a_zoom_the_limits_refuse_leaves_everything_as_it_was(lasx, client, tmp_path):
    lasx.pan = [0.0005, 0.0]
    lasx.answers["SetZoom"] = '<Result Error="1">Zoom 0.75 is out of range</Result>'

    result = pan.center(client, tmp_path)

    assert result["success"] is False
    assert lasx.pan == [0.0005, 0.0]


class Pad:
    """LAS X's pan arrows as the stand-in has them: each press moves the pan 3 % of the field shown."""

    def __init__(self, lasx):
        self.lasx, self.pressed = lasx, []

    def press(self, arrow):
        self.pressed.append((arrow, self.lasx.live[(*SCANNING, "Zoom")]))
        base_um = 0.0011625 * 1e6  # the stand-in's field at zoom 1
        click = 0.03 * base_um / self.lasx.live[(*SCANNING, "Zoom")] / pan.micrometres_per_pan(base_um)
        across, down = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}[arrow]
        self.lasx.pan = [self.lasx.pan[0] + across * click, self.lasx.pan[1] + down * click]


def test_the_pan_is_moved_by_whole_clicks_at_a_zoom_where_they_land_on_the_distance(lasx, client, tmp_path):
    lasx.live[(*SCANNING, "Zoom")] = 4.177
    pad = Pad(lasx)

    result = pan.move(client, tmp_path, 20.0, -12.0, pad=pad)

    assert result["success"] is True and result["confirmed"] is True
    assert result["pan"]["x_um"] == pytest.approx(20.0, abs=0.01)
    assert result["pan"]["y_um"] == pytest.approx(-12.0, abs=0.01)
    assert lasx.live[(*SCANNING, "Zoom")] == pytest.approx(4.177)
    assert {arrow for arrow, _ in pad.pressed} == {"right", "down"}
    assert all(zoom >= 4.177 for _, zoom in pad.pressed)  # never below the zoom it had: a lower zoom pulls the pan in


def test_a_move_beyond_the_largest_pan_is_refused_and_nothing_is_pressed(lasx, client, tmp_path):
    pad = Pad(lasx)

    result = pan.move(client, tmp_path, 1000.0, 0.0, pad=pad)  # the 10x's largest pan shifts 775 um

    assert result["success"] is False and "largest" in result["message"]
    assert pad.pressed == [] and lasx.pan == [0.0, 0.0]


def test_a_move_that_did_not_land_is_not_confirmed(lasx, client, tmp_path):
    pad = Pad(lasx)
    pad.press = lambda arrow: None  # LAS X's arrows did nothing

    result = pan.move(client, tmp_path, 20.0, 0.0, pad=pad)

    assert result["confirmed"] is False and "20" in result["message"]


def test_the_pan_is_set_to_a_place_from_the_centre(lasx, client, tmp_path):
    lasx.live[(*SCANNING, "Zoom")] = 4.177
    pad = Pad(lasx)
    assert pan.move(client, tmp_path, 20.0, -12.0, pad=pad)["confirmed"] is True

    result = pan.set_to(client, tmp_path, -30.0, 10.0, pad=pad)

    assert result["confirmed"] is True
    assert (result["pan"]["x_um"], result["pan"]["y_um"]) == (pytest.approx(-30.0, abs=0.5), pytest.approx(10.0, abs=0.5))


def test_a_place_beyond_the_largest_pan_is_refused_and_nothing_is_pressed(lasx, client, tmp_path):
    pad = Pad(lasx)

    result = pan.set_to(client, tmp_path, 0.0, -900.0, pad=pad)

    assert result["success"] is False and pad.pressed == []
