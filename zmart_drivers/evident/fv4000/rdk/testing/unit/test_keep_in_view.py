"""An objective change keeps the sample in view: recorded before, turned, corrected after."""

import pytest

from zmart_drivers.evident.fv4000.rdk.procedures import keep_in_view, readings

TRANSLATIONS = {0: (0.0, 0.0, 0.0), 1: (3.0, -2.0, 0.5), 2: (10.0, -6.0, 2.0)}


@pytest.fixture(autouse=True)
def _stage_arrives_at_once(mock):
    # These tests check the arithmetic of the compensation, so the pretend stage arrives at once;
    # a move is confirmed within its tolerance, which on a travelling stage can be a hair early.
    mock.travel_s = 0.0


def test_the_stage_follows_the_objectives_offset(governed_client, mock):
    objectives = readings.get_objectives(governed_client)
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=1, translations=TRANSLATIONS
    )
    assert (
        result["success"]
        and result["confirmed"]
        and result["compensation_um"] == [3.0, -2.0, 0.5]
    )
    assert mock.at_rest() == (10_003_000, 7_998_000, 2_500_500)
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=2, translations=TRANSLATIONS
    )
    assert result["compensation_um"] == [7.0, -4.0, 1.5]
    assert mock.at_rest() == (10_010_000, 7_994_000, 2_502_000)


def test_an_uncalibrated_pair_is_refused_before_the_objective_turns(
    governed_client, mock
):
    objectives = readings.get_objectives(governed_client)
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=1, translations={0: (0.0, 0.0, 0.0)}
    )
    assert (
        result["success"] is False
        and "no calibration translation covers" in result["message"]
    )
    assert mock.objective_slot == 0
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=1, translations={}
    )
    assert result["success"] is False


def test_a_session_that_declined_the_calibration_turns_the_objective_bare(
    governed_client, mock
):
    objectives = readings.get_objectives(governed_client)
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=1, translations=None
    )
    assert result["success"] and "compensation_um" not in result
    assert mock.objective_slot == 1 and mock.x_nm() == 10_000_000
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=2, translations=TRANSLATIONS, compensate=False
    )
    assert result["success"] and "compensation_um" not in result


def test_the_same_objective_again_moves_nothing(governed_client, mock):
    objectives = readings.get_objectives(governed_client)
    result = keep_in_view.set_objective(
        governed_client, objectives, slot=0, translations=TRANSLATIONS
    )
    assert (
        result["success"]
        and "compensation_um" not in result
        and mock.x_nm() == 10_000_000
    )
