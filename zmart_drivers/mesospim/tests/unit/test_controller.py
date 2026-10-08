"""The ZMART controller contract, driven end to end against the mock server."""

from __future__ import annotations

import pytest

from zmart_drivers.mesospim import zmart_controller_plugin as driver
from zmart_drivers.mesospim import mesospim_zmart_adapter as adapter
from zmart_drivers.mesospim.limits import checks as limits


@pytest.fixture(autouse=True)
def _no_limits():
    # Controller moves should not be blocked by leaked module-level limits.
    limits.clear_stage_limits()
    yield
    limits.clear_stage_limits()


@pytest.fixture
def session(server, tmp_path):
    """A connected controller Session wired to the mock command server."""
    import zmart_controller

    connection = {
        "microscope": "mesospim-test",
        "host": server.host,
        "port": server.port,
        "output_root": str(tmp_path / "run"),
        # Hermetic machine dir: origin persistence and limits resolution must
        # never touch the real ProgramData root from a test.
        "machine_root": str(tmp_path / "machine"),
    }
    sess = zmart_controller.ZmartController(driver, connection)
    try:
        yield sess
    finally:
        sess.disconnect()


def test_context_names_the_driver(session):
    assert session.context == {"driver": "zmart_drivers.mesospim.zmart_controller_plugin"}


def test_actuators_and_origin(session):
    assert session.get_actuators()["content"] == {
        "x": ["motoric"],
        "y": ["motoric"],
        "z": ["motoric"],
    }
    # The origin is driver setup, so it is set on the driver handle directly.
    out = adapter.set_origin(session._handle)
    assert "origin" in out


def test_controller_session_does_not_offer_set_origin(session):
    # Setting the origin is a one-time driver setup step, not a controller command.
    assert not hasattr(session, "set_origin")
    assert "set_origin" not in adapter.ops_table()


def test_set_and_get_xyz_relative_to_origin(session):
    session.set_xyz(10, 20, 5)
    adapter.set_origin(session._handle)  # current position becomes (0,0,0)
    pos = session.get_xyz()["content"]
    assert pos["x"]["position"] == 0.0
    # The motor's own reading is the stage's number, untouched by the origin.
    assert pos["x"]["actuators"] == {"motoric": 10.0}
    session.set_xyz(3, 0, 0)
    assert session.get_xyz()["content"]["x"]["position"] == 3.0


def test_set_xyz_answers_like_get_xyz(session):
    """A move answers the position read back from the stage, in get_xyz's shape."""
    moved = session.set_xyz(10, 20, 5)["content"]
    assert moved == session.get_xyz()["content"]
    for axis, expected in (("x", 10.0), ("y", 20.0), ("z", 5.0)):
        assert list(moved[axis]) == ["position", "unit", "actuators", "canvas"]
        assert moved[axis]["position"] == expected
        assert moved[axis]["unit"] == "micrometer"
        assert moved[axis]["actuators"] == {"motoric": expected}  # origin is still 0 here
    # The old move record is gone: nothing but the three axes.
    assert set(moved) == {"x", "y", "z"}


def test_state_capture_and_reapply(session):
    state = session.get_state()["content"]
    assert list(state) == ["changeable", "observed"]  # changeable first
    state["changeable"]["intensity"] = 77.0
    session.set_state(state)
    assert session.get_state()["content"]["changeable"]["intensity"] == 77.0


def test_observed_is_a_report_never_an_instruction(session):
    # A mismatching observed part does not block applying the changeable part
    # (operator decision: set_state acts on changeable only).
    state = session.get_state()["content"]
    assert state["observed"]["microscope"] == "mesospim-test"
    state["observed"]["host"] = "10.0.0.99"
    state["changeable"]["intensity"] = 55.0
    session.set_state(state)
    assert session.get_state()["content"]["changeable"]["intensity"] == 55.0


def test_acquire_stack_z_bounds_use_origin(session, monkeypatch):
    # z_start/z_end are given in the user frame; with a non-zero origin they must
    # be mapped to raw stage coordinates before the capture.
    import zmart_drivers.mesospim.mesospim_zmart_adapter as ctl

    captured = {}
    real = ctl._acq.acquire

    def spy(client, label, *, options=None, state=None):
        captured["options"] = dict(options or {})
        return real(client, label, options=options, state=state)

    monkeypatch.setattr(ctl._acq, "acquire", spy)

    session.set_xyz(0, 0, 100)
    adapter.set_origin(session._handle)  # raw z=100 now reads as user z=0
    session.acquire(position_label="C3", acquisition_settings={"z_start": 0, "z_end": 4, "z_step": 1})

    assert captured["options"]["z_start"] == 100.0  # 0 (user) + 100 (origin)
    assert captured["options"]["z_end"] == 104.0
    assert captured["options"]["z_step"] == 1  # a delta, unchanged


def test_acquisition_settings(session):
    opts = session.get_acquisition_settings()["content"]
    assert "format" in opts and "backlash_correction" in opts


def test_acquire_captures_and_saves(session, tmp_path):
    record = session.acquire(position_label="A1", acquisition_settings={"format": "ome-tiff"})["content"]
    assert record["position_label"] == "A1"
    assert len(record["planes"]) == 1
    assert "image_files" not in record
    from pathlib import Path

    # Every file saved: the image, then the metadata written beside it.
    assert record["files"] == [*record["files"][:-1], record["metadata_file"]]
    assert all(Path(path).exists() for path in record["files"])


def test_acquire_stack(session):
    record = session.acquire(position_label="B2", acquisition_settings={"z_start": 0, "z_end": 4, "z_step": 1})[
        "content"
    ]
    assert len(record["planes"]) == 5
    # Every plane sits in the one stack file, one micrometre above the last.
    assert [plane["z_um"] for plane in record["planes"]] == pytest.approx([0, 1, 2, 3, 4])
    assert {plane["path"] for plane in record["planes"]} == {record["files"][0]}
    # A 5-plane stack is one multi-page file (matches the real Tiff writer).
    assert len(record["files"]) == 2  # the stack and its metadata


def test_acquire_cleans_staging_and_does_not_duplicate(session, tmp_path):
    record = session.acquire(position_label="A1")["content"]
    from pathlib import Path

    out = Path(record["files"][0])
    assert out.exists() and out.parent.name == "data"
    # staging is transient: the writer's originals are removed after relocation.
    staging = out.parent.parent / "_staging"
    assert not staging.exists() or not any(staging.rglob("*.tiff"))


def test_a_folder_setting_groups_the_files(session):
    from pathlib import Path

    record = session.acquire(position_label="A1", acquisition_settings={"folder": "prescan"})["content"]
    assert Path(record["files"][0]).parent.name == "prescan"
    assert Path(record["files"][0]).parent.parent.name == "data"


def test_repeated_same_label_acquire_does_not_overwrite(session):
    r1 = session.acquire(position_label="A1")["content"]
    r2 = session.acquire(position_label="A1")["content"]
    # Same label twice must yield two distinct saved datasets, not a clobber.
    assert r1["files"][0] != r2["files"][0]
    from pathlib import Path

    assert Path(r1["files"][0]).exists() and Path(r2["files"][0]).exists()


def test_acquire_stack_z_out_of_limits_raises(session):
    from zmart_drivers.mesospim.limits import checks as limits

    limits.set_stage_limits(z=(0, 100))  # tight envelope for this test
    # The controller turns the driver's RuntimeError into a failed answer.
    answer = session.acquire(position_label="Z9", acquisition_settings={"z_start": 0, "z_end": 500, "z_step": 1})
    assert answer["success"] is False
    assert "RuntimeError" in answer["content"] and "stage limits" in answer["content"]


def test_procedures(session):
    from zmart_drivers.mesospim import MesospimError

    procs = session.get_procedures()["content"]
    assert "autofocus" in procs and "move_focus" in procs
    assert (
        session.run_procedure({"name": "move_focus", "value": 12.0})["content"]["ran"]
        == "move_focus"
    )
    # autofocus/find_sample are advertised but the resident server NAKs them today
    # (TODO §5), so forwarding raises rather than silently "succeeding". Through
    # the controller, a raised error becomes a failed answer naming the error.
    answer = session.run_procedure({"name": "autofocus"})
    assert answer["success"] is False and MesospimError.__name__ in answer["content"]
    answer = session.run_procedure({"name": "nope"})
    assert answer["success"] is False and "ValueError" in answer["content"]


def test_info(session):
    info = session.get_info()["content"]
    assert "initial_positions" in info
    assert "output_root" in info


# =============================================================================
# machine-local config: function limits, persisted origin, machine envelope
# =============================================================================


def _connection(server, tmp_path):
    return {
        "microscope": "mesospim-test",
        "host": server.host,
        "port": server.port,
        "output_root": str(tmp_path / "run"),
        "machine_root": str(tmp_path / "machine"),
    }


def test_bundled_function_limits_cover_every_mutating_op():
    """THE completeness guard: adding a mutating op without a limits entry fails here."""
    from zmart_drivers.mesospim import mesospim_zmart_adapter as controller
    from zmart_drivers.mesospim.calibration import machine
    from zmart_drivers.mesospim.limits import function_limits as shared_limits

    path = machine._bundled_default(machine.FUNCTION_LIMITS_FILENAME)
    loaded = shared_limits.load(path, functions=controller._MUTATING_OPS)
    assert loaded.source == "defaults"


def test_origin_set_with_driver_is_loaded_by_controller_session(server, tmp_path):
    """The origin is set once with the driver, saved, and used by later controller sessions."""
    import zmart_controller

    connection = _connection(server, tmp_path)

    # Setup step, done with the driver directly (not through the controller).
    handle = adapter.connect(connection)
    try:
        adapter.set_xyz(handle, 100, 200, 50)  # move somewhere first (origin still 0)
        out = adapter.set_origin(handle)
        assert out["origin_file"]  # saved to the machine configuration folder
        assert adapter.get_xyz(handle)["x"]["position"] == 0.0
    finally:
        adapter.disconnect(handle)

    session = zmart_controller.ZmartController(driver, connection)
    try:
        # The saved origin is loaded at connect, so the same spot reads (0, 0, 0),
        # while the motors still report the stage's own numbers.
        pos = session.get_xyz()["content"]
        assert tuple(pos[axis]["position"] for axis in "xyz") == (0.0, 0.0, 0.0)
        assert tuple(pos[axis]["actuators"]["motoric"] for axis in "xyz") == (100.0, 200.0, 50.0)
    finally:
        session.disconnect()


def test_machine_stage_envelope_overrides_bundled(server, tmp_path):
    """A machine copy of stage_limits.json governs moves, not the bundled default."""
    import json

    import zmart_controller

    connection = _connection(server, tmp_path)
    machine_dir = tmp_path / "machine" / "mesospim" / "mesospim-test"
    machine_dir.mkdir(parents=True)
    (machine_dir / "stage_limits.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "defaults",
                "axes": {
                    "x": [0.0, 500.0],  # tighter than the bundled 25000
                    "y": [0.0, 25000.0],
                    "z": [0.0, 25000.0],
                    "f": [0.0, 25000.0],
                    "theta": [-360.0, 360.0],
                },
            }
        ),
        encoding="utf-8",
    )
    sess = zmart_controller.ZmartController(driver, connection)
    try:
        assert sess.set_xyz(400, 0, 0)["success"] is True  # inside the machine envelope
        refused = sess.set_xyz(600, 0, 0)  # inside bundled, outside the machine copy
        assert refused["success"] is False and "stage.x" in refused["content"]
    finally:
        sess.disconnect()


def test_focus_and_rotation_procedures_are_limit_gated(session):
    refused = session.run_procedure({"name": "move_focus", "value": 99999.0})
    assert refused["success"] is False and "stage.f" in refused["content"]
    refused = session.run_procedure({"name": "move_rotation", "value": 720.0})
    assert refused["success"] is False and "stage.theta" in refused["content"]
    # In-bounds still runs.
    assert (
        session.run_procedure({"name": "move_rotation", "value": 15.0})["content"]["ran"]
        == "move_rotation"
    )


def test_mutating_ops_refuse_without_function_limits(session):
    """Fail-closed: no loaded limits means no mutations — reads still work."""
    session._handle.function_limits = None
    # Called on the driver directly, the refusal is raised ...
    with pytest.raises(RuntimeError, match="function limits are not configured"):
        adapter.set_origin(session._handle)
    # ... and through the controller it is a failed answer carrying the same words.
    for call in (
        lambda: session.set_xyz(1, 1, 1),
        lambda: session.set_state({"changeable": {}}),
        lambda: session.run_procedure({"name": "zero_stage"}),
    ):
        answer = call()
        assert answer["success"] is False
        assert "function limits are not configured" in answer["content"]
    assert "move_focus" in session.get_procedures()["content"]  # read-only unaffected


def test_observed_reports_limits_provenance(session):
    observed = session.get_state()["content"]["observed"]
    assert observed["limits"]["schema_version"] == 1
    assert observed["limits"]["is_fallback"] is True  # no machine copy in this fixture
