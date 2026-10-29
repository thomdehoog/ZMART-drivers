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
        ("tile_scan", [(0.0, 0.0), (600.0, 0.0)], {"position_label": "row", "acquisition_type": "tile_scan", "output_folder": None}),
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
        "pinhole_airy": pytest.approx(0.9994, abs=1e-3), "objective": 0,
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
