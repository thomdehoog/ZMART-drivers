"""The check at the microscope runs end to end against the stand-in, so it is known to work before it meets ZEN.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from mock_zen_api import FakeGRPCError, FakeScope, build_fake_client
from setup_fixtures import provision

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods

CHECK = Path(__file__).with_name("check_on_zen.py")


def load_check():
    spec = importlib.util.spec_from_file_location("check_on_zen", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def zen(monkeypatch, tmp_path):
    provision()
    scope = FakeScope(image_output_folder=str(tmp_path / "zen_images"))
    monkeypatch.setattr(
        methods, "open_connection", lambda _c: build_fake_client(scope)[0]
    )
    return scope


def test_every_step_passes_on_a_well_set_up_microscope(zen, tmp_path):
    pytest.importorskip("pylibCZIrw")
    code = load_check().main(
        [
            "--password",
            "x",
            "--experiment",
            "ZMART_Snap",
            "--allow-move",
            "--allow-acquire",
            f"--report-dir={tmp_path}",
        ]
    )

    record = json.loads((tmp_path / "check_on_zen.json").read_text(encoding="utf-8"))
    assert code == 0, [r for r in record if not r["ok"]]
    assert len(record) == 12
    assert (zen.x_um, zen.y_um, zen.z_um) == (20000.0, 15000.0, 1000.0)


def test_a_zen_that_refuses_control_fails_the_move_steps(zen, tmp_path):
    zen.refusals["stage.move_to"] = [FakeGRPCError("PERMISSION_DENIED", "no")] * 10

    code = load_check().main(
        ["--password", "x", "--allow-move", f"--report-dir={tmp_path}"]
    )

    record = json.loads((tmp_path / "check_on_zen.json").read_text(encoding="utf-8"))
    assert code == 1
    assert [r["step"] for r in record if not r["ok"]][
        0
    ] == "a small move along x and y, read back"
