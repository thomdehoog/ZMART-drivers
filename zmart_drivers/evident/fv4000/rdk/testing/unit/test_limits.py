"""The limits: the file checked, the handshake, the fallback, the backstop, and the gate asked before anything is sent.

Policy: a malformed or over-wide ``limits.json`` must never govern, but it
does not leave the session dead: the bundled defaults take over, loudly. Two
things never relax: the physical backstop bounds every move regardless, and
a connection that never handshook refuses every change.
"""

import json
import math
import os

import pytest
from limits_fixtures import (
    DEFAULT_STAGE_UM,
    merged_limits_payload,
    provision_machine_limits,
)

from zmart_drivers.evident.fv4000.rdk.dispatcher import limits as gate
from zmart_drivers.evident.fv4000.rdk.driver_setup.limits import config as limits_config
from zmart_drivers.evident.fv4000.rdk.driver_setup.limits import handshake, limit_rules
from zmart_drivers.evident.fv4000.rdk.driver_setup.store import MachineProfile
from zmart_drivers.evident.fv4000.rdk.primitives.rdk import writes
from zmart_drivers.evident.fv4000.rdk.procedures import changes


def _root():
    return os.environ["ZMART_MICROSCOPY_ROOT"]


# --- the file -------------------------------------------------------------------------------


def test_the_bundled_defaults_name_exactly_the_vocabulary():
    payload = json.loads(limits_config.defaults_path().read_text())
    assert set(payload) == {
        *limit_rules.AXIS_FILE_KEYS.values(),
        "objective_slot",
        *limit_rules.SETTER_LIMIT_KEYS,
    }
    limits_config.validate_payload(payload)


def test_the_setter_keys_are_every_setting_of_the_writes_plus_load_parameter():
    stage = {f"set_{name}" for name in writes.STAGE_POLICY_SETTINGS}
    expected = {f"set_{name}" for name in writes.SETTINGS} - stage | {
        "set_load_parameter"
    }
    assert set(limit_rules.SETTER_LIMIT_KEYS) == expected
    assert all(command in limit_rules.MUTATING_COMMANDS for command in stage)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda p: p.pop("z_um"), "missing limits entries"),
        (lambda p: p.update(extra=[]), "unknown limits entries"),
        (lambda p: p.update(x_um={"range": [1, 0]}), "min > max"),
        (lambda p: p.update(x_um={"range": [0, float("nan")]}), "not finite"),
        (lambda p: p.update(x_um={"allowed": [1, 2]}), "must use 'range'"),
        (lambda p: p.update(set_scanner_type={"allowed": []}), "must not be empty"),
        (lambda p: p.update(set_scanner_type={"allowed": ["A", "A"]}), "duplicates"),
        (lambda p: p.update(objective_slot={"allowed": [-1]}), "whole numbers from 0"),
        (
            lambda p: p.update(set_channel_hv={"range": [0, 1], "allowed": [1]}),
            "must be",
        ),
    ],
)
def test_a_malformed_file_is_refused_with_what_is_wrong(change, said):
    payload = merged_limits_payload(DEFAULT_STAGE_UM)
    change(payload)
    with pytest.raises(ValueError, match=said):
        limits_config.validate_payload(payload)


def test_a_file_wider_than_the_backstop_is_not_published():
    payload = merged_limits_payload({**DEFAULT_STAGE_UM, "x": [-1e9, 1e9]})
    with pytest.raises(RuntimeError, match="physical backstop"):
        limits_config.adopt_limits(
            payload, machine=MachineProfile(programdata_root=_root())
        )


def test_adopt_limits_publishes_a_dated_record_with_the_notebook(tmp_path):
    notebook = tmp_path / "set_limits.ipynb"
    notebook.write_text("{}")
    machine = MachineProfile(programdata_root=_root())
    published = limits_config.adopt_limits(
        merged_limits_payload(DEFAULT_STAGE_UM),
        machine=machine,
        notebook_paths=[notebook],
    )
    assert machine.limits_path() == machine.latest_snapshot("limits") / "limits.json"
    assert (
        published["notebook_paths"] and "set_limits_" in published["notebook_paths"][0]
    )
    assert (
        limits_config.load(published["limits_path"])["stage_um"]["x"]
        == DEFAULT_STAGE_UM["x"]
    )


# --- the handshake --------------------------------------------------------------------------


def test_the_published_file_governs_the_session(client):
    provision_machine_limits(_root())
    status = handshake.connect_handshake(client)
    assert (
        status.ok
        and status.limits.source == "machine"
        and not status.limits.is_fallback
    )
    assert limit_rules.get_stage_limits()["x"] == tuple(DEFAULT_STAGE_UM["x"])
    assert changes.move_xy(client, 10000.0, 8000.0)["success"] is True


def test_without_a_published_file_the_defaults_govern_and_say_so(client, caplog):
    status = handshake.connect_handshake(client)
    assert (
        status.ok and status.limits.source == "defaults" and status.limits.is_fallback
    )
    assert "limits fallback" in caplog.text and "set_limits.ipynb" in caplog.text


def test_a_malformed_published_file_does_not_govern(client):
    machine = provision_machine_limits(_root())
    path = machine.limits_path()
    path.write_text(path.read_text().replace('"x_um"', '"x_uM"'))
    status = handshake.connect_handshake(client)
    assert status.limits.is_fallback and status.limits.source == "defaults"


def test_a_hand_widened_file_does_not_govern(client):
    provision_machine_limits(_root(), stage_um={**DEFAULT_STAGE_UM, "x": [-5e8, 5e8]})
    status = handshake.connect_handshake(client)
    assert status.limits.is_fallback
    assert limit_rules.get_stage_limits()["x"] != (-5e8, 5e8)


def test_skipping_the_limits_still_installs_the_defaults(client):
    provision_machine_limits(_root())
    status = handshake.connect_handshake(client, load=False)
    assert (
        status.limits.is_fallback
        and changes.move_xy(client, 0.0, 0.0)["success"] is True
    )


def test_when_even_the_defaults_cannot_be_read_every_change_refuses(
    client, monkeypatch
):
    class Broken(MachineProfile):
        def bundled_default_path(self, filename):
            return self.root() / "missing.json"

    status = handshake.connect_handshake(
        client, machine=Broken(programdata_root=_root())
    )
    assert not status.ok and "refuses" in status.error
    result = changes.move_xy(client, 10000.0, 8000.0)
    assert result["success"] is False and "refused" in result["message"]


# --- the gate ---------------------------------------------------------------------------------


def test_moves_refuse_before_any_handshake(client):
    assert gate.state_of(client) is None
    assert changes.move_xy(client, 10000.0, 8000.0)["success"] is False
    assert changes.change(client, "scanner_type", value="GALVANO")["success"] is False


@pytest.mark.parametrize(
    "x,y", [(float("nan"), 0.0), (float("inf"), 0.0), (-1.0, 0.0), (0.0, 1e9)]
)
def test_a_poisoned_or_outside_target_is_refused(governed_client, mock, x, y):
    limit_rules.set_stage_limits(
        x=(0.0, 60000.0), y=(0.0, 40000.0), z=(0.0, 8000.0), z_piezo=(-100.0, 100.0)
    )
    result = changes.move_xy(governed_client, x, y)
    assert result["success"] is False and (
        "refused" in result["message"] or "not a finite" in result["message"]
    )
    assert not [c for c in mock.calls if c[0] == "Parameter.setParameter"], (
        "nothing was sent"
    )


def test_the_backstop_holds_even_when_the_travel_in_force_is_widened(governed_client):
    limit_rules.set_stage_limits(
        x=(-1e9, 1e9), y=(-1e9, 1e9), z=(-1e9, 1e9), z_piezo=(-1e9, 1e9)
    )
    with pytest.raises(RuntimeError, match="physical backstop"):
        limit_rules.check_xy(5e8, 0.0)
    with pytest.raises(RuntimeError, match="physical backstop"):
        limit_rules.check_z(1e6)
    # Through the gate the same target is refused too, by the file's own range or the backstop, whichever comes first.
    assert changes.move_xy(governed_client, 5e8, 0.0)["success"] is False
    assert changes.move_z(governed_client, 1e6)["success"] is False


def test_the_objective_allow_list_fences_the_nosepiece(client):
    from limits_fixtures import install_permissive_limits

    install_permissive_limits(client, objective_slot={"allowed": [0, 1]})
    objectives = [{"slot": 0, "name": "a"}, {"slot": 2, "name": "c"}]
    assert changes.set_objective(client, objectives, slot=0)["success"] is True
    refused = changes.set_objective(client, objectives, slot=2)
    assert (
        refused["success"] is False
        and "objective_slot=2 not allowed" in refused["message"]
    )


def test_a_setting_range_accepts_its_ends_and_refuses_outside(client):
    from limits_fixtures import install_permissive_limits

    install_permissive_limits(client, set_laser_intensity={"range": [0, 10]})
    assert (
        changes.change(client, "laser_intensity", laser_id="LASER_488", value=10.0)[
            "success"
        ]
        is True
    )
    refused = changes.change(
        client, "laser_intensity", laser_id="LASER_488", value=10.5
    )
    assert refused["success"] is False and "outside range" in refused["message"]


def test_a_setting_allow_list_refuses_another_value(client):
    from limits_fixtures import install_permissive_limits

    install_permissive_limits(client, set_scanner_type={"allowed": ["GALVANO"]})
    assert changes.change(client, "scanner_type", value="RESONANT")["success"] is False
    assert changes.change(client, "scanner_type", value="GALVANO")["success"] is True


def test_the_piezo_has_its_own_travel(client):
    from limits_fixtures import install_permissive_limits

    install_permissive_limits(client)
    limit_rules.set_stage_limits(z_piezo=(-50.0, 50.0))
    assert changes.move_piezo(client, 40.0)["confirmed"] is True
    assert changes.move_piezo(client, 60.0)["success"] is False


def test_the_mapping_is_total_over_the_vocabulary_and_the_installed_policy_is_frozen(
    client,
):
    assert set(limit_rules.SETTER_LIMIT_KEYS) <= set(limit_rules.MUTATING_COMMANDS)
    payload = merged_limits_payload(DEFAULT_STAGE_UM)
    limits = limit_rules.EvidentLimits(
        payload, source="test", path=None, is_fallback=False
    )
    payload["set_scanner_type"] = {"allowed": ["RESONANT"]}
    limits.check(
        "set_scanner_type", {"value": "GALVANO"}
    )  # the later edit does not reach the compiled policy
    with pytest.raises(TypeError):
        limits._policy["x_um"] = None
    assert limits.describe() == {"source": "test", "path": None, "is_fallback": False}
    with pytest.raises(KeyError):
        limits.check_command("no_such_command", {})
    with pytest.raises(gate.LimitsError):
        limit_rules.EvidentLimits(
            {**payload, "set_scanner_type": {"allowed": ["X"]}},
            source="t",
            path=None,
            is_fallback=False,
        ).check("set_scanner_type", {})


def test_check_z_names_the_axes_it_knows():
    limit_rules.set_stage_limits(z=(0.0, 10.0), z_piezo=(-1.0, 1.0))
    with pytest.raises(ValueError):
        limit_rules.check_z(0.0, "galvo")
    with pytest.raises(RuntimeError, match="measured travel"):
        limit_rules.check_z(11.0, "z")
    limit_rules.check_z(0.5, "z_piezo")
    assert math.isfinite(limit_rules.get_stage_limits()["z"][1])
