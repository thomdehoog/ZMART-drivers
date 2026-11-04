"""The driver used by itself: connect, move, change settings, change the objective, capture, save and clear.

``navigator.connect()`` hands back a :class:`Microscope`, the driver's own
way to drive the STELLARIS from a notebook or a script, without the ZMART
Controller. These tests connect it to the stand-in for LAS X's plain
Navigator and check what comes back. The capture, saving and clearing
themselves are tested with their procedures; here they are checked to be
called with what was asked.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import hermetic_mock_machine_root

from zmart_drivers.leica.stellaris import navigator
from zmart_drivers.leica.stellaris.navigator import methods
from zmart_drivers.leica.stellaris.navigator.primitives.direct.writes import SETTINGS


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator(z_wide_um=100.0))


@pytest.fixture
def scope(lasx, tmp_path):
    """A Microscope connected to the stand-in, with a throwaway configuration folder."""
    hermetic_mock_machine_root()
    scope = navigator.connect(output_root=str(tmp_path / "out"))
    yield scope
    scope.close()


def test_connect_hands_back_a_microscope(scope):
    assert isinstance(scope, navigator.Microscope)


def test_the_package_offers_only_the_microscope():
    """Nothing that goes around the session: no raw reads or commands on a client."""
    offered = {name for name in vars(navigator) if not name.startswith("__")} - {"methods"}

    assert sorted(navigator.__all__) == ["Microscope", "__version__", "connect"]
    assert offered <= {"Microscope", "connect"} | {"dispatcher", "driver_setup", "procedures", "primitives", "zmart_controller_plugin", "testing"}


def test_without_las_x_connecting_fails_with_a_plain_message(tmp_path):
    with pytest.raises(RuntimeError, match="LAS X does not answer"):
        navigator.connect(output_root=str(tmp_path))


def test_las_x_showing_navigator_expert_is_refused(lasx, tmp_path):
    lasx.nodes.jobs = lambda: {"HiRes": (152, 3)}
    lasx.nodes.reload()

    with pytest.raises(RuntimeError, match="Navigator Expert"):
        navigator.connect(output_root=str(tmp_path))


def test_a_host_that_is_not_this_computer_is_refused(lasx, tmp_path):
    with pytest.raises(ValueError, match="this computer only"):
        navigator.connect(host="microscope-pc-2", output_root=str(tmp_path))


def test_the_running_log_gets_the_messages_of_every_part_of_the_driver(scope):
    import logging

    driver = logging.getLogger(methods.DRIVER_PACKAGE)

    assert methods.DRIVER_PACKAGE == navigator.__name__
    assert any(getattr(h, "baseFilename", "").endswith("running_log.txt") for h in driver.handlers)


def test_the_stage_moves_and_reads_back_in_the_sample_frame(scope):
    start = {axis: scope.position()[axis]["position"] for axis in "xyz"}

    moved = scope.move_to(start["x"] + 120.0, start["y"] - 40.0, start["z"] + 3.0)
    here = scope.position()

    assert moved["x"]["position"] == pytest.approx(start["x"] + 120.0, abs=1.0)
    assert here["y"]["position"] == pytest.approx(start["y"] - 40.0, abs=1.0)
    assert here["z"]["position"] == pytest.approx(start["z"] + 3.0, abs=0.5)
    assert here["x"]["unit"] == "micrometer"
    assert here["z"]["actuator_readings"].keys() == {"z-wide", "z-galvo"}


def test_the_focus_moves_with_z_wide_alone(scope, lasx):
    here = scope.position()
    galvo_before = lasx.z_um[1]

    scope.move_to(here["x"]["position"], here["y"]["position"], here["z"]["position"] + 5.0)

    assert lasx.z_um[1] == pytest.approx(galvo_before)
    assert lasx.z_um[2] == pytest.approx(105.0)


def test_a_move_outside_the_limits_is_refused_and_the_stage_stays(scope, lasx):
    before = scope.position()["x"]["position"]

    with pytest.raises(RuntimeError, match="refused"):
        scope.move_to(-1e9, 0.0, 0.0)

    assert scope.position()["x"]["position"] == pytest.approx(before)
    assert lasx.commands() == []


def test_the_motors_of_each_axis_are_named(scope):
    assert scope.motors()["z"] == {"options": ["z-wide"], "default": "z-wide"}


def test_a_setting_is_changed_by_name_and_read_back(scope, lasx):
    result = scope.change_setting("zoom", 4.0)

    assert result["confirmed"] is True
    assert scope.settings()["zoom"]["current"] == pytest.approx(4.0)


def test_an_unknown_setting_is_refused_with_the_names_there_are(scope):
    with pytest.raises(ValueError, match="zoom"):
        scope.change_setting("brightness", 1.0)


def test_a_setting_las_x_refuses_raises_with_its_reason(scope):
    with pytest.raises(RuntimeError, match="out of range"):
        scope.change_setting("zoom", 500.0)


def test_the_objective_changes_and_the_sample_stays_in_view(scope, lasx):
    """The calibration says the 20x looks 10 um right and 6 um up of the 10x: the stage follows."""
    x0, y0 = lasx.x_um, lasx.y_um

    result = scope.change_objective(slot=1)

    assert result["confirmed"] is True
    assert result["objective_compensation"]["applied_translation_um"] == [10.0, -6.0, -3.0]
    assert (lasx.x_um, lasx.y_um) == (pytest.approx(x0 + 10.0), pytest.approx(y0 - 6.0))
    assert lasx.live[("Microscope", "SlotPosition")] == 1


def test_an_objective_of_another_immersion_is_refused_before_the_turret_turns(scope, lasx):
    lasx.nodes.objectives[1]["Immersion"] = "OIL"
    lasx.nodes.reload()

    with pytest.raises(RuntimeError, match="immersion"):
        scope.change_objective(slot=1)

    assert "SetSlotPosition" not in lasx.commands()
    assert lasx.live[("Microscope", "SlotPosition")] == 0


def test_an_objective_the_calibration_does_not_cover_is_refused_before_the_turret_turns(scope, lasx):
    scope.handle.translations = {0: (0.0, 0.0, 0.0)}

    with pytest.raises(RuntimeError, match="calibration"):
        scope.change_objective(slot=1)

    assert "SetSlotPosition" not in lasx.commands()
    assert lasx.live[("Microscope", "SlotPosition")] == 0


def test_capture_tile_scan_save_and_clear_call_their_procedures(scope, monkeypatch):
    calls = []
    monkeypatch.setattr(methods.acquire_step, "acquire", lambda handle, **asked: calls.append(("acquire", asked)) or {"position_label": "A1"})
    monkeypatch.setattr(methods.acquire_step, "tile_scan", lambda handle, positions, **asked: calls.append(("tile_scan", positions, asked)) or {"position_label": "row"})
    monkeypatch.setattr(methods.save_step, "save_waiting", lambda handle, label=None: calls.append(("save", label)) or [{"files": []}])
    monkeypatch.setattr(methods.clear_step, "clear", lambda handle: calls.append(("clear", None)) or {"cleared": [], "failed": {}})

    scope.acquire("A1", acquisition_type="overview")
    scope.tile_scan([(0.0, 0.0), (600.0, 0.0)], "row")
    scope.save("A1")
    scope.clear_cache()

    assert calls == [
        ("acquire", {"position_label": "A1", "acquisition_settings": None, "acquisition_type": "overview", "output_folder": None}),
        ("tile_scan", [(0.0, 0.0), (600.0, 0.0)], {"position_label": "row", "acquisition_type": "tile_scan", "output_folder": None, "button": "preview", "autofocus": False, "focus_map": None}),
        ("save", "A1"),
        ("clear", None),
    ]


def test_a_row_of_fields_is_laid_out_a_field_and_a_tenth_apart(scope):
    row = scope.row_of_fields(3)

    x, y = scope.position()["x"]["position"], scope.position()["y"]["position"]
    assert [round(px - x, 1) for px, _ in row] == [0.0, 1278.8, 2557.5]  # 1162.5 um field at zoom 1
    assert all(py == pytest.approx(y) for _, py in row)


def test_the_state_names_the_settings_and_the_objective(scope):
    state = scope.state()

    assert state["changeable"] == {
        "zoom": 1.0, "scan_speed": 400, "scan_field_rotation": 0.0, "line_average": 1,
        "pinhole_airy": pytest.approx(0.9994, abs=1e-3), "frame_average": 1, "line_accumulation": 1,
        "frame_accumulation": 1, "objective": 0,
    }
    assert state["observed"]["navigator"] == "plain"
    assert state["observed"]["image_size_px"] == [512, 512]


def test_what_only_the_controller_asks_for_is_not_on_the_microscope():
    """The description for get_info and the procedure menu are the controller's, in zmart_controller_plugin/."""
    for name in ("info", "procedures", "run_procedure"):
        assert not hasattr(navigator.Microscope, name), name


def test_a_closed_microscope_refuses(scope):
    scope.close()

    with pytest.raises(RuntimeError, match="disconnected"):
        scope.position()


def test_every_call_of_the_microscope_and_every_setting_is_in_the_readme():
    """The README is the manual of the driver used by itself; nothing it can do is left out."""
    import inspect
    import re
    from pathlib import Path

    readme = (Path(methods.__file__).resolve().parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## The calls") : readme.index("## Setting up the driver")]
    calls = [name for name, _ in inspect.getmembers(navigator.Microscope) if not name.startswith("_")]

    assert [name for name in calls if not re.search(rf"`{name}[`(]", section)] == []
    assert [name for name in SETTINGS if f"| `{name}` |" not in section] == []


def test_the_z_stack_is_set_and_answered_as_las_x_holds_it(scope, lasx):
    lasx.z_um[2] = 1000.0

    stack = scope.set_z_stack("z-wide", 995.0, 1005.0, 2.5)

    assert (stack["begin"], stack["end"], stack["sections"], stack["mode"]) == (
        pytest.approx(995.0), pytest.approx(1005.0), 5, "z-wide"
    )
    assert scope.settings()["z_stack"]["sections"] == 5


def test_a_z_stack_far_from_the_focus_is_refused(scope, lasx):
    lasx.z_um[2] = 1000.0

    with pytest.raises(RuntimeError, match="step_z_wide_um"):
        scope.set_z_stack("z-wide", 3000.0, 3010.0, 2.5)

    assert "SetZStackDefinition" not in lasx.commands()



def test_scan_mode_and_autofocus_are_changed_like_every_setting(scope, lasx):
    assert scope.change_setting("autofocus", False)["confirmed"] is True
    assert scope.settings()["autofocus"] is False
    assert scope.change_setting("scan_mode", "xyz")["confirmed"] is True



def test_a_laser_line_is_set_by_its_wavelength_in_percent(scope, lasx):
    assert scope.set_laser_power(488.0, 5.0)["confirmed"] is True
    assert scope.set_laser_shutter(488.0, False)["confirmed"] is True



def test_the_acquisition_settings_are_changed_like_every_setting(scope, lasx):
    assert scope.change_setting("frame_average", 2)["confirmed"] is True
    assert scope.change_setting("format", (256, 256))["confirmed"] is True
    assert scope.set_filter_wheel(140, "Substrate")["confirmed"] is True



def test_a_detector_is_set_through_a_setting_file(scope, lasx):
    assert scope.set_detector("HyD S 3", gain=40.0)["confirmed"] is True



def test_a_detector_s_channel_gets_a_colour(scope, lasx):
    assert scope.set_channel_colour("HyD S 3", "Green")["confirmed"] is True



def test_a_detector_s_mode_and_dye_are_set(scope, lasx):
    assert scope.set_detector("HyD S 3", operating_mode="Analog", dye="Leica/ALEXA 488")["confirmed"] is True


def test_the_format_is_set_to_the_one_las_x_computes_as_optimal(scope, lasx):
    # LAS X's "Optimize XY Format" button: the optimal format is in LCS's state (simulator, 2026-11-01).
    block = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo")
    lasx.live.update({(*block, "OptimizedFormatX"): 2824, (*block, "OptimizedFormatY"): 2824, (*block, "IsOptimizeFormatValid"): True})

    result = scope.optimize_format()

    assert result["confirmed"] is True
    assert scope.settings()["format"] == "2824 x 2824"


def test_without_a_valid_optimal_format_nothing_is_changed(scope, lasx):
    block = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo")
    lasx.live.update({(*block, "OptimizedFormatX"): 2824, (*block, "OptimizedFormatY"): 2824, (*block, "IsOptimizeFormatValid"): False})

    with pytest.raises(RuntimeError, match="optimal"):
        scope.optimize_format()

    assert "SetFormat" not in lasx.commands()



def test_bidirectional_is_changed_like_every_setting(scope, lasx):
    assert scope.change_setting("bidirectional", True)["confirmed"] is True
    assert scope.settings()["bidirectional"] is True



def test_the_pinhole_and_phase_x_are_read_with_the_settings(scope, lasx):
    assert scope.change_setting("phase_x", 10)["confirmed"] is True
    shown = scope.settings()
    assert shown["phase_x"] == pytest.approx(10.0)
    assert shown["pinhole"]["unit"] == "airy" and shown["pinhole"]["emission_nm"] == 580



def test_the_system_optimized_step_is_read_with_the_settings(scope, lasx):
    assert scope.settings()["system_optimized_step"] is False
    assert scope.change_setting("system_optimized_step", True)["confirmed"] is True
    assert scope.settings()["system_optimized_step"] is True


def test_the_bit_depth_is_read_with_the_settings(scope, lasx):
    assert scope.settings()["bit_depth"] == 8
    assert scope.change_setting("bit_depth", 12)["confirmed"] is True
    assert scope.settings()["bit_depth"] == 12


def test_the_pan_is_read_and_centred_through_the_scanner(scope, lasx, monkeypatch):
    from test_pan import Steps

    from zmart_drivers.leica.stellaris.navigator.procedures import pan

    monkeypatch.setattr(pan.scanner, "PanSteps", lambda: Steps(lasx))
    lasx.pan = [0.0001743713507108436, 0.0]
    assert scope.pan()["x_um"] == pytest.approx(17.44, abs=0.01)
    assert scope.pan_centre()["confirmed"] is True
    assert scope.pan()["x_um"] == pytest.approx(0.0, abs=0.01)
    assert not hasattr(scope, "center_pan")


def test_the_pan_is_moved_by_micrometres_through_the_scanner(scope, lasx, monkeypatch):
    from test_pan import Steps

    from zmart_drivers.leica.stellaris.navigator.procedures import pan

    monkeypatch.setattr(pan.scanner, "PanSteps", lambda: Steps(lasx))
    assert scope.move_pan(15.0, 0.0)["confirmed"] is True
    assert scope.pan()["x_um"] == pytest.approx(15.0, abs=0.01)



def test_an_objective_without_a_collar_is_not_motorised_on_a_stand_that_could_turn_one(monkeypatch):
    """The stand's CanDoMotCorr alone said True for the STELLARIS's 10x dry, which has no collar (2026-11-04)."""
    import state_tree_stand_in

    from zmart_drivers.leica.stellaris.navigator.primitives.direct import reads

    monkeypatch.setitem(state_tree_stand_in.SIMULATOR_OBJECTIVES[0], "IsMotCorrApplicable", False)
    attach(monkeypatch, PlainNavigator(z_wide_um=100.0))

    assert reads.get_collar()["motorised"] is False


def test_the_collar_is_read_with_the_settings(scope, lasx):
    assert scope.settings()["collar"] == {"percent": 0.0, "motorised": True, "reference": None}
    assert scope.change_setting("collar", 25)["confirmed"] is True
    assert scope.settings()["collar"]["percent"] == 25.0



def test_smartcorr_and_the_collar_s_reference(scope, lasx):
    assert scope.change_setting("smartcorr_range", (20, 80, 5))["confirmed"] is True
    assert scope.settings()["smartcorr"] == {"begin": 20.0, "end": 80.0, "steps": 5}
    lasx.smartcorr_finds = 55.0
    assert scope.smartcorr(project=24)["collar"]["percent"] == 55.0
    lasx.nodes.objectives[0]["MotCorrRefPos"] = 10.0
    assert scope.collar_to_reference()["confirmed"] is True
    assert scope.settings()["collar"]["reference"] == 10.0



def test_the_collar_s_reference_is_set_gone_to_and_reset(scope, lasx):
    assert scope.change_setting("collar", 40)["confirmed"] is True
    assert scope.set_collar_reference()["confirmed"] is True
    assert scope.change_setting("collar", 0)["confirmed"] is True
    assert scope.collar_to_reference()["confirmed"] is True
    assert scope.settings()["collar"]["percent"] == 40.0
    assert scope.reset_collar_reference()["confirmed"] is True
    assert scope.settings()["collar"]["reference"] is None


def test_the_channel_colour_lock_is_pressed_in_las_x_s_window(scope, monkeypatch):
    from test_lasx_window import Lock, Window

    from zmart_drivers.leica.stellaris.navigator.procedures import lasx_window

    lock = Lock()
    monkeypatch.setattr(lasx_window, "MainWindow", lambda: Window(lock))
    assert scope.lock_channel_colour(True)["confirmed"] is True
    assert lock.on is True



def test_the_setting_is_switched_renamed_and_read(scope, lasx):
    one = [{"id": 3, "name": "Setting 1", "enabled": True}]
    assert scope.settings()["setting"] == {"id": 3, "name": "Setting 1", "enabled": True, "all": one}
    assert scope.enable_setting(False)["confirmed"] is True
    assert scope.change_setting("setting_name", "ZMART test")["confirmed"] is True
    shown = scope.settings()["setting"]
    assert (shown["id"], shown["name"], shown["enabled"]) == (3, "ZMART test", False)
    assert shown["all"] == [{"id": 3, "name": "ZMART test", "enabled": False}]


def test_a_setting_is_added_and_deleted(scope, lasx):
    assert scope.add_setting()["confirmed"] is True
    assert len(scope.settings()["setting"]["all"]) == 2
    assert scope.delete_setting("Setting 2")["confirmed"] is True


def test_a_detector_is_locked_for_the_dye_assistant(scope, lasx, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.procedures import readings

    line = {"route": (30,), "line_index": 0, "wavelength": 405.0, "percent": 2.0}
    monkeypatch.setattr(readings, "get_setting_definitions", lambda client, **k: [{"name": "Setting 1", "lines": [line], "detectors": []}])
    assert scope.lock_detector("HyD S 3", True)["confirmed"] is True



def test_the_sequential_mode_is_changed_and_read(scope, lasx):
    assert scope.settings()["sequential_mode"] == "line"
    assert scope.change_setting("sequential_mode", "stack")["confirmed"] is True
    assert scope.settings()["sequential_mode"] == "stack"



def test_a_white_light_line_is_tuned(scope, lasx):
    assert scope.tune_laser_line(497, 510)["confirmed"] is True



def test_a_white_light_line_is_added(scope, lasx):
    assert scope.add_laser_line(600)["confirmed"] is True



def test_a_laser_line_is_removed(scope, lasx):
    assert scope.add_laser_line(600)["confirmed"] is True
    assert scope.remove_laser_line(600)["confirmed"] is True



def test_a_setting_is_made_current(scope, lasx):
    assert scope.add_setting()["confirmed"] is True
    assert scope.activate_setting("Setting 1")["confirmed"] is True
    assert scope.settings()["setting"]["name"] == "Setting 1"



def test_the_autofocus_is_run(scope, lasx, tmp_path, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.procedures import changes

    monkeypatch.setattr(changes, "LCS_LOG", tmp_path / "LCS.LOG")
    assert scope.autofocus(project=24)["found"] is True


@pytest.mark.parametrize("name, direction", [
    ("pan_up", "up"), ("pan_down", "down"), ("pan_left", "left"), ("pan_right", "right"), ("pan_up_left", "up-left"),
    ("pan_up_right", "up-right"), ("pan_down_left", "down-left"), ("pan_down_right", "down-right"), ("pan_centre", "centre"),
])
def test_each_of_the_pan_pad_s_nine_buttons_has_its_function(name, direction, monkeypatch):
    from zmart_drivers.leica.stellaris.navigator.methods import Microscope

    asked = []
    monkeypatch.setattr(Microscope, "step_pan", lambda self, *a, **k: asked.append((a, k)) or {"success": True})
    scope = Microscope.__new__(Microscope)

    getattr(scope, name)() if name == "pan_centre" else getattr(scope, name)(2, percent=1)

    expected = ((direction,), {}) if name == "pan_centre" else ((direction, 2), {"percent": 1})
    assert asked == [expected]
