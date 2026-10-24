"""Pytest gate for the setup wizard check, against the stand-in LAS X.

``check_setup_wizard.py`` walks the wizard over its own web calls and must
never change this microscope's settings folder. Here LAS X is the offline
stand-in the wizard's own tests use, and the "microscope's settings folder" is
the stand-in's throwaway one, so these tests can see whether the check would
notice a write into it.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import check_setup_wizard
import pytest
from wizard_standins import install_lasx, install_orientation

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import (
    client as lasx_client,
)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "workspace"


def orientation_field(monkeypatch, workspace, field):
    """Pictures that shift with the stage, put in place just before the orientation is measured.

    The orientation stand-in replaces the whole LAS X connection, so, as in the
    wizard's own tests, it comes after Connect. It names its settings folder
    once, so it is told the check's copy, under ``workspace/settings``.
    ``field["structure"]`` says whether the field under the objective has
    structure the orientation can be worked out from.
    """
    call = check_setup_wizard.Page.call

    def call_then(page, step, action, values=None):
        if (step, action) == ("orientation", "measure"):
            install_orientation(
                monkeypatch, workspace / "settings", trusted=field["structure"]
            )
        return call(page, step, action, values)

    monkeypatch.setattr(check_setup_wizard.Page, "call", call_then)


@pytest.fixture
def lasx(monkeypatch, workspace):
    """The stand-in LAS X, and a field with structure for the orientation."""
    client = install_lasx(monkeypatch)
    workspace.mkdir()
    real = store.MachineProfile().snapshot_root()
    field = {"structure": True}
    orientation_field(monkeypatch, workspace, field)
    return client, real, field


def test_every_step_passes_and_the_real_settings_stay_as_they_were(lasx, workspace):
    _client, real, _field = lasx
    before = check_setup_wizard.fingerprint(real)
    checks = check_setup_wizard.run_checks(workspace)
    assert check_setup_wizard.failed(checks) == [], checks
    assert checks["this microscope's settings folder is untouched"] is True
    assert check_setup_wizard.fingerprint(real) == before
    assert checks["Objective calibration"].startswith("not checked")
    copy = workspace / "settings" / real.relative_to(real.parents[2])
    for item in ("limits", "origin", "orientation"):
        assert list((copy / item).iterdir()), f"nothing saved for {item} in the copy"


def test_a_field_without_structure_is_reported_and_not_failed(lasx, workspace):
    _client, _real, field = lasx
    field["structure"] = False
    checks = check_setup_wizard.run_checks(workspace)
    assert check_setup_wizard.failed(checks) == [], checks
    assert "said why" in checks["orientation: the field under the objective"]


def test_a_wizard_that_saves_into_the_real_folder_is_caught(
    lasx, monkeypatch, workspace
):
    _client, real, _field = lasx
    real_root = os.environ[store.PROGRAMDATA_ROOT_ENV]

    def no_copy(space):
        # As if the check forgot to point the driver at the copy.
        copy = space / "settings" / real.relative_to(Path(real_root))
        copy.mkdir(parents=True)
        return real, copy

    monkeypatch.setattr(check_setup_wizard, "throwaway_settings", no_copy)
    checks = check_setup_wizard.run_checks(workspace)
    wrong = check_setup_wizard.failed(checks)
    assert "this microscope's settings folder is untouched" in wrong, checks


def test_lasx_that_does_not_answer_fails_connect_and_stops_the_walk(
    lasx, monkeypatch, workspace
):
    def unreachable(**_kw):
        raise ConnectionError("no CAM server")

    monkeypatch.setattr(lasx_client, "connect_python_client", unreachable)
    checks = check_setup_wizard.run_checks(workspace)
    wrong = check_setup_wizard.failed(checks)
    assert wrong == [
        "Connect: LAS X reached, the checks name the stage, the objective and the copied settings"
    ], checks
    assert "Could not reach LAS X" in checks[wrong[0]]
    assert checks["this microscope's settings folder is untouched"] is True


def test_the_report_is_written_with_its_verdict(lasx, monkeypatch, tmp_path, workspace):
    # Only the check's own workspace is fixed; the wizard keeps its own temporary files.
    monkeypatch.setattr(
        check_setup_wizard,
        "tempfile",
        SimpleNamespace(mkdtemp=lambda prefix: str(workspace)),
    )
    monkeypatch.setattr(
        "sys.argv", ["check_setup_wizard.py", f"--report-dir={tmp_path}"]
    )
    assert check_setup_wizard.main() == 0
    report = json.loads(
        (tmp_path / "check_setup_wizard.json").read_text(encoding="utf-8")
    )
    assert report["verdict"] == "ALL CHECKS PASSED"
