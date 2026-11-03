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

import time

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
    """LAS X's pan pad as the stand-in has it: each press moves the pan 3 % of the field shown, within LAS X's window;
    the middle button centres it."""

    percent = None

    def __init__(self, lasx):
        self.lasx, self.pressed = lasx, []

    def press(self, arrow):
        self.pressed.append((arrow, self.lasx.live[(*SCANNING, "Zoom")]))
        if arrow == "centre":
            self.lasx.pan = [0.0, 0.0]
            return
        base_um = 0.0011625 * 1e6  # the stand-in's field at zoom 1
        zoom = self.lasx.live[(*SCANNING, "Zoom")]
        click = 0.03 * base_um / zoom / pan.micrometres_per_pan(base_um)
        across, down = pan.VECTORS[arrow]
        edge = max(0.0, 0.00775 * (1 - 0.75 / zoom))
        self.lasx.pan = [max(-edge, min(edge, self.lasx.pan[0] + across * click)),
                         max(-edge, min(edge, self.lasx.pan[1] + down * click))]

    def centre(self):
        self.press("centre")

    def close(self):
        pass


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


def test_the_window_las_x_allows_grows_with_the_zoom(lasx, client):
    # 0.00775 * (1 - 0.75 / zoom) of pan (simulator, 2026-11-03), in micrometres with the stand-in's 10x.
    assert pan.window_um(client) == pytest.approx(193.85, abs=0.01)
    assert pan.window_um(client, 0.75) == 0.0
    assert pan.window_um(client, 2.0) == pytest.approx(484.6, abs=0.1)


def test_a_move_beyond_the_window_goes_to_the_closest_place_and_says_how_far_over(lasx, client, tmp_path):
    pad = Pad(lasx)

    result = pan.move(client, tmp_path, 1000.0, 0.0, pad=pad)

    assert result["success"] is True and result["confirmed"] is True
    assert result["pan"]["x_um"] == pytest.approx(193.85, abs=0.5)
    clipped = result["clipped"]
    assert clipped["asked_um"] == (pytest.approx(1000.0), 0.0) and clipped["over_um"][0] == pytest.approx(806.15, abs=0.01)
    assert clipped["window_um"] == pytest.approx(193.85, abs=0.01) and "1000" in result["message"]


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


def test_a_place_beyond_the_window_goes_to_the_closest_place(lasx, client, tmp_path):
    result = pan.set_to(client, tmp_path, 50.0, -900.0, pad=Pad(lasx))

    assert result["confirmed"] is True
    assert (result["pan"]["x_um"], result["pan"]["y_um"]) == (pytest.approx(50.0, abs=0.5), pytest.approx(-193.85, abs=0.5))
    assert result["clipped"]["over_um"][0] == 0.0 and result["clipped"]["over_um"][1] == pytest.approx(-706.15, abs=0.01)


def test_within_the_window_nothing_is_clipped(lasx, client, tmp_path):
    assert "clipped" not in pan.set_to(client, tmp_path, 50.0, -100.0, pad=Pad(lasx))


class Steps:
    """LCS's COM scanner as far as the driver opens it: steps of its percent of the field shown, and the centre."""

    def __init__(self, lasx, works=True):
        self.lasx, self.works, self.value, self.taken = lasx, works, 3, []

    def step(self, direction):
        self.taken.append((direction, self.value, self.lasx.live[(*SCANNING, "Zoom")]))
        if direction == "centre":
            self.lasx.pan = [0.0, 0.0]
            return
        if not self.works:
            return
        base_um = 0.0011625 * 1e6
        click = self.value / 100 * base_um / self.lasx.live[(*SCANNING, "Zoom")] / pan.micrometres_per_pan(base_um)
        across, down = pan.VECTORS[direction]
        edge = 0.00775 * (1 - 0.75 / self.lasx.live[(*SCANNING, "Zoom")])  # LAS X clips to its window
        self.lasx.pan = [max(-edge, min(edge, self.lasx.pan[0] + across * click)),
                         max(-edge, min(edge, self.lasx.pan[1] + down * click))]

    def percent(self):
        return self.value

    def set_percent(self, value, *, putting_back=False):
        self.value = value


def test_through_the_scanner_the_pan_is_set_in_one_percent_steps_and_the_step_put_back(lasx, client, tmp_path):
    steps = Steps(lasx)

    result = pan.set_to(client, tmp_path, 7.3, -2.1, pad=pan.ScannerPad(client, steps=steps))

    assert result["confirmed"] is True
    assert (result["pan"]["x_um"], result["pan"]["y_um"]) == (pytest.approx(7.3, abs=0.5), pytest.approx(-2.1, abs=0.5))
    assert {percent for _, percent, _ in steps.taken} == {1} and steps.value == 3
    assert lasx.live[(*SCANNING, "Zoom")] == pytest.approx(1.0)


def test_through_the_scanner_the_pan_is_centred_without_a_zoom_change(lasx, client, tmp_path):
    lasx.pan = [0.0005, -0.0002]
    steps = Steps(lasx)

    result = pan.center_by_pad(client, tmp_path, pan.ScannerPad(client, steps=steps))

    assert result["confirmed"] is True and lasx.pan == [0.0, 0.0]
    assert not [e for e in lasx.sent if 'Command="SetZoom"' in e] and steps.value == 3


def test_the_scanner_is_refused_on_anything_but_the_simulator(lasx, client):
    lasx.live[("Acquire", "ActiveDataSource", "CurrentSerialNumber")] = "STELLARIS 8 y42h93"
    steps = Steps(lasx)

    with pytest.raises(pan.ScannerRefused, match="simulator only"):
        pan.ScannerPad(client, steps=steps)
    assert steps.taken == [] and steps.value == 3


def test_the_scanner_is_refused_while_las_x_scans(lasx, client):
    lasx.scanning_until = time.monotonic() + 60
    steps = Steps(lasx)

    with pytest.raises(pan.ScannerRefused, match="scanning"):
        pan.ScannerPad(client, steps=steps)
    assert steps.value == 3


def test_a_first_step_that_does_not_move_as_the_formula_says_stops_and_centres(lasx, client, tmp_path):
    steps = Steps(lasx, works=False)

    result = pan.set_to(client, tmp_path, 40.0, 0.0, pad=pan.ScannerPad(client, steps=steps))

    assert result["success"] is False and "first step" in result["message"]
    assert len([d for d, _, _ in steps.taken if d != "centre"]) == 1 and steps.taken[-1][0] == "centre"
    assert steps.value == 3 and lasx.live[(*SCANNING, "Zoom")] == pytest.approx(1.0)


def test_the_pad_s_diagonal_steps_move_both_ways_as_the_formula_says(lasx, client, tmp_path):
    steps = Steps(lasx)

    result = pan.step(client, tmp_path, "up-right", 2, pan.ScannerPad(client, percent=3, steps=steps))

    assert result["confirmed"] is True and result["step_um"] == pytest.approx(34.875)
    assert (result["pan"]["x_um"], result["pan"]["y_um"]) == (pytest.approx(69.75, abs=0.01), pytest.approx(69.75, abs=0.01))
    assert [d for d, _, _ in steps.taken] == ["up-right", "up-right"] and steps.value == 3


def test_steps_past_the_window_stop_at_its_edge_and_say_how_far_over(lasx, client, tmp_path):
    steps = Steps(lasx)

    result = pan.step(client, tmp_path, "left", 30, pan.ScannerPad(client, percent=3, steps=steps))  # 1046 um asked

    assert result["confirmed"] is True and steps.value == 3
    assert result["pan"]["x_um"] == pytest.approx(-193.85, abs=0.5)
    assert result["clipped"]["over_um"][0] == pytest.approx(-852.4, abs=0.1)


def test_a_first_step_from_the_edge_outwards_is_not_taken_for_a_fault(lasx, client, tmp_path):
    lasx.pan = [0.0019375, 0.0]  # at the window's right edge
    steps = Steps(lasx)

    result = pan.step(client, tmp_path, "right", 2, pan.ScannerPad(client, percent=3, steps=steps))

    assert result["confirmed"] is True and [d for d, _, _ in steps.taken] == ["right", "right"]


@pytest.mark.parametrize("direction, count", [("sideways", 1), ("up", 0), ("up", 101)])
def test_an_unknown_direction_or_count_is_refused(lasx, client, tmp_path, direction, count):
    steps = Steps(lasx)

    with pytest.raises(ValueError):
        pan.step(client, tmp_path, direction, count, pan.ScannerPad(client, steps=steps))
    assert steps.taken == [] and steps.value == 3


def test_the_step_is_read_in_percent_and_micrometres(lasx, client):
    lasx.live[(*SCANNING, "Zoom")] = 2.0
    lasx.live[("Acquire", "AcquireImageSizeInfo", "ImageSizeX")] = 0.0011625 / 2

    held = pan.step_size(client, pan.ScannerPad(client, percent=3, steps=Steps(lasx)))

    assert held == {"percent": 3, "um": pytest.approx(17.4375)}


def test_through_the_scanner_a_place_beyond_the_window_goes_to_its_edge(lasx, client, tmp_path):
    steps = Steps(lasx)

    result = pan.set_to(client, tmp_path, 900.0, 0.0, pad=pan.ScannerPad(client, steps=steps))

    assert result["confirmed"] is True and steps.value == 3
    assert result["pan"]["x_um"] == pytest.approx(193.85, abs=0.5) and result["clipped"]["asked_um"][0] == 900.0


@pytest.mark.parametrize("direction", ["up-left", "down-right", "left"])
def test_the_window_s_buttons_step_the_pan_in_their_direction(lasx, client, tmp_path, direction):
    lasx.live[(*SCANNING, "Zoom")] = 2.0
    lasx.live[("Acquire", "AcquireImageSizeInfo", "ImageSizeX")] = 0.0011625 / 2
    pad = Pad(lasx)

    result = pan.step(client, tmp_path, direction, 2, pad)

    across, up = pan.VECTORS[direction]
    assert result["confirmed"] is True and [arrow for arrow, _ in pad.pressed] == [direction, direction]
    assert (result["pan"]["x_um"], result["pan"]["y_um"]) == (pytest.approx(across * 34.875, abs=0.01), pytest.approx(up * 34.875, abs=0.01))


def test_the_window_s_middle_button_centres_the_pan(lasx, client, tmp_path):
    lasx.pan = [0.0005, 0.0002]

    result = pan.step(client, tmp_path, "centre", 1, Pad(lasx))

    assert result["confirmed"] is True and lasx.pan == [0.0, 0.0]


def test_with_the_resonant_scanner_the_window_is_not_known_and_nothing_is_clipped_ahead(lasx, client, tmp_path):
    lasx.live[("Confocal", "ScanningParameter", "IsResonantConfocalScanner")] = True

    assert pan.window_um(client) is None
    assert pan.clip(client, 900.0, 0.0) == {"asked_um": (900.0, 0.0), "to_um": (900.0, 0.0), "over_um": (0.0, 0.0), "window_um": None}


def test_the_window_s_step_is_read_from_lcs(lasx, client):
    assert pan.window_step(client) == {"percent": 3.0, "um": pytest.approx(34.875)}


@pytest.mark.parametrize("route", ["window", "scanner"])
def test_the_window_is_measured_from_where_las_x_clips_and_matches_the_formula(lasx, client, tmp_path, route):
    steps = Steps(lasx)
    pad = Pad(lasx) if route == "window" else pan.ScannerPad(client, percent=3, steps=steps)

    result = pan.measure_window(client, tmp_path, pad, zooms=(1.0, 2.0))

    assert result["largest_pan"] == pytest.approx(0.00775, rel=1e-3) and result["field_fraction"] == pytest.approx(0.6667, rel=1e-3)
    assert result["matches_the_code"] is True and [m["zoom"] for m in result["measured"]] == [1.0, 2.0]
    assert lasx.pan == [0.0, 0.0] and lasx.live[(*SCANNING, "Zoom")] == pytest.approx(1.0)
