"""The CI runner's arguments, and the folders it resolves from its own place."""

import pytest
import run_ci


def test_the_default_is_the_offline_suite_and_the_modes_exclude_each_other():
    args = run_ci.parse_args([])
    assert not args.hardware and not args.controller and not args.allow_move
    args = run_ci.parse_args(
        ["--hardware", "--allow-move", "--host=10.0.0.5", "--controller"]
    )
    assert (
        args.hardware
        and args.allow_move
        and args.host == "10.0.0.5"
        and args.controller
    )
    with pytest.raises(SystemExit):
        run_ci.parse_args(["--mock", "--hardware"])


def test_the_paths_are_resolved_from_the_file_not_the_working_directory():
    assert (
        run_ci.DRIVER_ROOT.name == "rdk"
        and (run_ci.REPO_ROOT / "pyproject.toml").is_file()
    )
    env = run_ci.build_env()
    assert str(run_ci.REPO_ROOT) in env["PYTHONPATH"] and env["PYTHONSAFEPATH"] == "1"
