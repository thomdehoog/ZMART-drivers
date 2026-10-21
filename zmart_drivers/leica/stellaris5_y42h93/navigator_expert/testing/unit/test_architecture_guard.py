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
DISPATCHER = "dispatcher"
# The Leica rulebook defines the check functions; the gate in the dispatcher enforces them.
RULEBOOK = Path("vendor_specific/primitive_handling/settings/limit_rules.py")

# Source selection is policy, not caller behaviour. The read engine implements
# the profile-controlled modes; the confirmation modules construct explicit
# API/log legs that the command policy races. Higher-level code must leave
# ``mode`` unset and consume the configured reader abstraction.
READER_SOURCE_POLICY_MODULES = {
    Path("dispatcher/reader.py"),
    Path("vendor_specific/primitive_handling/readings/state.py"),
    Path("vendor_specific/primitive_handling/readings/sources.py"),
    Path("dispatcher/send_loop.py"),
    Path("dispatcher/confirm_loop.py"),
    Path("vendor_specific/primitive_handling/readings/readings.py"),
    Path("vendor_specific/primitive_handling/confirmations/confirmations.py"),
    Path("vendor_specific/primitive_handling/confirmations/confirm_select_job.py"),
    # Calibration geometry is saved for good, so it is read from the API
    # and never from a log entry that may be stale (review finding M4).
    Path("driver_setup/optical_calibration/calibration_common.py"),
    # The stage limits are saved for good too: the corners are read from the API.
    Path("driver_setup/limits/measure_limits.py"),
    # Whether a command may fire is the API's to decide: the idle check.
    Path("vendor_specific/primitive_handling/profiles/prechecks.py"),
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

    Only the change definitions in ``vendor_specific/primitive_handling/settings/changes.py`` may touch them: that
    is where the limit checks run, immediately before the native call. A
    native-motion reference anywhere else would be a second, unchecked door.
    """
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if "PyApiMoveHardware" in text
        and rel
        not in (
            Path("vendor_specific/primitive_handling/settings/changes.py"),
            # The direct route's table of how each CAM command is written for the hardware
            # server. It sends nothing itself: only the direct transport does, and that is
            # reached through the same change definitions and the same limits gate.
            Path("vendor_specific/primitives/direct/translations.py"),
        )
    ]
    assert offenders == [], (
        f"native stage motion referenced outside vendor_specific/primitive_handling/settings/changes.py: {offenders}; "
        f"route the move through the move_xy / move_z actions instead"
    )


def test_limit_checks_are_called_only_from_the_changes_and_the_dispatcher():
    """``check_xy`` / ``check_z`` are *defined* in the dispatcher's rulebook and
    *called* only from the change definitions in ``vendor_specific/primitive_handling/settings/changes.py``. A call anywhere else
    is a second whistle: a copy of enforcement that can drift out of sync with
    the real one."""
    allowed = {
        Path(
            "vendor_specific/primitive_handling/settings/changes.py"
        ),  # the change definitions: where the checks run
    }
    offenders = []
    for rel, text in _driver_sources():
        if rel.parts[0] == DISPATCHER or rel in allowed or rel == RULEBOOK:
            continue
        if "check_xy(" in text or "check_z(" in text:
            offenders.append(str(rel))
    assert offenders == [], (
        f"limit checks called outside vendor_specific/primitive_handling/settings/changes.py and dispatcher/: {offenders}; "
        f"the changes already check every move; delete the duplicate call"
    )


def test_the_rulebook_lives_in_the_dispatcher():
    """``LeicaLimits`` (the compiled limits document) is defined in
    vendor_specific/primitive_handling/settings/limit_rules.py; the gate only imports it. If the class definition
    moves back into the gate, rulebook and whistle have merged again."""
    checks_text = (
        DRIVER_ROOT
        / "vendor_specific"
        / "primitive_handling"
        / "settings"
        / "limit_rules.py"
    ).read_text(encoding="utf-8")
    gate_text = (DRIVER_ROOT / "dispatcher" / "gate.py").read_text(encoding="utf-8")
    assert "class LeicaLimits" in checks_text
    assert "class LeicaLimits" not in gate_text


def test_reader_source_is_selected_only_by_reader_and_confirmation_policy():
    """Operational callers consume the configured reader policy.

    An adapter, calibration routine, or action that pins
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
        if rel.parts[0] != "vendor_specific"
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
    "vendor_specific": {
        # upward: the readers and the handling notes use the dispatcher's tuning,
        # envelope and read engine
        "dispatcher",
    },
    # The engines know no microscope: the vendor part hands in its sources,
    # profiles, error sorting, waiting numbers and limits.
    "dispatcher": set(),
    # What the driver remembers about one connection; every part that works
    # on a connection reads it, and it uses only the vendor part to ask LAS X.
    "session": {
        "vendor_specific",
    },
    "procedures": {
        "session",
        "vendor_specific",
        "dispatcher",
        "driver_setup",
        "__version__",  # acquisition.py records the driver version in the saved state
    },
    "driver_setup": {
        "session",
        "vendor_specific",  # the limits item validates through the rulebook
        "dispatcher",  # the limits handshake installs the limits in the gate
        # The measuring code of an item takes pictures and moves the stage, so it
        # uses the commands. Run-time procedures never import that measuring code
        # (see the test below): they read the saved values only.
        "procedures",
    },
    # The driver used by itself: connect() and the Microscope, built on every
    # part below it.
    "for_direct_use": {
        "session",
        "vendor_specific",
        "dispatcher",
        "procedures",
        "driver_setup",
    },
    # Everything that faces the controller: the ZmartDriver class, which only
    # translates the controller's commands into calls of the Microscope.
    "for_zmart_controller": {
        "for_direct_use",
    },
    "__init__": {
        "for_direct_use",
        "vendor_specific",
        "dispatcher",
        "procedures",
        "driver_setup",  # the limits handshake is re-exported for the setup notebooks
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
        paths = (
            folder.rglob("*.py") if folder.is_dir() else [DRIVER_ROOT / f"{part}.py"]
        )
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            used = _imported_parts(path, part)
            assert used <= allowed, (
                f"{path.relative_to(DRIVER_ROOT)} imports {sorted(used - allowed)}; "
                f"the anatomy lets {part} import only {sorted(allowed)}"
            )


def test_a_reading_never_uses_a_change():
    """A get never calls a set: vendor_specific/primitive_handling/readings/readings.py must not import vendor_specific/primitive_handling/settings/changes.py."""
    readings = (
        DRIVER_ROOT
        / "vendor_specific"
        / "primitive_handling"
        / "readings"
        / "readings.py"
    )
    tree = ast.parse(readings.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or "", *(a.name for a in node.names)]
            assert "changes" not in names, (
                "vendor_specific/primitive_handling/readings/readings.py imports vendor_specific/primitive_handling/settings/changes.py"
            )


def test_nothing_but_the_tests_imports_from_testing():
    for rel, text in _driver_sources():
        assert "testing" not in _imported_parts(DRIVER_ROOT / rel, rel.parts[0]), str(
            rel
        )


# The measuring code of the setup items. It writes the saved configuration files;
# run-time procedures only read those files, through each item's loader.
MEASURING_MODULES = {
    "origin.set_origin",
    "limits.measure_limits",
    "orientation.measure_orientation",
    "orientation.registration",
    "optical_calibration.calibrate_objective_pair",
    "optical_calibration.calibration_common",
    "optical_calibration.check_calibration",
    "optical_calibration.adopt_calibration",
    "optical_calibration.focus",
}


def _measuring_imports(path: Path) -> set[str]:
    """The measuring modules this file imports, as ``item.module``."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        for name in MEASURING_MODULES:
            item, leaf = name.split(".")
            if module.endswith(f"driver_setup.{item}.{leaf}"):
                found.add(name)
            elif module.endswith(f"driver_setup.{item}") or module == item:
                if any(alias.name == leaf for alias in node.names):
                    found.add(name)
    return found


def test_run_time_procedures_never_import_the_measuring_code():
    """The two sides meet at the saved files in ProgramData, not in each other's code."""
    offenders = [
        f"{path.relative_to(DRIVER_ROOT)} -> {name}"
        for path in sorted((DRIVER_ROOT / "procedures").rglob("*.py"))
        for name in sorted(_measuring_imports(path))
    ]
    assert offenders == [], (
        f"procedures import measuring code: {offenders}; read the saved values "
        "through the item's loader instead"
    )


# --- no loose files -----------------------------------------------------------------


def test_the_driver_keeps_its_files_in_folders_that_say_what_they_are_for():
    """A file left loose in a part's root has no stated job, so the roots stay bare.

    ``procedures/`` holds one folder per recipe, ``vendor_specific/`` holds
    ``primitives/``, ``connection/`` and ``primitive_handling/``, and the
    driver's own root holds only its ``__init__.py`` and README.
    """
    allowed_in_root = {"__init__.py", "README.md"}
    for folder in ("procedures", "vendor_specific"):
        loose = sorted(
            p.name
            for p in (DRIVER_ROOT / folder).glob("*.py")
            if p.name != "__init__.py"
        )
        assert loose == [], (
            f"{folder}/ has loose files {loose}; put each in a folder named for its job"
        )
    root_files = sorted(
        p.name
        for p in DRIVER_ROOT.glob("*")
        if p.is_file() and p.name not in allowed_in_root
    )
    assert root_files == [], f"the driver's root has loose files {root_files}"
