"""Pytest gate for the hardware validator's mock backend.

The canonical validation flow lives in ``validate_hardware.py`` so the
same checks can run against the in-process mock, LAS X simulator, or
real hardware. This pytest file keeps the mock-backed path in the
regular test suite without duplicating the validator logic.
"""
# ruff: noqa: E402,I001

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_HELPERS = _HERE.parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))
_REPO_ROOT = _HERE.parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import validate_hardware
from mock_lasx_api import MockLasxClient, _SET_DISPATCH
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import profiles


def test_classify_result_statuses():
    """Driver result envelopes map to PASS / WARN / FAIL consistently."""
    assert (
        validate_hardware._classify_result({"success": True, "confirmed": True})
        == "PASS"
    )
    assert validate_hardware._classify_result({"success": True}) == "PASS"
    assert (
        validate_hardware._classify_result({"success": True, "confirmed": False})
        == "WARN"
    )
    assert (
        validate_hardware._classify_result({"success": False, "confirmed": True})
        == "FAIL"
    )


def test_compact_status_includes_driver_timing():
    """Human status strings should include message and useful timing fields."""
    status = validate_hardware._compact_status(
        {
            "message": "Zoom -> 5.0",
            "timing": {
                "total_s": 1.23456,
                "attempts": 2,
                "confirm_attempts": 3,
                "method": "async",
            },
        }
    )

    assert status == "Zoom -> 5.0; [total=1.235s, att=2, conf=3, m=async]"


def test_stage_limit_helpers_report_out_of_bounds_points():
    """Movement phases should explain why a point is outside hard limits."""
    limits = {
        "x_min": 1000.0,
        "x_max": 130000.0,
        "y_min": 1000.0,
        "y_max": 100000.0,
        "z_galvo_min": -50.0,
        "z_galvo_max": 50.0,
        "z_wide_min": -200.0,
        "z_wide_max": 200.0,
    }

    assert validate_hardware._xy_limit_error(50000.0, 30000.0, limits) is None
    assert validate_hardware._z_limit_error(0.0, "galvo", limits) is None
    assert "X=0.0 outside calibrated limits" in (
        validate_hardware._xy_limit_error(0.0, 30000.0, limits) or ""
    )
    assert "Z galvo=60.0 outside calibrated limits" in (
        validate_hardware._z_limit_error(60.0, "galvo", limits) or ""
    )


def test_mock_starts_inside_typical_calibrated_envelope():
    """The Python mock should model a sane stage start, not LAS X's 0,0 quirk."""
    mock = MockLasxClient(latency=0.0)

    assert mock._stage_x >= 0.001
    assert mock._stage_y >= 0.001


def test_mock_scan_window_survives_delayed_first_poll():
    """A starved status poller must still observe the scan.

    Regression for the suite-load acquire flake, which needs two guarantees:

    - The mock's scanning window was purely wall-clock (0.1 s) while the
      driver polls at 0.1 s, so under CPU load the whole window could pass
      between the acquire command and the first status poll.
    - confirm_acquire's fail-closed freshness gate discards a reading
      stamped in the same wall-clock tick as the command start -- and that
      discarded first poll still consumes an observation.

    So the mock guarantees TWO observed scanning reads per command-initiated
    scan: even with the first eaten by the tick gate, the next poll (a
    strictly later tick) still sees the scan.
    """
    import time
    from types import SimpleNamespace

    mock = MockLasxClient(latency=0.0)
    mock._handle_acquire_job(SimpleNamespace(JobName="HiRes"))
    time.sleep(0.25)  # well past the 0.1 s wall-clock window
    assert mock.PyApiStatus.Model.ScanStatus == "eScanStarted"  # may be tick-rejected
    assert mock.PyApiStatus.Model.ScanStatus == "eScanStarted"  # the poll that counts
    assert mock.PyApiStatus.Model.ScanStatus == "eScanIdle"  # then idle

    # StartScan gets the same guarantee.
    mock._handle_start_scan(SimpleNamespace(JobName="HiRes"))
    time.sleep(0.25)
    assert mock.PyApiStatus.Model.ScanStatus == "eScanStarted"
    assert mock.PyApiStatus.Model.ScanStatus == "eScanStarted"
    assert mock.PyApiStatus.Model.ScanStatus == "eScanIdle"


def test_validate_hardware_full_mock_run(tmp_path):
    """Run the full reversible validation flow against the Python mock."""
    output = tmp_path / "hardware_mock.jsonl"

    exit_code = validate_hardware.main(
        [
            "--mock",
            "--allow-xy",
            "--allow-z",
            "--allow-objective",
            "--allow-acquire",
            "--output",
            str(output),
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records
    assert records[-1]["name"] == "__summary__"
    assert records[-1]["context"]["counts"]["FAIL"] == 0
    assert records[-1]["context"]["counts"]["WARN"] == 0
    assert records[-1]["context"]["counts"]["PASS"] >= 30

    names = {record["name"] for record in records}
    assert {
        "limits: connect handshake",
        "job selection: select job",
        "settings: read",
        "zoom: write alternate",
        "scan_resonant: write alternate",
        "scan_mode: is xyz",
        "sequential_mode: write alternate",
        "scan_field_rotation: write alternate",
        "frame_accumulation: write alternate",
        "frame_average: write alternate",
        "line_accumulation: write alternate",
        "line_average: write alternate",
        "pinhole_airy: write alternate",
        "xy: move 01",
        "z: move alternate",
        "objective: switch alternate",
        "acquire: job",
    } <= names
    xy_moves = [record for record in records if record["name"].startswith("xy: move ")]
    assert len(xy_moves) == 10
    assert {record["context"]["index"] for record in xy_moves} == set(range(1, 11))

    select_job_records = [
        record for record in records if record["name"] == "job selection: select job"
    ]
    selected_job_records = [
        record
        for record in records
        if record["name"].startswith("job selection: confirmed ")
    ]
    # The autofocus job runs as a procedure, never as state, so it is not selected.
    assert {record["context"]["job"] for record in select_job_records} == {
        "HiRes",
        "Overview",
    }
    assert {
        record["name"].removeprefix("job selection: confirmed ")
        for record in selected_job_records
    } == {"HiRes", "Overview"}

    # The Markdown run report is produced and carries the summary table,
    # the timing overview, and every instrument change incl. restores.
    reports = sorted(tmp_path.glob("hardware_run_report_*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "## Run metadata" in text
    assert "- **Backend**: mock (in-process MockLasxClient" in text
    assert "- **Driver commit**: " in text
    assert "| Phase | Actions attempted | Passed | Warned | Failed | Skipped " in text
    assert "## Timing overview" in text
    assert "## Chronological detail (every attempted action)" in text
    assert "xy: move 01" in text
    assert "xy: restore" in text
    assert "objective: restore" in text
    assert "success+CONFIRMED" in text


def test_state_reader_mode_argument_overrides_profile(tmp_path):
    output = tmp_path / "reader_mode.jsonl"
    prior = profiles.STATE_READERS
    try:
        exit_code = validate_hardware.main(
            [
                "--mock",
                "--read-only",
                "--state-reader-mode",
                "api",
                "--output",
                str(output),
                "--report-dir",
                str(tmp_path),
            ]
        )
        assert exit_code == 0
        assert profiles.STATE_READERS.xy_mode == "api"
        assert profiles.STATE_READERS.job_settings_mode == "api"
        assert profiles.STATE_READERS.selected_job_mode == "api"
    finally:
        profiles.STATE_READERS = prior


def test_the_direct_reader_route_can_be_chosen(caplog):
    """The direct reader is the driver's default route, so the validator runs on it too."""
    prior = profiles.STATE_READERS
    try:
        args = validate_hardware.parse_args(["--state-reader-mode", "direct"])
        validate_hardware._apply_state_reader_mode(
            args.state_reader_mode, validate_hardware.logging.getLogger("t")
        )
        assert profiles.STATE_READERS.xy_mode == "direct"
        assert profiles.STATE_READERS.job_settings_mode == "direct"
        assert profiles.STATE_READERS.scan_status_mode == "direct"
    finally:
        profiles.STATE_READERS = prior


def test_the_direct_route_keeps_the_drivers_own_job_confirmation():
    """Job selection is confirmed by api, log or hybrid; on the direct route the driver's default stays."""
    prior = profiles.STATE_READERS
    try:
        args = validate_hardware.parse_args(["--state-reader-mode", "direct"])
        log = validate_hardware.logging.getLogger("t")
        validate_hardware._apply_state_reader_mode(args.state_reader_mode, log)
        validate_hardware._apply_log_select_confirmation(args, log)
        assert profiles.STATE_READERS.selected_job_confirm_source == "hybrid"
        assert profiles.STATE_READERS.xy_mode == "direct"
    finally:
        profiles.STATE_READERS = prior


def test_the_zoom_test_values_lie_in_the_jobs_own_range():
    """Whatever the job, the alternate zoom is one LAS X accepts (HiRes on the simulator: 7.5 to 48)."""
    for zoom in (
        {"current": 7.5, "min": 7.5, "max": 48.0},
        {"current": 1.0, "min": 0.75, "max": 48.0},
        {"current": 48.0, "min": 7.5, "max": 48.0},
    ):
        candidates = validate_hardware.zoom_candidates(zoom)
        assert candidates
        assert all(zoom["min"] <= c <= zoom["max"] for c in candidates)
        assert all(abs(c - zoom["current"]) > 0.1 for c in candidates)


def test_job_selection_switches_between_normal_jobs_whatever_they_are_called():
    """An autofocus job is run as a procedure, never selected as state: the round trip leaves it out."""
    jobs = [
        {"Name": "Focus A", "IsAutofocus": True},
        {"Name": "Wide", "IsAutofocus": False},
        {"Name": "Detail", "IsAutofocus": False, "IsSelected": True},
    ]
    assert validate_hardware.selectable_job_names(jobs) == ["Wide", "Detail"]


def test_a_capture_is_saved_as_a_file_or_as_a_store(tmp_path):
    """An OME-TIFF plane is a file; an OME-Zarr store, the default format, is a folder."""
    tiff = tmp_path / "A1.ome.tif"
    tiff.write_bytes(b"II*\0 pixels")
    empty = tmp_path / "empty.ome.tif"
    empty.write_bytes(b"")
    store = tmp_path / "A1.ome.zarr"
    store.mkdir()
    (store / "zarr.json").write_text("{}", encoding="utf-8")
    hollow = tmp_path / "hollow.ome.zarr"
    hollow.mkdir()
    assert validate_hardware.capture_saved(tiff) is True
    assert validate_hardware.capture_saved(store) is True
    assert validate_hardware.capture_saved(empty) is False
    assert validate_hardware.capture_saved(hollow) is False
    assert validate_hardware.capture_saved(tmp_path / "missing.ome.tif") is False


def test_a_stores_ome_xml_is_read_from_its_ome_folder(tmp_path):
    store = tmp_path / "A1.ome.zarr"
    (store / "OME").mkdir(parents=True)
    (store / "OME" / "METADATA.ome.xml").write_text(
        "<OME>the capture</OME>", encoding="utf-8"
    )
    assert validate_hardware.embedded_ome_xml(store) == b"<OME>the capture</OME>"


class LaggingJobs:
    """The CAM API's selected job: the old one for the first *lag* reads, then the new one."""

    def __init__(self, old, new, lag):
        self.old, self.new, self.lag, self.reads = old, new, lag, 0

    def get_jobs(self, client, mode=None):
        self.reads += 1
        name = self.old if self.reads <= self.lag else self.new
        return [{"Name": name, "IsSelected": True}]


def test_a_selected_job_the_cam_api_reports_late_still_counts():
    """LAS X switches at once; the CAM API's answer follows within 0.25 s (simulator, 2026-10-15)."""
    drv = LaggingJobs("AF Job", "Overview", lag=2)
    assert (
        validate_hardware.settled_selected_job(
            drv, None, "Overview", window_s=1.0, poll_s=0.01
        )
        == "Overview"
    )


def test_a_selected_job_the_cam_api_never_reports_is_what_it_reports():
    drv = LaggingJobs("AF Job", "Overview", lag=10_000)
    assert (
        validate_hardware.settled_selected_job(
            drv, None, "Overview", window_s=0.05, poll_s=0.01
        )
        == "AF Job"
    )


def test_explicit_log_mode_treats_empty_job_list_as_expected():
    """The job LIST is API-only (no log leg): a log-mode read returning
    nothing is the declared capability, not a driver fault. phase_readonly
    must record it as an expected SKIP and enumerate the list via the API
    leg (selected-job confirmation still exercises the log leg elsewhere)."""

    class DummyDrv:
        @staticmethod
        def ping(_client):
            return True

        @staticmethod
        def get_scan_status(_client):
            return "eScanIdle"

        @staticmethod
        def get_jobs(_client, *, mode=None, **_kwargs):
            # The log profile (mode is None here) has no job-list leg; only the
            # API fallback (mode="api") can enumerate the full list.
            return [{"Name": "HiRes", "IsSelected": True}] if mode == "api" else None

        @staticmethod
        def get_hardware_info(_client):
            return {"ok": True}

        @staticmethod
        def get_xy(_client):
            return {"x_um": 1.0, "y_um": 1.0}

        @staticmethod
        def make_changeable_copy(raw):
            return dict(raw or {})

        @staticmethod
        def get_job_settings(_client, _job, **_kwargs):
            return {}

    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    args = validate_hardware.parse_args(["--state-reader-mode", "log"])

    job = validate_hardware.phase_readonly(DummyDrv, validator, object(), args)

    assert job == "HiRes"  # resolved via the API fallback
    assert validator.exit_code() == 0  # an empty log job list is not a failure
    resolve = next(r for r in records if r.name == "job: resolve")
    assert resolve.status == "SKIP"


def test_selected_job_api_lag_after_log_confirm_is_warn():
    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    command_result = {
        "success": True,
        "confirmed": True,
        "logs": [
            {
                "level": "info",
                "msg": "SelectJob 'HiRes' | confirmed by log leg (0.578s)",
            },
        ],
    }

    validate_hardware._record_selected_job_postcheck(
        validator,
        command_result,
        {"job": "HiRes"},
        "HiRes",
        "AF Job",
    )

    assert validator.exit_code() == 0
    assert len(records) == 1
    record = records[0]
    assert record.status == "WARN"
    assert record.name == "job selection: API lag after log-confirmed HiRes"
    assert record.context["expected"] == "HiRes"
    assert record.context["api_selected"] == "AF Job"
    assert record.context["confirmation_evidence"] == "log"


def test_selected_job_api_mismatch_without_log_confirm_stays_fail():
    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    command_result = {
        "success": True,
        "confirmed": True,
        "logs": [
            {
                "level": "info",
                "msg": "SelectJob 'HiRes' | confirmed by api leg (0.100s)",
            },
        ],
    }

    validate_hardware._record_selected_job_postcheck(
        validator,
        command_result,
        {"job": "HiRes"},
        "HiRes",
        "AF Job",
    )

    assert validator.exit_code() == 1
    assert len(records) == 1
    record = records[0]
    assert record.status == "FAIL"
    assert record.name == "job selection: confirmed HiRes"
    assert record.message == "expected='HiRes' actual='AF Job'"


def test_job_restore_is_skipped_only_after_confirmed_original():
    """The validator should not force a no-op restore after a proved restore."""
    assert not validate_hardware._needs_select_job_restore(
        "Overview",
        "Overview",
        {"success": True, "confirmed": True},
    )
    assert validate_hardware._needs_select_job_restore(
        "Overview",
        "Overview",
        {"success": True, "confirmed": False},
    )
    assert validate_hardware._needs_select_job_restore(
        "Overview",
        "Overview",
        {"success": False, "confirmed": True},
    )
    assert validate_hardware._needs_select_job_restore(
        "Overview",
        "HiRes",
        {"success": True, "confirmed": True},
    )


def test_job_selection_candidate_enumeration_uses_api_when_log_participates():
    """Candidate discovery must not depend on an incomplete log job catalog."""
    assert validate_hardware._job_selection_read_mode("api") is None
    assert validate_hardware._job_selection_read_mode("log") == "api"
    assert validate_hardware._job_selection_read_mode("hybrid") == "api"


def test_mock_set_dispatch_table_matches_surface():
    """The mock's explicit PyApi command table must stay wired."""
    mock = MockLasxClient(latency=0.0)

    for command_name, handler_name in sorted(_SET_DISPATCH.items()):
        assert hasattr(mock, command_name), command_name
        assert callable(getattr(mock, handler_name)), handler_name


_ENVELOPE = {
    "x_min": 1000.0,
    "x_max": 130000.0,
    "y_min": 1000.0,
    "y_max": 100000.0,
    "z_galvo_min": -250.0,
    "z_galvo_max": 250.0,
    "z_wide_min": 0.0,
    "z_wide_max": 8000.0,
}


def test_xy_start_outside_envelope_is_skip_not_fail():
    """A stage parked outside the envelope (the sim homes at 0,0, outside a
    real machine's envelope) is a positioning precondition, not a driver
    fault: phase_xy SKIPs, never FAILs, and never asks the gate to move."""

    class DummyDrv:
        moved = False

        @staticmethod
        def get_xy(_client, **_kwargs):
            return {"x_um": 0.0, "y_um": 0.0}

        @staticmethod
        def get_stage_limits():
            return dict(_ENVELOPE)

        @staticmethod
        def move_xy(*_args, **_kwargs):
            DummyDrv.moved = True
            return {"success": True}

    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    args = validate_hardware.parse_args(["--allow-xy"])

    validate_hardware.phase_xy(DummyDrv, validator, object(), args)

    counts = validator.counts()
    assert counts["FAIL"] == 0 and counts["SKIP"] == 1
    assert validator.exit_code() == 0
    assert not DummyDrv.moved  # the gate is never asked to move from out of bounds
    row = next(r for r in records if r.name == "xy: pattern")
    assert row.status == "SKIP"
    assert "outside the calibrated envelope" in row.message


def test_z_start_outside_envelope_is_skip_not_fail():
    """Z parked outside the envelope is the same precondition class: SKIP,
    not FAIL, and no move attempted."""

    class DummyDrv:
        moved = False

        @staticmethod
        def make_changeable_copy(raw):
            return dict(raw or {})

        @staticmethod
        def get_job_settings(_client, _job, **_kwargs):
            return {"zPosition": {"z-galvo": 500.0}}  # outside [-250, 250]

        @staticmethod
        def get_stage_limits():
            return dict(_ENVELOPE)

        @staticmethod
        def move_z(*_args, **_kwargs):
            DummyDrv.moved = True
            return {"success": True}

    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    args = validate_hardware.parse_args(["--allow-z"])

    validate_hardware.phase_z(DummyDrv, validator, object(), "HiRes", args)

    counts = validator.counts()
    assert counts["FAIL"] == 0 and counts["SKIP"] == 1
    assert not DummyDrv.moved
    row = next(r for r in records if r.name == "z: round-trip")
    assert row.status == "SKIP"


def test_the_readbacks_ask_the_direct_reader_not_the_cam_api(tmp_path, monkeypatch):
    """XY and job-settings readbacks read LAS X as the driver does: directly.

    Only the two deliberate CAM API checks (the job list as a control for the
    log reader, and the independent check of a job switch) still ask the API.
    """
    import zmart_drivers.leica.stellaris5_y42h93.navigator_expert as drv

    asked = []
    for name in ("get_xy", "get_job_settings"):
        real = getattr(drv, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            asked.append((_name, kwargs.get("mode")))
            return _real(*args, **kwargs)

        monkeypatch.setattr(drv, name, spy)

    exit_code = validate_hardware.main(
        [
            "--mock",
            "--allow-xy",
            "--allow-z",
            "--output",
            str(tmp_path / "run.jsonl"),
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert asked
    assert [call for call in asked if call[1] == "api"] == []


def test_the_z_readback_asks_the_direct_reader_not_the_cam_api():
    """The z check reads where z-galvo went from the direct reader.

    On the STELLARIS (15 October 2026), with a z-galvo stack set up in the
    job, the CAM API kept answering the stack's middle (8.64 um) after a
    move to 10.64 um, while the direct reader saw the move at once. The
    driver reads the direct reader by default, so the check reads it too;
    otherwise a correct move is graded as a failure.
    """

    class DummyDrv:
        z = 8.64
        asked: list = []

        @staticmethod
        def make_changeable_copy(raw):
            return dict(raw or {})

        @staticmethod
        def get_job_settings(_client, _job, *, mode=None, **_kwargs):
            DummyDrv.asked.append(mode)
            # The CAM API stays at the stack's middle; the direct reader follows the drive.
            return {"zPosition": {"z-galvo": 8.64 if mode == "api" else DummyDrv.z}}

        @staticmethod
        def get_stage_limits():
            return dict(_ENVELOPE)

        @staticmethod
        def move_z(_client, _job, z_um, **_kwargs):
            DummyDrv.z = z_um
            return {"success": True, "confirmed": True}

    records = []
    validator = validate_hardware.Validator(
        sink=records.append,
        log=validate_hardware._configure_logging("ERROR", jsonl_to_stdout=False),
    )
    args = validate_hardware.parse_args(["--allow-z"])

    validate_hardware.phase_z(DummyDrv, validator, object(), "HiRes", args)

    row = next(r for r in records if r.name == "z: readback")
    assert row.status == "PASS"
    assert "api" not in DummyDrv.asked
