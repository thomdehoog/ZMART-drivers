"""Guard tests for the anatomy of the driver.

The Leica driver's guard, with the names of this driver's parts put in (its
one primitive class is ``rdk``, its rulebook class ``EvidentLimits``); every
rule is the Leica driver's.

The driver is built from the parts of the ZMART driver anatomy
(``docs/driver-anatomy.md`` in this repository): ``methods.py``,
``procedures/``, ``dispatcher/``, ``primitives/``, ``driver_setup/`` and
``zmart_controller_plugin/``. A driver that leaves the anatomy fails these
tests.

These tests read the driver's source files and fail when a module breaks a
rule, so the rule survives as a red test instead of a code-review comment.
If one of these fails, the fix is almost never to edit this file: move the
offending code to the part the anatomy gives it.

- Every stage move goes through the limits gate: only the stage writes name
  the native stage moves, and only the rules check the limits.
- Each part imports only what ``ALLOWED`` lets it: the primitives import only
  the dispatcher, and the dispatcher imports nothing of the driver.
- Every file sits where the anatomy puts it: the root holds the parts and
  nothing else, ``dispatcher/`` holds its four files, ``primitives/`` holds one
  folder per class, each with its profile, and a folder in ``procedures/`` or
  ``primitives/`` holds more than one file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[2]

# The parts that may fire native stage motion or call the limit-check functions.
DISPATCHER = "dispatcher"
# The Evident rulebook defines the check functions; the gate in the dispatcher enforces them.
RULEBOOK = Path("driver_setup/limits/limit_rules.py")

# Which source a reading asks is the read order of the RDK profile, not the
# caller's choice. The read loop, the named readings and the profile name the
# sources; everyone else leaves ``mode`` unset and takes the read order.
READER_SOURCE_POLICY_MODULES = {
    Path("dispatcher/read.py"),
    Path("dispatcher/write.py"),
    Path("procedures/readings.py"),
    Path("primitives/rdk/profile.py"),
}
EXPLICIT_READER_MODE = re.compile(r"\bmode\s*=\s*['\"](?:rdk)['\"]")
# The RDK's reads are reached only through the dispatcher: ``reads.get_xy(client)``
# outside the primitives would be a second, unchecked door to FLUOVIEW.
LOW_LEVEL_READER_CALL = re.compile(r"(?<!\w)(?:rdk_reads|reads)\.get_\w+\(")


def _driver_sources():
    """Yield (relative_path, text) for every non-test driver module."""
    for path in sorted(DRIVER_ROOT.rglob("*.py")):
        rel = path.relative_to(DRIVER_ROOT)
        if rel.parts[0] == "testing" or "__pycache__" in rel.parts:
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_native_stage_motion_is_named_only_by_the_stage_writes():
    """FLUOVIEW's stage settings are the stage's real door.

    Only the stage moves in ``primitives/rdk/writes.py`` may name them (and
    the mock, which answers them): they are run only through the dispatcher's
    ``run()``, which asks the limits before the request is made. A reference
    anywhere else would be a second, unchecked door.
    """
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if (
            "XY_STAGE_POSITION_SETTING" in text
            or "Z_STAGE_POSITION_SETTING" in text
            or "LSM_Z_PIEZO_POSITION_SETTING" in text
        )
        and rel
        not in (Path("primitives/rdk/writes.py"), Path("primitives/rdk/reads.py"))
    ]
    assert offenders == [], (
        f"native stage motion referenced outside primitives/rdk/writes.py: {offenders}; "
        f"route the move through the named changes move_xy / move_z instead"
    )


def test_limit_checks_are_called_only_from_the_rules():
    """``check_xy`` / ``check_z`` are defined in the rulebook and called only by
    its own rules, which the dispatcher's gate asks before anything is sent. A
    call anywhere else is a second whistle: a copy of enforcement that can
    drift out of sync with the real one."""
    offenders = []
    for rel, text in _driver_sources():
        if rel.parts[0] == DISPATCHER or rel == RULEBOOK:
            continue
        if "check_xy(" in text or "check_z(" in text:
            offenders.append(str(rel))
    assert offenders == [], (
        f"limit checks called outside the rulebook: {offenders}; the dispatcher "
        f"already asks the limits for every change; delete the duplicate call"
    )


def test_the_rulebook_lives_in_driver_setup():
    """``EvidentLimits`` (the compiled limits document) is defined in
    driver_setup/limits/limit_rules.py; the gate only imports it. If the class definition
    moves back into the gate, rulebook and whistle have merged again."""
    checks_text = (DRIVER_ROOT / RULEBOOK).read_text(encoding="utf-8")
    gate_text = (DRIVER_ROOT / "dispatcher" / "limits.py").read_text(encoding="utf-8")
    assert "class EvidentLimits" in checks_text
    assert "class EvidentLimits" not in gate_text


def test_reader_source_is_selected_only_by_reader_and_confirmation_policy():
    """Callers take the read order of the direct profile.

    A procedure, a calibration routine or a method that pins
    ``rdk`` silently defeats the profile's read order.
    Explicit modes belong only to the modules listed in
    ``READER_SOURCE_POLICY_MODULES``.
    """
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel not in READER_SOURCE_POLICY_MODULES and EXPLICIT_READER_MODE.search(text)
    ]
    assert offenders == [], (
        f"reader source pinned outside policy layer: {offenders}; remove the "
        "mode override and let the read order of the profile choose"
    )


def test_low_level_readers_are_called_only_by_reader_and_confirmation_policy():
    """Outside the primitives, a class's reads are reached only through ``dispatcher.run()``."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel.parts[0] != "primitives"
        and rel not in READER_SOURCE_POLICY_MODULES
        and LOW_LEVEL_READER_CALL.search(text)
    ]
    assert offenders == [], (
        f"low-level reader called outside policy layer: {offenders}; route the "
        "read through dispatcher.run instead"
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

# The parts of the anatomy, and what each may import. A part may always import
# from itself.
ALLOWED = {
    # One folder per class of primitive: its transfer, reads, writes and profile.
    "primitives": {
        "dispatcher",  # the Reading, the dialogs around a delivery, the transport chain
    },
    # Runs one primitive; the same in every driver, and knows no microscope.
    "dispatcher": set(),
    # Chains of steps; most run their primitives through dispatcher.run().
    "procedures": {
        "primitives",
        "dispatcher",
        "driver_setup",
        "__version__",  # the saved state records the driver version
    },
    # What the operator sets up once: limits, origin, orientation, calibration, data location.
    "driver_setup": {
        "primitives",
        "dispatcher",
        # The measuring code of an item takes pictures and moves the stage, so it
        # uses the procedures. Run-time procedures never import that measuring
        # code (see the test below): they read the saved values only.
        "procedures",
    },
    # What the user calls: connect() and the Microscope.
    "methods": {
        "primitives",
        "dispatcher",
        "procedures",
        "driver_setup",
    },
    # What faces the controller: the ZmartControllerPlugin class, and the
    # description and procedure menu only the controller asks for.
    "zmart_controller_plugin": {
        "methods",
        "procedures",
        "driver_setup",
        "dispatcher",
    },
    # The package offers the driver used by itself, and nothing else.
    "__init__": {"methods"},
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
            elif "rdk." in module:
                found.add(module.split("rdk.")[1].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "rdk." in alias.name:
                    found.add(alias.name.split("rdk.")[1].split(".")[0])
    return found - {part}


# The setup wizard sits in driver_setup/ but is an operator tool above the driver:
# it connects to the microscope the way a user does, through methods.connect(),
# and names the driver's version among what Connect found. It is the only part of
# driver_setup/ allowed to import methods and the version.
WIZARD = Path("driver_setup/wizard")


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
            if WIZARD in path.relative_to(DRIVER_ROOT).parents:
                used -= {"methods", "__version__"}
            assert used <= allowed, (
                f"{path.relative_to(DRIVER_ROOT)} imports {sorted(used - allowed)}; "
                f"the anatomy lets {part} import only {sorted(allowed)}"
            )


def test_a_reading_never_uses_a_change():
    """A get never calls a set: no class profile (the table of readings among them) imports a procedure, the named changes among them."""
    profiles = sorted((DRIVER_ROOT / "primitives").glob("*/profile.py"))
    assert DRIVER_ROOT / "primitives" / "rdk" / "profile.py" in profiles
    for path in profiles:
        assert "procedures" not in _imported_parts(path, "primitives"), (
            f"{path.relative_to(DRIVER_ROOT)} imports a procedure"
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


# --- every file where the anatomy puts it -------------------------------------------

#: The folders of the anatomy, at the driver's root.
ANATOMY_FOLDERS = {
    "procedures",
    "dispatcher",
    "primitives",
    "driver_setup",
    "zmart_controller_plugin",
    "testing",
}
#: The files of the anatomy, at the driver's root.
ANATOMY_FILES = {"__init__.py", "methods.py", "README.md"}
#: The dispatcher's files: the same in every driver.
DISPATCHER_FILES = {"__init__.py", "dispatch.py", "read.py", "write.py", "limits.py"}


def _modules(folder: Path) -> list[str]:
    """The Python files directly in *folder*, without ``__init__.py``."""
    return sorted(p.name for p in folder.glob("*.py") if p.name != "__init__.py")


def _python_folders(folder: Path) -> list[Path]:
    """The folders under *folder*, itself included, that hold Python files."""
    return sorted(
        {p.parent for p in folder.rglob("*.py") if "__pycache__" not in p.parts}
    )


def test_the_driver_keeps_its_files_where_the_anatomy_puts_them():
    """The root holds the anatomy's parts and nothing else; ``dispatcher/`` its
    four files; ``primitives/`` one folder per class, each with its profile;
    and a folder in ``procedures/`` or ``primitives/`` more than one file."""
    root_files = sorted(
        p.name
        for p in DRIVER_ROOT.glob("*")
        if p.is_file() and p.name not in ANATOMY_FILES
    )
    assert root_files == [], f"the driver's root has loose files {root_files}"
    root_folders = sorted(
        p.name
        for p in DRIVER_ROOT.glob("*")
        if p.is_dir() and _python_folders(p) and p.name not in ANATOMY_FOLDERS
    )
    assert root_folders == [], (
        f"the driver's root has folders outside the anatomy {root_folders}; "
        f"the parts are {sorted(ANATOMY_FOLDERS)}"
    )

    dispatcher = DRIVER_ROOT / "dispatcher"
    assert _python_folders(dispatcher) == [dispatcher], "dispatcher/ has a subfolder"
    files = {p.name for p in dispatcher.glob("*.py")}
    assert files == DISPATCHER_FILES, (
        f"dispatcher/ holds {sorted(files)}; the anatomy's dispatcher is {sorted(DISPATCHER_FILES)}"
    )

    primitives = DRIVER_ROOT / "primitives"
    loose = _modules(primitives)
    assert loose == [], (
        f"primitives/ has loose files {loose}; each belongs in its class's folder"
    )
    classes = [p for p in _python_folders(primitives) if p.parent == primitives]
    assert classes, "primitives/ has no class"
    without_profile = sorted(
        p.name for p in classes if not (p / "profile.py").is_file()
    )
    assert without_profile == [], (
        f"primitive classes without a profile.py: {without_profile}"
    )

    single = sorted(
        str(folder.relative_to(DRIVER_ROOT))
        for part in ("procedures", "primitives")
        for folder in _python_folders(DRIVER_ROOT / part)
        if folder != DRIVER_ROOT / part and len(_modules(folder)) < 2
    )
    assert single == [], (
        f"folders with fewer than two files {single}; a folder only where there is more than one"
    )


def test_nothing_in_the_driver_imports_the_setup_wizard():
    """The wizard calls the setup items; no item, procedure or method calls it back."""
    for rel, text in _driver_sources():
        if WIZARD in rel.parents or rel.parts[0] == "testing":
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(a.name for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            assert not any(
                n == "wizard" or n.endswith(".wizard") or ".wizard." in n for n in names
            ), str(rel)
