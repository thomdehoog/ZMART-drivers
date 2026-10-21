"""Pytest gate for the installed-driver validator's mock backend.

The validation itself lives in ``validate_installed_driver.py`` so the same
checks run against the LAS X mock, the LAS X simulator, or the microscope.
This file keeps the mock-backed path, registering the driver's folder and
connecting by name through the real controller, in the offline suite.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_HELPERS = _HERE.parent / "helpers"
_REPO_ROOT = _HERE.parents[5]
for _p in (_HERE, _HELPERS, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import validate_installed_driver

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.connection import (
    lasx_client as adapter,
)


def _run_mock(tmp_path, *extra):
    """Run the validator against the mock, restoring the adapter's seam afterwards."""
    output = tmp_path / "installed_mock.jsonl"
    original_connect = adapter.connect_python_client
    try:
        exit_code = validate_installed_driver.main(
            ["--mock", "--output", str(output), "--report-dir", str(tmp_path), *extra]
        )
    finally:
        adapter.connect_python_client = original_connect
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


class RecordingValidator:
    """Stands in for the validator: runs each call and keeps every comparison."""

    def __init__(self):
        self.compared = {}

    @contextlib.contextmanager
    def phase(self, name):
        yield

    def callable(self, name, fn, mutating=False):
        return fn()

    def compare(self, name, actual, expected):
        self.compared[name] = actual


def test_a_capture_that_failed_is_reported_not_a_crash():
    """A failed capture answers with words, not a record: the checks say it failed, nothing breaks."""
    failed = {
        "success": False,
        "output": "RuntimeError: this microscope does not know where to save its data",
    }
    session = SimpleNamespace(_session=SimpleNamespace(acquire=lambda **kwargs: failed))
    v = RecordingValidator()

    validate_installed_driver.phase_acquire(v, session, SimpleNamespace(mock=False))

    assert v.compared["acquire: the controller accepts the answer"] != []
    assert v.compared["acquire: at least one file"] is False
