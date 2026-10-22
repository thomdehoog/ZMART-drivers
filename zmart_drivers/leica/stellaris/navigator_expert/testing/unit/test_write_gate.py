"""The dispatcher's write asks the limits gate, once, so no change skips it.

It asks before the request is even made, so a refused change never touches
LAS X's client. The rules it asks hold the stage's physical backstop too.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import dispatch as dispatcher

DRIVER_ROOT = Path(__file__).resolve().parents[2]


def test_write_refuses_a_client_with_no_limits_and_sends_nothing():
    api_obj = SimpleNamespace()  # touching it in any way would raise an error
    client = object()  # never shook hands with the gate

    result = dispatcher.write(
        client,
        api_obj,
        "Move",
        command="move_xy",
        values={"x_um": 0.0, "y_um": 0.0},
        profile=None,
        setup_fn=None,
    )

    assert result["success"] is False
    assert "refused" in result["message"]
    assert result["timing"]["attempts"] == 0


def test_every_change_hands_its_name_and_values_to_the_dispatcher():
    """Each call to ``dispatch_change`` says which command it is and what it sets."""
    sources = [
        DRIVER_ROOT / "operations" / "changes" / f"{name}.py"
        for name in ("stage", "objective", "select_job", "acquire", "job_settings")
    ] + [
        DRIVER_ROOT
        / "procedures"
        / "keep_in_view.py",
    ]
    calls = 0
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            if name == "dispatch_change":
                calls += 1
                keywords = {k.arg for k in node.keywords}
                assert {"command", "values"} <= keywords, (
                    f"{path.name} line {node.lineno}: dispatch_change needs command= and values="
                )
    assert calls >= 6


def test_the_gate_holds_the_physical_backstop():
    """A move past the stage's physical travel is refused by the gate itself,
    however wide the configured envelope is."""
    from limits_fixtures import install_permissive_limits

    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as gate

    client = object()
    install_permissive_limits(client, wide_stage=True)
    try:
        refusal = gate.check_refusal(client, "move_xy", {"x_um": 500_000.0, "y_um": 10.0})
        assert refusal is not None and "backstop" in refusal
        refusal = gate.check_refusal(client, "move_z", {"z_wide_um": 9_000.0})
        assert refusal is not None and "backstop" in refusal
        assert gate.check_refusal(client, "move_xy", {"x_um": 100.0, "y_um": 10.0}) is None
    finally:
        gate.uninstall(client)


def test_a_change_asks_the_limits_once(monkeypatch):
    """Only the dispatcher asks the limits, once for each change sent."""
    from limits_fixtures import install_permissive_limits
    from mock_lasx_api import MockLasxClient

    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as gate
    from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import stage

    asked = []
    real = gate.check_refusal
    monkeypatch.setattr(gate, "check_refusal", lambda *a, **k: asked.append(a[1]) or real(*a, **k))
    client = MockLasxClient(latency=0.0)
    install_permissive_limits(client, wide_stage=True)
    try:
        result = stage.move_xy(client, 100.0, 200.0)
    finally:
        gate.uninstall(client)

    assert result["success"] is True
    assert asked == ["move_xy"]


def test_a_refused_stage_move_keeps_its_shape():
    """Refused by the limits, a stage move sends nothing and has no position."""
    from limits_fixtures import install_permissive_limits

    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as gate
    from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import stage

    client = SimpleNamespace(PyApiMoveHardwareXY=SimpleNamespace())  # sending would touch it
    install_permissive_limits(client, wide_stage=True)
    try:
        result = stage.move_xy(client, 500_000.0, 10.0)
    finally:
        gate.uninstall(client)

    assert result["success"] is False and "backstop" in result["message"]
    assert result["position"] is None
    assert result["timing"]["attempts"] == 0
