"""Pytest gate for the cache-release check's verdict: when has LAS X let go of the captures?

Each capture leaves a hidden copy in LAS X's cache, at least the capture's
own size, and only clearing the Navigator canvas lets go of it. So a run
passes when every capture was saved, every Clear press went through, and the
hidden copies did not grow: a press that goes through but frees nothing has
to fail too.
"""

import json
import sys

import check_cache_release
import pytest
from wizard_standins import install_lasx

from zmart_drivers.leica.stellaris.navigator_expert import methods
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    collect as container,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    save_waiting as saving,
)


def test_a_run_that_kept_the_cache_flat_passes():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=-11.1
    )
    assert all(checks.values()), checks


def test_hidden_copies_that_pile_up_fail_even_when_every_press_went_through():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=12.4
    )
    assert checks["the hidden copies did not grow"] is False


def test_a_margin_well_below_one_capture_is_allowed():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=0.4
    )
    assert checks["the hidden copies did not grow"] is True


def test_a_press_that_did_not_go_through_fails():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=1, hidden_grown_mb=0.0
    )
    assert checks["the canvas cleared at every release"] is False


def test_a_failed_capture_fails():
    checks = check_cache_release.verdict(
        captures=10,
        done=10,
        errors=["capture 3: no picture"],
        not_cleared=0,
        hidden_grown_mb=0.0,
    )
    assert checks["every capture saved"] is False


@pytest.fixture
def driver_alone(monkeypatch, tmp_path):
    """The driver over the stand-in LAS X, on a computer without the ZMART Controller.

    Saving and clearing read LAS X's own cache folder, which only exists on
    Windows with LAS X, so those two calls are counted here instead. The
    capture, the job and the stage are the driver's own, against the stand-in.
    """
    install_lasx(monkeypatch)
    monkeypatch.setitem(sys.modules, "zmart_controller", None)
    # The check turns projections off for its run; put the setting back after the test.
    monkeypatch.setattr(saving, "IMAGE_SAVE", saving.IMAGE_SAVE)
    calls = {"save": 0, "clear_cache": 0}

    def save(scope, label=None):
        calls["save"] += 1
        return [{"files": [], "planes": []}]

    def clear_cache(scope):
        calls["clear_cache"] += 1
        return {"cleared": [calls["clear_cache"]], "failed": []}

    monkeypatch.setattr(methods.Microscope, "save", save)
    monkeypatch.setattr(methods.Microscope, "clear_cache", clear_cache)
    cache = tmp_path / "lasx-cache"
    cache.mkdir()
    monkeypatch.setattr(container, "data_container_root", lambda: cache)
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_cache_release.py",
            "--captures",
            "3",
            "--label",
            "ci",
            f"--save-to={tmp_path}",
            f"--report-dir={tmp_path}",
        ],
    )
    return calls


def test_each_capture_is_saved_then_cleared_through_the_driver_alone(
    driver_alone, tmp_path
):
    assert check_cache_release.main() == 0
    assert driver_alone == {"save": 3, "clear_cache": 3}
    report = json.loads(
        (tmp_path / "check_cache_release_ci.json").read_text(encoding="utf-8")
    )
    assert report["job"] == "HiRes"  # the job selected in the stand-in


def test_a_save_that_fails_is_counted_and_fails_the_run(
    driver_alone, monkeypatch, tmp_path
):
    def broken(scope, label=None):
        raise OSError("the cache folder cannot be read")

    monkeypatch.setattr(methods.Microscope, "save", broken)
    assert check_cache_release.main() == 1
    report = json.loads(
        (tmp_path / "check_cache_release_ci.json").read_text(encoding="utf-8")
    )
    assert report["checks"]["every capture saved"] is False
    assert "the cache folder cannot be read" in report["errors"][0]
