"""The shared dispatcher runs a second, made-up vendor, with nothing of LAS X.

The pretend vendor (``testing/helpers/pretend_vendor.py``) hands the dispatcher
only what the vendor contract asks for. These tests move its stage and read it
back through the same door the Leica driver uses. If one of them needs a change
to the dispatcher to pass, something of LAS X had leaked into the dispatcher.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import ast
from pathlib import Path

import pretend_vendor
import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import gate


@pytest.fixture
def scope():
    scope = pretend_vendor.connect()
    yield scope
    gate.uninstall(scope)


def test_a_reading_comes_from_the_pretend_stage(scope):
    reading = pretend_vendor.read_x(scope)

    assert reading.value == 0.0
    assert reading.source == "stage"
    assert reading.error is None


def test_a_move_is_sent_once_and_confirmed_when_the_stage_arrives(scope):
    result = pretend_vendor.move_x(scope, 250.0)

    assert result["success"] is True
    assert result["confirmed"] is True
    assert [r["x_um"] for r in scope.received] == [250.0]
    assert pretend_vendor.read_x(scope).value == pytest.approx(250.0)


def test_a_move_outside_the_limits_is_refused_and_nothing_is_sent(scope):
    result = pretend_vendor.move_x(scope, 5000.0)

    assert result["success"] is False
    assert "outside" in result["message"]
    assert scope.received == []


def test_a_busy_microscope_is_sent_the_move_again(scope):
    scope.busy_answers = 1

    result = pretend_vendor.move_x(scope, 40.0)

    assert result["confirmed"] is True
    assert len(scope.received) == 2


def test_a_command_marked_never_resend_is_sent_once_whatever_the_caller_asks(scope):
    scope.busy_answers = 5

    result = pretend_vendor.start_scan(scope, max_retries=3)

    assert result["success"] is False
    assert len(scope.received) == 1


def test_a_command_marked_never_resend_is_not_sent_again_when_unconfirmed(scope):
    result = pretend_vendor.start_scan(scope, max_confirm_attempts=3)

    assert result["confirmed"] is False
    assert len(scope.received) == 1


def test_the_pretend_vendor_uses_only_the_dispatcher():
    tree = ast.parse(Path(pretend_vendor.__file__).read_text(encoding="utf-8"))
    driver = "zmart_drivers.leica.stellaris5_y42h93.navigator_expert."
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(driver)
    }

    assert imported
    assert all(name[len(driver) :].startswith("dispatcher") for name in imported)
