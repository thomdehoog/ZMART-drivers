"""The items the operator sets up once: limits, origin, orientation, objective calibration, data location.

Each is measured on the stand-in the way its notebook measures it, saved,
and loaded again at the next connection.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from mock_zen_api import FakeScope
from setup_fixtures import connected, provision

from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher import limits as gate
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup import data_location
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits import (
    config,
    limit_rules,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits.measure_limits import (
    CornerRecorder,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.optical_calibration import (
    model,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.optical_calibration.calibrate_objective_pair import (
    PairCalibration,
    adopt_calibration,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.orientation import (
    Orientation,
    orientation_from_config,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.orientation import (
    measure_orientation as orientation_step,
)

DRIVER_ROOT = Path(__file__).resolve().parents[2]


# --- limits ------------------------------------------------------------------------------


def test_the_bundled_limits_are_valid_and_within_the_backstop():
    loaded = config.load()

    limit_rules.check_envelope_within_backstop(loaded["stage_um"])
    assert set(loaded["policy"]) == set(config.REQUIRED_FILE_KEYS)


@pytest.mark.parametrize(
    "change, says",
    [
        ({"x_um": {"range": [10, 1]}}, "min > max"),
        ({"z_um": {"allowed": [1]}}, "must use 'range'"),
        ({"surprise": []}, "unknown limits entries"),
        ({"objective_position": {"allowed": [0]}}, "from 1"),
    ],
)
def test_a_malformed_limits_file_is_refused_with_what_is_wrong(change, says):
    payload = config.build_limits_payload({"x": [0, 1], "y": [0, 1], "z": [0, 1]})
    payload.update(change)

    with pytest.raises(ValueError, match=says):
        config.validate_payload(payload)


def test_every_write_the_driver_builds_has_a_rule():
    """A write whose command the rulebook does not know would reach ZEN unchecked; the gate would raise instead."""
    source = (DRIVER_ROOT / "primitives" / "gateway" / "writes.py").read_text(
        encoding="utf-8"
    )
    commands = {
        keyword.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Write"
        for keyword in node.keywords
        if keyword.arg == "command"
    }

    assert commands and commands <= set(limit_rules.MUTATING_COMMANDS)


def test_a_damaged_limits_file_falls_back_to_the_defaults_and_says_so(
    monkeypatch, tmp_path
):
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.store import (
        MACHINE,
    )

    provision()
    published = MACHINE.limits_path()
    published.write_text("{not json", encoding="utf-8")
    microscope, _ = connected(monkeypatch, tmp_path)
    try:
        assert gate.describe(microscope.client)["is_fallback"] is True
    finally:
        microscope.close()


def test_the_limits_are_measured_at_four_corners_and_two_focus_ends(
    monkeypatch, tmp_path
):
    microscope, fake = connected(monkeypatch, tmp_path)
    corners = CornerRecorder(microscope.client)
    try:
        for number, (x, y) in enumerate(
            [(1000, 2000), (90000, 2000), (90000, 70000), (1000, 70000)], start=1
        ):
            fake.x_um, fake.y_um = x, y
            corners.record(number)
        fake.z_um = 100.0
        corners.record_focus("low")
        fake.z_um = 9000.0
        corners.record_focus("high")
        measured = corners.limits()["limits"]
    finally:
        microscope.close()

    assert measured == {
        "x_um": {"range": [1000.0, 90000.0]},
        "y_um": {"range": [2000.0, 70000.0]},
        "z_um": {"range": [100.0, 9000.0]},
    }
    published = config.adopt_limits(
        {
            **config.build_limits_payload({"x": [0, 1], "y": [0, 1], "z": [0, 1]}),
            **measured,
        }
    )
    assert json.loads(Path(published["limits_path"]).read_text(encoding="utf-8"))[
        "x_um"
    ] == {"range": [1000.0, 90000.0]}


# --- origin and data location --------------------------------------------------------------


def test_the_origin_is_saved_and_loaded_at_the_next_connection(monkeypatch, tmp_path):
    provision()
    first, fake = connected(monkeypatch, tmp_path)
    first.set_origin()
    first.close()

    fake.x_um += 100.0
    second, _ = connected(monkeypatch, tmp_path, scope=fake)
    try:
        assert second.position()["x"]["position"] == pytest.approx(100.0)
        assert second.handle.origin["objective"]["position"] == 1
    finally:
        second.close()


def test_a_data_location_on_a_network_share_is_refused(tmp_path):
    assert data_location.check(r"\\server\share") != []
    assert data_location.check(tmp_path) == []
    data_location.set_data_location(tmp_path)
    assert data_location.data_location() == tmp_path.resolve()


# --- orientation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "camera",
    [((1, 0), (0, 1)), ((0, -1), (1, 0)), ((-1, 0), (0, -1)), ((0, 1), (1, 0))],
)
def test_the_orientation_and_the_pixel_size_are_measured_from_three_snaps(
    monkeypatch, tmp_path, camera
):
    pytest.importorskip("pylibCZIrw")
    provision(orientation=False)
    microscope, fake = connected(
        monkeypatch, tmp_path, scope=FakeScope(camera_image_to_stage=camera)
    )
    try:
        start = (fake.x_um, fake.y_um)
        measured = orientation_step.measure_orientation(microscope.handle)
        written_left = list(Path(fake.image_output_folder).glob("zmart_orientation_*"))
    finally:
        microscope.close()

    assert measured["orientation"].image_to_stage == camera
    assert measured["frame"]["pixel_size_um"] == pytest.approx([0.65, 0.65], rel=0.02)
    assert measured["frame"]["image_size_px"] == [128, 96]
    assert (fake.x_um, fake.y_um) == start
    assert written_left == []


def test_a_measured_orientation_is_loaded_with_its_frame_at_the_next_connection(
    monkeypatch, tmp_path
):
    pytest.importorskip("pylibCZIrw")
    provision(orientation=False)
    microscope, fake = connected(
        monkeypatch, tmp_path, scope=FakeScope(camera_image_to_stage=((0, -1), (1, 0)))
    )
    orientation_step.adopt_orientation(
        orientation_step.measure_orientation(microscope.handle)
    )
    microscope.close()

    again, _ = connected(monkeypatch, tmp_path, scope=fake)
    try:
        assert again.handle.orientation == Orientation(rotate_deg=90)
        assert again.state()["observed"]["image_size_px"] == [96, 128]
    finally:
        again.close()


def test_a_blank_field_gives_no_orientation_and_says_why():
    with pytest.raises(RuntimeError, match="did not shift"):
        orientation_step.orientation_from_shifts(15.0, (0.0, 0.0), (0.0, 0.0))


def test_an_orientation_document_that_contradicts_itself_is_refused():
    with pytest.raises(ValueError):
        orientation_from_config(
            {
                "schema_version": 1,
                "measured": True,
                "rotation_deg": 45,
                "reflection": False,
                "frame": None,
            }
        )


# --- objective calibration ------------------------------------------------------------------------


def test_an_objective_pair_is_measured_by_the_operators_eye_and_saved(
    monkeypatch, tmp_path
):
    provision(calibration=False)
    microscope, fake = connected(monkeypatch, tmp_path)
    try:
        pair = PairCalibration(microscope.handle)
        pair.record_reference()
        pair.change_to(2)
        # The operator centres and focuses the same feature under the 20x.
        fake.x_um += 4.0
        fake.y_um -= 2.0
        fake.z_um += 3.0
        pair.record_other()
        saved = adopt_calibration(pair.result())
    finally:
        microscope.close()

    assert saved["calibration"]["objectives"]["1"]["translation_um"] == [0.0, 0.0, 0.0]
    assert saved["calibration"]["objectives"]["2"]["translation_um"] == pytest.approx(
        [4.0, -2.0, 3.0]
    )
    translations, info = model.load_objective_calibration()
    assert info["measured_positions"] == [1, 2]
    assert model.translation_delta_um(translations, 2, 1) == pytest.approx(
        (-4.0, 2.0, -3.0)
    )


def test_a_pair_against_another_reference_is_refused(monkeypatch, tmp_path):
    provision()
    with pytest.raises(ValueError, match="reference"):
        adopt_calibration(
            {
                "reference": {"position": 2},
                "other": {"position": 3},
                "translation_um": [1, 1, 1],
            }
        )


def test_an_uncovered_pair_is_never_taken_as_zero():
    with pytest.raises(RuntimeError, match="no calibration covers"):
        model.translation_delta_um({1: (0, 0, 0)}, 1, 2)


# --- the notebooks ---------------------------------------------------------------------------


def test_every_notebook_imports_this_driver_and_carries_no_outputs():
    notebooks = sorted((DRIVER_ROOT / "driver_setup").rglob("*.ipynb"))

    assert {p.stem for p in notebooks} == {
        "set_limits",
        "set_orientation",
        "calibrate_objective_pair",
        "set_data_location",
    }
    for path in notebooks:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        code = "\n".join(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        )
        assert "zmart_drivers.zeiss.light_microscope.zen_api_grpc" in code, path.name
        assert "leica" not in code, path.name
        assert all(
            not cell.get("outputs")
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        ), path.name
        compile(code.replace("\n%", "\n#"), path.name, "exec")
