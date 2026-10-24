import importlib.util
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


def test_run_ci_exposes_only_mock_and_hardware_modes():
    run_ci = _load_run_ci()

    assert run_ci.parse_args([]).hardware is False
    assert run_ci.parse_args(["--mock"]).mock is True
    assert run_ci.parse_args(["--hardware"]).hardware is True

    assert run_ci.parse_args(["--hardware", "--full"]).full is True

    with pytest.raises(SystemExit):
        run_ci.parse_args(["--mock", "--hardware"])
    with pytest.raises(SystemExit):
        run_ci.parse_args(["--full"])  # --full goes with --hardware
    with pytest.raises(SystemExit):
        run_ci.parse_args(["--no-cov"])


def test_coverage_keeps_its_data_file_with_the_reports():
    """A run started in the driver folder must leave no .coverage there; the folder guard refuses loose files."""
    run_ci = _load_run_ci()

    assert run_ci.build_env()["COVERAGE_FILE"] == str(run_ci.REPORT_DIR / ".coverage")


def _capture_steps(
    run_ci, monkeypatch, tmp_path, argv, folders=None, *, controller=True, exit_code=0
):
    """Run run_ci with every step replaced; return (name, command, fatal) per step.

    *folders*, when given, is filled with the folder each step runs in.
    *controller* says whether the ZMART Controller counts as installed.
    """
    monkeypatch.setattr(run_ci, "controller_installed", lambda: controller)
    captured: list[tuple[str, list[str], bool]] = []
    folders = {} if folders is None else folders

    monkeypatch.setattr(run_ci, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(run_ci, "build_env", lambda: {})
    monkeypatch.setattr(
        run_ci.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""),
    )

    def fake_run_step(name, cmd, env, *, fatal, cwd=None):
        captured.append((name, cmd, fatal))
        folders[name] = cwd
        return {
            "name": name,
            "ok": True,
            "fatal": fatal,
            "returncode": 0,
            "seconds": 0.0,
            "error": None,
            "command": cmd,
        }

    monkeypatch.setattr(run_ci, "run_step", fake_run_step)
    assert run_ci.main(argv) == exit_code
    return captured


def test_the_hardware_steps_run_in_the_reports_folder(monkeypatch, tmp_path):
    """LAS X writes DCLogFile.log where a script starts; it must land with the reports, not in the driver."""
    run_ci = _load_run_ci()
    folders = {}
    _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware"], folders)

    hardware = {
        name: cwd for name, cwd in folders.items() if name.startswith("hardware:")
    }
    assert hardware
    assert set(hardware.values()) == {tmp_path}


def test_the_quick_hardware_run_is_the_acceptance(monkeypatch, tmp_path):
    """--hardware: the limits self-check, the installed driver, the adapter, the direct route, the cache; nothing mocked."""
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware"])

    commands = {name: cmd for name, cmd, _fatal in captured}
    assert (
        "limits: mock self-check (fail-closed gate proven before any hardware)"
        in commands
    )
    fatal_by_name = {name: fatal for name, _cmd, fatal in captured}

    installed_cmd = _command_for(captured, "validate_installed_driver.py")
    assert "--allow-move" in installed_cmd
    assert "--allow-acquire" in installed_cmd
    assert "--mock" not in installed_cmd
    assert (
        fatal_by_name["hardware: installed driver (register folder, connect by name)"]
        is True
    )

    adapter_cmd = next(
        cmd
        for cmd in _commands_for(captured, "validate_zmart_adapter.py")
        if "--allow-acquire" in cmd
    )
    assert "--allow-acquire" in adapter_cmd
    assert "--allow-missing-lasx" not in adapter_cmd
    assert "--mock" not in adapter_cmd
    assert fatal_by_name["hardware: zmart adapter (move/state/acquire)"] is True

    hardware_cmds = _commands_for(captured, "validate_hardware.py")
    assert len(hardware_cmds) == 1
    assert "direct" in hardware_cmds[0]
    assert "--allow-acquire" in hardware_cmds[0]
    assert fatal_by_name["hardware: end-to-end validator [direct reader]"] is True

    saving = _command_for(captured, "check_cache_route.py")
    assert "--job" not in saving  # no job name: the job selected in LAS X is captured
    assert f"--report-dir={tmp_path}" in saving
    assert f"--save-to={tmp_path / 'captures'}" in saving
    assert (tmp_path / "captures").is_dir()
    assert "--autosave-off" not in saving  # the cache route never asks about AutoSave
    assert (
        fatal_by_name["hardware: saving from LAS X's cache (one capture, checked)"]
        is True
    )

    release = _command_for(captured, "check_cache_release.py")
    assert "--job" not in release
    assert release[release.index("--captures") + 1] == "10"
    assert "--autosave-off" not in release
    assert f"--report-dir={tmp_path}" in release
    assert (
        fatal_by_name[
            "hardware: letting go of LAS X's cache (10 captures, the canvas cleared)"
        ]
        is True
    )

    buttons = _command_for(captured, "check_buttons.py")
    assert f"--report-dir={tmp_path}" in buttons
    assert (
        fatal_by_name[
            "hardware: the buttons the driver presses (Jobs, Experiment, Clear) and LAS X's dialogs"
        ]
        is True
    )
    # The canvas the button check leaves shown is what the cache checks need.
    order = [name for name, _cmd, _fatal in captured]
    assert order.index(
        "hardware: the buttons the driver presses (Jobs, Experiment, Clear) and LAS X's dialogs"
    ) < order.index("hardware: saving from LAS X's cache (one capture, checked)")

    # The setup wizard runs after the end-to-end validator and before the buttons,
    # and fails the run.
    wizard = _command_for(captured, "check_setup_wizard.py")
    assert f"--report-dir={tmp_path}" in wizard
    wizard_step = "hardware: the setup wizard (into a copy of the settings)"
    assert fatal_by_name[wizard_step] is True
    assert (
        order.index("hardware: end-to-end validator [direct reader]")
        < order.index(wizard_step)
        < order.index(
            "hardware: the buttons the driver presses (Jobs, Experiment, Clear) and LAS X's dialogs"
        )
    )

    assert not _commands_for(captured, "probe_four_readers.py")
    assert not _commands_for(captured, "validate_readers_side_by_side.py")
    assert not _commands_for(captured, "benchmark_readers.py")


def test_the_stage_goes_to_the_ci_baseline_before_any_step_moves_it(
    monkeypatch, tmp_path
):
    """After a stage initialisation the stage stands at 0, 0, outside its limits (simulator, 2026-10-15)."""
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware"])
    hardware = [
        (name, cmd) for name, cmd, _fatal in captured if name.startswith("hardware:")
    ]
    first_name, first_cmd = hardware[0]
    assert first_name == "hardware: the stage to the CI baseline"
    # Through the driver alone, so it also runs without the ZMART Controller.
    assert any(part.endswith("ci_baseline.py") for part in first_cmd)
    assert not [part for part in first_cmd if part.startswith("--allow-")]


def test_the_full_hardware_run_adds_the_diagnostics(monkeypatch, tmp_path):
    """--hardware --full: the quick set plus the three readers, the reader probe, parity, and the api and log routes."""
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware", "--full"])
    fatal_by_name = {name: fatal for name, _cmd, fatal in captured}

    readers = _command_for(captured, "benchmark_readers.py")
    assert "--check" in readers
    assert f"--out={tmp_path / 'readers'}" in readers
    assert (
        fatal_by_name[
            "hardware: the direct, api and log readers side by side (reads only)"
        ]
        is True
    )

    assert _command_for(captured, "check_buttons.py")
    assert _command_for(captured, "check_cache_route.py")
    assert _command_for(captured, "check_cache_release.py")
    assert _command_for(captured, "validate_installed_driver.py")
    assert _command_for(captured, "probe_four_readers.py")
    assert "--mock" not in _command_for(captured, "validate_readers_side_by_side.py")
    assert fatal_by_name["hardware: reader parity + routed modes"] is False

    hardware_cmds = _commands_for(captured, "validate_hardware.py")
    assert len(hardware_cmds) == 4
    for cmd in hardware_cmds:
        assert "--allow-acquire" in cmd
        assert "--mock" not in cmd
    assert fatal_by_name["hardware: end-to-end validator [api reader]"] is False
    assert fatal_by_name["hardware: end-to-end validator [log reader]"] is False
    assert fatal_by_name["hardware: end-to-end validator [hybrid reader]"] is False
    assert fatal_by_name["hardware: end-to-end validator [direct reader]"] is True


def test_the_cache_checks_capture_the_job_asked_for(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(
        run_ci, monkeypatch, tmp_path, ["--hardware", "--job", "Overview"]
    )
    for script in ("check_cache_route.py", "check_cache_release.py"):
        cmd = _command_for(captured, script)
        assert cmd[cmd.index("--job") + 1] == "Overview"


def _commands_for(
    captured: list[tuple[str, list[str], bool]], script_name: str
) -> list[list[str]]:
    return [
        cmd
        for _name, cmd, _fatal in captured
        if any(part.endswith(script_name) for part in cmd)
    ]


def _command_for(
    captured: list[tuple[str, list[str], bool]], script_name: str
) -> list[str]:
    matches = _commands_for(captured, script_name)
    assert len(matches) == 1
    return matches[0]


CONTROLLER_HARDWARE = (
    "hardware: installed driver (register folder, connect by name)",
    "hardware: zmart adapter (move/state/acquire)",
)


def _summary(tmp_path):
    import json

    return json.loads((tmp_path / "ci_summary.json").read_text(encoding="utf-8"))


def test_the_controller_layer_runs_after_the_driver_layer(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, ["--hardware"])
    order = [name for name, _cmd, _fatal in captured]
    last_driver_step = order.index(
        "hardware: letting go of LAS X's cache (10 captures, the canvas cleared)"
    )
    assert all(order.index(name) > last_driver_step for name in CONTROLLER_HARDWARE)


def test_without_the_controller_its_layer_is_skipped_and_the_run_passes(
    monkeypatch, tmp_path
):
    run_ci = _load_run_ci()
    captured = _capture_steps(
        run_ci, monkeypatch, tmp_path, ["--hardware"], controller=False
    )
    ran = [name for name, _cmd, _fatal in captured]
    assert not set(CONTROLLER_HARDWARE) & set(ran)
    # The driver layer is all there, and moves nothing through the controller.
    for script in (
        "ci_baseline.py",
        "validate_hardware.py",
        "check_setup_wizard.py",
        "check_buttons.py",
        "check_cache_route.py",
        "check_cache_release.py",
    ):
        assert _commands_for(captured, script), script
    skipped = {s["name"]: s for s in _summary(tmp_path)["steps"] if s.get("skipped")}
    assert set(skipped) == set(CONTROLLER_HARDWARE)
    assert _summary(tmp_path)["passed"] is True


def test_controller_asks_for_the_layer_and_fails_without_it(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    _capture_steps(
        run_ci,
        monkeypatch,
        tmp_path,
        ["--hardware", "--controller"],
        controller=False,
        exit_code=1,
    )
    assert set(_summary(tmp_path)["fatal_failures"]) == set(CONTROLLER_HARDWARE)


def _marks(cmd: list[str]) -> str:
    """The marks a pytest command selects: what follows its last ``-m`` (the first is ``-m pytest``)."""
    return cmd[len(cmd) - cmd[::-1].index("-m")]


def test_the_offline_suite_runs_in_two_layers(monkeypatch, tmp_path):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, [])
    tests = [(name, cmd) for name, cmd, _fatal in captured if name.startswith("tests:")]
    (driver_name, driver_cmd), (controller_name, controller_cmd) = tests
    assert driver_name.startswith("tests: the driver, offline")
    assert _marks(driver_cmd) == "not hardware and not controller"
    assert controller_name.startswith("tests: the driver in the ZMART Controller")
    assert _marks(controller_cmd) == "controller and not hardware"
    assert "--cov-append" not in driver_cmd
    if any(part.startswith("--cov=") for part in controller_cmd):
        assert "--cov-append" in controller_cmd  # one coverage report for both layers


def test_without_the_controller_the_offline_suite_skips_its_layer(
    monkeypatch, tmp_path
):
    run_ci = _load_run_ci()
    captured = _capture_steps(run_ci, monkeypatch, tmp_path, [], controller=False)
    assert [name for name, _cmd, _fatal in captured if name.startswith("tests:")] == [
        next(
            name
            for name, _c, _f in captured
            if name.startswith("tests: the driver, offline")
        )
    ]
    assert any(
        s.get("skipped")
        and s["name"].startswith("tests: the driver in the ZMART Controller")
        for s in _summary(tmp_path)["steps"]
    )
