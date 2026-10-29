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
    return script


@pytest.fixture
def everything_on(monkeypatch):
    script = load_script()
    monkeypatch.setattr(
        script, "ALLOW_GROUPS", {group: True for group in script.ALLOW_GROUPS}
    )
    for switch in (
        "ALLOW_STAGE",
        "ALLOW_OBJECTIVE",
        "ALLOW_OPTICAL_CONFIGURATION",
        "ALLOW_SNAP",
    ):
        monkeypatch.setattr(script, switch, True)
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
    assert summary.strip().endswith("0 failed")
    assert list((tmp_path / "results").glob("snap_*.tif"))


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


def test_by_default_nothing_changes(tmp_path):
    script = load_script()
    nis = make_nis()
    report = script.main(nis, str(tmp_path / "results"))
    assert not any(c["changes"] for c in report["checks"])
    writes = {
        "StgMoveXY",
        "StgMoveZ",
        "Capture",
        "SelectOptConf",
        "Stg_SetShutterStateEx",
        "Stg_Filter_SetPosition",
    }
    assert writes.isdisjoint(nis.scope.calls)
    inventory = next(
        c for c in report["checks"] if c["check"].startswith("every hardware setting")
    )
    assert inventory["outcome"] == "ok" and inventory["answer"]["shutter"]["devices"]


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
    monkeypatch.setenv("ZMART_CHECK_RESULTS", str(tmp_path / "here"))
    monkeypatch.setitem(sys.modules, "nis", make_nis())
    exec(line, {})  # noqa: S102 -- the macro's own line, run as NIS would
    assert list((tmp_path / "here" / "results").glob("*/check_summary.txt"))


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


def test_with_the_microscope_off_every_function_is_called_once_and_nothing_changes(
    tmp_path,
):
    """With the hardware off, the check tries each of the driver's functions with its arguments, as NIS would take them."""
    script = load_script()
    nis = make_nis()
    nis.scope.hardware_on = False
    before = (
        dict(nis.scope.shutters),
        dict(nis.scope.filters),
        nis.scope.light_path,
        nis.scope.condenser,
        nis.scope.nosepiece,
    )
    report = script.main(nis, str(tmp_path / "results"))
    tried = {
        c["check"]: c for c in report["checks"] if c["check"].startswith("arguments of")
    }
    assert len(tried) > 80
    assert [name for name, c in tried.items() if c["outcome"] != "ok"] == []
    assert (
        "arguments of Stg_SetShutterStateEx (a change, with the microscope off)"
        in tried
    )
    assert not any(
        "Stg_HeatStage_SetTemperature" in name or "Stg_Incubator_Set" in name
        for name in tried
    )
    laser = tried[
        "arguments of Stg_SetMultiLaserPower (a change, with the microscope off)"
    ]
    assert laser["answer"]["arguments"][-1] == 0, "a laser is only ever sent 0"
    after = (
        dict(nis.scope.shutters),
        dict(nis.scope.filters),
        nis.scope.light_path,
        nis.scope.condenser,
        nis.scope.nosepiece,
    )
    assert after == before
    summary = (tmp_path / "results" / "check_summary.txt").read_text()
    assert summary.count("\n") > 80 and summary.strip().endswith("0 failed")


def test_wrong_arguments_are_named_in_the_summary(tmp_path, monkeypatch):
    import mock_nis

    script = load_script()
    nis = make_nis()
    nis.scope.hardware_on = False

    def takes_three(scope, a, b, c):
        return 1

    monkeypatch.setitem(
        mock_nis.FUNCTIONS, "Stg_SetLightPathPosition", mock_nis._function(takes_three)
    )
    report = script.main(nis, str(tmp_path / "results"))
    failed = [c for c in report["checks"] if c["outcome"] != "ok"]
    assert [c["check"] for c in failed] == [
        "arguments of Stg_SetLightPathPosition (a change, with the microscope off)"
    ]
    assert "Bad number of parameters" in failed[0]["answer"]


def test_with_the_microscope_on_no_change_is_sent_by_the_argument_check(tmp_path):
    script = load_script()
    nis = make_nis()
    report = script.main(nis, str(tmp_path / "results"))
    assert not any(c["changes"] for c in report["checks"])
    note = next(c for c in report["checks"] if c["check"] == "the changes' arguments")
    assert "not tried" in note["answer"]
