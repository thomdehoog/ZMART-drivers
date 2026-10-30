"""The hardware check that runs inside NIS: here it runs against the pretend NIS, every change switched on.

At the microscope the check runs in NIS's own Python, started by
``check_hardware.mac``. These tests run the same script and the same macro
line over the pretend NIS, so a mistake in either shows here and not at the
microscope.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest
from mock_nis import make_nis, open_operator_image

HARDWARE = Path(__file__).resolve().parents[1] / "hardware"
SCRIPT = HARDWARE / "check_hardware_nis.py"
REPO_ROOT = Path(__file__).resolve().parents[5]


def load_script():
    spec = importlib.util.spec_from_file_location("check_hardware_nis", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    script.SETTLE_S = 0.0  # the pretend microscope settles at once
    return script


@pytest.fixture
def everything_on(monkeypatch):
    """The check with every change it can make: the nosepiece turned and Perfect Focus switched too."""
    script = load_script()
    monkeypatch.setattr(script, "TURN_NOSEPIECE", True)
    monkeypatch.setattr(script, "SWITCH_PFS", True)
    monkeypatch.setattr(script, "SETTLE_S", 0.0)
    return script


def test_with_everything_switched_on_every_check_passes_and_everything_is_put_back(
    everything_on, tmp_path
):
    nis = make_nis()
    scope = nis.scope
    scope.selected = "GFP"
    mine = open_operator_image(scope)
    before = (
        dict(scope.shutters),
        json.dumps(scope.lamps),
        dict(scope.filters),
        scope.light_path,
        scope.condenser,
        scope.nosepiece,
    )
    report = everything_on.main(nis, str(tmp_path / "results"))
    failed = [c for c in report["checks"] if c["outcome"] != "ok"]
    assert failed == []
    names = {c["check"] for c in report["checks"]}
    assert {
        "change shutter/EPI",
        "change filter/Filter Turret 1",
        "change light_path",
        "change pfs",
        "change laser_power/0.1",
    } <= names
    after = (
        dict(scope.shutters),
        json.dumps(scope.lamps),
        dict(scope.filters),
        scope.light_path,
        scope.condenser,
        scope.nosepiece,
    )
    assert after == before
    time.sleep(0.1)  # the pretend stage takes a moment to arrive
    assert scope.position() == pytest.approx((1000.0, 2000.0, 3000.0))
    assert scope.documents == [mine], (
        "the operator's image stays, and the check's own picture is closed"
    )
    summary = (tmp_path / "results" / "check_summary.txt").read_text()
    assert " 0 failed" in summary
    assert list((tmp_path / "results").glob("snap_*.tif"))
    # The driver itself ran, through its own bridge, and so did the controller's commands.
    driver = {
        c["check"]
        for c in report["checks"]
        if c["check"].startswith(("driver:", "controller plugin:"))
    }
    assert {
        "driver: connect()",
        "driver: move_to() x 5 um and back",
        "driver: set_device('shutter/EPI') and back",
        "driver: acquire(), saved as OME-Zarr",
        "controller plugin: get_info()",
        "controller plugin: get_xyz()",
        "controller plugin: get_state()",
    } <= driver
    assert list((tmp_path / "results" / "driver_data" / "data").glob("*.ome.zarr"))


def test_a_guarded_setting_is_only_ever_set_to_its_own_value(everything_on, tmp_path):
    nis = make_nis()
    nis.scope.laser_lines["488 nm"][1] = 12.5
    report = everything_on.main(nis, str(tmp_path / "results"))
    found = next(c for c in report["checks"] if c["check"] == "change laser_power/0.1")
    assert found["answer"]["set to"] == found["answer"]["before"] == 12.5
    line = next(c for c in report["checks"] if c["check"] == "change laser_line/0.1")
    assert line["answer"]["set to"] == line["answer"]["before"] == 0, (
        "a laser line is never switched on by the check"
    )


def test_a_function_nis_does_not_offer_is_reported(tmp_path, monkeypatch):
    import mock_nis

    script = load_script()
    monkeypatch.delitem(mock_nis.FUNCTIONS, "Stg_GetCondenserPosition")
    report = script.main(make_nis(), str(tmp_path / "results"))
    found = next(
        c
        for c in report["checks"]
        if c["check"] == "which of the driver's functions NIS offers"
    )
    assert found["answer"]["not offered"] == ["Stg_GetCondenserPosition"]


def test_a_spinning_disk_answering_nonsense_with_the_hardware_off_is_not_offered(
    tmp_path, monkeypatch
):
    """NIS 7 read a disk speed of 7297880 with the hardware off: no believable disk, and so no disk shutter."""
    import mock_nis

    script = load_script()
    monkeypatch.setitem(mock_nis.FUNCTIONS, "Stg_GetDiskSpeed", lambda scope: 7297880)
    monkeypatch.setitem(mock_nis.FUNCTIONS, "Stg_GetCSUShutterState", lambda scope: 0)
    report = script.main(make_nis(), str(tmp_path / "results"))
    inventory = next(
        c for c in report["checks"] if c["check"].startswith("every hardware setting")
    )["answer"]
    assert (
        inventory["disk_speed"]["devices"] == []
        and inventory["disk_shutter"]["devices"] == []
    )
    monkeypatch.setitem(mock_nis.FUNCTIONS, "Stg_GetDiskSpeed", lambda scope: 4000)
    report = script.main(make_nis(), str(tmp_path / "again"))
    inventory = next(
        c for c in report["checks"] if c["check"].startswith("every hardware setting")
    )["answer"]
    assert inventory["disk_speed"]["devices"] and inventory["disk_shutter"]["devices"]


def test_the_macro_finds_the_script_on_the_desktop_and_runs_it(tmp_path, monkeypatch):
    """The macro's own Python line, run as NIS would, with this repository on a pretend Desktop."""
    line = re.search(
        r'Python_RunString\("(.*)"\);',
        (HARDWARE / "check_hardware.mac").read_text(encoding="ascii"),
    ).group(1)
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    # As Windows' "Extract all" leaves it: the unzipped folder inside a folder of the same name.
    unzipped = home / "Desktop" / "ZMART-drivers-claude-zmart-nikon-nis-7-driver-ocjk5t"
    unzipped.mkdir()
    (unzipped / "ZMART-drivers-claude-zmart-nikon-nis-7-driver-ocjk5t").symlink_to(
        REPO_ROOT, target_is_directory=True
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("ZMART_CHECK_RESULTS", raising=False)
    monkeypatch.setitem(sys.modules, "nis", make_nis())
    exec(line, {})  # noqa: S102 -- the macro's own line, run as NIS would
    # The results go to a short place on the Desktop, not two folders deep beside the script,
    # because Windows refuses paths longer than 260 characters.
    assert list((home / "Desktop" / "ZMART check results").glob("*/check_summary.txt"))


def test_the_check_needs_only_the_standard_library():
    """NIS's Python has nothing installed: the check, the table and the bridge import nothing else."""
    import subprocess

    code = (
        "import sys, builtins;"
        "real = builtins.__import__;"
        "blocked = ('numpy', 'tifffile', 'zarr', 'zmart_drivers', 'zmart_controller');"
        "builtins.__import__ = lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.split('.')[0] in blocked else real(name, *a, **k);"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'helpers')!r});"
        "import importlib.util;"
        f"spec = importlib.util.spec_from_file_location('c', {str(SCRIPT)!r}); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "print(len(m.load('s', m.os.path.join(m.NIS_PYTHON, 'server.py')).devices.TABLE))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert int(done.stdout.strip()) > 20


def test_a_device_is_put_back_even_when_its_reading_fails_after_the_change(
    everything_on, tmp_path, monkeypatch
):
    import mock_nis

    real = mock_nis.FUNCTIONS["Stg_GetShutterStateEx"]
    calls = {"n": 0}

    def fails_once_after_the_change(scope, name, state):
        calls["n"] += 1
        if name == "EPI" and scope.shutters["EPI"] == 1 and calls["n"] < 50:
            raise RuntimeError("the reading failed")
        return real(scope, name, state)

    monkeypatch.setitem(
        mock_nis.FUNCTIONS, "Stg_GetShutterStateEx", fails_once_after_the_change
    )
    nis = make_nis()
    report = everything_on.main(nis, str(tmp_path / "results"))
    failed = next(c for c in report["checks"] if c["check"] == "change shutter/EPI")
    assert failed["outcome"] == "FAIL"
    assert nis.scope.shutters["EPI"] == 0, (
        "the shutter is closed again although its reading failed"
    )


def test_nis_own_macro_reference_is_found_and_quoted(tmp_path, monkeypatch):
    """The reference on the PC says what each function takes: its text is quoted, a compiled help file copied whole."""
    script = load_script()
    nis_folder = tmp_path / "NIS-Elements"
    (nis_folder / "Help" / "Macro").mkdir(parents=True)
    (nis_folder / "Help" / "Macro" / "Stg_SetShutterStateEx.htm").write_text(
        "<p>int Stg_SetShutterStateEx(char *Name, int State);</p>", encoding="utf-16"
    )
    (nis_folder / "Help" / "MacroFunctions.chm").write_bytes(
        b"ITSF pretend compiled help"
    )
    monkeypatch.setattr(script, "NIS_FOLDERS", (str(nis_folder),))
    report = script.main(make_nis(), str(tmp_path / "results"))
    found = next(c for c in report["checks"] if c["check"] == "NIS's macro reference")
    assert found["outcome"] == "ok" and found["answer"]["functions found in them"] >= 1
    assert "Stg_SetShutterStateEx" not in found["answer"]["functions not found in them"]
    assert list((tmp_path / "results" / "help_pages").iterdir()), (
        "the page that names it is copied, to be sent along"
    )
    assert (
        "int Stg_SetShutterStateEx(char *Name, int State)"
        in (tmp_path / "results" / "reference.txt").read_text()
    )
    assert (tmp_path / "results" / "help_MacroFunctions.chm").is_file()


def test_a_function_list_naming_functions_without_brackets_is_quoted(
    tmp_path, monkeypatch
):
    """NIS 7's own FunctionList page names the functions without their brackets; it is found all the same."""
    script = load_script()
    docs = tmp_path / "NIS-Elements" / "Docs" / "nis"
    docs.mkdir(parents=True)
    (docs / "FunctionList_7.01.03.html").write_text(
        "<tr><td>Stg_GetShutterStateEx</td><td>Device</td></tr><tr><td>Stg_GetShutterStateExtra</td></tr>",
        encoding="utf-8",
    )
    monkeypatch.setattr(script, "NIS_FOLDERS", (str(tmp_path / "NIS-Elements"),))
    report = script.main(make_nis(), str(tmp_path / "results"))
    found = next(c for c in report["checks"] if c["check"] == "NIS's macro reference")[
        "answer"
    ]
    assert found["functions found in them"] == 1
    assert found["pages copied into help_pages"] == 1


def test_with_changes_off_nothing_is_ever_sent(tmp_path, monkeypatch):
    script = load_script()
    monkeypatch.setattr(script, "CHANGES", False)
    nis = make_nis()
    report = script.main(nis, str(tmp_path / "results"))
    assert [c["check"] for c in report["checks"] if c["outcome"] != "ok"] == []
    writes = {
        "StgMoveXY",
        "StgMoveZ",
        "Capture",
        "Stg_SetShutterStateEx",
        "Stg_Filter_SetPosition",
        "Stg_SetLightPathPosition",
    }
    changed = [c for c in nis.scope.calls if c in writes]
    # With CHANGES off, a setting is only ever set to the value it already has.
    assert "Capture" not in changed
    assert nis.scope.position() == pytest.approx((1000.0, 2000.0, 3000.0))


def test_the_changes_are_the_smallest_that_can_be_seen(tmp_path, monkeypatch):
    script = load_script()
    monkeypatch.setattr(script, "SETTLE_S", 0.0)
    report = script.main(make_nis(), str(tmp_path / "results"))
    by = {c["check"]: c for c in report["checks"]}
    assert by["change filter/Emission Wheel"]["answer"]["set to"] == 2, (
        "one position, from 1"
    )
    voltage = by["change lamp_voltage/DIA LED"]["answer"]
    assert abs(voltage["set to"] - voltage["before"]) == pytest.approx(0.15)
    stage = by["stage x 5 um and back"]["answer"]
    assert stage["to"][0] - stage["from"][0] == 5.0
    assert (
        by["change pfs"]["answer"]["set to"] == by["change pfs"]["answer"]["before"]
    ), "Perfect Focus is not switched by default"
    assert (
        by["nosepiece set to its own position"]["answer"]["to"]
        == by["nosepiece set to its own position"]["answer"]["from"]
    )


def test_with_the_microscope_off_every_call_is_tried_and_the_answers_are_the_baseline(
    tmp_path, monkeypatch
):
    """The same checks with the hardware off: NIS answers "not available", which is fine; nothing changes."""
    script = load_script()
    monkeypatch.setattr(script, "SETTLE_S", 0.0)
    nis = make_nis()
    nis.scope.hardware_on = False
    for name in (
        "Stg_GetShutterCount",
        "Stg_Light_GetCount",
        "Stg_Filter_GetCount",
        "Stg_IsLightPathPresent",
        "Stg_IsCondenserPresent",
        "Stg_GetMultiLaserCount",
    ):
        nis.scope.answer_next[
            name
        ] = -4  # as NIS 7.01.03 answered with the hardware off
    report = script.main(nis, str(tmp_path / "results"))
    failed = [c["check"] for c in report["checks"] if c["outcome"] != "ok"]
    assert failed == []
    by = {c["check"]: c for c in report["checks"]}
    assert (
        "read StgGetPosXY" in by and by["read StgGetPosXY"]["answer"]["returned"] == -4
    )
    assert (
        "sent once (the stage is not available)"
        in by["stage x 5 um and back"]["answer"]
    )
    assert (
        "capture" in by["one picture, saved as a TIFF, its document closed"]["answer"]
    )
    assert len(report["checks"]) > 90
    driver = {
        name: c
        for name, c in by.items()
        if name.startswith(("driver:", "controller plugin:"))
    }
    assert (
        "driver: connect()" in driver
        and "driver: move_to() while the stage is not available" in driver
    )
    assert all(c["outcome"] != "FAIL" for c in driver.values()), {
        n: c["answer"] for n, c in driver.items() if c["outcome"] == "FAIL"
    }


def test_wrong_arguments_are_named_in_the_summary(tmp_path, monkeypatch):
    import mock_nis

    script = load_script()
    monkeypatch.setattr(script, "SETTLE_S", 0.0)

    def takes_three(scope, a, b, c):
        return 1

    monkeypatch.setitem(
        mock_nis.FUNCTIONS, "Stg_SetLightPathPosition", mock_nis._function(takes_three)
    )
    report = script.main(make_nis(), str(tmp_path / "results"))
    failed = [c for c in report["checks"] if c["outcome"] != "ok"]
    # The call itself, and the driver's own set_device, which sends the same call through the bridge.
    assert [c["check"] for c in failed] == [
        "change light_path",
        "driver: set_device('light_path') and back",
    ]
    assert all("Bad number of parameters" in c["answer"] for c in failed)


def test_a_driver_that_cannot_load_is_said_and_the_rest_still_runs(
    tmp_path, monkeypatch
):
    """Should NIS's Python lack what the driver needs (numpy), the check says so in one line and finishes."""
    import zmart_drivers.nikon.nis_elements_ar7 as package

    script = load_script()
    # As NIS's Python would be without numpy: the driver's methods cannot be loaded.
    monkeypatch.setitem(
        sys.modules, "zmart_drivers.nikon.nis_elements_ar7.methods", None
    )
    monkeypatch.delattr(package, "methods", raising=False)
    report = script.main(make_nis(), str(tmp_path / "results"))
    line = next(
        c for c in report["checks"] if c["check"] == "driver: loads in NIS's Python"
    )
    assert line["outcome"] == "FAIL" and "cannot be loaded" in line["answer"]
    assert (tmp_path / "results" / "check_summary.txt").is_file()


def test_a_reading_nis_refuses_is_tried_with_other_arguments_until_one_is_taken(
    tmp_path,
):
    """NIS 7 refused some readings' arguments ("Bad number of parameters"); the check finds the ones it takes."""
    script = load_script()
    nis = make_nis()
    real = nis.mac

    class ZoomAnswersDirectly:
        """A NIS whose zoom reading takes nothing and answers the zoom itself."""

        def __getattr__(self, name):
            if name == "Stg_GetZoomPosition":

                def zoom(*args):
                    if args:
                        raise RuntimeError("Bad number of parameters")
                    return 2

                return zoom
            return getattr(real, name)

    nis.mac = ZoomAnswersDirectly()
    report = script.main(nis, str(tmp_path / "results"))
    found = {e["check"]: e for e in report["checks"]}
    assert found["read Stg_GetZoomPosition"]["outcome"] == "FAIL"
    probe = found["arguments Stg_GetZoomPosition takes"]
    assert probe["outcome"] == "ok"
    assert probe["answer"]["takes"] == []
    assert probe["answer"]["answer"]["return"] == 2


def test_a_whole_number_setting_is_sent_as_a_whole_number():
    """NIS refused 7297880.0 for the disk's speed: it wants 7297880."""
    devices = load_script().load(
        "d", str(Path(SCRIPT).parents[2] / "primitives" / "nis_python" / "devices.py")
    )
    row = next(r for r in devices.TABLE if r.key == "disk_speed")
    sent = devices.write_arguments(row, None, 1500.0)
    assert sent == [1500]
    assert isinstance(sent[0], int)
