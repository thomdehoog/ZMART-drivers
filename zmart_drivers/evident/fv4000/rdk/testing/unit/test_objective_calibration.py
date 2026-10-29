"""The objective calibration, ported from the Leica driver, run against the pretend FLUOVIEW.

The pretend FLUOVIEW's objectives look at slightly different spots and focus
at slightly different heights, by amounts the test sets. The calibration only
sees pictures, as on the microscope: a focus stack under each objective, a
picture under each, and the stage moves between them. It has to find those
amounts again, save them where the driver loads them, and refuse, with a
reason, whatever it cannot measure.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("skimage")
pytest.importorskip("matplotlib")

from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration import (  # noqa: E402
    adopt_calibration as adopt,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration import (  # noqa: E402
    calibrate_objective_pair as cop,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration import (  # noqa: E402
    check_calibration as check,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration import (  # noqa: E402
    model,
)

#: A focus stack that brackets every objective's sharp plane: 13 slices, 0.5 um apart, from 3 um below to 3 um above.
STACK = {
    "z_stack_enabled": True,
    "z_stack_slices": 13,
    "z_stack_step_um": 0.5,
    "z_stack_start_um": -3.0,
    "z_stack_end_um": 3.0,
}
OFFSETS = {1: (-1.5, 2.5, -0.5), 2: (3.0, -2.0, 1.0)}


@pytest.fixture
def optics(scope, mock, monkeypatch):
    """Objectives with known offsets, and a zoom set so every objective gives the same pixel size."""
    import matplotlib

    matplotlib.use("Agg")
    monkeypatch.setattr("IPython.display.display", lambda *a, **k: None, raising=False)
    mock.travel_s = 0.0
    mock.pixel_um_by_slot = {0: 0.5, 1: 0.5, 2: 0.5}
    mock.objective_offsets_um.update(OFFSETS)
    return scope


def measure_pair(scope, mock, target, *, session_id="run1", reference=0):
    """The four measurements, as the notebook and the wizard run them, with the operator's objective switches."""
    session = cop.start_session(
        session_id=session_id,
        reference_slot=reference,
        acquisition_name=f"slot{reference}-slot{target}",
        client=scope.client,
    )
    mock.objective_slot = reference
    scope.set_state(STACK)
    cop.measure_parfocality_reference(session)
    mock.objective_slot = target
    cop.measure_parfocality_target(session)
    scope.set_state({"z_stack_enabled": False})
    mock.objective_slot = reference
    cop.measure_parcentricity_reference(session)
    mock.objective_slot = target
    cop.measure_parcentricity_target_and_save(session)
    return session


def test_the_calibration_finds_the_true_offsets_of_an_objective(optics, mock):
    session = measure_pair(optics, mock, 2)
    assert session.registration["trusted"] is True
    assert session.translation_xy_um == pytest.approx(OFFSETS[2][:2], abs=0.1)
    assert session.translation_z_um == pytest.approx(OFFSETS[2][2], abs=0.1)
    report = json.loads(
        (session.paths.reports_dir / "objective_pair_report.json").read_text()
    )
    assert report["config_written"] is True and report["to_slot"] == 2


def test_adopting_publishes_the_calibration_the_driver_loads(optics, mock, tmp_path):
    session = measure_pair(optics, mock, 2)
    notebook = tmp_path / "calibrate_objective_pair.ipynb"
    notebook.write_text("{}")
    published = adopt.adopt_calibration(session, notebook_paths=[notebook])
    translations, info = model.load_objective_calibration()
    assert info["loaded"] and info["measured_slots"] == [0, 2]
    assert translations[0] == (0.0, 0.0, 0.0)
    assert translations[2] == pytest.approx(OFFSETS[2], abs=0.1)
    assert (tmp_path / "calibrate_objective_pair.ipynb").name in " ".join(
        published["notebook_paths"]
    )


def test_pairs_that_share_the_reference_combine(optics, mock, tmp_path):
    first = measure_pair(optics, mock, 1)
    measure_pair(optics, mock, 2)
    published = adopt.adopt_calibration(first)
    translations = model.load_translations()
    delta = model.translation_delta_um(translations, 1, 2)
    expected = tuple(b - a for a, b in zip(OFFSETS[1], OFFSETS[2], strict=True))
    assert delta == pytest.approx(expected, abs=0.15)
    assert published["snapshot"]


def test_a_step_with_the_wrong_objective_is_refused_and_loses_nothing(optics, mock):
    session = cop.start_session(
        session_id="run2",
        reference_slot=0,
        acquisition_name="slot0-slot2",
        client=optics.client,
    )
    optics.set_state(STACK)
    cop.measure_parfocality_reference(session)
    kept = session.focus_z_ref_um
    with pytest.raises(RuntimeError, match="still the reference objective"):
        cop.measure_parfocality_target(session)  # nobody switched the objective
    assert session.focus_z_ref_um == kept
    mock.objective_slot = 2
    cop.measure_parfocality_target(session)
    mock.objective_slot = 1
    with pytest.raises(RuntimeError, match="wrong objective for reference step"):
        cop.measure_parcentricity_reference(session)


def test_the_steps_out_of_order_are_refused_in_words(optics, mock):
    session = cop.start_session(
        session_id="run3",
        reference_slot=0,
        acquisition_name="slot0-slot2",
        client=optics.client,
    )
    with pytest.raises(RuntimeError, match="must run before"):
        cop.measure_parfocality_target(session)
    with pytest.raises(RuntimeError, match="must run before"):
        cop.measure_parcentricity_reference(session)
    with pytest.raises(RuntimeError, match="must run before"):
        cop.measure_parcentricity_target_and_save(session)


def test_a_reference_slot_that_holds_no_objective_is_refused(optics):
    with pytest.raises(ValueError, match="not occupied"):
        cop.start_session(session_id="run4", reference_slot=7, client=optics.client)


def test_a_focus_stack_with_too_few_slices_is_refused(optics, mock):
    session = cop.start_session(
        session_id="run5",
        reference_slot=0,
        acquisition_name="slot0-slot2",
        client=optics.client,
    )
    optics.set_state({**STACK, "z_stack_slices": 3})
    with pytest.raises(RuntimeError, match="at least"):
        cop.measure_parfocality_reference(session)


def test_pictures_at_different_pixel_sizes_are_refused(optics, mock):
    mock.pixel_um_by_slot[2] = (
        0.25  # the zoom was not matched under the target objective
    )
    with pytest.raises(ValueError, match="same zoom|pixel size"):
        measure_pair(optics, mock, 2)


def test_a_field_without_structure_saves_nothing(optics, mock):
    session = cop.start_session(
        session_id="run6",
        reference_slot=0,
        acquisition_name="slot0-slot2",
        client=optics.client,
    )
    optics.set_state(STACK)
    cop.measure_parfocality_reference(session)
    mock.objective_slot = 2
    cop.measure_parfocality_target(session)
    optics.set_state({"z_stack_enabled": False})
    mock.objective_slot = 0
    cop.measure_parcentricity_reference(session)
    mock.objective_slot = 2
    mock.field = np.full_like(mock.field, 70)
    session.ref_image = np.full_like(session.ref_image, 70)
    cop.measure_parcentricity_target_and_save(session)
    assert session.translation_xy_um is None and session.failure_reason
    with pytest.raises(FileNotFoundError, match="No trusted"):
        adopt.adopt_calibration(session)


def test_the_check_lands_on_the_same_spot_with_an_adopted_calibration(optics, mock):
    adopt.adopt_calibration(measure_pair(optics, mock, 2))
    mock.objective_slot = 0
    session = check.start_session(session_id="check1", client=optics.client)
    check.measure_reference(session)
    mock.objective_slot = 2
    report = check.measure_target_and_report(session, show=False)
    assert report["trusted"] is True and report["offset_um"] < 0.3
    assert (session.paths.reports_dir / "calibration_check.png").is_file()


def test_the_check_refuses_an_objective_the_calibration_does_not_cover(optics, mock):
    adopt.adopt_calibration(measure_pair(optics, mock, 2))
    mock.objective_slot = 1
    session = check.start_session(session_id="check2", client=optics.client)
    with pytest.raises(RuntimeError, match="not covered"):
        check.measure_reference(session)


def test_a_named_calibration_set_is_refused_in_words():
    with pytest.raises(ValueError, match="named calibration sets"):
        model.default_path("lens-setup-2")


def test_the_notebook_runs_the_four_measurements_and_the_adoption():
    from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration.notebooks import (
        bootstrap,
    )

    notebook = json.loads(bootstrap.NOTEBOOK_PATH.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"
    )
    for line in (
        "objective_pair.measure_parfocality_reference(session)",
        "objective_pair.measure_parfocality_target(session)",
        "objective_pair.measure_parcentricity_reference(session)",
        "summary = objective_pair.measure_parcentricity_target_and_save(session)",
        "adopt.adopt_calibration(session, notebook_paths=[notebook])",
    ):
        assert line in code
