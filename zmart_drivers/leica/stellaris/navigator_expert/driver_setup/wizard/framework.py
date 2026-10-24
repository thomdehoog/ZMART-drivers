"""The microscope-independent core of a setup wizard.

A wizard is an ordered list of steps. A step has a title, a few plain
sentences saying what it is for, and a set of *actions*: things the page can
ask the driver to do, such as "record corner 1" or "save the limits". An
action takes a small dictionary of values and answers with a dictionary the
page can show.

When an action fails, the wizard does not crash. It answers with the reason in
words, so the operator can fix the cause (for example, a stage that is still
moving) and press the button again.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Action = Callable[[dict], dict]


class StepError(Exception):
    """A problem the operator can fix. The message is shown to them as written."""


@dataclass
class Step:
    """One numbered step in the wizard.

    ``status`` is asked for when the page opens, and answers what is already
    set up on this computer, so a microscope that was set up before shows its
    steps as done instead of starting from nothing.
    """

    id: str
    title: str
    purpose: str
    actions: dict[str, Action] = field(default_factory=dict)
    status: Callable[[], dict] = lambda: {"done": False}


class Wizard:
    """The steps, and the rule that only one action talks to the microscope at a time."""

    def __init__(self, title: str, steps: list[Step]):
        self.title = title
        self.steps = steps
        self._by_id = {step.id: step for step in steps}
        # A microscope answers one request at a time; two buttons pressed
        # together must wait for each other.
        self._busy = threading.Lock()

    def outline(self) -> dict:
        """What the page needs to draw itself: the title, and each step with its status."""
        out = []
        for step in self.steps:
            try:
                status = step.status()
            except Exception as why:  # a status that cannot be read is shown, not fatal
                status = {"done": False, "note": f"Could not read what is set up: {why}"}
            out.append(
                {"id": step.id, "title": step.title, "purpose": step.purpose, "status": status}
            )
        return {"title": self.title, "steps": out}

    def call(self, step_id: str, action: str, values: dict | None = None) -> dict:
        """Run one action. Answers ``{"ok": True, "result": ...}`` or ``{"ok": False, "error": ...}``."""
        step = self._by_id.get(step_id)
        if step is None or action not in step.actions:
            return {"ok": False, "error": f"There is no '{action}' in step '{step_id}'."}
        if not self._busy.acquire(blocking=False):
            return {
                "ok": False,
                "error": "The microscope is still busy with the previous action. "
                "Wait for it to finish and try again.",
            }
        try:
            return {"ok": True, "result": step.actions[action](dict(values or {}))}
        except StepError as why:
            return {"ok": False, "error": str(why)}
        except Exception as why:
            return {"ok": False, "error": f"{type(why).__name__}: {why}"}
        finally:
            self._busy.release()
