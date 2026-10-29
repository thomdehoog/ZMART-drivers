"""Measuring the camera's orientation from three pictures, on the pretend microscope mounted every possible way.

The pretend camera can be mounted turned or mirrored (``camera_mount``). The
measurement takes a picture, one after a +x move and one after a +y move, and
must find the turn that undoes the mount, and say so plainly when the field
gives it nothing to go on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import numpy as np
import pytest

from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
    measure_orientation as mo,
)


@pytest.mark.parametrize(
    "mount", [(d, m) for m in (False, True) for d in (0, 90, 180, 270)]
)
def test_the_measurement_finds_how_the_camera_is_mounted(
    scope, stand_in, tmp_path, mount
):
    stand_in.scope.camera_mount = mount
    found = mo.measure(scope, step_um=6.0, folder=tmp_path)
    assert found.accepted, found.why
    assert (found.chosen.rotate_deg, found.chosen.mirrored) == mount
    assert stand_in.scope.position()[:2] == pytest.approx((1000.0, 2000.0), abs=0.5), (
        "the stage is back where it started"
    )


def test_an_empty_field_is_said_plainly_not_guessed(
    scope, stand_in, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        type(stand_in.scope),
        "picture",
        lambda self: np.full((48, 64), 100, dtype=np.uint16),
    )
    found = mo.measure(scope, step_um=6.0, folder=tmp_path)
    assert not found.accepted and found.why


def test_a_move_the_limits_refuse_measures_nothing_and_moves_nothing(
    scope, stand_in, tmp_path
):
    stand_in.scope.place(49990.0, 2000.0, 3000.0)
    with pytest.raises(RuntimeError):
        mo.measure(scope, step_um=50.0, folder=tmp_path)
    assert stand_in.scope.position()[:2] == pytest.approx((49990.0, 2000.0))
