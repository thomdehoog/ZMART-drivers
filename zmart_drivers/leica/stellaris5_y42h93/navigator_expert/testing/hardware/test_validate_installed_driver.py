"""Pytest gate for the installed-driver validator's mock backend.

The validation itself lives in ``validate_installed_driver.py`` so the same
checks run against the LAS X mock, the LAS X simulator, or the microscope.
This file keeps the mock-backed path, registering the driver's folder and
connecting by name through the real controller, in the offline suite.
"""


from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HELPERS = _HERE.parent / "helpers"
_REPO_ROOT = _HERE.parents[5]
for _p in (_HERE, _HELPERS, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import validate_installed_driver

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
    zmart_adapter as adapter,
)


def _run_mock(tmp_path, *extra):
    """Run the validator against the mock, restoring the adapter's seam afterwards."""
    output = tmp_path / "installed_mock.jsonl"
    original_connect = adapter._session.connect_python_client
    try:
        exit_code = validate_installed_driver.main(
            ["--mock", "--output", str(output), "--report-dir", str(tmp_path), *extra]
        )
    finally:
        adapter._session.connect_python_client = original_connect
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return exit_code, records


def test_the_folder_installs_and_connects_by_name(tmp_path):
    exit_code, records = _run_mock(tmp_path, "--allow-move")

    assert exit_code == 0
    by_name = {r["name"]: r["status"] for r in records}
    assert by_name["install: register_driver(folder)"] == "PASS"
    assert by_name["install: get_instruments lists it"] == "PASS"
    assert by_name["contract: every get_* answer fits"] == "PASS"
    assert by_name["connect: by name"] == "PASS"
    assert by_name["limits: a far move is answered as a failure"] == "PASS"
    assert by_name["limits: the stage did not move"] == "PASS"
    assert by_name["set_xyz: x read back"] == "PASS"
    assert by_name["restore: x where it was"] == "PASS"
    assert by_name["phase: acquire"] == "SKIP"
    counts = records[-1]["context"]["counts"]
    assert counts["FAIL"] == 0


def test_the_report_ends_with_the_acceptance_checklist(tmp_path):
    exit_code, _records = _run_mock(tmp_path, "--allow-move")
    assert exit_code == 0
    reports = list(tmp_path.glob("hardware_run_report_*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "Acceptance checklist" in text
    assert "PASS: installed by folder" in text
    assert "PASS: the limits gate refuses before anything moves" in text
    assert "SKIP: the controller accepts an acquisition" in text
