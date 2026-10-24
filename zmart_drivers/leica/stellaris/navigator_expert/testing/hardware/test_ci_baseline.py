"""Pytest gate for the CI baseline, which moves the stage through the driver alone.

``ci_baseline.py`` is the first hardware step, and it must work on a computer
without the ZMART Controller. Here it runs against the stand-in LAS X.
"""

import json
import subprocess
import sys
from pathlib import Path

import ci_baseline

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import client


def test_hardware_ci_default_is_the_four_measured_coordinates():
    assert ci_baseline.CI_DEFAULT_POSITION_UM == {
        "x_um": 63_500.0,
        "y_um": 41_500.0,
        "z_wide_um": 0.0,
        "z_galvo_um": 0.0,
    }
    limits = {
        "x_min": 1_000.0,
        "x_max": 130_000.0,
        "y_min": 1_000.0,
        "y_max": 100_000.0,
        "z_wide_min": 0.0,
        "z_wide_max": 8_000.0,
        "z_galvo_min": -250.0,
        "z_galvo_max": 250.0,
    }
    assert ci_baseline._ci_default_position_error(limits) is None


def test_a_target_outside_the_limits_is_named_before_anything_moves():
    limits = {
        "x_min": 1_000.0,
        "x_max": 50_000.0,
        "y_min": 1_000.0,
        "y_max": 100_000.0,
        "z_wide_min": 0.0,
        "z_wide_max": 8_000.0,
        "z_galvo_min": -250.0,
        "z_galvo_max": 250.0,
    }
    assert "x target 63500.0 um outside" in ci_baseline._ci_default_position_error(
        limits
    )


def test_the_stage_reaches_the_baseline_on_the_stand_in(monkeypatch, tmp_path):
    # --mock points the driver's LAS X connection at the stand-in; put it back after the test.
    monkeypatch.setattr(client, "connect_python_client", client.connect_python_client)
    records = tmp_path / "baseline.jsonl"
    code = ci_baseline.main(
        ["--mock", f"--output={records}", f"--report-dir={tmp_path}"]
    )
    lines = [
        json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()
    ]
    failed = [r for r in lines if r.get("status") == "FAIL"]
    assert code == 0, failed
    readbacks = {r["name"] for r in lines if r["name"].endswith("readback")}
    assert {"ci default: X readback", "ci default: Z-wide readback"} <= readbacks


def test_it_runs_where_the_controller_is_not_installed(tmp_path):
    # Any import of the controller fails in the child, as on a computer without it.
    script = (
        "import sys, runpy; sys.modules['zmart_controller'] = None; "
        f"sys.argv = ['ci_baseline.py', '--mock', '--report-dir={tmp_path.as_posix()}']; "
        f"runpy.run_path({str(Path(ci_baseline.__file__))!r}, run_name='__main__')"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
