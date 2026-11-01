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
