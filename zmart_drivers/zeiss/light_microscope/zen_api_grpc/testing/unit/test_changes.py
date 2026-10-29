"""The named changes through the dispatcher: the limits first, one send, a read-back, a second send only when it is safe.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest
from mock_zen_api import FakeGRPCError, build_fake_client
from setup_fixtures import allow_everything

from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher import limits as gate
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits import (
    handshake,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.procedures import (
    changes,
    readings,
)


@pytest.fixture
def fake():
    client, scope = build_fake_client()
    yield client, scope
    client.close()


def calls(scope, name):
    return [call for call in scope.calls if call[0] == name]


def test_a_move_is_refused_without_limits_and_nothing_is_sent(fake):
    client, scope = fake

    result = changes.move_xy(client, 100.0, 100.0)

    assert result["success"] is False and "no limits are installed" in result["message"]
    assert result["position"] is None
    assert scope.calls == []


def test_a_move_within_the_limits_is_sent_once_and_read_back(fake):
    client, scope = fake
    handshake.connect_handshake(client, load=False)

    result = changes.move_xy(client, 21000.0, 16000.0)

    assert result["success"] is True and result["confirmed"] is True
    assert len(calls(scope, "stage.move_to")) == 1
    assert readings.get_xy(client) == {
        "x_um": pytest.approx(21000.0),
        "y_um": pytest.approx(16000.0),
    }


def test_a_move_outside_the_limits_is_refused_and_the_stage_stays(fake):
    client, scope = fake
    handshake.connect_handshake(client, load=False)

    result = changes.move_xy(client, 500000.0, 0.0)

    assert result["success"] is False and "outside" in result["message"]
    assert scope.calls == [] and scope.x_um == 20000.0


def test_the_backstop_holds_whatever_the_limits_say(fake):
    """A hand-widened file and envelope still cannot pass the backstop."""
    client, _ = fake
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits import (
        config,
        limit_rules,
    )

    wide = config.build_limits_payload(
        {"x": [-1e5, 1e5], "y": [-1e5, 1e5], "z": [-1e4, 1e4]}
    )
    for key in ("x_um", "y_um", "z_um"):
        wide[key] = {"range": [-1e9, 1e9]}
    limits = limit_rules.ZeissLimits(
        wide, source="hand-widened", path=None, is_fallback=False
    )
    gate.install(client, gate.GateState(limits=limits))
    limit_rules.set_stage_limits(
        x_min=-1e9, x_max=1e9, y_min=-1e9, y_max=1e9, z_min=-1e9, z_max=1e9
    )

    assert "backstop" in gate.check_refusal(
        client, "move_xy", {"x_um": 300000.0, "y_um": 0.0}
    )
    assert "backstop" in gate.check_refusal(client, "move_z", {"z_um": 60000.0})
    assert gate.check_refusal(client, "move_z", {"z_um": 100.0}) is None


def test_a_busy_zen_is_sent_the_move_again(fake):
    client, scope = fake
    allow_everything(client)
    scope.refusals["stage.move_to"] = [FakeGRPCError("UNAVAILABLE", "busy")]

    result = changes.move_xy(client, 20500.0, 15000.0)

    assert result["confirmed"] is True
    assert (
        len(calls(scope, "stage.move_to")) == 1
    )  # the refused one never reached the stage
    assert result["timing"]["attempts"] == 2


def test_a_permanent_refusal_is_not_sent_again(fake):
    client, scope = fake
    allow_everything(client)
    scope.refusals["focus.move_to"] = [FakeGRPCError("OUT_OF_RANGE", "too far")]

    result = changes.move_z(client, 1200.0)

    assert result["success"] is False and "OUT_OF_RANGE" in result["message"]
    assert result["timing"]["attempts"] == 1


def test_a_picture_is_sent_once_even_after_a_temporary_error(fake, tmp_path):
    client, scope = fake
    allow_everything(client)
    scope.image_output_folder = str(tmp_path)
    loaded = changes.load_experiment(client, "ZMART_Snap")
    scope.refusals["experiment.run_experiment"] = [FakeGRPCError("UNAVAILABLE", "busy")]

    result = changes.acquire(client, loaded["experiment_id"], "ZMART_Snap", "once")

    assert result["success"] is False
    assert result["timing"]["attempts"] == 1


def test_a_picture_is_confirmed_by_the_experiments_status_and_names_its_file(
    fake, tmp_path
):
    client, scope = fake
    allow_everything(client)
    scope.image_output_folder = str(tmp_path)
    loaded = changes.load_experiment(client, "ZMART_ZStack")

    result = changes.acquire(
        client, loaded["experiment_id"], "ZMART_ZStack", "stack_A1"
    )

    assert result["success"] is True and result["confirmed"] is True
    assert result["output_name"] == "stack_A1"
    assert (tmp_path / "stack_A1.czi").is_file()
    assert len(calls(scope, "experiment.run_experiment")) == 1


def test_an_experiment_zen_does_not_have_is_refused_by_zen(fake):
    client, _ = fake
    allow_everything(client)

    result = changes.load_experiment(client, "No such experiment")

    assert result["success"] is False and result["experiment_id"] is None


def test_the_limits_can_fence_the_experiments_that_may_run(fake):
    client, scope = fake
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits import (
        config,
        limit_rules,
    )

    payload = config.build_limits_payload({"x": [0, 1e5], "y": [0, 1e5], "z": [0, 1e4]})
    payload["experiment"] = {"allowed": ["ZMART_Snap"]}
    stage = {
        "policy": config.validate_payload(payload),
        "stage_um": {"x": [0, 1e5], "y": [0, 1e5], "z": [0, 1e4]},
    }
    gate.install(
        client,
        gate.GateState(
            limits=limit_rules.build_limits(
                stage, source="test", path=None, is_fallback=False
            )
        ),
    )

    assert changes.load_experiment(client, "ZMART_Snap")["success"] is True
    refused = changes.load_experiment(client, "ZMART_ZStack")
    assert refused["success"] is False and "not allowed" in refused["message"]


def test_a_z_stack_is_changed_and_read_back_within_the_focus_limits(fake):
    client, scope = fake
    handshake.connect_handshake(client, load=False)
    loaded = changes.load_experiment(client, "ZMART_ZStack")

    changed = changes.set_z_stack(
        client, loaded["experiment_id"], "ZMART_ZStack", first_um=980.0, interval_um=2.0
    )
    refused = changes.set_z_stack(
        client, loaded["experiment_id"], "ZMART_ZStack", last_um=20000.0
    )

    assert changed["confirmed"] is True
    assert scope.experiments["ZMART_ZStack"].zstack == {
        "first_um": 980.0,
        "last_um": 1010.0,
        "interval_um": 2.0,
    }
    assert refused["success"] is False and "z-stack end" in refused["message"]


def test_the_objective_is_chosen_by_position_name_or_magnification(fake):
    client, scope = fake
    allow_everything(client)
    objectives = readings.get_objectives(client)

    assert (
        changes.set_objective(client, objectives, magnification=63.0)["confirmed"]
        is True
    )
    assert scope.objective_position == 3
    assert changes.set_objective(client, objectives, position=9)["success"] is False
    assert (
        changes.set_objective(client, objectives, position=1, name="x")["success"]
        is False
    )


def test_the_autofocus_answers_the_focus_it_found(fake):
    client, scope = fake
    allow_everything(client)
    loaded = changes.load_experiment(client, "ZMART_Snap")

    result = changes.autofocus(client, loaded["experiment_id"], "ZMART_Snap")

    assert result["success"] is True and result["focus_um"] == pytest.approx(
        scope.best_focus_z_um
    )
