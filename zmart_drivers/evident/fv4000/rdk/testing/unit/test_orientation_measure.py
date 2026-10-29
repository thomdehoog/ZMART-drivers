"""The orientation measurement, ported from the Leica driver, run against the pretend FLUOVIEW.

The pretend FLUOVIEW turns and mirrors its pictures the way a scanner mounted
that way would, and the stage really moves the field of view. So these tests
run the whole measurement as the notebook and the wizard do (three pictures,
two moves, the eight candidates, the check picture, adoption) and check that it
finds the mounting that was set, and refuses when it cannot tell.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("skimage")
pytest.importorskip("matplotlib")

from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation import (  # noqa: E402
    Orientation,
    load_rig_orientation,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation import (  # noqa: E402
    measure_orientation as wf,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.store import MACHINE  # noqa: E402
from zmart_drivers.evident.fv4000.rdk.procedures import changes  # noqa: E402


def _measured(scope, **options):
    session = wf.start_session(client=scope.client, settle_s=0.0, **options)
    return wf.measure(session)


def _record(tmp_path) -> Path:
    path = tmp_path / "set_orientation.ipynb"
    path.write_text("{}")
    return path


@pytest.mark.parametrize("rotate_deg", [0, 90, 180, 270])
@pytest.mark.parametrize("mirrored", [False, True])
def test_every_way_the_scanner_can_be_mounted_is_recovered(
    scope, mock, rotate_deg, mirrored
):
    mock.orientation = (rotate_deg, mirrored)
    session = _measured(scope)
    assert session.d4_accepted is True, session.failure_reason
    assert session.orientation == Orientation(rotate_deg, mirrored)
    assert session.is_mirrored is mirrored


def test_an_aligned_microscope_needs_no_correction(scope, mock):
    session = _measured(scope)
    assert session.d4_label == "+X +Y" and session.orientation.is_identity
    report = json.loads(
        (session.paths.reports_dir / "orientation_report.json").read_text()
    )
    assert report["accepted"] is True and report["image_to_stage"] == [[1, 0], [0, 1]]
    assert (session.paths.reports_dir / wf.DIAGNOSTIC_NAME).is_file(), (
        "the sheet of eight candidates"
    )


def test_the_stage_is_brought_back_where_it_began(scope, mock):
    before = mock.at_rest()[:2]
    _measured(scope)
    assert mock.at_rest()[:2] == before


def test_a_field_without_structure_is_not_accepted_and_nothing_is_adopted(
    scope, mock, tmp_path
):
    mock.field = np.full_like(mock.field, 70)
    session = _measured(scope)
    # Two pictures that do not move tell nothing: the match is untrusted, or the two shifts cannot be told apart.
    assert not session.d4_accepted and session.orientation is None
    assert (
        "not trusted" in session.failure_reason or "singular" in session.failure_reason
    )
    with pytest.raises(RuntimeError, match="measure and accept"):
        wf.adopt_orientation(session, _record(tmp_path))
    assert not load_rig_orientation()[1]["measured"]


def test_a_failed_picture_brings_the_stage_back_and_raises(scope, mock, monkeypatch):
    real = changes.take_picture
    taken = []

    def take_picture(client, **options):
        taken.append(1)
        if len(taken) == 2:
            raise RuntimeError("the scan never started")
        return real(client, **options)

    monkeypatch.setattr(changes, "take_picture", take_picture)
    before = mock.at_rest()[:2]
    session = wf.start_session(client=scope.client, settle_s=0.0)
    with pytest.raises(RuntimeError, match="never started"):
        wf.measure(session)
    assert mock.at_rest()[:2] == before


def test_the_orientation_becomes_active_only_after_the_check_picture_and_adoption(
    scope, mock, tmp_path
):
    mock.orientation = (90, False)
    session = _measured(scope)
    assert not load_rig_orientation()[1]["measured"], "measuring alone changes nothing"
    with pytest.raises(RuntimeError, match="validation before adoption"):
        wf.adopt_orientation(session, _record(tmp_path))
    image, store = wf.acquire_validation_image(session)
    assert store.is_dir() and store.parent.parent.name == "validation"
    assert np.array_equal(image, mock.view(0)), (
        "the check picture is stored in the stage's axes"
    )
    adopted = wf.adopt_orientation(session, _record(tmp_path))
    assert Path(adopted["orientation_path"]).is_file()
    orientation, info = load_rig_orientation()
    assert orientation == Orientation(90) and info["measured"]
    assert MACHINE.latest_snapshot("orientation") == Path(adopted["snapshot"])


def test_measuring_again_replaces_this_runs_own_outputs(scope, mock):
    session = _measured(scope)
    first = sorted(p.name for p in session.paths.data_dir.rglob("*.ome.zarr"))
    session = wf.measure(session)
    again = sorted(p.name for p in session.paths.data_dir.rglob("*.ome.zarr"))
    assert len(first) == len(again) == 3, (
        "home, +X and +Y, no leftovers from the first run"
    )


def test_a_stage_move_that_is_not_positive_is_refused():
    with pytest.raises(ValueError, match="stage_move_um"):
        wf.start_session(client=object(), stage_move_um=0)


def test_the_notebook_runs_the_measurement_the_check_and_the_adoption():
    from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation.notebooks import (
        bootstrap,
    )

    notebook = json.loads(bootstrap.NOTEBOOK_PATH.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"
    )
    for line in (
        "session = wf.run_notebook_measurement()",
        "validation_path = wf.validate(session)",
        "wf.adopt_orientation(session, notebook)",
    ):
        assert line in code
