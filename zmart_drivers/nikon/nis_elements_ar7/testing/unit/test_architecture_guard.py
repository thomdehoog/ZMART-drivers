"""The driver keeps to the anatomy of a ZMART driver.

The driver is built from the parts the anatomy names
(``docs/driver-anatomy.md`` in this repository): ``methods.py``,
``procedures/``, ``dispatcher/``, ``primitives/``, ``driver_setup/`` and
``zmart_controller_plugin/``. These tests read the driver's own source files
and fail when one breaks a rule, so that the rule lives on as a red test and
not as a comment in a review. When one fails, the fix is almost never to edit
this file: move the code to the part the anatomy gives it.

- Every change goes through the limits gate: only the change definitions
  and the bridge name NIS's stage moves, and only the transfer sends a change.
- The bridge that runs inside NIS imports only Python's standard library.
- Each part imports only what ``ALLOWED`` lets it.
- Every file sits where the anatomy puts it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``testing/unit/test_architecture_guard.py``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "nis_elements_ar7."
RULEBOOK = Path("driver_setup/limits/limit_rules.py")
NIS_PYTHON = Path("primitives/nis_python")


def _driver_sources():
    """(relative path, text) for every module of the driver, the tests left out."""
    for path in sorted(DRIVER_ROOT.rglob("*.py")):
        rel = path.relative_to(DRIVER_ROOT)
        if rel.parts[0] == "testing" or "__pycache__" in rel.parts:
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_nis_stage_moves_are_named_only_by_the_change_definitions_and_the_bridge():
    """``StgMoveXY`` and ``StgMoveZ`` are the stage's real door. The change definitions name them,
    and only ``dispatch.run`` sends a definition, after asking the limits. The bridge names them to
    refuse a move beyond NIS's own travel. A name anywhere else would be a second, unchecked door."""
    allowed = {NIS_PYTHON / "writes.py", NIS_PYTHON / "server.py"}
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if ("StgMoveXY" in text or "StgMoveZ" in text) and rel not in allowed
    ]
    assert offenders == [], (
        f"NIS's stage moves named outside the change definitions: {offenders}; use procedures/changes.py"
    )


def test_only_the_transfer_sends_a_change():
    """``send_change`` is how a change reaches NIS; only the transfer, which the dispatcher drives, calls it."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if ".send_change(" in text and rel not in {NIS_PYTHON / "transfer.py"}
    ]
    assert offenders == [], f"a change sent around the dispatcher: {offenders}"


def test_limit_checks_are_called_only_from_the_rules():
    """``check_xy`` and ``check_z`` belong to the rulebook; the gate asks it. A call elsewhere could drift out of step."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if rel.parts[0] != "dispatcher"
        and rel != RULEBOOK
        and ("check_xy(" in text or "check_z(" in text)
    ]
    assert offenders == [], f"limit checks called outside the rulebook: {offenders}"


def test_the_rulebook_lives_in_driver_setup():
    rules = (DRIVER_ROOT / RULEBOOK).read_text(encoding="utf-8")
    gate = (DRIVER_ROOT / "dispatcher" / "limits.py").read_text(encoding="utf-8")
    assert "class NikonLimits" in rules
    assert "class NikonLimits" not in gate


def test_the_bridge_imports_only_the_standard_library():
    """``server.py`` runs inside NIS's own Python, where nothing else is installed (``nis`` is imported where it is used)."""
    tree = ast.parse(
        (DRIVER_ROOT / NIS_PYTHON / "server.py").read_text(encoding="utf-8")
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                "server.py imports from the driver; it must stand alone"
            )
            imported.add((node.module or "").split(".")[0])
    outside = sorted(
        name
        for name in imported - {"nis", "ctypes"}
        if name not in sys.stdlib_module_names
    )
    assert outside == [], (
        f"server.py imports {outside}, which NIS's Python does not have"
    )


def test_the_bridge_and_the_readings_agree_on_what_may_be_read():
    """Every NIS function the readings call is one the bridge allows, and every one it allows is used."""
    from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import server

    text = (DRIVER_ROOT / NIS_PYTHON / "reads.py").read_text(encoding="utf-8")
    called = set()
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            called.add(node.args[0].value)
    from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import devices

    called |= (
        devices.READ_FUNCTIONS
    )  # the table's readings, which reads.py calls through it
    assert called - server.READS == set(), (
        f"readings call NIS functions the bridge refuses: {sorted(called - server.READS)}"
    )
    assert server.READS - called == set(), (
        f"the bridge allows unused readings: {sorted(server.READS - called)}"
    )


# --- the parts, and which may import which ----------------------------------------

ALLOWED = {
    "primitives": {"dispatcher"},
    "dispatcher": set(),
    "procedures": {"primitives", "dispatcher", "driver_setup", "__version__"},
    "driver_setup": {"primitives", "dispatcher", "procedures"},
    "methods": {"primitives", "dispatcher", "procedures", "driver_setup"},
    "zmart_controller_plugin": {"methods", "procedures", "driver_setup", "dispatcher"},
    "__init__": {"methods"},
}


def _imported_parts(path: Path, part: str) -> set[str]:
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
            elif PACKAGE in module:
                found.add(module.split(PACKAGE)[1].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if PACKAGE in alias.name:
                    found.add(alias.name.split(PACKAGE)[1].split(".")[0])
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
                f"{path.relative_to(DRIVER_ROOT)} imports {sorted(used - allowed)}; the anatomy lets {part} import only {sorted(allowed)}"
            )


def test_a_reading_never_uses_a_change():
    for path in sorted((DRIVER_ROOT / "primitives").glob("*/profile.py")):
        assert "procedures" not in _imported_parts(path, "primitives"), str(
            path.relative_to(DRIVER_ROOT)
        )


def test_nothing_but_the_tests_imports_from_testing():
    for rel, _ in _driver_sources():
        assert "testing" not in _imported_parts(DRIVER_ROOT / rel, rel.parts[0]), str(
            rel
        )


MEASURING_MODULES = {"origin.set_origin", "limits.measure_limits"}


def test_run_time_procedures_never_import_the_measuring_code():
    """The procedures read what was set up through each item's loader; they never run the measuring code."""
    offenders = []
    for path in sorted((DRIVER_ROOT / "procedures").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for name in MEASURING_MODULES:
                    item, leaf = name.split(".")
                    if module.endswith(f"driver_setup.{item}.{leaf}") or (
                        module.endswith(f"driver_setup.{item}")
                        and any(a.name == leaf for a in node.names)
                    ):
                        offenders.append(f"{path.relative_to(DRIVER_ROOT)} -> {name}")
    assert offenders == []


# --- every file where the anatomy puts it -------------------------------------------

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


def _modules(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.py") if p.name != "__init__.py")


def _python_folders(folder: Path) -> list[Path]:
    return sorted(
        {p.parent for p in folder.rglob("*.py") if "__pycache__" not in p.parts}
    )


def test_the_driver_keeps_its_files_where_the_anatomy_puts_them():
    loose = sorted(
        p.name
        for p in DRIVER_ROOT.glob("*")
        if p.is_file() and p.name not in ANATOMY_FILES
    )
    assert loose == [], f"the driver's root has loose files {loose}"
    folders = sorted(
        p.name
        for p in DRIVER_ROOT.glob("*")
        if p.is_dir() and _python_folders(p) and p.name not in ANATOMY_FOLDERS
    )
    assert folders == [], f"the driver's root has folders outside the anatomy {folders}"

    dispatcher = DRIVER_ROOT / "dispatcher"
    assert _python_folders(dispatcher) == [dispatcher]
    assert {p.name for p in dispatcher.glob("*.py")} == DISPATCHER_FILES

    primitives = DRIVER_ROOT / "primitives"
    assert _modules(primitives) == []
    classes = [p for p in _python_folders(primitives) if p.parent == primitives]
    assert classes and all((p / "profile.py").is_file() for p in classes)

    single = sorted(
        str(folder.relative_to(DRIVER_ROOT))
        for part in ("procedures", "primitives")
        for folder in _python_folders(DRIVER_ROOT / part)
        if folder != DRIVER_ROOT / part and len(_modules(folder)) < 2
    )
    assert single == [], f"folders with fewer than two files {single}"


def test_the_dispatcher_is_the_same_as_in_every_other_driver():
    """The dispatcher is shared by every driver, byte for byte; it is copied, never edited here."""
    leica = (
        DRIVER_ROOT.parents[1]
        / "leica"
        / "stellaris"
        / "navigator_expert"
        / "dispatcher"
    )
    if not leica.is_dir():
        return
    for name in DISPATCHER_FILES:
        assert (DRIVER_ROOT / "dispatcher" / name).read_bytes() == (
            leica / name
        ).read_bytes(), name
