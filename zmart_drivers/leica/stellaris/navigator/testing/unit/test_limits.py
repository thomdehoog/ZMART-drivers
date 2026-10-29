"""The limits self-check: the gate refuses before anything is sent, and every change goes through it.

``run_ci.py --hardware`` runs this file first and refuses to touch the
STELLARIS when any of it fails. It proves four things with no hardware at
all: the physical backstop holds whatever the configured envelope says, a
tile scan's every position is checked, the five settings are checked by
their own keys in ``limits.json``, and every change the driver can build is
known to the rulebook (``MUTATING_COMMANDS``), so none can slip past.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest
from limits_fixtures import (
    DEFAULT_STAGE_UM,
    install_permissive_limits,
    merged_limits_payload,
    provision_machine_limits,
)

from zmart_drivers.leica.stellaris.navigator.dispatcher import dispatch as dispatcher
from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as gate
from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import (
    config as limits_config,
)
from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import (
    handshake,
    limit_rules,
)
from zmart_drivers.leica.stellaris.navigator.driver_setup.store import MachineProfile
from zmart_drivers.leica.stellaris.navigator.primitives.direct import profile, writes

DRIVER_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client():
    client = object()
    install_permissive_limits(client, wide_stage=True)
    yield client
    gate.uninstall(client)


class TestTheBackstop:
    def test_a_move_past_the_physical_travel_is_refused_whatever_the_envelope_says(self, client):
        refusal = gate.check_refusal(client, "move_xy", {"x_um": 500_000.0, "y_um": 10.0})
        assert refusal is not None and "backstop" in refusal
        refusal = gate.check_refusal(client, "move_z", {"z_wide_um": 9_000.0})
        assert refusal is not None and "backstop" in refusal
        assert gate.check_refusal(client, "move_xy", {"x_um": 100.0, "y_um": 10.0}) is None

    @pytest.mark.parametrize("bad", [math.nan, math.inf, None, "100", True])
    def test_a_target_that_is_not_a_finite_number_is_refused(self, client, bad):
        refusal = gate.check_refusal(client, "move_xy", {"x_um": bad, "y_um": 10.0})
        assert refusal is not None and "refused" in refusal

    def test_an_envelope_wider_than_the_backstop_is_not_accepted(self):
        with pytest.raises(RuntimeError, match="outside the physical backstop"):
            limit_rules.check_envelope_within_backstop({**DEFAULT_STAGE_UM, "x": [0.0, 200_000.0]})

    def test_a_hand_widened_limits_file_is_replaced_by_the_defaults_not_honoured(self, tmp_path):
        root = tmp_path / "machine"
        provision_machine_limits(root, stage_um={**DEFAULT_STAGE_UM, "x": [0.0, 200_000.0]})
        client = object()
        try:
            status = handshake.connect_handshake(client, machine=MachineProfile(programdata_root=root))
            assert status.ok and status.limits.is_fallback is True
            refusal = gate.check_refusal(client, "move_xy", {"x_um": 150_000.0, "y_um": 10.0})
            assert refusal is not None and "refused" in refusal
        finally:
            gate.uninstall(client)


class TestWithoutAHandshake:
    def test_a_client_with_no_limits_is_refused_and_nothing_is_made(self):
        client = object()
        write = writes.move_xy(0.0, 0.0)

        def never(client, write):
            raise AssertionError("a refused change must never make its request")

        vendor = type("Vendor", (), {"limits_setup_hint": "the limits notebook", "make_request": staticmethod(never), "WRITING": None})
        result = dispatcher.run(vendor, client, write)

        assert result["success"] is False and "refused" in result["message"]
        assert result["timing"]["attempts"] == 0
        assert profile.limits_setup_hint in gate.check_refusal(client, "move_xy", {}, profile.limits_setup_hint)


class TestATileScan:
    def test_every_position_is_checked_and_one_outside_refuses_the_whole_scan(self, client):
        inside = [(63500.0, 41500.0, 0.0), (64100.0, 41500.0, 0.0)]
        assert gate.check_refusal(client, "tile_scan", {"positions_um": inside, "project": 1}) is None
        refusal = gate.check_refusal(client, "tile_scan", {"positions_um": [*inside, (63500.0, 41500.0, 400.0)], "project": 1})
        assert refusal is not None and "galvo" in refusal

    @pytest.mark.parametrize("positions", [[], None, [(1.0, 2.0)], "63500,41500,0"])
    def test_positions_that_are_not_a_list_of_xyz_are_refused(self, client, positions):
        assert gate.check_refusal(client, "tile_scan", {"positions_um": positions}) is not None

    def test_the_put_back_after_a_tile_scan_is_a_stage_move_too(self, client):
        refusal = gate.check_refusal(client, "after_tile_scan", {"x_um": 500_000.0, "y_um": 10.0, "z_galvo_um": 0.0})
        assert refusal is not None and "backstop" in refusal


class TestTheFiveSettings:
    def test_each_setting_has_its_own_key_in_the_bundled_limits(self):
        bundled = json.loads((DRIVER_ROOT / "driver_setup" / "limits" / "defaults" / "limits.json").read_text())
        assert set(limit_rules.SETTER_LIMIT_KEYS) <= set(bundled)
        assert set(limit_rules.SETTER_LIMIT_KEYS) == {f"set_{name}" for name in writes.SETTINGS}

    @pytest.mark.parametrize("name", list(writes.SETTINGS))
    def test_a_ranged_setting_is_refused_outside_its_range_and_passed_inside(self, name):
        payload = merged_limits_payload(
            {k: [-1e9, 1e9] for k in DEFAULT_STAGE_UM}, functions={f"set_{name}": {"range": [1, 4]}}
        )
        limits = limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False)
        client = object()
        gate.install(client, gate.GateState(limits=limits, stage_cfg=None, error=None))
        try:
            assert gate.check_refusal(client, f"set_{name}", {"value": 2}) is None
            assert "outside range" in gate.check_refusal(client, f"set_{name}", {"value": 8})
            assert "no value" in gate.check_refusal(client, f"set_{name}", {})
        finally:
            gate.uninstall(client)

    def test_an_allow_list_setting_takes_only_what_it_lists(self):
        payload = merged_limits_payload({k: [-1e9, 1e9] for k in DEFAULT_STAGE_UM}, functions={"set_line_average": {"values": [1, 2, 4]}})
        limits = limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False)
        client = object()
        gate.install(client, gate.GateState(limits=limits, stage_cfg=None, error=None))
        try:
            assert gate.check_refusal(client, "set_line_average", {"value": 2}) is None
            assert "not allowed" in gate.check_refusal(client, "set_line_average", {"value": 3})
        finally:
            gate.uninstall(client)

    def test_the_limits_file_keys_are_exactly_what_the_rulebook_knows(self):
        loaded = limits_config.load()
        assert set(loaded["policy"]) == {"x_um", "y_um", "z_galvo_um", "z_wide_um", "objective_slot", *limit_rules.SETTER_LIMIT_KEYS}


class TestEveryChangeIsKnown:
    def test_every_write_the_driver_builds_names_a_command_the_rulebook_knows(self):
        """Each ``Write(...)`` in writes.py has command= and values=, and the command is in MUTATING_COMMANDS."""
        source = (DRIVER_ROOT / "primitives" / "direct" / "writes.py").read_text(encoding="utf-8")
        commands = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Write":
                keywords = {k.arg: k.value for k in node.keywords}
                assert {"command", "values"} <= set(keywords), f"writes.py line {node.lineno}: Write needs command= and values="
                if isinstance(keywords["command"], ast.Constant):
                    commands.add(keywords["command"].value)
        # The settings build their command from the setting's name.
        commands |= {f"set_{name}" for name in writes.SETTINGS}
        assert commands >= {"move_xy", "move_z", "set_objective", "capture", "tile_scan", "after_tile_scan"}
        assert commands <= set(limit_rules.MUTATING_COMMANDS), commands - set(limit_rules.MUTATING_COMMANDS)

    @pytest.mark.parametrize("make", [
        lambda: writes.setting("zoom", 2.0),
        lambda: writes.move_xy(1.0, 2.0),
        lambda: writes.move_z("z-wide", 10.0, now=lambda: 0.0),
        lambda: writes.set_objective(1, "20x"),
        lambda: writes.capture(5),
        lambda: writes.tile_scan([(1.0, 2.0, 0.0)], 5),
        lambda: writes.after_tile_scan(1.0, 2.0, 0.0),
    ])
    def test_a_built_change_passes_the_permissive_gate_by_its_own_command(self, client, make):
        write = make()
        assert gate.check_refusal(client, write.command, write.values) is None

    def test_an_unknown_command_is_a_mistake_in_the_driver_not_a_refusal(self, client):
        limits = gate.state_of(client).limits
        with pytest.raises(KeyError):
            limits.check_command("open_shutter", {})
