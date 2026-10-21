"""The driver used by itself: connect, move, change settings, capture, save and clear.

``navigator_expert.connect()`` hands back a :class:`Microscope`, the driver's
own way to drive the STELLARIS from a notebook or a script, without the ZMART
Controller. These tests connect it to the offline LAS X stand-in and check
what comes back. The capture, saving and clearing themselves are tested with
their procedures; here they are checked to be called with what was asked.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import job_settings
from limits_fixtures import hermetic_mock_machine_root
from mock_lasx_api import MockLasxClient

import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as navigator_expert
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_api import microscope
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.connection import lasx_client
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.readings import reading_settings



@pytest.fixture
def scope(monkeypatch, tmp_path):
    """A Microscope connected to the LAS X stand-in, with a throwaway machine folder."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", os.environ["ZMART_MICROSCOPY_ROOT"])
    monkeypatch.setattr(
        reading_settings,
        "STATE_READERS",
        replace(reading_settings.STATE_READERS, selected_job_confirm_source="api"),
    )
    hermetic_mock_machine_root()
    monkeypatch.setattr(lasx_client, "connect_python_client", lambda **_kw: MockLasxClient(latency=0.0))
    scope = navigator_expert.connect(output_root=str(tmp_path / "out"))
    yield scope
    scope.close()


def test_connect_hands_back_a_microscope(scope):
    assert isinstance(scope, navigator_expert.Microscope)


def test_the_package_offers_only_the_microscope():
    """Nothing that goes around the session: no raw reads or commands on a client."""
    offered = {name for name in vars(navigator_expert) if not name.startswith("__")} - {"driver_api"}

    assert sorted(navigator_expert.__all__) == ["Microscope", "__version__", "connect"]
    assert offered <= {"Microscope", "connect"} | {"dispatcher", "driver_setup", "procedures", "session", "connection", "primitives", "operations", "zmart_controller_plugin", "testing"}


def test_the_running_log_gets_the_messages_of_every_part_of_the_driver(scope):
    """Not only of the folder that connects: the dispatcher's and the procedures' too."""
    import logging

    driver = logging.getLogger(microscope.DRIVER_PACKAGE)

    assert microscope.DRIVER_PACKAGE == navigator_expert.__name__
    assert any(getattr(h, "baseFilename", "").endswith("running_log.txt") for h in driver.handlers)


def test_the_stage_moves_and_reads_back_in_the_sample_frame(scope):
    start = {axis: scope.position()[axis]["position"] for axis in "xyz"}

    moved = scope.move_to(start["x"] + 120.0, start["y"] - 40.0, start["z"] + 3.0)
    here = scope.position()

    assert moved["x"]["position"] == pytest.approx(start["x"] + 120.0, abs=1.0)
    assert here["y"]["position"] == pytest.approx(start["y"] - 40.0, abs=1.0)
    assert here["z"]["position"] == pytest.approx(start["z"] + 3.0, abs=0.5)
    assert here["x"]["unit"] == "micrometer"


def test_a_move_outside_the_limits_is_refused_and_the_stage_stays(scope):
    before = scope.position()["x"]["position"]

    with pytest.raises(RuntimeError, match="refused"):
        scope.move_to(-1e9, 0.0, 0.0)

    assert scope.position()["x"]["position"] == pytest.approx(before)


def test_the_motors_of_each_axis_are_named(scope):
    assert scope.motors()["z"] == {"options": ["z-wide"], "default": "z-wide"}


def test_the_jobs_are_listed_with_the_autofocus_jobs_apart(scope):
    jobs = scope.jobs()

    assert "HiRes" in jobs["jobs"]
    assert "AF Job" in jobs["autofocus_jobs"]
    assert "AF Job" not in jobs["jobs"]
    assert jobs["selected"] in jobs["jobs"]


def test_a_job_is_selected_by_name(scope):
    scope.select_job("Overview")

    assert scope.jobs()["selected"] == "Overview"


def test_an_unknown_job_is_refused(scope):
    with pytest.raises(ValueError, match="no longer exists"):
        scope.select_job("No such job")


def test_a_setting_is_changed_by_name_and_read_back(scope):
    scope.select_job("HiRes")

    result = scope.change_setting("zoom", value=4.0)

    assert result["confirmed"] is True
    assert scope.job_settings()["zoom"]["current"] == pytest.approx(4.0)


def test_a_setting_is_changed_for_the_job_named(scope):
    scope.select_job("HiRes")

    scope.change_setting("zoom", job="Overview", value=2.5)

    assert scope.job_settings("Overview")["zoom"]["current"] == pytest.approx(2.5)
    assert scope.job_settings("HiRes")["zoom"]["current"] == pytest.approx(10.0)


def test_a_setting_with_optional_arguments_takes_only_those_given(scope):
    """The z-stack's reset flags may be left out, as they may in the named change."""
    scope.select_job("HiRes")

    result = scope.change_setting("z_stack_definition", begin_um=-5.0, end_um=5.0)

    assert result["success"] is True


def test_a_wrong_argument_is_refused_with_the_arguments_the_setting_takes(scope):
    with pytest.raises(ValueError, match="value"):
        scope.change_setting("zoom", factor=2.0)


def test_an_unknown_setting_is_refused_with_the_names_there_are(scope):
    with pytest.raises(ValueError, match="zoom"):
        scope.change_setting("brightness", value=1.0)


def test_a_setting_lasx_refuses_raises_with_its_reason(scope):
    scope.select_job("HiRes")

    with pytest.raises(RuntimeError, match="out of range"):
        scope.change_setting("zoom", value=500.0)


def test_capture_save_and_clear_call_their_procedures(scope, monkeypatch):
    calls = []
    monkeypatch.setattr(microscope.acquire_step, "acquire", lambda handle, **asked: calls.append(("acquire", asked)) or {"position_label": "A1"})
    monkeypatch.setattr(microscope.save_step, "save_waiting", lambda handle, label=None: calls.append(("save", label)) or [{"files": []}])
    monkeypatch.setattr(microscope.clear_step, "clear", lambda handle: calls.append(("clear", None)) or {"cleared": [], "failed": {}})

    scope.acquire("A1", acquisition_type="overview")
    scope.save("A1")
    scope.clear_cache()

    assert calls == [
        ("acquire", {"position_label": "A1", "acquisition_settings": None, "acquisition_type": "overview", "output_folder": None}),
        ("save", "A1"),
        ("clear", None),
    ]


def test_the_state_names_the_selected_job(scope):
    assert scope.state()["changeable"]["job"] in scope.jobs()["jobs"]


def test_what_only_the_controller_asks_for_is_not_on_the_microscope():
    """The description for get_info and the procedure menu are the controller's, in zmart_controller_plugin/."""
    for name in ("info", "procedures", "run_procedure", "autofocus"):
        assert not hasattr(navigator_expert.Microscope, name), name


def test_a_closed_microscope_refuses(scope):
    scope.close()

    with pytest.raises(RuntimeError, match="disconnected"):
        scope.position()


def test_every_call_of_the_microscope_and_every_setting_is_in_the_readme():
    """The README is the manual of the driver used by itself; nothing it can do is left out."""
    import inspect
    import re
    from pathlib import Path

    readme = (Path(microscope.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## 4. Using the driver by itself") : readme.index("## 5.")]
    calls = [name for name, _ in inspect.getmembers(navigator_expert.Microscope) if not name.startswith("_")]

    assert [name for name in calls if not re.search(rf"`{name}[`(]", section)] == []
    assert [name for name in job_settings.SETTINGS if f"| `{name}` |" not in section] == []
