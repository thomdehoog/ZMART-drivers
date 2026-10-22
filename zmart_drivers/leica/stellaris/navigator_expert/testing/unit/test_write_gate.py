"""The dispatcher's write asks the limits gate itself, so no change skips it.

The wrappers also check early, to refuse before doing any other work. These
tests cover the last line of defence: even a caller that forgot its own check
cannot send a change the limits refuse.
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
