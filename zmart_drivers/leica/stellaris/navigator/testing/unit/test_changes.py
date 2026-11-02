"""The named changes through the dispatcher, against the stand-in for LAS X's plain Navigator.

Every change goes the way a workflow's does: the limits gate first, then the
elements to the hardware server, then the read-back from the state tree. The
stand-in applies the elements as LAS X does, so what is checked is the whole
chain: a setting confirmed, a move refused by the limits before anything is
sent, a z move sent as a step and confirmed where it lands, an objective
change, a capture watched to the end, and a tile scan with its put-back.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import install_permissive_limits

from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as gate
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    reads as direct_reader,
)
from zmart_drivers.leica.stellaris.navigator.procedures import changes
from zmart_drivers.leica.stellaris.navigator.procedures.clear_cache import (
    data_container,
)


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator())


@pytest.fixture
def client(lasx):
    return install_permissive_limits(hardware_server.Client("test"), wide_stage=True)


SCANNING = ("Confocal", "ScanningParameter")


class TestSettings:
    def test_a_setting_is_sent_once_and_confirmed_by_the_read_back(self, lasx, client):
        result = changes.change(client, "zoom", 2.5)

        assert result["success"] and result["confirmed"] is True
        assert result["route"] == "direct"
        assert lasx.commands() == ["SetZoom"]
        assert lasx.live[(*SCANNING, "Zoom")] == 2.5
        assert direct_reader.get_zoom()["current"] == 2.5

    @pytest.mark.parametrize(
        ("name", "value", "path"),
        [
            ("scan_speed", 600, (*SCANNING, "Speed")),
            ("scan_field_rotation", 5.0, ("Confocal", "HWParameter", "ScanRotationAngle")),
            ("line_average", 2, (*SCANNING, "LineAverage")),
            ("pinhole_airy", 1.5, ("Confocal", "HWParameter", "PinholeAU")),
        ],
    )
    def test_each_of_the_five_settings_lands_in_the_state_tree(self, lasx, client, name, value, path):
        result = changes.change(client, name, value)

        assert result["confirmed"] is True, result["message"]
        assert lasx.live[path] == pytest.approx(value)

    def test_a_value_las_x_refuses_fails_at_once_with_its_reason(self, lasx, client):
        result = changes.change(client, "zoom", 100)

        assert result["success"] is False
        assert "out of range" in result["message"]
        assert lasx.commands() == ["SetZoom"], "a permanent refusal is not sent again"

    def test_an_adjusted_value_is_accepted_and_the_read_back_decides(self, lasx, client):
        result = changes.change(client, "pinhole_airy", 100)

        assert result["success"] is True
        assert result["confirmed"] is False, "LAS X set the pinhole to its maximum, not to 100"

    def test_a_setting_the_limits_refuse_is_never_sent(self, lasx):
        client = install_permissive_limits(hardware_server.Client("test"), wide_stage=True)
        gate.install(
            client,
            gate.GateState(
                limits=gate.state_of(client).limits.__class__(
                    {**{k: [] for k in ("x_um", "y_um", "z_galvo_um", "z_wide_um", "objective_slot")},
                     "set_zoom": {"range": [1, 4]}, "set_scan_speed": [], "set_scan_field_rotation": [],
                     "set_line_average": [], "set_pinhole_airy": []},
                    source="test", path=None, is_fallback=False,
                ),
                stage_cfg=None,
                error=None,
            ),
        )

        result = changes.change(client, "zoom", 8)

        assert result["success"] is False and "refused" in result["message"]
        assert lasx.commands() == []

    def test_an_unknown_setting_raises(self, client):
        with pytest.raises(ValueError, match="laser"):
            changes.change(client, "laser", 1)


class TestMoves:
    def test_the_stage_moves_and_is_read_back(self, lasx, client):
        result = changes.move_xy(client, 63600.0, 41400.0)

        assert result["confirmed"] is True
        assert result["position"] == {"x_um": 63600.0, "y_um": 41400.0, "x": 0.0636, "y": 0.0414}
        assert (lasx.x_um, lasx.y_um) == (pytest.approx(63600.0), pytest.approx(41400.0))

    def test_a_move_outside_the_limits_is_refused_before_anything_is_sent(self, lasx, client):
        result = changes.move_xy(client, 500_000.0, 10.0)

        assert result["success"] is False and "backstop" in result["message"]
        assert result["position"] is None
        assert lasx.commands() == []

    def test_a_z_move_is_sent_as_a_step_from_where_the_drive_stands(self, lasx, client):
        lasx.z_um[2] = 100.0

        result = changes.move_z(client, "z-wide", 102.0)

        assert result["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(102.0)
        assert 'PosZ="0.0000020000" RelativeCoordinates="1" ZUseMode="2"' in lasx.sent[-1]

    def test_a_z_move_the_limits_refuse_is_never_sent(self, lasx, client):
        result = changes.move_z(client, "z-galvo", 500.0)

        assert result["success"] is False and "outside galvo limits" in result["message"]
        assert lasx.commands() == []

    def test_a_z_move_that_only_half_happened_is_sent_again_with_the_rest(self, lasx, client, monkeypatch):
        """LAS X's plain Navigator moves z by a step; a second send must not add the whole step again."""
        halves = {"left": 1}

        def half_step(self, e):
            drive, step_um = int(e.get("ZUseMode")), float(e.get("PosZ")) * 1e6
            if halves["left"]:
                halves["left"] -= 1
                step_um /= 2
            self.z_um[drive] += step_um

        monkeypatch.setattr(PlainNavigator, "take_SetZPosition", half_step)

        result = changes.move_z(client, "z-galvo", 4.0, tolerance=0.1)

        assert result["confirmed"] is True
        assert lasx.z_um[1] == pytest.approx(4.0)
        assert ['PosZ="0.0000040000"' in x for x in lasx.sent if "SetZPosition" in x] == [True, False]


class TestTheObjective:
    def objectives(self):
        return direct_reader.get_objectives()

    def test_the_objective_turns_by_the_window_s_own_command_and_is_read_back(self, lasx, client):
        result = changes.set_objective(client, self.objectives(), slot_index=1)

        assert result["confirmed"] is True
        assert lasx.live[("Microscope", "SlotPosition")] == 1
        assert lasx.commands() == ["SetSlotPosition"]

    def test_the_objective_is_found_by_name_or_magnification(self, lasx, client):
        assert changes.find_objective(self.objectives(), magnification=20.0)[0] == 1
        assert changes.find_objective(self.objectives(), name="HC PL APO CS  20.0x0.70 UV")[0] == 1
        assert changes.find_objective(self.objectives(), slot_index=5) == (None, None)

    def test_exactly_one_way_of_choosing_is_taken(self, lasx, client):
        result = changes.set_objective(client, self.objectives(), slot_index=1, magnification=20.0)
        assert result["success"] is False and "exactly one" in result["message"]
        assert lasx.commands() == []


class TestPictures:
    @pytest.fixture(autouse=True)
    def project(self, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)

    def test_a_capture_is_sent_once_and_watched_until_the_scan_is_over(self, lasx, client):
        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "capture"
        assert acq.command_result["confirmed"] is True
        assert lasx.commands() == ["StopAcquiring", "CaptureSingleFrame"]
        assert 'DCParentElementID="2518"' in lasx.sent[-1]
        assert lasx.captures == 1

    def test_a_capture_whose_scan_never_starts_fails(self, lasx, client, monkeypatch):
        monkeypatch.setattr(PlainNavigator, "take_CaptureSingleFrame", lambda self, e: None)

        with pytest.raises(RuntimeError, match="never started"):
            changes.take_picture(client, start_timeout=0.2, poll_interval=0.01)

    def test_a_capture_las_x_refuses_fails_with_its_reason_and_is_not_sent_again(self, lasx, client):
        lasx.answers["CaptureSingleFrame"] = '<Result Error="1">Invalid or deleted element ID used</Result>'

        with pytest.raises(RuntimeError, match="deleted element"):
            changes.take_picture(client, poll_interval=0.01)

        assert lasx.commands().count("CaptureSingleFrame") == 1

    def test_a_capture_without_a_project_sends_nothing(self, lasx, client, monkeypatch):
        def no_project():
            raise RuntimeError("LAS X's Data Container did not give a project for the image")

        monkeypatch.setattr(data_container, "experiment_for_capture", no_project)

        result = changes.capture(client)

        assert result["success"] is False and "project" in result["message"]
        assert lasx.commands() == []

    def test_a_tile_scan_runs_and_puts_the_stage_back_at_the_first_field(self, lasx, client):
        positions = [(63500.0, 41500.0, 0.0), (64139.0, 41500.0, 0.0), (64778.0, 41500.0, 0.0)]

        acq = changes.take_tile_scan(client, positions, poll_interval=0.01)

        assert acq.picture == "tile_scan"
        assert acq.command_result["confirmed"] is True
        sent = lasx.commands()
        assert sent[:9] == [
            "StopAcquiring", "SetStageUseMode", "SetStageIrregularTileScanPositions", "ATL_SuppressAtlasAttachment",
            "ScanMode", "ActivateAutoFocus", "SetStageUseMode", "SetStageIrregularTileScanPositions", "StartAcquiring",
        ]
        assert sent[9:] == ["StageMoveToCoordinates", "SetStageUseMode", "ATL_SuppressAtlasAttachment", "ScanMode", "ActivateAutoFocus"]
        assert acq.command_result["put_back"]["confirmed"] is True
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyz"

    def test_a_tile_scan_whose_scan_never_starts_still_puts_everything_back(self, lasx, client, monkeypatch):
        # As on the LAS X simulator on 2026-10-30: the preparation is taken (the
        # scan mode is xyt), then LAS X cannot start the scan, because no channel
        # is active. What the preparation changed must not be left behind.
        monkeypatch.setattr(PlainNavigator, "take_StartAcquiring", lambda self, e: None)
        positions = [(63500.0, 41500.0, 0.0), (64139.0, 41500.0, 0.0)]

        with pytest.raises(RuntimeError, match="never started"):
            changes.take_tile_scan(client, positions, start_timeout=0.2, poll_interval=0.01)

        assert lasx.commands()[-5:] == ["StageMoveToCoordinates", "SetStageUseMode", "ATL_SuppressAtlasAttachment", "ScanMode", "ActivateAutoFocus"]
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyz"

    def test_a_tile_scan_leaves_z_wide_where_it_is_when_the_z_stack_is_on_z_wide(self, lasx, client):
        # On the simulator (2026-11-01) LCS read a tile's ZPos as the position of the
        # z-stack's drive: with the stack on z-wide, z-wide went from 1522.7 µm to 0.
        lasx.live[("Acquire", "ZPositionMode")] = 2
        lasx.z_um = {1: 0.0, 2: 1522.7}
        positions = [(63500.0, 41500.0, 0.0), (64800.0, 41500.0, 0.0)]

        changes.take_tile_scan(client, positions, poll_interval=0.01)

        assert lasx.z_um[2] == pytest.approx(1522.7)
        tiles = next(x for x in lasx.sent if "SetStageIrregularTileScanPositions" in x)
        assert tiles.count('ZPos="0.0015227000"') == 2
        assert 'PosZ="0.0015227000"' in next(x for x in lasx.sent if "StageMoveToCoordinates" in x)

    def test_a_tile_scan_keeps_z_galvo_s_position_when_the_z_stack_is_on_z_galvo(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 1
        lasx.z_um = {1: 2.0, 2: 1522.7}

        changes.take_tile_scan(client, [(63500.0, 41500.0, 2.0)], poll_interval=0.01)

        assert 'ZPos="0.0000020000"' in next(x for x in lasx.sent if "SetStageIrregularTileScanPositions" in x)
        assert lasx.z_um == {1: pytest.approx(2.0), 2: pytest.approx(1522.7)}

    def test_a_tile_scan_at_another_z_galvo_position_is_refused_when_the_z_stack_is_on_z_wide(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 2
        lasx.z_um = {1: 0.0, 2: 1522.7}

        with pytest.raises(RuntimeError, match="z-wide"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 5.0)], poll_interval=0.01)

        assert lasx.commands() == []

    def test_a_tile_scan_is_refused_when_the_z_stack_s_drive_cannot_be_read(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 7

        with pytest.raises(RuntimeError, match="drive"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 0.0)], poll_interval=0.01)

        assert lasx.commands() == []

    def test_a_tile_scan_with_a_position_outside_the_limits_sends_nothing(self, lasx, client):
        with pytest.raises(RuntimeError, match="backstop"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 0.0), (500_000.0, 41500.0, 0.0)])

        assert lasx.commands() == []


class TestTheLargestStepOnTheWay:
    """The changes hand the gate where each drive stands, so the step is checked before anything is sent."""

    @pytest.fixture
    def stepped(self, lasx):
        from limits_fixtures import DEFAULT_STAGE_UM, merged_limits_payload

        from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules

        client = hardware_server.Client("test")
        payload = merged_limits_payload(dict(DEFAULT_STAGE_UM))
        gate.install(client, gate.GateState(
            limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False),
            stage_cfg=None, error=None))
        limit_rules.set_stage_limits(
            x_min=1000.0, x_max=130000.0, y_min=1000.0, y_max=100000.0,
            z_galvo_min=-250.0, z_galvo_max=250.0, z_wide_min=0.0, z_wide_max=8000.0,
        )
        yield client
        gate.uninstall(client)

    def test_a_large_z_wide_move_is_refused_and_nothing_is_sent(self, lasx, stepped):
        result = changes.move_z(stepped, "z-wide", 600.0)

        assert result["success"] is False and "step_z_wide_um" in result["message"]
        assert lasx.commands() == []

    def test_a_small_z_wide_move_goes_through(self, lasx, stepped):
        result = changes.move_z(stepped, "z-wide", 150.0, tolerance=0.1)

        assert result["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(150.0)

    def test_a_stage_move_hands_the_gate_where_the_stage_stands(self, lasx, stepped):
        assert changes.move_xy(stepped, 64000.0, 41500.0, tolerance=1)["confirmed"] is True



class TestTheZStack:
    def test_the_stack_is_set_drive_range_and_step_and_read_back(self, lasx, client):
        lasx.z_um = {1: 0.0, 2: 1000.0}

        result = changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        assert result["confirmed"] is True
        assert result["z_stack"] == {
            "begin": pytest.approx(995.0), "end": pytest.approx(1005.0), "stepSize": pytest.approx(2.5),
            "size": pytest.approx(10.0), "sections": 5, "mode": "z-wide",
        }
        assert lasx.commands() == ["SetZUseMode", "SetZStackDefinition", "SetStackStepSize"]

    def test_with_a_stack_set_a_picture_takes_the_whole_stack(self, lasx, client, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)
        lasx.z_um = {1: 0.0, 2: 1000.0}
        changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "z_stack"
        assert lasx.commands()[-2:] == ["StopAcquiring", "StartAcquiring"]

    def test_without_a_stack_a_picture_is_one_frame(self, lasx, client, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)

        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "capture"
        assert lasx.commands()[-1] == "CaptureSingleFrame"



class TestScanModeAndAutofocus:
    def test_the_scan_mode_is_changed_and_read_back(self, lasx, client):
        result = changes.change(client, "scan_mode", "xyt")

        assert result["confirmed"] is True
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyt"

    def test_autofocus_is_switched_off_and_read_back(self, lasx, client):
        result = changes.change(client, "autofocus", False)

        assert result["confirmed"] is True
        assert lasx.live[("Acquire", "AutoFocusInfo", "IsSeriesScanAutofocusActive")] is False



class TestLasers:
    def test_a_line_not_in_use_is_found_by_its_wavelength_switched_on_and_read_back(self, lasx, client):
        result = changes.set_laser_power(client, 488.0, 5.0)

        assert result["confirmed"] is True
        assert '<BeamPosition BeamPositionLevel="1" BeamPosition="30"/>' in lasx.sent[-1]
        lines = {line["wavelength"]: line for line in direct_reader.get_laser_lines()}
        assert lines[488.0]["intensity"] == pytest.approx(0.05098, abs=0.001)

    def test_zero_switches_the_line_off(self, lasx, client):
        changes.set_laser_power(client, 488.0, 5.0)

        assert changes.set_laser_power(client, 488.0, 0.0)["confirmed"] is True
        assert 488.0 not in {line["wavelength"] for line in direct_reader.get_laser_lines()}

    def test_a_wavelength_no_laser_has_is_refused_and_nothing_is_sent(self, lasx, client):
        result = changes.set_laser_power(client, 500.0, 5.0)

        assert result["success"] is False and "500" in result["message"]
        assert lasx.commands() == []

    def test_a_line_s_shutter_is_closed_and_read_back(self, lasx, client):
        result = changes.set_laser_shutter(client, 405.0, False)

        assert result["confirmed"] is True
        assert direct_reader.get_laser_lines()[0]["shutterOpen"] is False

    def test_a_power_above_the_limit_is_refused_before_anything_is_sent(self, lasx):
        from limits_fixtures import DEFAULT_STAGE_UM, merged_limits_payload

        from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules

        limited = hardware_server.Client("test")
        payload = merged_limits_payload(dict(DEFAULT_STAGE_UM))
        gate.install(limited, gate.GateState(
            limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False),
            stage_cfg=None, error=None))
        try:
            result = changes.set_laser_power(limited, 405.0, 50.0)
        finally:
            gate.uninstall(limited)

        assert result["success"] is False and "set_laser_power" in result["message"]
        assert lasx.commands() == []



class TestTheAcquisitionSettings:
    @pytest.mark.parametrize(
        ("name", "node"),
        [("frame_average", "FrameAverage"), ("line_accumulation", "LineAccu"), ("frame_accumulation", "FrameAccumulation")],
    )
    def test_averaging_and_accumulation_are_changed_and_read_back(self, lasx, client, name, node):
        assert changes.change(client, name, 2)["confirmed"] is True
        assert lasx.live[(*SCANNING, node)] == 2

    def test_the_format_is_changed_and_read_back(self, lasx, client):
        assert changes.change(client, "format", (256, 256))["confirmed"] is True
        assert lasx.live[(*SCANNING, "CurrentFormat")] == "256 x 256"

    def test_the_resonant_scanner_is_switched_and_read_back(self, lasx, client):
        assert changes.change(client, "resonant_scanner", True)["confirmed"] is True
        assert lasx.live[(*SCANNING, "IsResonantConfocalScanner")] is True

    def test_a_filter_wheel_is_turned_to_a_filter_by_its_name(self, lasx, client):
        result = changes.set_filter_wheel(client, 140, "Substrate")

        assert result["confirmed"] is True
        assert direct_reader.get_filter_wheels()[1]["filterName"].strip() == "Substrate"

    def test_lasx_s_own_galvo_and_scanner_wheels_are_never_turned(self, lasx, client):
        result = changes.set_filter_wheel(client, 200, "Galvo X Pan Mirror")

        assert result["success"] is False and "200" in result["message"]
        assert lasx.commands() == []

    def test_a_filter_the_wheel_does_not_hold_is_refused(self, lasx, client):
        result = changes.set_filter_wheel(client, 140, "Polarizer")

        assert result["success"] is False and "Polarizer" in result["message"]
        assert lasx.commands() == []



class TestChannelColour:
    def test_a_detector_s_colour_is_set_and_read_back(self, lasx, client):
        result = changes.set_channel_colour(client, "HyD S 3", "Green")

        assert result["confirmed"] is True
        assert direct_reader.get_channel_colours()[3] == "Green"

    def test_a_detector_outside_the_spectral_detection_is_refused(self, lasx, client):
        result = changes.set_channel_colour(client, "Trans PMT", "Green")

        assert result["success"] is False and "Trans PMT" in result["message"]
        assert lasx.commands() == []



class TestBidirectional:
    def test_bidirectional_is_switched_in_the_state_tree_and_read_back(self, lasx, client):
        assert changes.change(client, "bidirectional", True)["confirmed"] is True
        assert lasx.live[(*SCANNING, "ScanDirectionX")] == 2
        assert changes.change(client, "bidirectional", False)["confirmed"] is True
        assert lasx.live[(*SCANNING, "ScanDirectionX")] == 1
        assert lasx.commands() == []  # no command: the value is written into the state tree



HW = ("Confocal", "HWParameter")


class TestPinholeAndPhase:
    def test_phase_x_is_written_and_read_back(self, lasx, client):
        assert changes.change(client, "phase_x", -20)["confirmed"] is True
        assert lasx.live[(*HW, "PhaseX")] == -20.0
        assert lasx.commands() == []

    def test_the_pinhole_is_written_in_metres_and_read_back(self, lasx, client):
        assert changes.change(client, "pinhole_um", 250)["confirmed"] is True
        assert lasx.live[(*HW, "Pinhole")] == pytest.approx(0.00025)
        assert lasx.commands() == ["SetPinhole"]  # the window's own command, not the state tree

    def test_the_unit_and_the_emission_wavelength_are_written_and_read_back(self, lasx, client):
        assert changes.change(client, "pinhole_unit", "um")["confirmed"] is True
        assert lasx.live[(*HW, "PinholeUnit")] == 1
        assert changes.change(client, "emission_wavelength", 520)["confirmed"] is True
        assert lasx.live[(*HW, "EmissionWaveLengthForAiryAnddzCalculation")] == 520

    @pytest.mark.parametrize(("name", "value", "node"), [("pinhole_um", 2000, "Pinhole"), ("pinhole_um", 50, "Pinhole"), ("phase_x", 150, "PhaseX")])
    def test_a_value_outside_lcs_own_range_is_refused_and_nothing_is_written(self, lasx, client, name, value, node):
        before = lasx.live[(*HW, node)]

        result = changes.change(client, name, value)

        assert result["success"] is False and "range" in result["message"]
        assert lasx.live[(*HW, node)] == before
        assert "SetPinhole" not in lasx.commands()  # refused before it was sent: LCS's command does not keep to its range

    def test_the_range_is_lcs_own_read_at_the_write(self, lasx, client):
        lasx.live[(*HW, "PinholeMax")] = 0.0005

        assert changes.change(client, "pinhole_um", 600)["success"] is False
        assert changes.change(client, "pinhole_um", 500)["confirmed"] is True



ZSTACK = ("Acquire", "AcquireZStackInfo")


class TestBitDepthAndOptimizedStep:
    def test_the_bit_depth_is_written_and_read_back(self, lasx, client):
        assert changes.change(client, "bit_depth", 12)["confirmed"] is True
        assert lasx.live[(*SCANNING, "Resolution")] == 12
        assert lasx.commands() == []

    def test_a_bit_depth_lcs_does_not_list_is_refused_and_nothing_is_written(self, lasx, client):
        result = changes.change(client, "bit_depth", 10)

        assert result["success"] is False and "8, 12, 16" in result["message"]
        assert lasx.live[(*SCANNING, "Resolution")] == 8

    def test_the_system_optimized_step_takes_lcs_optimal_step_and_is_switched_off_again(self, lasx, client):
        assert changes.change(client, "system_optimized_step", True)["confirmed"] is True
        assert lasx.live[(*ZSTACK, "StackCalculationMode")] == 2
        assert lasx.live[(*ZSTACK, "StepSize")] == lasx.live[(*ZSTACK, "OptimalStepSize")]
        assert changes.change(client, "system_optimized_step", False)["confirmed"] is True
        assert lasx.live[(*ZSTACK, "StackCalculationMode")] == 1



class TestCollar:
    def test_the_collar_is_moved_and_read_back(self, lasx, client):
        assert changes.change(client, "collar", 50)["confirmed"] is True
        assert lasx.live[("Microscope", "CurrentMotCorrPos")] == 50.0
        assert lasx.commands() == ["SetMotCorrPosition"]



MOTCORR = ("Microscope", "MotCorrAutomationInfo")


class TestSmartCorr:
    def test_the_range_is_written_into_the_state_tree_and_read_back(self, lasx, client):
        assert changes.change(client, "smartcorr_range", (20, 80, 5))["confirmed"] is True
        assert (lasx.live[(*MOTCORR, "AutomationBegin")], lasx.live[(*MOTCORR, "AutomationEnd")], lasx.live[(*MOTCORR, "AutomationSteps")]) == (20.0, 80.0, 5)
        assert lasx.commands() == []

    def test_smartcorr_runs_and_answers_where_the_collar_went(self, lasx, client):
        lasx.smartcorr_finds = 40.0

        result = changes.smartcorr(client, project=24)

        assert result["success"] is True and result["confirmed"] is True
        assert result["collar"]["percent"] == 40.0
        assert lasx.commands() == ["StopAcquiring", "StartMotCorrAutomation"]

    def test_the_collar_goes_to_the_objective_s_reference_as_goto_does(self, lasx, client):
        lasx.nodes.objectives[0]["MotCorrRefPos"] = 30.0

        assert changes.collar_to_reference(client)["confirmed"] is True
        assert lasx.live[("Microscope", "CurrentMotCorrPos")] == 30.0

    def test_without_a_reference_nothing_is_sent(self, lasx, client):
        result = changes.collar_to_reference(client)

        assert result["success"] is False and "reference" in result["message"]
        assert lasx.commands() == []



class TestCollarReference:
    def test_set_stores_where_the_collar_stands_for_the_objective_in_the_light_path(self, lasx, client):
        lasx.live[("Microscope", "SlotPosition")] = 1
        lasx.live[("Microscope", "CurrentMotCorrPos")] = 35.0

        assert changes.set_collar_reference(client)["confirmed"] is True
        assert lasx.nodes.objectives[1]["MotCorrRefPos"] == 35.0
        assert lasx.nodes.objectives[0]["MotCorrRefPos"] == -1.0  # the other objectives keep theirs
        assert lasx.commands() == []

    def test_reset_clears_it_and_goto_is_then_refused(self, lasx, client):
        lasx.nodes.objectives[0]["MotCorrRefPos"] = 20.0

        assert changes.reset_collar_reference(client)["confirmed"] is True
        assert lasx.nodes.objectives[0]["MotCorrRefPos"] == -1.0
        assert changes.collar_to_reference(client)["success"] is False



SETTING = ("Acquire", "ATL", "CurrentSetting")


class TestTheSetting:
    def test_the_setting_is_switched_off_and_on_by_its_id(self, lasx, client):
        assert changes.enable_setting(client, False)["confirmed"] is True
        assert lasx.live[(*SETTING, "IsEnabledForScanning")] is False
        assert 'SettingID="3"' in lasx.sent[-1] and 'EnableForScanning="0"' in lasx.sent[-1]
        assert changes.enable_setting(client, True)["confirmed"] is True
        assert lasx.live[(*SETTING, "IsEnabledForScanning")] is True

    def test_the_setting_is_renamed_in_the_state_tree(self, lasx, client):
        assert changes.change(client, "setting_name", "ZMART test")["confirmed"] is True
        assert lasx.live[(*SETTING, "Name")] == "ZMART test"
        assert lasx.commands() == []


class TestTheDetectorLock:
    def test_a_detector_and_its_excitation_line_are_locked_and_read_back(self, lasx, client, tmp_path):
        result = changes.lock_detector(client, tmp_path, "HyD S 3", True)

        assert result["confirmed"] is True
        assert lasx.commands() == ["ATL_Block_Save_To_XML", "BlockLightSinkDetectorForDyeAssistant", "BlockLightSourceLineForDyeAssistant"]
        assert 'LineIndex="0"' in lasx.sent[-1] and 'BeamPosition="30"' in lasx.sent[-1]  # the 405 line, the setting's excitation line
        assert changes.lock_detector(client, tmp_path, "HyD S 3", False)["confirmed"] is True

    def test_a_detector_not_in_use_is_refused_and_nothing_is_sent(self, lasx, client, tmp_path):
        result = changes.lock_detector(client, tmp_path, "Trans PMT", True)

        assert result["success"] is False and "in use" in result["message"]
        assert lasx.commands() == []



class TestSettingsAddedAndDeleted:
    def test_a_setting_is_added_becomes_the_current_one_and_is_deleted_again(self, lasx, client):
        result = changes.add_setting(client)
        assert result["confirmed"] is True
        shown = direct_reader.get_setting()
        assert [s["name"] for s in shown["all"]] == ["Setting 1", "Setting 2"] and shown["name"] == "Setting 2"

        assert changes.delete_setting(client, "Setting 2")["confirmed"] is True
        shown = direct_reader.get_setting()
        assert [s["name"] for s in shown["all"]] == ["Setting 1"] and shown["name"] == "Setting 1"

    def test_the_last_setting_and_an_unknown_one_are_not_deleted(self, lasx, client):
        assert changes.delete_setting(client, "Setting 1")["success"] is False
        assert changes.delete_setting(client, "Setting 9")["success"] is False
        assert lasx.commands() == []



class TestSequentialMode:
    def test_the_mode_is_written_into_the_state_tree_and_read_back(self, lasx, client):
        assert changes.change(client, "sequential_mode", "frame")["confirmed"] is True
        assert lasx.live[(*SCANNING, "SequentialMode")] == 1
        assert changes.change(client, "sequential_mode", "line")["confirmed"] is True
        assert lasx.live[(*SCANNING, "SequentialMode")] == 0
        assert lasx.commands() == []



WLL = ("10;10", 0)


def wll_line(lasx, index):
    source = next(s for s in lasx.nodes.light_sources if s["LightSourceName"] == "SuperContVisible Light")
    return next(a for a in source["AOTF"]["AotfLine"] if a["LineIndex"] == index)


class TestWhiteLightLaser:
    def test_a_line_that_is_on_is_tuned_and_read_back(self, lasx, client):
        result = changes.tune_laser_line(client, 497, 510)

        assert result["confirmed"] is True
        assert wll_line(lasx, 0)["WaveLength"] == 510.0
        assert lasx.commands() == ["SetLightSourceWavelength"]

    @pytest.mark.parametrize(("to_nm", "why"), [(480, "485"), (700, "685")])
    def test_a_wavelength_outside_the_laser_s_range_is_refused(self, lasx, client, to_nm, why):
        result = changes.tune_laser_line(client, 497, to_nm)

        assert result["success"] is False and why in result["message"]
        assert lasx.commands() == []

    def test_a_wavelength_within_8_nm_of_another_line_that_is_on_is_refused(self, lasx, client):
        wll_line(lasx, 1)["Intensity"] = 1.0  # the 681 nm line on

        result = changes.tune_laser_line(client, 497, 676)

        assert result["success"] is False and "8 nm" in result["message"]
        assert lasx.commands() == []

    def test_a_line_that_is_not_on_or_not_tunable_is_refused(self, lasx, client):
        assert changes.tune_laser_line(client, 520, 530)["success"] is False
        assert changes.tune_laser_line(client, 405, 410)["success"] is False  # the 405 line does not tune
        assert lasx.commands() == []

    def test_a_tunable_line_that_is_on_gets_its_power_by_its_wavelength(self, lasx, client):
        assert changes.set_laser_power(client, 497, 5)["confirmed"] is True
        assert wll_line(lasx, 0)["Intensity"] == pytest.approx(5.0, abs=0.4)



class TestWhiteLightLineAdded:
    def test_a_line_is_switched_on_at_the_next_free_index_and_tuned(self, lasx, client):
        result = changes.add_laser_line(client, 600, 3)

        assert result["confirmed"] is True
        assert wll_line(lasx, 1)["WaveLength"] == 600.0 and wll_line(lasx, 1)["Intensity"] == pytest.approx(3.0, abs=0.4)
        assert wll_line(lasx, 0)["WaveLength"] == 497.0 and wll_line(lasx, 0)["Intensity"] > 0  # the line that was on stays on
        assert lasx.commands() == ["SetConfocalLaserLinesDefinition", "SetLaserLineIntensity"]

    def test_a_power_outside_0_to_100_percent_is_refused_before_anything_is_sent(self, lasx, client):
        result = changes.add_laser_line(client, 600, 150)

        assert result["success"] is False
        assert lasx.commands() == []

    def test_a_wavelength_too_near_a_line_that_is_on_is_refused(self, lasx, client):
        result = changes.add_laser_line(client, 501)

        assert result["success"] is False and "8 nm" in result["message"]
        assert lasx.commands() == []

    def test_without_a_free_line_nothing_is_sent(self, lasx, client):
        wll_line(lasx, 1)["Intensity"] = 1.0
        wll_line(lasx, 1)["WaveLength"] = 640.0

        result = changes.add_laser_line(client, 600)

        assert result["success"] is False and "free" in result["message"]
        assert lasx.commands() == []



class TestLaserLineRemoved:
    def test_a_line_is_removed_by_sending_the_others(self, lasx, client):
        assert changes.add_laser_line(client, 600)["confirmed"] is True

        result = changes.remove_laser_line(client, 600)

        assert result["confirmed"] is True
        assert wll_line(lasx, 1)["Intensity"] == 0
        assert wll_line(lasx, 0)["Intensity"] > 0  # the 497 line stays on
        sent = lasx.sent[-1]
        assert "<WaveLength>600</WaveLength>" not in sent and "<WaveLength>497</WaveLength>" in sent

    def test_a_line_that_is_not_on_is_refused_and_nothing_is_sent(self, lasx, client):
        result = changes.remove_laser_line(client, 600)

        assert result["success"] is False and "600" in result["message"]
        assert lasx.commands() == []



class TestTheCurrentSetting:
    """LCS takes a change into the setting its index names, 0 the first (simulator, 2026-11-01); the driver names the current one."""

    def test_a_change_is_addressed_to_the_current_setting_by_its_place(self, lasx, client):
        assert changes.add_setting(client)["confirmed"] is True  # Setting 2, now current

        changes.change(client, "line_average", 2)
        changes.change(client, "collar", 30)
        changes.set_laser_power(client, 497, 5)

        addressed = [e for e in lasx.sent if "SettingIndex=" in e or "<Sequence Index=" in e]
        assert addressed and all('SettingIndex="1"' in e or '<Sequence Index="1">' in e for e in addressed)

    def test_with_the_first_setting_current_the_index_is_0(self, lasx, client):
        assert changes.add_setting(client)["confirmed"] is True
        assert changes.activate_setting(client, "Setting 1")["confirmed"] is True

        changes.set_laser_power(client, 497, 5)

        assert 'SingleSettingIndex="0"' in lasx.sent[-1]

    def test_a_setting_is_made_current_as_the_window_does(self, lasx, client):
        assert changes.add_setting(client)["confirmed"] is True

        assert changes.activate_setting(client, "Setting 1")["confirmed"] is True
        assert direct_reader.get_setting()["name"] == "Setting 1"
        assert '<ATL SettingID="3" Command="ATL_Setting_Activate" Attribute="2"/>' in lasx.sent[-1]
        assert changes.activate_setting(client, "Setting 9")["success"] is False



class TestSharedLaserSlots:
    """A white-light slot's wavelength is shared by every setting; each has its own power (simulator, 2026-11-01)."""

    @pytest.fixture
    def another_setting_uses_slot_1(self, monkeypatch):
        from zmart_drivers.leica.stellaris.navigator.procedures import readings

        monkeypatch.setattr(readings, "get_laser_slots", lambda client, **k: [
            {"setting": "Setting 1", "beamRoute": "10;10", "lineIndex": 0, "wavelength": 497.0},
            {"setting": "Setting 2", "beamRoute": "10;10", "lineIndex": 1, "wavelength": 681.0},
        ])

    def test_a_line_is_added_on_a_slot_no_setting_uses(self, lasx, client, another_setting_uses_slot_1):
        lasx.nodes.light_sources[2]["AOTF"]["AotfLine"].append({"WaveLength": 553.0, "IsLineChecked": True, "LineIndex": 2, "Intensity": 0.0})

        assert changes.add_laser_line(client, 600)["confirmed"] is True
        assert "<LineIndex>2</LineIndex>" in lasx.sent[-2]

    def test_a_line_within_8_nm_of_another_setting_s_line_is_refused(self, lasx, client, another_setting_uses_slot_1):
        result = changes.add_laser_line(client, 677)

        assert result["success"] is False and "8 nm" in result["message"]

    def test_a_slot_another_setting_uses_is_not_tuned(self, lasx, client, another_setting_uses_slot_1):
        wll_line(lasx, 1)["Intensity"] = 2.0
        wll_line(lasx, 1)["WaveLength"] = 681.0

        result = changes.tune_laser_line(client, 681, 640)

        assert result["success"] is False and "Setting 2" in result["message"]
        assert lasx.commands() == []


def test_a_ninth_visible_line_in_a_setting_is_refused_as_las_x_would(lasx, client, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.procedures import readings

    eight = [{"beamRoute": "30", "lineIndex": i, "wavelength": 400.0 + 20 * i, "intensity": 0.02} for i in range(8)]
    monkeypatch.setattr(readings, "get_laser_lines", lambda client, **k: eight)

    result = changes.add_laser_line(client, 600)

    assert result["success"] is False and "8" in result["message"]
    assert lasx.commands() == []



class TestCoverslipReflection:
    def test_the_offset_is_written_and_read_back(self, lasx, client):
        result = changes.set_coverslip_offset(client, 12.5)

        assert result["confirmed"] is True and result["coverslip_offset_um"] == pytest.approx(12.5)

    @pytest.mark.parametrize("um", [6000.0, -6000.0, "5", True])
    def test_an_offset_beyond_the_capture_range_is_refused(self, lasx, client, um):
        assert changes.set_coverslip_offset(client, um)["success"] is False
        assert direct_reader.get_coverslip_offset() == 0.0

    def test_find_reports_the_offset_las_x_found(self, lasx, client, tmp_path, monkeypatch):
        monkeypatch.setattr(changes, "LASX_LOG", tmp_path / "LAS X.log")
        lasx.coverslip = (None, 7.0)

        result = changes.find_coverslip_reflection(client, project=24)

        assert result["success"] is True and result["found"] is True
        assert result["coverslip_offset_um"] == pytest.approx(7.0)
        assert lasx.commands() == ["StartFindCoverSlipReflection"]

    def test_find_reports_when_las_x_found_no_reflection(self, lasx, client, tmp_path, monkeypatch):
        log = tmp_path / "LAS X.log"
        monkeypatch.setattr(changes, "LASX_LOG", log)
        lasx.coverslip = (log, None)

        result = changes.find_coverslip_reflection(client, project=24)

        assert result["success"] is True and result["found"] is False
        assert result["coverslip_offset_um"] == 0.0

    def test_a_search_beyond_the_z_limits_is_refused_and_nothing_is_sent(self, lasx, client, tmp_path, monkeypatch):
        monkeypatch.setattr(changes, "LASX_LOG", tmp_path / "LAS X.log")
        lasx.live[("Acquire", "AutoFocusInfo", "FocusRange")] = 100000

        assert changes.find_coverslip_reflection(client, project=24)["success"] is False
        assert lasx.commands() == []


class TestAutofocusRun:
    def test_the_autofocus_runs_and_reports_where_the_focus_went(self, lasx, client, tmp_path, monkeypatch):
        monkeypatch.setattr(changes, "LCS_LOG", tmp_path / "LCS.LOG")
        lasx.autofocus_finds_um = 4.0

        result = changes.autofocus(client, project=24)

        assert result["success"] is True and result["found"] is True
        assert result["drive"] == "z-galvo" and result["settings"]["range_um"] == 80.0
        assert result["z_after_um"] - result["z_before_um"] == pytest.approx(4.0)
        assert lasx.commands() == ["StartAutoFocus"]

    def test_lcs_s_validate_failed_is_reported_as_not_found(self, lasx, client, tmp_path, monkeypatch):
        log = tmp_path / "LCS.LOG"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(changes, "LCS_LOG", log)
        lasx.autofocus_log = (log, "2026-11-02 19:16:24.903 FFFFFFFEh WARNING 521 AutoFocus.cpp 'ValidateFocus failed'\n")

        result = changes.autofocus(client, project=24)

        assert result["success"] is True and result["found"] is False
        assert result["z_after_um"] == result["z_before_um"]

    def test_a_search_beyond_the_z_limits_is_refused_and_nothing_is_sent(self, lasx, client, tmp_path, monkeypatch):
        monkeypatch.setattr(changes, "LCS_LOG", tmp_path / "LCS.LOG")
        lasx.live[("Acquire", "AutoFocusInfo", "FocusRange")] = 100000

        result = changes.autofocus(client, project=24)

        assert result["success"] is False
        assert lasx.commands() == []



class TestAutofocusSettings:
    def test_the_settings_are_written_and_read_back(self, lasx, client):
        for name, value in (("autofocus_method", "noise"), ("autofocus_steps", 21), ("autofocus_range_um", 60.0),
                            ("autofocus_drive", "z-wide"), ("autofocus_precision", 2)):
            assert changes.change(client, name, value)["confirmed"] is True, name
        shown = direct_reader.get_autofocus_settings()
        assert shown == {"drive": "z-wide", "range_um": 60.0, "method": "noise", "precision": 2, "steps": None}
        assert lasx.commands() == []



AF_SETTING = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialAutofocusSetting")


class TestBestFocusSetting:
    def test_its_scan_is_set_and_read_back(self, lasx, client):
        for name, value in (("autofocus_format", (128, 128)), ("autofocus_speed", 400),
                            ("autofocus_bidirectional", False), ("autofocus_zoom", 2.0)):
            assert changes.change(client, name, value)["confirmed"] is True, name
        assert direct_reader.get_autofocus_scan() == {
            "format": (128, 128), "speed": 400, "bidirectional": False, "zoom": 2.0, "enabled": True, "id": 4}

    def test_a_zoom_outside_lcs_own_range_for_it_is_refused(self, lasx, client):
        result = changes.change(client, "autofocus_zoom", 60.0)

        assert result["success"] is False and "range" in result["message"]

    def test_its_optics_are_set_with_a_power_the_limits_allow(self, lasx, client):
        result = changes.set_autofocus_optics(client, 405, 8, "HyD 2", (410, 480), "Leica/ALEXA 405")

        assert result["confirmed"] is True
        assert direct_reader.get_autofocus_optics()["lines"] == [{"beamRoute": "30", "lineIndex": 0, "wavelength": 405.0, "percent": 8.0}]

    def test_a_power_above_the_limits_is_refused_before_anything_is_sent(self, lasx, client):
        result = changes.set_autofocus_optics(client, 405, 50, "HyD 2", (410, 480), "Leica/ALEXA 405")

        assert result["success"] is False
        assert lasx.commands() == []



class TestBestFocusLaserPower:
    def test_a_line_s_power_in_the_best_focus_setting_is_changed_and_read_back(self, lasx, client):
        assert changes.set_autofocus_optics(client, 405, 3, "HyD 2", (410, 480), "Leica/ALEXA 405")["confirmed"] is True

        assert changes.set_autofocus_laser_power(client, 405, 6)["confirmed"] is True
        assert direct_reader.get_autofocus_optics()["lines"][0]["percent"] == 6.0
        assert client.reply["element"] == '<StateTree Command="AutofocusLaserPower" Value="6.0" At="0,0"/>'

    def test_a_line_not_in_the_best_focus_setting_is_refused(self, lasx, client):
        result = changes.set_autofocus_laser_power(client, 405, 6)

        assert result["success"] is False and "set_autofocus_optics" in result["message"]

    def test_a_power_above_the_limits_is_refused_before_anything_is_sent(self, lasx, client):
        assert changes.set_autofocus_optics(client, 405, 3, "HyD 2", (410, 480), "Leica/ALEXA 405")["confirmed"] is True
        sent = len(lasx.sent)

        assert changes.set_autofocus_laser_power(client, 405, 50)["success"] is False
        assert len(lasx.sent) == sent



class TestTheAobs:
    def lines(self, lasx):
        return lasx.nodes.aobs_lines

    def test_the_lines_are_read_as_the_window_shows_them(self, lasx, client):
        assert direct_reader.get_aobs_lines() == [
            {"wavelength": 497.0, "reflection": False, "enhanced_dynamics": True, "percent": 100.0, "at": 0}
        ]

    def test_a_line_is_put_into_reflection_and_back(self, lasx, client):
        result = changes.set_aobs_line(client, 497, reflection=True)

        assert result["confirmed"] is True
        assert self.lines(lasx)[0]["IsReflection"] is True and self.lines(lasx)[0]["IsDoubleModeEnabled"] is False
        assert changes.set_aobs_line(client, 497, reflection=False)["confirmed"] is True
        assert self.lines(lasx)[0]["IsReflection"] is False

    def test_enhanced_dynamics_is_switched_on_its_own(self, lasx, client):
        assert changes.set_aobs_line(client, 497, enhanced_dynamics=False)["confirmed"] is True
        assert self.lines(lasx)[0]["IsDoubleModeEnabled"] is False
        assert changes.set_aobs_line(client, 497, enhanced_dynamics=True)["confirmed"] is True
        assert self.lines(lasx)[0]["IsDoubleModeEnabled"] is True

    def test_reflection_with_enhanced_dynamics_is_refused_as_the_window_does_not_have_it(self, lasx, client):
        result = changes.set_aobs_line(client, 497, reflection=True, enhanced_dynamics=True)

        assert result["success"] is False
        assert self.lines(lasx)[0]["IsReflection"] is False

    def test_a_line_the_aobs_does_not_carry_is_refused(self, lasx, client):
        result = changes.set_aobs_line(client, 561, reflection=True)

        assert result["success"] is False and "497" in result["message"]

    def test_enhanced_dynamics_where_the_microscope_has_none_is_refused(self, lasx, client):
        lasx.nodes.enhanced_dynamics_possible = False

        assert changes.set_aobs_line(client, 497, enhanced_dynamics=True)["success"] is False

    def test_nothing_asked_is_refused(self, lasx, client):
        assert changes.set_aobs_line(client, 497)["success"] is False


class TestFastLive:
    def test_fast_live_starts_as_the_window_starts_it(self, lasx, client):
        assert changes.start_live(client, project=24, fast=True)["confirmed"] is True
        assert lasx.sent[-1] == '<AcquireControl Mode="6" Command="StartLiveAcquiring" DCParentElementID="24"/>'
        assert changes.stop_live(client)["confirmed"] is True

    def test_the_settings_are_read_as_the_window_shows_them(self, lasx, client):
        fov = direct_reader.get_fast_live()["fov"]

        assert fov == {
            "format": 256, "scan_frequency": 600, "overview_zoom": None, "bidirectional": False, "no_average": True,
            "no_dynamic_signal_enhancement": False, "selected_setting_only": True, "no_tau_sted_xtend": True,
        }

    def test_each_setting_is_written_and_read_back(self, lasx, client):
        result = changes.set_fast_live(
            client, "fov", format=512, scan_frequency=400, overview_zoom=2.0, bidirectional=True, no_average=False,
            no_dynamic_signal_enhancement=True, selected_setting_only=False, no_tau_sted_xtend=False,
        )

        assert result["confirmed"] is True
        assert result["fast_live"]["fov"] == {
            "format": 512, "scan_frequency": 400, "overview_zoom": 2.0, "bidirectional": True, "no_average": False,
            "no_dynamic_signal_enhancement": True, "selected_setting_only": False, "no_tau_sted_xtend": False,
        }
        assert result["fast_live"]["resonant"]["format"] == 256

    def test_the_overview_zoom_is_switched_off(self, lasx, client):
        changes.set_fast_live(client, "fov", overview_zoom=2.0)

        assert changes.set_fast_live(client, "fov", overview_zoom=False)["fast_live"]["fov"]["overview_zoom"] is None

    @pytest.mark.parametrize(("option", "value"), [
        ("format", 300), ("format", 1024), ("scan_frequency", 3000), ("overview_zoom", 100.0), ("bidirectional", "yes"), ("speed", 1),
    ])
    def test_what_the_window_does_not_offer_is_refused_and_nothing_is_written(self, lasx, client, option, value):
        before = direct_reader.get_fast_live()

        result = changes.set_fast_live(client, "fov", **{option: value})

        assert result["success"] is False
        assert direct_reader.get_fast_live() == before

    def test_the_resonant_scanner_s_fixed_frequency_cannot_be_changed(self, lasx, client):
        assert changes.set_fast_live(client, "resonant", scan_frequency=8000)["success"] is False

    def test_a_scanner_the_window_does_not_have_is_refused(self, lasx, client):
        assert changes.set_fast_live(client, "galvo", format=256)["success"] is False


class TestApplyToAllSettings:
    @pytest.mark.parametrize(("name", "element"), [
        ("line_average", '<Scanner Command="SetLineAverage" LineAverage="1" SetMode="1" SingleSettingIndex="0" BlockId="0"/>'),
        ("frame_accumulation", '<Scanner Command="SetFrameAccu" FrameAccu="1" SetMode="1" SingleSettingIndex="0" BlockId="0"/>'),
    ])
    def test_the_current_value_is_sent_to_every_setting_as_the_window_s_box_does(self, lasx, client, name, element):
        result = changes.apply_to_all_settings(client, name)

        assert result["success"] is True
        assert lasx.sent[-1] == element

    def test_the_pinhole_is_sent_to_every_setting(self, lasx, client):
        assert changes.apply_to_all_settings(client, "pinhole_um")["success"] is True
        assert lasx.sent[-1].startswith('<Scanner Command="SetPinhole"') and 'SetMode="1"' in lasx.sent[-1]

    def test_a_setting_the_window_has_no_box_for_is_refused(self, lasx, client):
        assert changes.apply_to_all_settings(client, "zoom")["success"] is False


class TestLaserOutputPower:
    def test_the_white_light_laser_s_output_power_is_read(self, lasx, client):
        assert direct_reader.get_laser_output_power() == {"SuperContVisible Light": 85.0}

    def test_it_is_set_as_the_laser_overview_s_slider_does_and_read_back(self, lasx, client):
        result = changes.set_laser_output_power(client, 60.0)

        assert result["confirmed"] is True and result["output_power"] == {"SuperContVisible Light": 60.0}
        assert lasx.nodes.output_power[2] == 60.0

    @pytest.mark.parametrize("percent", [-1.0, 101.0, "85", True])
    def test_a_power_outside_0_to_100_is_refused(self, lasx, client, percent):
        assert changes.set_laser_output_power(client, percent)["success"] is False
        assert direct_reader.get_laser_output_power() == {"SuperContVisible Light": 85.0}

    def test_a_light_source_without_an_output_power_is_refused(self, lasx, client):
        assert changes.set_laser_output_power(client, 50.0, source="UV")["success"] is False

    def test_the_limits_gate_sees_the_power(self, lasx, client):
        from limits_fixtures import DEFAULT_STAGE_UM, merged_limits_payload

        from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules

        limited = hardware_server.Client("test")
        payload = merged_limits_payload(dict(DEFAULT_STAGE_UM), functions={"set_laser_output_power": {"range": [0, 50]}})
        gate.install(limited, gate.GateState(
            limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False),
            stage_cfg=None, error=None))
        try:
            result = changes.set_laser_output_power(limited, 60.0)
        finally:
            gate.uninstall(limited)

        assert result["success"] is False
        assert direct_reader.get_laser_output_power() == {"SuperContVisible Light": 85.0}


class TestZDrive:
    def test_the_drive_is_chosen_as_the_z_stack_panel_does(self, lasx, client):
        result = changes.set_z_drive(client, "z-galvo")

        assert result["confirmed"] is True and result["z_drive"] == "z-galvo"
        assert lasx.commands()[-1] == "SetZUseMode"
        assert changes.set_z_drive(client, "z-wide")["z_drive"] == "z-wide"

    def test_a_drive_las_x_does_not_have_is_refused(self, lasx, client):
        assert changes.set_z_drive(client, "z-piezo")["success"] is False


class TestLambdaScan:
    def test_the_detection_range_step_and_bandwidth_are_set_with_the_window_s_commands(self, lasx, client):
        result = changes.set_lambda_scan(client, begin_nm=400, end_nm=700, step_nm=10, bandwidth_nm=20)

        assert result["confirmed"] is True
        assert result["lambda_scan"] == {"begin_nm": 400.0, "end_nm": 700.0, "step_nm": 10.0, "bandwidth_nm": 20.0}
        assert lasx.sent[-4:] == [  # step and bandwidth first: a later change of either moved the end
            '<Scanner Command="SetLambdaDetectionStepsize" Stepsize="10"/>', '<Scanner Command="SetLambdaDetectionBandwidth" Bandwidth="20"/>',
            '<Scanner Command="SetLambdaDetectionBegin" Begin="400"/>', '<Scanner Command="SetLambdaDetectionEnd" End="700"/>',
        ]

    @pytest.mark.parametrize("keywords", [
        {"begin_nm": 370}, {"end_nm": 900}, {"begin_nm": 700, "end_nm": 600}, {"step_nm": 0}, {"bandwidth_nm": -5},
        {"begin_nm": 400.5}, {},
    ])
    def test_what_the_window_does_not_offer_is_refused_and_nothing_is_sent(self, lasx, client, keywords):
        sent = len(lasx.sent)

        assert changes.set_lambda_scan(client, **keywords)["success"] is False
        assert len(lasx.sent) == sent


class TestAutoSync:
    def test_auto_sync_goes_on_with_the_window_s_command_and_off(self, lasx, client):
        on = changes.set_auto_sync(client, True)

        assert on["confirmed"] is True and on["auto_sync"]["on"] is True
        assert lasx.commands()[-1] == "ResetAutoFocusToAutoSync"
        assert changes.set_auto_sync(client, False)["auto_sync"]["on"] is False

    def test_the_options_are_written_and_read_back(self, lasx, client):
        changes.set_auto_sync(client, True)

        result = changes.set_auto_sync_options(client, "fov", format=128, scan_frequency=1000, overview_zoom=2.0,
                                               bidirectional=False, no_average=False)

        assert result["confirmed"] is True
        assert result["auto_sync"]["fov"] == {"format": 128, "scan_frequency": 1000, "overview_zoom": 2.0,
                                              "bidirectional": False, "no_average": False}

    def test_an_option_is_switched_off_with_false(self, lasx, client):
        changes.set_auto_sync(client, True)

        assert changes.set_auto_sync_options(client, "fov", format=False)["auto_sync"]["fov"]["format"] is None

    @pytest.mark.parametrize(("option", "value"), [("format", 8), ("format", 1024), ("scan_frequency", 3000), ("overview_zoom", 100.0),
                                                   ("bidirectional", "yes"), ("speed", 1)])
    def test_what_the_window_does_not_offer_is_refused(self, lasx, client, option, value):
        changes.set_auto_sync(client, True)
        before = direct_reader.get_auto_sync()

        assert changes.set_auto_sync_options(client, "fov", **{option: value})["success"] is False
        assert direct_reader.get_auto_sync() == before

    def test_the_options_are_refused_while_auto_sync_is_off(self, lasx, client):
        assert changes.set_auto_sync_options(client, "fov", format=128)["success"] is False


class TestAutofocusInATimeSeries:
    @pytest.mark.parametrize(("when", "shown"), [("every", "every"), (4, 4), ("first", "first")])
    def test_when_the_autofocus_runs_is_written_and_read_back(self, lasx, client, when, shown):
        result = changes.set_autofocus_in_time_series(client, when)

        assert result["confirmed"] is True and result["in_time_series"] == shown

    @pytest.mark.parametrize("when", ["sometimes", 0, 1, 2.5, True])
    def test_what_the_tab_does_not_offer_is_refused(self, lasx, client, when):
        assert changes.set_autofocus_in_time_series(client, when)["success"] is False
        assert direct_reader.get_autofocus_in_time_series() == "first"


class TestZCompensation:
    @pytest.fixture
    def stacked(self, lasx, client):
        lasx.z_um = {1: 0.0, 2: 1000.0}
        changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)
        return client

    def test_the_modes_are_switched_on_and_off_with_the_window_s_command(self, lasx, stacked):
        result = changes.set_z_compensation(stacked, excitation=True, detector=True)

        assert result["confirmed"] is True and result["z_compensation"]["modes"] == ["excitation", "detector"]
        assert lasx.sent[-1] == '<Scanner Command="ActivateZCompensationMode" Mode="2" Activate="1"/>'
        assert result["z_compensation"]["points"][0]["z_um"] == pytest.approx(1005.0)
        assert changes.set_z_compensation(stacked, excitation=False, detector=False)["z_compensation"]["modes"] == []

    def test_a_point_is_added_where_the_focus_is_and_removed(self, lasx, stacked):
        changes.set_z_compensation(stacked, excitation=True)
        lasx.z_um[2] = 1001.0

        added = changes.add_z_compensation_point(stacked)

        assert [p["z_um"] for p in added["z_compensation"]["points"]] == pytest.approx([1005.0, 1001.0, 995.0])
        assert added["z_compensation"]["points"][1]["values"] == {"AOTF 499": 2.0, "HyD S 3": 100.0}
        removed = changes.remove_z_compensation_point(stacked, 1001.0)
        assert [p["z_um"] for p in removed["z_compensation"]["points"]] == pytest.approx([1005.0, 995.0])
        assert changes.remove_z_compensation_point(stacked, None)["z_compensation"]["points"] == []

    def test_the_focus_moves_to_a_point(self, lasx, stacked):
        changes.set_z_compensation(stacked, excitation=True)

        assert changes.move_to_z_compensation_point(stacked, 1005.0)["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(1005.0)

    def test_a_point_that_is_not_there_is_refused(self, lasx, stacked):
        changes.set_z_compensation(stacked, excitation=True)

        assert changes.remove_z_compensation_point(stacked, 1002.0)["success"] is False
        assert changes.move_to_z_compensation_point(stacked, 1002.0)["success"] is False

    def test_the_reference_is_written_and_read_back(self, lasx, stacked):
        result = changes.set_z_compensation(stacked, reference_um=998.0)

        assert result["z_compensation"]["reference_um"] == pytest.approx(998.0)

    def test_nothing_asked_or_not_a_switch_is_refused(self, lasx, stacked):
        assert changes.set_z_compensation(stacked)["success"] is False
        assert changes.set_z_compensation(stacked, excitation="yes")["success"] is False


class TestRoiScanAndBleachMode:
    def test_roi_scan_is_switched_on_and_off(self, lasx, client):
        assert changes.set_roi_scan(client, True)["roi"] == {"on": True, "sections": None, "bleach_mode": False}
        assert changes.set_roi_scan(client, False)["roi"]["on"] is False

    def test_the_roi_scan_is_limited_to_sections_of_the_stack(self, lasx, client):
        lasx.z_um = {1: 0.0, 2: 1000.0}
        changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        result = changes.set_roi_scan(client, True, sections=(2, 4))

        assert result["confirmed"] is True and result["roi"]["sections"] == (2, 4)

    @pytest.mark.parametrize("sections", [(0, 2), (3, 2), (2, 99), (1.5, 3)])
    def test_sections_outside_the_stack_are_refused(self, lasx, client, sections):
        lasx.z_um = {1: 0.0, 2: 1000.0}
        changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        assert changes.set_roi_scan(client, True, sections=sections)["success"] is False
        assert direct_reader.get_roi_scan()["on"] is False

    def test_sections_without_a_stack_are_refused(self, lasx, client):
        assert changes.set_roi_scan(client, True, sections=(1, 2))["success"] is False

    def test_the_bleach_mode_is_switched_on_and_off(self, lasx, client):
        assert changes.set_bleach_mode(client, True)["roi"]["bleach_mode"] is True
        assert changes.set_bleach_mode(client, False)["roi"]["bleach_mode"] is False

    def test_roi_scan_and_bleach_mode_exclude_each_other_as_in_the_window(self, lasx, client):
        changes.set_roi_scan(client, True)

        assert changes.set_bleach_mode(client, True)["success"] is False


class TestTimeSeries:
    @pytest.fixture
    def timed(self, lasx, client):
        lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] = "xyt"
        return client

    def test_frames_and_interval_are_set_and_read_back(self, lasx, timed):
        result = changes.set_time_series(timed, frames=7, interval_s=10.0)

        assert result["confirmed"] is True
        assert result["time_series"] == {
            "frames": 7, "interval_s": 10.0, "duration_s": 60.0, "until_stopped": False, "shortest_interval_s": 1.28,
        }

    def test_until_stopped_is_switched_on_and_off(self, lasx, timed):
        assert changes.set_time_series(timed, until_stopped=True)["time_series"]["until_stopped"] is True
        result = changes.set_time_series(timed, frames=3)

        assert result["time_series"]["until_stopped"] is False and result["time_series"]["frames"] == 3

    @pytest.mark.parametrize("keywords", [
        {"interval_s": 0.5}, {"frames": 0}, {"frames": 2.5}, {"frames": 3, "until_stopped": True}, {},
    ])
    def test_what_the_window_does_not_offer_is_refused_and_nothing_is_written(self, lasx, timed, keywords):
        before = direct_reader.get_time_series()

        assert changes.set_time_series(timed, **keywords)["success"] is False
        assert direct_reader.get_time_series() == before

    def test_without_a_time_series_in_the_scan_mode_it_is_refused(self, lasx, client):
        assert changes.set_time_series(client, frames=3)["success"] is False


class TestLive:
    def test_live_starts_as_the_window_starts_it_and_stops(self, lasx, client):
        started = changes.start_live(client, project=24)

        assert started["confirmed"] is True
        assert lasx.sent[-1] == '<AcquireControl Mode="0" Command="StartLiveAcquiring" DCParentElementID="24"/>'

        stopped = changes.stop_live(client)

        assert stopped["confirmed"] is True
        assert lasx.commands()[-1] == "StopAcquiring"
        assert direct_reader.get_scan_status() == "eScanIdle"

    def test_stop_when_nothing_runs_is_harmless(self, lasx, client):
        assert changes.stop_live(client)["confirmed"] is True


class TestTheZStackPanel:
    @pytest.fixture
    def stacked(self, lasx, client):
        lasx.z_um = {1: 0.0, 2: 1000.0}
        assert changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)["confirmed"] is True
        return client

    def test_the_size_grows_around_the_middle(self, lasx, stacked):
        result = changes.set_z_stack_size(stacked, 20.0)

        assert result["confirmed"] is True
        assert (result["z_stack"]["begin"], result["z_stack"]["end"]) == (pytest.approx(990.0), pytest.approx(1010.0))
        assert lasx.commands()[-1] == "SetZStackSize"

    def test_a_size_whose_ends_leave_the_limits_is_refused_and_nothing_is_sent(self, lasx, stacked):
        sent = len(lasx.sent)

        assert changes.set_z_stack_size(stacked, 100000.0)["success"] is False
        assert len(lasx.sent) == sent

    def test_begin_and_end_are_set_where_the_focus_is(self, lasx, stacked):
        lasx.z_um[2] = 1001.0
        assert changes.set_z_stack_here(stacked, "end")["confirmed"] is True
        lasx.z_um[2] = 998.0
        result = changes.set_z_stack_here(stacked, "begin")

        assert result["confirmed"] is True
        assert (result["z_stack"]["begin"], result["z_stack"]["end"]) == (pytest.approx(998.0), pytest.approx(1001.0))
        assert lasx.commands()[-2:] == ["BeforeBeginOrEndOfStackIsSet", "SetZStackDefinition"]

    def test_re_center_puts_the_stack_around_the_focus(self, lasx, stacked):
        lasx.z_um[2] = 1020.0

        result = changes.recenter_z_stack(stacked)

        assert result["confirmed"] is True
        assert (result["z_stack"]["begin"], result["z_stack"]["end"]) == (pytest.approx(1015.0), pytest.approx(1025.0))

    def test_the_number_of_sections_sets_the_step(self, lasx, stacked):
        result = changes.set_z_stack_sections(stacked, 11)

        assert result["confirmed"] is True and result["z_stack"]["sections"] == 11
        assert result["z_stack"]["stepSize"] == pytest.approx(1.0)

    def test_without_a_stack_these_are_refused(self, lasx, client):
        assert changes.set_z_stack_size(client, 20.0)["success"] is False
        assert changes.recenter_z_stack(client)["success"] is False
        assert changes.move_to_z_stack(client, "begin")["success"] is False
        assert changes.clear_z_stack(client)["success"] is False

    @pytest.mark.parametrize(("place", "z"), [("begin", 995.0), ("end", 1005.0), ("center", 1000.0)])
    def test_the_focus_moves_to_the_stack_s_begin_end_and_center(self, lasx, stacked, place, z):
        lasx.z_um[2] = 1002.0

        result = changes.move_to_z_stack(stacked, place)

        assert result["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(z)
        assert lasx.commands()[-1] == "SetZPosition"

    def test_the_focus_moves_to_the_focus_plane_set_in_the_window(self, lasx, stacked):
        lasx.live[("Acquire", "AcquireZStackInfo", "AFPlanePosition")] = 0.001003
        lasx.live[("Acquire", "AcquireZStackInfo", "IsAFPlanePosition")] = True

        assert changes.move_to_z_stack(stacked, "focus")["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(1003.0)

    def test_without_a_focus_plane_move_to_focus_is_refused(self, lasx, stacked):
        sent = len(lasx.sent)

        result = changes.move_to_z_stack(stacked, "focus")

        assert result["success"] is False and "Set Focus" in result["message"]
        assert len(lasx.sent) == sent

    def test_a_place_the_panel_does_not_have_is_refused(self, lasx, stacked):
        assert changes.move_to_z_stack(stacked, "middle")["success"] is False

    def test_clear_leaves_no_stack(self, lasx, stacked):
        lasx.z_um[2] = 1001.0

        result = changes.clear_z_stack(stacked)

        assert result["confirmed"] is True and result["z_stack"] is None
        assert lasx.commands()[-4:] == ["BeforeBeginOrEndOfStackIsSet", "SetZStackDefinition"] * 2
