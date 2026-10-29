"""The ZEISS driver keeps the anatomy of a ZMART driver.

The parts are those of ``docs/driver-anatomy.md``: ``methods.py``,
``procedures/``, ``dispatcher/``, ``primitives/``, ``driver_setup/`` and
``zmart_controller_plugin/``. These tests read the driver's own files and
fail when a file sits outside its place or imports a part it may not. If one
fails, the fix is almost never to edit this file: move the code to the part
the anatomy gives it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on the Leica driver's guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[2]
LEICA_DISPATCHER = (
    DRIVER_ROOT.parents[2] / "leica" / "stellaris" / "navigator_expert" / "dispatcher"
)
PACKAGE = "zen_api_grpc."

ALLOWED = {
    "primitives": {"dispatcher"},
    "dispatcher": set(),
    "procedures": {"primitives", "dispatcher", "driver_setup", "__version__"},
    "driver_setup": {"primitives", "dispatcher", "procedures"},
    "methods": {"primitives", "dispatcher", "procedures", "driver_setup"},
    "zmart_controller_plugin": {"methods", "procedures", "driver_setup", "dispatcher"},
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

#: The measuring code of the setup items; run-time procedures only read what it saves.
MEASURING_MODULES = (
    "origin.set_origin",
    "limits.measure_limits",
    "orientation.measure_orientation",
    "optical_calibration.calibrate_objective_pair",
)


def _driver_sources():
    for path in sorted(DRIVER_ROOT.rglob("*.py")):
        rel = path.relative_to(DRIVER_ROOT)
        if rel.parts[0] == "testing" or "__pycache__" in rel.parts:
            continue
        yield rel, path.read_text(encoding="utf-8")


def _imported_parts(path: Path, part: str) -> set[str]:
    """The driver's parts that *path* imports from."""
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
                    found.update(alias.name for alias in node.names)
            elif PACKAGE in module:
                found.add(module.split(PACKAGE)[1].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if PACKAGE in alias.name:
                    found.add(alias.name.split(PACKAGE)[1].split(".")[0])
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


def test_the_dispatcher_is_the_leica_drivers_unchanged():
    """The dispatcher is the same in every driver: a copy, byte for byte."""
    for name in DISPATCHER_FILES:
        assert (DRIVER_ROOT / "dispatcher" / name).read_bytes() == (
            LEICA_DISPATCHER / name
        ).read_bytes(), name


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
        if p.is_dir() and any(p.rglob("*.py")) and p.name not in ANATOMY_FOLDERS
    )
    assert folders == [], f"the driver's root has folders outside the anatomy {folders}"
    assert {
        p.name for p in (DRIVER_ROOT / "dispatcher").glob("*.py")
    } == DISPATCHER_FILES
    primitives = DRIVER_ROOT / "primitives"
    assert (
        sorted(p.name for p in primitives.glob("*.py") if p.name != "__init__.py") == []
    )
    classes = [
        p for p in primitives.iterdir() if p.is_dir() and p.name != "__pycache__"
    ]
    assert classes and all((p / "profile.py").is_file() for p in classes)
    for part in ("procedures", "primitives"):
        for folder in (DRIVER_ROOT / part).rglob("*"):
            if folder.is_dir() and folder.name != "__pycache__":
                modules = [p for p in folder.glob("*.py") if p.name != "__init__.py"]
                assert len(modules) >= 2, (
                    f"{folder.relative_to(DRIVER_ROOT)} holds fewer than two files"
                )


def test_only_the_client_imports_zeiss_packages():
    """``zen_api`` and ``grpclib`` are named only in the gateway's client, the one place that knows them."""
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if ("import zen_api" in text or "from zen_api" in text or "grpclib" in text)
        and rel != Path("primitives/gateway/client.py")
    ]
    assert offenders == []


def test_the_stage_is_moved_only_by_the_gateway_writes():
    """The ZEN calls that move hardware are named only in the writes and the transfer's table of calls."""
    moving = ("stage.move_to", "focus.move_to", "objective.move_to")
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if any(call in text for call in moving)
        and rel
        not in (
            Path("primitives/gateway/writes.py"),
            Path("primitives/gateway/transfer.py"),
        )
    ]
    assert offenders == []


def test_the_limits_are_checked_only_by_the_rules():
    offenders = [
        str(rel)
        for rel, text in _driver_sources()
        if ("check_xy(" in text or "check_z(" in text or "check_refusal(" in text)
        and rel.parts[0] != "dispatcher"
        and rel != Path("driver_setup/limits/limit_rules.py")
    ]
    assert offenders == []


def test_run_time_procedures_never_import_the_measuring_code():
    for path in sorted((DRIVER_ROOT / "procedures").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for module in MEASURING_MODULES:
            item, leaf = module.split(".")
            assert (
                f"{item}.{leaf}" not in text and f"{item} import {leaf}" not in text
            ), f"{path.name} -> {module}"


def test_nothing_in_the_driver_imports_from_testing():
    for rel, _ in _driver_sources():
        assert "testing" not in _imported_parts(DRIVER_ROOT / rel, rel.parts[0]), str(
            rel
        )


def test_nothing_in_the_driver_imports_the_setup_wizard():
    """The wizard calls the setup items; no item, procedure or method calls it back."""
    for rel, text in _driver_sources():
        if WIZARD in rel.parents:
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
