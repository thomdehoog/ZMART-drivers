"""The elements the plain Navigator route sends, as LAS X took them on the STELLARIS (2026-10-15).

Ported from the private branch's ``test_navigator_route.py``: the current
settings are job 0, the objective goes by the window's own SetSlotPosition, a
capture and a tile scan are the window's Capture Image and Preview, each
naming the project its picture goes into. Here the elements are read off the
``Write`` each builder makes, by filling its request as the transfer does.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator.primitives.direct import writes


def elements(write, **fills):
    """The elements *write* sends, filled in as the transfer fills them."""
    request = writes.Request(write.request)
    write.fill(request)
    return request.elements


class TestSettings:
    @pytest.mark.parametrize(
        ("name", "value", "xml"),
        [
            ("zoom", 2.5, '<Scanner Command="SetZoom" Zoom="2.5" BlockId="0"/>'),
            ("scan_speed", 600, '<Scanner Command="SetScanSpeed" Speed="600" BlockId="0"/>'),
            ("scan_field_rotation", 5, '<Scanner Command="SetConfocalScanFieldRotation" Rotation="5" BlockId="0"/>'),
            ("line_average", 2, '<Scanner Command="SetLineAverage" LineAverage="2" SetMode="2" SingleSettingIndex="0" BlockId="0"/>'),
            ("pinhole_airy", 1.5, '<Scanner Command="SetPinholeAU" PinholeAU="1.5" SetMode="2" SingleSettingIndex="0" BlockId="0"/>'),
        ],
    )
    def test_a_setting_is_written_to_the_current_settings(self, name, value, xml):
        assert elements(writes.setting(name, value)) == [xml]

    def test_a_setting_names_itself_for_the_limits(self):
        write = writes.setting("zoom", 2.5)
        assert (write.command, write.values) == ("set_zoom", {"value": 2.5})

    def test_an_unknown_setting_is_refused(self):
        with pytest.raises(ValueError, match="laser"):
            writes.setting("laser", 1)

    def test_a_setting_takes_a_number(self):
        with pytest.raises(ValueError, match="number"):
            writes.setting("zoom", "two")

    @pytest.mark.parametrize(
        ("name", "reading", "shows"),
        [
            ("zoom", {"current": 2.52}, True),
            ("zoom", {"current": 3.0}, False),
            ("scan_speed", {"value": 600}, True),
            ("scan_speed", {"value": 599}, False),
            ("line_average", {"lineAverage": 2}, True),
            ("pinhole_airy", {"airy": {"value": 1.52}}, True),
            ("pinhole_airy", {"airy": {"value": 1.7}}, False),
            ("pinhole_airy", {}, False),
        ],
    )
    def test_the_read_back_shows_the_setting_within_its_tolerance(self, name, reading, shows):
        value = {"zoom": 2.5, "scan_speed": 600, "line_average": 2, "pinhole_airy": 1.5}[name]
        assert writes.setting(name, value).confirm.accept(reading) is shows


class TestMoves:
    def test_z_moves_by_a_step_from_where_the_drive_stands_in_metres(self):
        write = writes.move_z("z-galvo", 2.0, now=lambda: 0.0)
        assert elements(write) == ['<Scanner Command="SetZPosition" PosZ="0.0000020000" RelativeCoordinates="1" ZUseMode="1" BlockId="0"/>']
        write = writes.move_z("z-wide", 98.0, now=lambda: 100.0)
        assert elements(write) == ['<Scanner Command="SetZPosition" PosZ="-0.0000020000" RelativeCoordinates="1" ZUseMode="2" BlockId="0"/>']

    def test_the_step_is_worked_out_afresh_at_every_send(self):
        """A move sent again after a half-done first send must not overshoot."""
        standing = [0.0]
        write = writes.move_z("z-galvo", 2.0, now=lambda: standing[0])
        assert 'PosZ="0.0000020000"' in elements(write)[0]
        standing[0] = 1.5
        assert 'PosZ="0.0000005000"' in elements(write)[0]

    def test_a_z_move_is_confirmed_by_the_drive_s_own_reading(self):
        write = writes.move_z("z-wide", 98.0, now=lambda: 100.0)
        assert (write.confirm.reading, write.confirm.about) == ("z", "z-wide")
        assert write.confirm.accept(98.4) and not write.confirm.accept(96.0)
        assert write.values == {"z_wide_um": 98.0}

    def test_an_unknown_drive_is_refused(self):
        with pytest.raises(ValueError, match="z-piezo"):
            writes.move_z("z-piezo", 1.0)

    def test_the_stage_moves_to_a_place_in_metres(self):
        write = writes.move_xy(63525, 41500)
        assert elements(write) == [
            '<Scanner Command="SetStagePositionInHwOnly" StagePosX="0.0635250000" StagePosY="0.0415000000" '
            'RelativeCoordinates="0" MoveStageX="1" MoveStageY="1"/>'
        ]
        assert write.values == {"x_um": 63525, "y_um": 41500}
        assert write.confirm.accept({"x_um": 63530.0, "y_um": 41490.0})
        assert not write.confirm.accept({"x_um": 63600.0, "y_um": 41500.0})

    def test_the_objective_goes_by_the_window_s_own_command(self):
        write = writes.set_objective(1, "20x")
        assert elements(write) == ['<Microscope Command="SetSlotPosition" SlotPosition="1"/>']
        assert write.values == {"objective_slot": 1}
        assert write.confirm.accept({"slotIndex": 1}) and not write.confirm.accept({"slotIndex": 0})

    def test_every_change_waits_for_the_scanner_to_be_idle_first(self):
        for write in (writes.setting("zoom", 2.0), writes.move_xy(1.0, 2.0), writes.move_z("z-galvo", 0.0, now=lambda: 0.0), writes.set_objective(0, "10x")):
            assert write.ready.reading == "scan_status"
            assert write.ready.accept("eScanIdle") and not write.ready.accept("eScanSingle")


class TestCapturing:
    """A capture and a tile scan name the project their picture goes into, as LAS X's window does.

    The STELLARIS refused a tile scan sent with ``"0"`` ("Invalid or deleted element ID"),
    though the simulator took it; with an open project, 2518, it took it and put the mosaic there.
    """

    def test_a_capture_is_the_window_s_capture_image_into_its_project(self):
        write = writes.capture(2518)
        assert elements(write) == [
            '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
            '<AcquireControl Command="CaptureSingleFrame" DCParentElementID="2518" IsSingleImageCapture="1" '
            'SingleScanMode="0" AdditionalValue="1.0000000000" AdditionalValue2="0"/>',
        ]
        assert write.never_resend and write.confirm is None
        assert write.ready.without_limit, "a capture waits for idle as long as it takes"

    @pytest.mark.parametrize("project", [None, 0, -1, True, 2.5])
    def test_a_picture_without_a_real_project_is_refused(self, project):
        with pytest.raises(ValueError, match="project"):
            writes.capture(project)
        with pytest.raises(ValueError, match="project"):
            writes.tile_scan([(63500, 41500, 0)], project)

    def test_a_tile_scan_is_the_window_s_preview_over_the_positions(self):
        write = writes.tile_scan([(63500, 41500, 0), (64000, 41500, 0)], 2518)
        positions = (
            '<Scanner Command="SetStageIrregularTileScanPositions" FocusOffset="0.0000000000">'
            '<Tile XPos="0.0635000000" YPos="0.0415000000" ZPos="0.0000000000" FocusStabilizerOffset="0.0000000000"/>'
            '<Tile XPos="0.0640000000" YPos="0.0415000000" ZPos="0.0000000000" FocusStabilizerOffset="0.0000000000"/>'
            "</Scanner>"
        )
        assert elements(write) == [
            '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
            '<Scanner Command="SetStageUseMode" StageUseMode="7"/>',
            positions,
            '<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="1"/>',
            '<Scanner Command="ScanMode" ScanMode="xyt" BlockId="0"/>',
            '<Scanner Command="ActivateAutoFocus" Activate="0" InitNew="0"/>',
            '<Scanner Command="SetStageUseMode" StageUseMode="7"/>',
            positions,
            '<AcquireControl Command="StartAcquiring" DCParentElementID="2518"/>',
        ]
        assert write.never_resend
        assert write.values == {"positions_um": [(63500.0, 41500.0, 0.0), (64000.0, 41500.0, 0.0)], "project": 2518}

    def test_after_a_tile_scan_everything_is_put_back_as_the_window_does(self):
        write = writes.after_tile_scan(63500, 41500, 0)
        assert elements(write) == [
            '<Scanner Command="StageMoveToCoordinates" PosX="0.0635000000" PosY="0.0415000000" PosZ="0.0000000000"/>',
            '<Scanner Command="SetStageUseMode" StageUseMode="0"/>',
            '<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="0"/>',
            '<Scanner Command="ScanMode" ScanMode="xyz" BlockId="0"/>',
            '<Scanner Command="ActivateAutoFocus" Activate="1" InitNew="0"/>',
        ]
        assert not write.never_resend
        assert write.confirm.reading == "xy" and write.values == {"x_um": 63500, "y_um": 41500, "z_galvo_um": 0}

    def test_a_tile_scan_needs_a_position(self):
        with pytest.raises(ValueError, match="no position"):
            writes.tile_scan([], 2518)


def test_every_change_hands_its_name_and_values_to_the_dispatcher():
    """Each builder makes a Write that says which command it is and what it sets, for the limits gate."""
    import ast
    from pathlib import Path

    source = Path(writes.__file__).read_text(encoding="utf-8")
    calls = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Write":
            calls += 1
            keywords = {k.arg for k in node.keywords}
            assert {"command", "values"} <= keywords, f"line {node.lineno}: Write needs command= and values="
    assert calls >= 7
