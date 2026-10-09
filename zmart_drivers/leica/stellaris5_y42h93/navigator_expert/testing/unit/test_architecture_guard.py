"""Guard tests for the driver's layering.

The driver is built from the parts of the ZMART driver anatomy
(``docs/driver-anatomy.md`` in this repository). Two sentences carry the
safety of the whole driver: every stage move goes through the limits gate,
and nothing above the actions checks limits or moves the stage itself.

These tests read the driver's source files and fail when a new module
breaks a rule, so the rule survives as a red test instead of a code-review
comment. If one of these fails, the fix is almost never to edit this file:
move the offending call down into ``actions/`` or ``dispatcher/`` instead.

The last test records which parts import which. Today the Leica driver still
has imports that run upward against the anatomy (the dispatcher reading the
actions' table, the vendor interface reading the tuning, the limits item
asking the rulebook). They are listed in ``ALLOWED`` so that the move that
put every file in its part stays honest about them, and so that no new
upward import can appear unnoticed. Each entry marked ``# upward`` is one to
remove, in a change of its own.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[2]

# The parts that may fire native stage motion or call the limit-check functions.
ACTIONS = "actions"
DISPATCHER = "dispatcher"

# Source selection is policy, not caller behaviour. The read engine implements
# the profile-controlled modes; the confirmation modules construct explicit
# API/log legs that the command policy races. Higher-level code must leave
# ``mode`` unset and consume the configured reader abstraction.
READER_SOURCE_POLICY_MODULES = {
    Path("dispatcher/read.py"),
    Path("dispatcher/change.py"),
    Path("actions/get.py"),
    Path("actions/confirmations.py"),
    Path("actions/confirm_select_job.py"),
    # Calibration geometry is saved for good, so it is read from the API
    # and never from a log entry that may be stale (review finding M4).
    Path("procedures/calibration_common.py"),
}
EXPLICIT_READER_MODE = re.compile(r"\bmode\s*=\s*['\"](?:api|log|hybrid)['\"]")
LOW_LEVEL_READER_CALL = re.compile(r"(?<!\w)_?(?:api_reader|log_reader)\.")


def _driver_sources():
    """Yield (relative_path, text) for every non-test driver module."""
    for path in sorted(DRIVER_ROOT.rglob("*.py")):
        rel = path.relative_to(DRIVER_ROOT)
        if rel.parts[0] == "testing" or "__pycache__" in rel.parts:
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_native_stage_motion_fires_only_from_the_actions():
    """The native CAM motion functions are the stage's real door.

    Only the change definitions in ``actions/set.py`` may touch them: that
    is where the limit checks run, immediately before the native call. A
    native-motion reference anywhere else would be a second, unchecked door.
    """
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if "PyApiMoveHardware" in text and rel != Path("actions/set.py")
    ]
    assert offenders == [], (
        f"native stage motion referenced outside actions/set.py: {offenders}; "
        f"route the move through the move_xy / move_z actions instead"
    )


def test_limit_checks_are_called_only_from_the_actions_and_the_dispatcher():
    """``check_xy`` / ``check_z`` are *defined* in the dispatcher's rulebook and
    *called* only from the actions (and the backlash procedure, which composes
    checked moves). A call anywhere else is a second whistle: a copy of
    enforcement that can drift out of sync with the real one."""
    allowed = {Path("procedures/backlash.py")}  # upward: composes checked moves
    offenders = []
    for rel, text in _driver_sources():
        if rel.parts[0] in (ACTIONS, DISPATCHER) or rel in allowed:
            continue
        if "check_xy(" in text or "check_z(" in text:
            offenders.append(str(rel))
    assert offenders == [], (
        f"limit checks called outside actions/ and dispatcher/: {offenders}; the "
        f"actions already check every move; delete the duplicate call"
    )


def test_the_rulebook_lives_in_the_dispatcher():
    """``LeicaLimits`` (the compiled limits document) is defined in
    dispatcher/checks.py; the gate only imports it. If the class definition
    moves back into the gate, rulebook and whistle have merged again."""
    checks_text = (DRIVER_ROOT / "dispatcher" / "checks.py").read_text(encoding="utf-8")
    gate_text = (DRIVER_ROOT / "dispatcher" / "gate.py").read_text(encoding="utf-8")
    assert "class LeicaLimits" in checks_text
    assert "class LeicaLimits" not in gate_text


def test_reader_source_is_selected_only_by_reader_and_confirmation_policy():
    """Operational callers consume the configured reader policy.

    An adapter, calibration routine, scanfield parser, or action that pins
    ``api``/``log``/``hybrid`` silently defeats ``StateReaderProfile``.
    Explicit modes belong only to the read engine and to confirmation-policy
    code constructing the individual legs of a configured race.
    """
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel not in READER_SOURCE_POLICY_MODULES and EXPLICIT_READER_MODE.search(text)
    ]
    assert offenders == [], (
        f"reader source pinned outside policy layer: {offenders}; remove the "
        "mode override and let StateReaderProfile/capabilities route the datum"
    )


def test_low_level_readers_are_called_only_by_reader_and_confirmation_policy():
    """Operational callers cannot bypass the routed reader API."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel.parts[0] != "vendor_interface"
        and rel not in READER_SOURCE_POLICY_MODULES
        and LOW_LEVEL_READER_CALL.search(text)
    ]
    assert offenders == [], (
        f"low-level reader called outside policy layer: {offenders}; route the "
        "read through dispatcher.read instead"
    )


def test_the_utils_grab_bag_stays_dissolved():
    """utils.py was dissolved on 2026-07-19 (each function moved to its
    natural owner). A folder called "utils" promises nothing and therefore
    accumulates everything; this test keeps the grab-bag from quietly coming
    back."""
    assert not (DRIVER_ROOT / "utils.py").exists(), (
        "utils.py has reappeared; give each function a truthful home instead"
    )


# --- the parts, and which may import which ----------------------------------------

# The anatomy's order, bottom to top. A part may always import from itself.
# Entries marked "upward" are imports the move kept as they were; each is to be
# removed in a change of its own, and none may be added.
ALLOWED = {
    "vendor_interface": {
        "dispatcher",  # upward: api_reader reads the tuning, errors reads the envelope
        "actions",  # upward: api_reader reads the profiles
    },
    "dispatcher": {
        "vendor_interface",
        "configuration",  # upward: the gate loads the limits item at connect
        "actions",  # upward: the read engine reads the actions' table of readings
    },
    "actions": {
        "vendor_interface",
        "dispatcher",
        "configuration",
        "scanfields",  # upward: select_job strips scan fields
        "experimental",  # upward: move_galvo_to_pixel uses the LRP edits
    },
    "procedures": {"vendor_interface", "dispatcher", "actions", "configuration", "output", "scanfields"},
    "output": {"vendor_interface", "dispatcher", "actions", "configuration"},
    "configuration": {
        "dispatcher",  # upward: the limits item validates through the rulebook
    },
    "scanfields": {"vendor_interface", "dispatcher", "output"},
    "experimental": {"scanfields"},
    "connect": {"vendor_interface", "dispatcher", "actions", "configuration"},
    "zmart_adapter": {
        "vendor_interface", "dispatcher", "actions", "procedures", "output",
        "configuration", "scanfields", "connect", "__version__",
    },
    "zmart_controller_plugin": {"zmart_adapter"},
    "__init__": {
        "vendor_interface", "dispatcher", "actions", "output", "scanfields",
        "experimental", "connect",
    },
}


def _imported_parts(path: Path, part: str) -> set[str]:
    """The driver's parts that ``path`` imports from, wherever in its part it sits."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    here = list(path.relative_to(DRIVER_ROOT).parent.parts)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = here[: len(here) - (node.level - 1)]
                target = [*base, *module.split(".")] if module else base
                if target:
                    found.add(target[0])
                else:
                    found.update(a.name for a in node.names)
            elif "navigator_expert." in module:
                found.add(module.split("navigator_expert.")[1].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "navigator_expert." in alias.name:
                    found.add(alias.name.split("navigator_expert.")[1].split(".")[0])
    return found - {part}


def test_each_part_only_imports_what_the_anatomy_allows():
    for part, allowed in ALLOWED.items():
        folder = DRIVER_ROOT / part
        paths = folder.rglob("*.py") if folder.is_dir() else [DRIVER_ROOT / f"{part}.py"]
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            used = _imported_parts(path, part)
            assert used <= allowed, (
                f"{path.relative_to(DRIVER_ROOT)} imports {sorted(used - allowed)}; "
                f"the anatomy lets {part} import only {sorted(allowed)}"
            )


def test_a_reading_never_uses_a_change():
    """A get never calls a set: actions/get.py must not import actions/set.py."""
    tree = ast.parse((DRIVER_ROOT / "actions" / "get.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or "", *(a.name for a in node.names)]
            assert "set" not in names, "actions/get.py imports actions/set.py"


def test_nothing_but_the_tests_imports_from_testing():
    for rel, text in _driver_sources():
        assert "testing" not in _imported_parts(DRIVER_ROOT / rel, rel.parts[0]), str(rel)
