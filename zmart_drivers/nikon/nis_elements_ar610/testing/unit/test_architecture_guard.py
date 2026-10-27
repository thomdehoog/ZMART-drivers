"""Guard tests for the anatomy of the driver.

The driver is built from the parts of the ZMART driver anatomy
(``docs/driver-anatomy.md`` in this repository). These tests read the
driver's source files and fail when a module breaks a rule, so the rule
survives as a red test instead of a code-review comment. If one fails, the
fix is almost never to edit this file: move the offending code to the part
the anatomy gives it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

DRIVER_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_TAIL = "nis_elements_ar610."

#: The rulebook defines the limits; the gate in the dispatcher only asks it.
RULEBOOK = Path("driver_setup/limits/limit_rules.py")

#: Outside the primitives, a class's reads are reached only through the dispatcher's run(),
#: and only the named readings may name them (the ping, which is not a reading, among them).
LOW_LEVEL_READER_CALL = re.compile(r"(?<!\w)(?:dll_reads|macro_reads)\.")
READER_POLICY_MODULES = {
    Path("procedures/readings.py"),
    Path("primitives/dll/profile.py"),
    Path("primitives/macro/profile.py"),
}

#: The parts of the anatomy, and what each may import. A part may always import from itself.
ALLOWED = {
    "primitives": {"dispatcher"},
    "dispatcher": set(),
    "procedures": {"primitives", "dispatcher", "driver_setup", "__version__"},
    "driver_setup": {"primitives", "dispatcher", "procedures"},
    "methods": {"primitives", "dispatcher", "procedures", "driver_setup"},
    "zmart_controller_plugin": {
        "methods",
        "procedures",
        "driver_setup",
        "dispatcher",
        "primitives",
    },
    "__init__": {"methods"},
}

ANATOMY_FOLDERS = {
    "procedures",
    "dispatcher",
    "primitives",
    "driver_setup",
    "zmart_controller_plugin",
    "testing",
}
ANATOMY_FILES = {"__init__.py", "methods.py", "README.md"}
DISPATCHER_FILES = {"__init__.py", "dispatch.py", "read.py", "write.py", "limits.py"}

#: The measuring code of the setup items; run-time procedures only read the saved files.
MEASURING_MODULES = {
    "origin.set_origin",
    "limits.measure_limits",
    "orientation.measure_orientation",
    "optical_calibration.calibrate_objective_pair",
}


def _driver_sources():
    for path in sorted(DRIVER_ROOT.rglob("*.py")):
        rel = path.relative_to(DRIVER_ROOT)
        if (
            rel.parts[0] == "testing"
            or "__pycache__" in rel.parts
            or "notebooks" in rel.parts
        ):
            continue
        yield rel, path.read_text(encoding="utf-8")


def _imported_parts(path: Path, part: str) -> set[str]:
    """The driver's parts that ``path`` imports from."""
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
            elif PACKAGE_TAIL in module:
                found.add(module.split(PACKAGE_TAIL)[1].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if PACKAGE_TAIL in alias.name:
                    found.add(alias.name.split(PACKAGE_TAIL)[1].split(".")[0])
    return found - {part}


def test_the_dispatcher_is_the_same_as_the_leica_drivers():
    """The dispatcher is the same in every driver: this one is a copy of the Leica driver's, unchanged."""
    leica = (
        DRIVER_ROOT.parents[1]
        / "leica"
        / "stellaris"
        / "navigator_expert"
        / "dispatcher"
    )
    if not leica.is_dir():
        pytest.skip("the Leica driver is not in this checkout")
    for name in DISPATCHER_FILES:
        assert (DRIVER_ROOT / "dispatcher" / name).read_bytes() == (
            leica / name
        ).read_bytes(), f"dispatcher/{name} differs"


def test_the_rulebook_lives_in_driver_setup():
    assert "class NikonLimits" in (DRIVER_ROOT / RULEBOOK).read_text(encoding="utf-8")
    assert "NikonLimits" not in (DRIVER_ROOT / "dispatcher" / "limits.py").read_text(
        encoding="utf-8"
    )


def test_limit_checks_are_made_only_by_the_rulebook():
    """``check_stage`` and ``check_setting`` are the rulebook's; nothing else may call them."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel != RULEBOOK and ("check_stage(" in text or "check_setting(" in text)
    ]
    assert offenders == [], f"limit checks called outside the rulebook: {offenders}"


def test_low_level_readers_are_called_only_by_the_named_readings():
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel.parts[0] != "primitives"
        and rel not in READER_POLICY_MODULES
        and LOW_LEVEL_READER_CALL.search(text)
    ]
    assert offenders == [], (
        f"a class's reads called outside the named readings: {offenders}"
    )


def test_bridge_requests_are_sent_only_by_the_primitives():
    """``client.request(...)`` is how a primitive reaches NIS-Elements; nothing above the primitives sends one."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel.parts[0] != "primitives" and re.search(r"\.request\(", text)
    ]
    assert offenders == [], f"a bridge request sent outside the primitives: {offenders}"


def test_each_part_only_imports_what_the_anatomy_allows():
    for part, allowed in ALLOWED.items():
        folder = DRIVER_ROOT / part
        paths = (
            folder.rglob("*.py") if folder.is_dir() else [DRIVER_ROOT / f"{part}.py"]
        )
        for path in paths:
            if "__pycache__" in path.parts or "notebooks" in path.parts:
                continue
            used = _imported_parts(path, part)
            assert used <= allowed, (
                f"{path.relative_to(DRIVER_ROOT)} imports {sorted(used - allowed)}; "
                f"the anatomy lets {part} import only {sorted(allowed)}"
            )


def test_a_reading_never_uses_a_change():
    """No class profile imports a procedure."""
    profiles = sorted((DRIVER_ROOT / "primitives").glob("*/profile.py"))
    assert DRIVER_ROOT / "primitives" / "dll" / "profile.py" in profiles
    for path in profiles:
        assert "procedures" not in _imported_parts(path, "primitives"), (
            f"{path.relative_to(DRIVER_ROOT)} imports a procedure"
        )


def test_nothing_but_the_tests_imports_from_testing():
    for rel, _text in _driver_sources():
        assert "testing" not in _imported_parts(DRIVER_ROOT / rel, rel.parts[0]), str(
            rel
        )


def _measuring_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        for name in MEASURING_MODULES:
            item, leaf = name.split(".")
            if (
                module.endswith(f"driver_setup.{item}.{leaf}")
                or (module.endswith(f"driver_setup.{item}") or module == item)
                and any(alias.name == leaf for alias in node.names)
            ):
                found.add(name)
    return found


def test_run_time_procedures_never_import_the_measuring_code():
    """The two sides meet at the saved files, not in each other's code."""
    offenders = [
        f"{path.relative_to(DRIVER_ROOT)} -> {name}"
        for path in sorted((DRIVER_ROOT / "procedures").rglob("*.py"))
        for name in sorted(_measuring_imports(path))
    ]
    assert offenders == [], f"procedures import measuring code: {offenders}"


def _modules(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.py") if p.name != "__init__.py")


def _python_folders(folder: Path) -> list[Path]:
    return sorted(
        {p.parent for p in folder.rglob("*.py") if "__pycache__" not in p.parts}
    )


def test_the_driver_keeps_its_files_where_the_anatomy_puts_them():
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
        f"the driver's root has folders outside the anatomy {root_folders}"
    )

    dispatcher = DRIVER_ROOT / "dispatcher"
    assert _python_folders(dispatcher) == [dispatcher], "dispatcher/ has a subfolder"
    assert {p.name for p in dispatcher.glob("*.py")} == DISPATCHER_FILES

    primitives = DRIVER_ROOT / "primitives"
    assert _modules(primitives) == [], "primitives/ has loose files"
    classes = [p for p in _python_folders(primitives) if p.parent == primitives]
    assert {p.name for p in classes} == {"dll", "macro"}
    assert all((p / "profile.py").is_file() for p in classes), (
        "a primitive class has no profile"
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


def test_every_file_names_its_author():
    """Every file begins with a docstring that says who wrote it, as the code style asks."""
    missing = [
        str(rel)
        for rel, text in _driver_sources()
        if "Author:" not in text and rel.parts[0] != "dispatcher"
    ]
    assert missing == [], f"files without an author line: {missing}"
