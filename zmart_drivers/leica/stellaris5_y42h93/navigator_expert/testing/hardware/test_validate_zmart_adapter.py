"""Pytest gate for the ZMART adapter validator's mock backend.

The canonical validation flow lives in ``validate_zmart_adapter.py`` so the
same checks run against the in-process mock, the LAS X simulator, or a real
scope. This file keeps the mock-backed path (driving the adapter through a real
``zmart_controller`` Session) in the regular offline suite without duplicating
the validator logic.
"""
# ruff: noqa: E402,I001

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

_HERE = Path(__file__).resolve().parent
_HELPERS = _HERE.parent / "helpers"
_REPO_ROOT = _HERE.parents[5]
for _p in (_HERE, _HELPERS, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import validate_zmart_adapter
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection import (
    lasx_client as adapter,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup import (
    store as _machine,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.save_data import (
    save as _save,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.acquisition import (
    run_job as _capture,
)


def _run_mock(tmp_path, *extra):
    """Run the validator against the mock, restoring global state afterwards."""
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import (
        profiles,
    )

    output = tmp_path / "adapter_mock.jsonl"
    original_connect = adapter.connect_python_client
    original_profile = profiles.STATE_READERS
    try:
        exit_code = validate_zmart_adapter.main(
            ["--mock", "--output", str(output), "--report-dir", str(tmp_path), *extra]
        )
    finally:
        adapter.connect_python_client = original_connect
        profiles.STATE_READERS = original_profile
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return exit_code, records


def test_within_envelope_helper():
    assert validate_zmart_adapter._within(5.0, 0.0, 10.0)
    assert validate_zmart_adapter._within(0.0, 0.0, 10.0)
    assert validate_zmart_adapter._within(10.0, 0.0, 10.0)
    assert not validate_zmart_adapter._within(-0.1, 0.0, 10.0)
    assert not validate_zmart_adapter._within(10.1, 0.0, 10.0)


def test_hardware_ci_default_is_the_four_measured_coordinates():
    assert validate_zmart_adapter.CI_DEFAULT_POSITION_UM == {
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
    assert validate_zmart_adapter._ci_default_position_error(limits) is None


def test_hardware_ci_rejects_a_negative_zwide_excursion():
    with pytest.raises(SystemExit):
        validate_zmart_adapter.parse_args(["--z-wide-delta-um", "-0.1"])


def test_hardware_ci_enforces_baseline_without_negative_zwide_target(tmp_path):
    exit_code, records = _run_mock(
        tmp_path,
        "--enforce-ci-default-position",
        "--allow-move",
    )
    assert exit_code == 0
    by_name = {record["name"]: record for record in records}
    assert by_name["ci default: X readback"]["status"] == "PASS"
    assert by_name["ci default: Y readback"]["status"] == "PASS"
    assert by_name["ci default: Z-wide readback"]["status"] == "PASS"
    assert by_name["ci default: Z-galvo readback"]["status"] == "PASS"
    zwide_move = by_name["ci default: move Z-wide"]
    assert zwide_move["context"]["target_z_wide_um"] == 0.0
    assert zwide_move["context"]["target_z_wide_um"] >= 0.0


def test_readonly_mock_run(tmp_path):
    exit_code, records = _run_mock(tmp_path, "--read-only")
    assert exit_code == 0
    assert records[-1]["name"] == "__summary__"
    counts = records[-1]["context"]["counts"]
    assert counts["FAIL"] == 0
    assert counts["WARN"] == 0
    names = {r["name"] for r in records}
    assert "get_xyz: x has position, unit, actuator, actuators and canvas" in names
    assert "get_xyz: actuators name every motor" in names
    assert "get_state: objective has a name" in names


def test_full_mock_run_move_and_acquire(tmp_path):
    """Read-only + move (both z drives) + state pass; acquire SKIPs under the mock."""
    exit_code, records = _run_mock(
        tmp_path, "--allow-move", "--allow-state", "--allow-acquire"
    )
    assert exit_code == 0

    summary = records[-1]
    assert summary["name"] == "__summary__"
    counts = summary["context"]["counts"]
    assert counts["FAIL"] == 0
    assert counts["WARN"] == 0
    assert counts["PASS"] >= 30

    by_name = {r["name"]: r for r in records}
    # The controller round-trip, z-focus model, and state round-trip are exercised.
    assert {
        "get_info: says where it saves",
        "set_origin",
        "xy: frame x",
        "zwide: frame z",
        "zwide: z-galvo drive parked at 0",
        "state: switched",
        "state: restored",
    } <= set(by_name)
    assert by_name["state: restored"]["status"] == "PASS"
    # set_origin is the driver's own setup step, called on the adapter with the
    # session's driver handle. The validator then removes the origin record it
    # saved, so the (hermetic) microscope configuration has no origin left over.
    assert by_name["set_origin"]["status"] == "PASS"
    assert by_name["origin: frame x -> 0"]["status"] == "PASS"
    assert by_name["origin: remove the validator's origin record"]["status"] == "PASS"
    assert _machine.MACHINE.latest_snapshot("origin") is None
    assert by_name["zwide: frame z"]["status"] == "PASS"
    assert by_name["zwide: z-galvo drive parked at 0"]["status"] == "PASS"
    assert by_name["get_info: reports no template positions"]["status"] == "PASS"
    # Acquire needs real LAS X export files, so it is skipped under the mock.
    assert by_name["phase: acquire"]["status"] == "SKIP"

    # The Markdown run report is produced with the summary table and lists the
    # controller-driven moves (incl. restores) as instrument changes.
    reports = sorted(tmp_path.glob("hardware_run_report_*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "| Phase | Actions attempted | Passed | Warned | Failed | Skipped " in text
    assert "## Chronological detail (every attempted action)" in text
    assert "set_xyz: XY move" in text
    assert "move: restore XY + focus (frame 0,0,0)" in text
    assert "set_state: restore" in text


def test_state_phase_does_not_switch_away_from_autofocus_job():
    captured = {
        "changeable": {"job": "AF Job"},
        "observed": {
            "jobs": [{"Name": "Overview"}, {"Name": "HiRes"}],
            "autofocus_jobs": [{"Name": "AF Job"}],
        },
    }
    session = SimpleNamespace(
        get_state=Mock(return_value=captured),
        set_state=Mock(side_effect=AssertionError("must not switch an autofocus job")),
    )
    validator = SimpleNamespace(
        phase=Mock(return_value=nullcontext()),
        callable=Mock(side_effect=lambda _name, run, **_kwargs: run()),
        compare=Mock(),
        skip=Mock(),
    )

    validate_zmart_adapter.phase_state(validator, session)

    session.set_state.assert_not_called()
    validator.compare.assert_not_called()
    validator.skip.assert_called_once_with(
        "state: switch",
        "current job 'AF Job' is autofocus-only and cannot be restored via set_state",
    )


def test_connect_session_leaves_output_root_for_driver_discovery():
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import (
        profiles,
    )

    original_connect = adapter.connect_python_client
    original_profile = profiles.STATE_READERS
    args = argparse.Namespace(
        mock=True, mock_latency=0.0, client_name="PythonClient", api_delay_ms=None
    )
    session = None
    try:
        session = validate_zmart_adapter._connect_session(args, adapter, None)
        assert session._handle.microscope.handle.connection.get("output_root") is None
    finally:
        adapter.connect_python_client = original_connect
        profiles.STATE_READERS = original_profile
        if session is not None:
            session.disconnect()


def test_acquire_through_the_controller_seam(tmp_path):
    """An acquisition crosses the real Session seam and reaches the capture.

    Unlike the adapter-direct unit tests (which patch driver internals and pass
    ``object()`` as the client), this opens a real ``zmart_controller.ZmartController``
    against the in-process mock CAM (same connect path ``phase_acquire`` would
    use live) and only patches the I/O boundary (``_capture``/``_save``) that a
    mock CAM cannot satisfy -- so the seam decision #3 cares about (Session ->
    ops table -> adapter) is what is actually exercised.
    """
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import (
        profiles,
    )

    original_connect = adapter.connect_python_client
    original_profile = profiles.STATE_READERS
    args = argparse.Namespace(
        mock=True, mock_latency=0.0, client_name="PythonClient", api_delay_ms=None
    )
    order = []
    session = None
    try:
        session = validate_zmart_adapter._connect_session(args, adapter, str(tmp_path))
        active_job = session.get_state()["changeable"]["job"]
        session.get_xyz()  # where the stage stands, which every saved plane is placed from

        def fake_capture(client, job, **kwargs):
            order.append(("capture", job))
            return SimpleNamespace(job=job, started_at=None, finished_at=None)

        def fake_save(client, acq, output_root, naming, **kwargs):
            return SimpleNamespace(image_paths={}, xml_paths={}, naming=naming)

        with (
            patch.object(_capture, "acquire", fake_capture),
            patch.object(_save, "save", fake_save),
        ):
            record = session.acquire(position_label="1")
    finally:
        adapter.connect_python_client = original_connect
        profiles.STATE_READERS = original_profile
        if session is not None:
            session.disconnect()

    assert order == [("capture", active_job)]
