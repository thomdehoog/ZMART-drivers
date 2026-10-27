"""The limits: the file checked, cut to what NIS-Elements allows, and refusing what it does not allow.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import re

import pytest

from zmart_drivers.nikon.nis_elements_ar610.dispatcher import limits as gate
from zmart_drivers.nikon.nis_elements_ar610.dispatcher.limits import (
    LimitsError,
    LimitViolation,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.limits import (
    config,
    handshake,
    limit_rules,
)

NIS = {"x": [-57000.0, 57000.0], "y": [-37500.0, 37500.0], "z": [0.0, 10000.0]}


def _limits(**changes):
    payload = json.loads(config.defaults_path().read_text(encoding="utf-8"))
    payload.update(changes)
    return payload


def test_the_bundled_defaults_check_out_and_fit_the_ti2_simulator():
    loaded = config.load()
    limits, notes = limit_rules.build_limits(
        loaded, NIS, source="defaults", path=None, is_fallback=True
    )

    assert notes == []
    assert limits.describe()["envelope_um"] == {
        "x": [-50000.0, 50000.0],
        "y": [-30000.0, 30000.0],
        "z": [0.0, 9000.0],
    }


@pytest.mark.parametrize(
    "broken, says",
    [
        ({"x_um": {"range": [1, 0]}}, "minimum above its maximum"),
        ({"x_um": {"allowed": [1]}}, re.escape("must be [] or")),
        ({"objective_slot": {"allowed": [0]}}, "counted from 1"),
        ({"exposure_ms": {"range": [1]}}, re.escape("must be [min, max]")),
        ({"extra": []}, "unknown entries"),
    ],
)
def test_a_malformed_file_is_refused_whole(broken, says):
    with pytest.raises(ValueError, match=says):
        config.validate_payload(_limits(**broken))


def test_a_missing_entry_is_refused():
    payload = _limits()
    del payload["z_um"]
    with pytest.raises(ValueError, match="missing entries"):
        config.validate_payload(payload)


def test_an_envelope_wider_than_nis_is_cut_and_said():
    envelope, notes = limit_rules.cut_to_nis(
        {"x": [-60000, 60000], "y": [0, 100], "z": [0, 9000]}, NIS
    )

    assert envelope["x"] == [-57000.0, 57000.0] and envelope["y"] == [0.0, 100.0]
    assert len(notes) == 1 and notes[0].startswith("x:")


def test_an_envelope_wholly_outside_nis_is_a_wrong_machine_file():
    with pytest.raises(LimitsError, match="wholly outside"):
        limit_rules.cut_to_nis({"x": [70000, 80000], "y": [0, 1], "z": [0, 1]}, NIS)


def test_the_rulebook_refuses_each_kind_of_wrong_value():
    limits, _ = limit_rules.build_limits(
        config.load(), NIS, source="defaults", path=None, is_fallback=True
    )
    limits.check_command("move_xyz", {"x_um": 0.0, "y_um": 0.0, "z_um": 100.0})
    with pytest.raises(LimitViolation, match="outside the limits"):
        limits.check_command("move_xyz", {"x_um": 51000.0, "y_um": 0.0, "z_um": 100.0})
    with pytest.raises(LimitViolation, match="not a number"):
        limits.check_command("move_xyz", {"x_um": "10", "y_um": 0.0, "z_um": 100.0})
    with pytest.raises(LimitViolation, match="not finite"):
        limits.check_command(
            "move_xyz", {"x_um": float("nan"), "y_um": 0.0, "z_um": 100.0}
        )
    with pytest.raises(LimitViolation, match="outside the range"):
        limits.check_command("set_exposure", {"exposure_ms": 0.0})
    with pytest.raises(LimitsError, match="was not shown"):
        limits.check_command("set_exposure", {})
    with pytest.raises(KeyError):
        limits.check_command("fly", {})
    limits.check_command("snap", {"path": "a.tif"})


def test_an_allow_list_fences_the_objectives_and_the_channels():
    loaded = {
        "policy": config.validate_payload(
            _limits(
                objective_slot={"allowed": [1, 2]},
                optical_configuration={"allowed": ["DAPI"]},
            )
        ),
        "stage_um": NIS,
    }
    limits, _ = limit_rules.build_limits(
        loaded, NIS, source="machine", path="x", is_fallback=False
    )

    limits.check_command("set_objective", {"objective_slot": 2})
    with pytest.raises(LimitViolation, match="not allowed"):
        limits.check_command("set_objective", {"objective_slot": 4})
    with pytest.raises(LimitViolation, match="not allowed"):
        limits.check_command("select_optical_configuration", {"name": "FITC"})


def test_nis_own_limits_are_a_backstop_even_for_a_widened_envelope():
    limits = limit_rules.NikonLimits(
        config.validate_payload(_limits()),
        envelope_um={"x": [-60000, 60000], "y": [-1, 1], "z": [0, 1]},
        nis_um=NIS,
        source="machine",
        path="x",
        is_fallback=False,
    )
    with pytest.raises(LimitViolation, match="what NIS-Elements allows"):
        limits.check_command("move_xyz", {"x_um": 58000.0})


def test_the_handshake_installs_the_published_file_and_falls_back_to_the_defaults_when_it_is_bad():
    client = object()
    adopted = config.adopt_limits(_limits(x_um={"range": [-10, 10]}))
    status = handshake.connect_handshake(client, NIS)
    assert (
        status.ok
        and status.limits.source == "machine"
        and not status.limits.is_fallback
    )
    assert gate.state_for(client).stage_cfg["stage_um"]["x"] == [-10.0, 10.0]

    (store.MACHINE.latest_path("limits")).write_text("{not json", encoding="utf-8")
    status = handshake.connect_handshake(client, NIS)
    assert (
        status.ok and status.limits.is_fallback and status.limits.source == "defaults"
    )
    assert adopted["limits_path"].endswith("limits.json")


def test_without_a_published_file_the_defaults_govern_and_say_so():
    client = object()
    status = handshake.connect_handshake(client, NIS)
    assert status.limits.is_fallback
    assert gate.check_refusal(
        client, "move_xyz", {"x_um": 51000.0, "y_um": 0.0, "z_um": 0.0}, "the notebook"
    )


def test_a_client_without_a_handshake_refuses_everything():
    message = gate.check_refusal(
        object(), "move_xyz", {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0}, "the notebook"
    )
    assert message and "no limits are installed" in message
