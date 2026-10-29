"""``run_ci.py``: the offline suite by default, the check at the microscope with ``--hardware``.

Every step is replaced here, so what is checked is which steps run, in what
order and with what, not what they do.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_run_ci():
    path = Path(__file__).resolve().parents[1] / "run_ci.py"
    spec = importlib.util.spec_from_file_location("run_ci_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_ci_offers_the_offline_suite_and_the_hardware_check():
    run_ci = _load_run_ci()

    assert run_ci.parse_args([]).hardware is False
    assert run_ci.parse_args(["--mock"]).mock is True
    hardware = run_ci.parse_args(["--hardware", "--out", "D:/ci", "--tiles", "4", "--allow-objective"])
    assert (hardware.hardware, hardware.out, hardware.tiles, hardware.allow_objective) == (True, "D:/ci", 4, True)

    with pytest.raises(SystemExit):
        run_ci.parse_args(["--mock", "--hardware"])
    with pytest.raises(SystemExit):
        run_ci.parse_args(["--out", "D:/ci"])  # goes with --hardware
    with pytest.raises(SystemExit):
        run_ci.parse_args(["--allow-objective"])


def test_coverage_keeps_its_data_file_with_the_reports():
    """A run started in the driver folder must leave no .coverage there; the folder guard refuses loose files."""
    run_ci = _load_run_ci()

    assert run_ci.build_env()["COVERAGE_FILE"] == str(run_ci.REPORT_DIR / ".coverage")


def _capture_steps(run_ci, monkeypatch, tmp_path, argv, *, controller=True, exit_code=0, selftest_ok=True):
    """Run run_ci with every step replaced; return (name, command, fatal, cwd) per step."""
    monkeypatch.setattr(run_ci, "controller_installed", lambda: controller)
    captured: list[tuple[str, list[str], bool, Path | None]] = []
    monkeypatch.setattr(run_ci, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(run_ci, "build_env", dict)
    monkeypatch.setattr(run_ci.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))

    def fake_run_step(name, cmd, env, *, fatal, cwd=None):
        captured.append((name, cmd, fatal, cwd))
        ok = selftest_ok or not name.startswith("limits:")
        return {"name": name, "ok": ok, "fatal": fatal, "returncode": 0 if ok else 1, "seconds": 0.0, "error": None, "command": cmd}

    monkeypatch.setattr(run_ci, "run_step", fake_run_step)
    assert run_ci.main(argv) == exit_code
    return captured


def _summary(tmp_path):
    return json.loads((tmp_path / "ci_summary.json").read_text(encoding="utf-8"))


def _marks(cmd: list[str]) -> str:
    """The marks a pytest command selects: what follows its last ``-m`` (the first is ``-m pytest``)."""
    return cmd[len(cmd) - cmd[::-1].index("-m")]


def test_the_offline_suite_runs_in_two_layers(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, [])
    tests = [(name, cmd) for name, cmd, _fatal, _cwd in captured if name.startswith("tests:")]
    (driver_name, driver_cmd), (controller_name, controller_cmd) = tests
    assert driver_name.startswith("tests: the driver, offline")
    assert _marks(driver_cmd) == "not hardware and not controller"
    assert controller_name.startswith("tests: the driver in the ZMART Controller")
    assert _marks(controller_cmd) == "controller and not hardware"
    assert "--cov-append" not in driver_cmd
    if any(part.startswith("--cov=") for part in controller_cmd):
        assert "--cov-append" in controller_cmd  # one coverage report for both layers
    assert not [name for name, *_ in captured if name.startswith("hardware:")]


def test_without_the_controller_the_offline_suite_skips_its_layer(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, [], controller=False)
    ran = [name for name, *_ in captured if name.startswith("tests:")]
    assert len(ran) == 1 and ran[0].startswith("tests: the driver, offline")
    assert any(s.get("skipped") and s["name"].startswith("tests: the driver in the ZMART Controller") for s in _summary(tmp_path)["steps"])
    assert _summary(tmp_path)["passed"] is True


def test_controller_asks_for_the_layer_and_fails_without_it(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    _capture_steps(run_ci, monkeypatch, tmp_path, ["--controller"], controller=False, exit_code=1)
    assert [name for name in _summary(tmp_path)["fatal_failures"]] == [
        s["name"] for s in _summary(tmp_path)["steps"] if s["name"].startswith("tests: the driver in the ZMART Controller")
    ]


def test_the_hardware_check_is_the_limits_self_check_then_navigator_ci(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware", "--tiles", "4", "--allow-objective"])

    names = [name for name, *_ in captured]
    assert names == [
        "limits: self-check (the gate proven before any hardware)",
        "hardware: the plain Navigator route on the live instrument",
    ]
    selftest, hardware = (cmd for _n, cmd, _f, _c in captured)
    assert selftest[-1].endswith("test_limits.py")
    assert any(part.endswith("navigator_ci.py") for part in hardware)
    assert hardware[hardware.index("--out") + 1] == str(tmp_path / "navigator-ci")
    assert hardware[hardware.index("--tiles") + 1] == "4"
    assert "--allow-objective" in hardware
    assert all(fatal for _n, _c, fatal, _cwd in captured)
    # LAS X's Data Container client writes DCLogFile.log where a script starts: with the reports.
    assert captured[1][3] == tmp_path


def test_the_hardware_check_saves_where_it_is_told(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware", "--out", str(tmp_path / "pictures")])
    hardware = captured[1][1]
    assert hardware[hardware.index("--out") + 1] == str(tmp_path / "pictures")
    assert "--allow-objective" not in hardware


def test_a_failed_self_check_keeps_the_stage_untouched(monkeypatch, tmp_path):
    """When the limits gate does not prove itself, navigator_ci.py is never started."""
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware"], exit_code=1, selftest_ok=False)
    assert [name for name, *_ in captured] == ["limits: self-check (the gate proven before any hardware)"]
