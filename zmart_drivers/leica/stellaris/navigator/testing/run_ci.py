#!/usr/bin/env python3
"""The plain Navigator driver's CI: the one entry point for its tests.

One command runs the driver's offline quality gate anywhere Python and the
driver import work (a developer laptop, a GitHub runner, the microscope PC),
and at the microscope it runs the check on the live instrument::

    python run_ci.py                 # OFFLINE (default): lint + offline tests + coverage
    python run_ci.py --mock          # explicit spelling of the default
    python run_ci.py --hardware --out D:/ZMART-check/navigator-ci
                                     # LIVE: navigator_ci.py on the STELLARIS, LAS X showing
                                     # its plain Navigator
    python run_ci.py --hardware --out ... --allow-objective
                                     # LIVE: also an objective round trip
    python run_ci.py --controller    # also require the ZMART Controller layer (with any of the above)

Two layers. The driver layer needs only the driver, because the driver is
also used by itself. The controller layer checks the driver inside the ZMART
Controller; it runs when the controller is installed, and is shown as SKIP
when it is not. ``--controller`` turns that SKIP into a failure, for a
computer that is meant to have the controller.

Every step prints its command, its result and its wall-clock time, and the
run writes machine-readable reports to ``testing/report/`` (env.json,
junit.xml, coverage.xml, htmlcov/, ci_summary.json), so a failure carries
its own context off-machine. Lint is reported but non-fatal; a test failure
fails the run.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[1]  # .../navigator
REPO_ROOT = DRIVER_ROOT.parents[3]  # the repository root (import root of zmart_drivers)
REPORT_DIR = DRIVER_ROOT / "testing" / "report"
TEST_PATHS = [DRIVER_ROOT / "testing"]


def build_env() -> dict:
    """Child-process environment with the repository root on PYTHONPATH up front.

    The driver imports by its full name, ``zmart_drivers.leica.stellaris.navigator``.
    Setting the root here (rather than relying only on conftest) means
    coverage, which starts before collection, can already import it, also
    when the repository has not been installed with pip.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), existing]) if existing else str(REPO_ROOT)
    # Do not put the working directory on the search path: the driver folder
    # holds folders with common names, which a run started there would
    # otherwise import in place of other packages.
    env["PYTHONSAFEPATH"] = "1"
    # Coverage keeps its data file in the folder a run starts in; keep it with
    # the reports, so a run started in the driver leaves no file there.
    env["COVERAGE_FILE"] = str(REPORT_DIR / ".coverage")
    return env


def controller_installed() -> bool:
    """Whether the ZMART Controller can be imported here, which its layer needs."""
    return importlib.util.find_spec("zmart_controller") is not None


def controller_missing(name: str, *, required: bool) -> dict:
    """The record of a controller step that could not run: a SKIP, or a failure when *required*."""
    reason = "the ZMART Controller is not installed"
    if required:
        reason += ' (--controller asks for it: pip install -e ".[leica,controller]")'
    print(f"\n{_rule('-')}")
    print(f"{'STEP' if required else 'SKIP'}: {name}")
    print(f"  {reason}", flush=True)
    return {
        "name": name,
        "ok": not required,
        "fatal": required,
        "skipped": not required,
        "returncode": None,
        "seconds": 0.0,
        "error": reason,
        "command": [],
    }


def _rule(char: str = "=") -> str:
    return char * 72


def run_step(name: str, cmd: list[str], env: dict, *, fatal: bool, cwd: Path | None = None) -> dict:
    """Run one CI step, streaming its output, and return a structured record."""
    print(f"\n{_rule('-')}")
    print(f"STEP: {name}{'' if fatal else '   (non-fatal)'}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{_rule('-')}", flush=True)
    start = time.perf_counter()
    try:
        completed = subprocess.run(cmd, env=env, cwd=cwd)
        returncode = completed.returncode
        error = None
    except FileNotFoundError as exc:
        returncode = 127
        error = f"command not found: {exc}"
        print(f"  ! {error}")
    except OSError as exc:
        returncode = 126
        error = f"command failed to launch: {exc}"
        print(f"  ! {error}")
    elapsed = time.perf_counter() - start
    ok = returncode == 0
    print(f"\n  -> {'OK' if ok else 'FAIL'}  (exit {returncode})  in {elapsed:.1f}s", flush=True)
    return {
        "name": name,
        "ok": ok,
        "fatal": fatal,
        "returncode": returncode,
        "seconds": round(elapsed, 2),
        "error": error,
        "command": cmd,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The plain Navigator driver's CI (offline suite, or the check at the microscope).")
    parser.add_argument("--mock", action="store_true", help="run the offline suite (default)")
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="the check at the microscope: navigator_ci.py, with LAS X showing its plain Navigator",
    )
    parser.add_argument("--out", default=None, help="with --hardware: the folder for the saved pictures and the report")
    parser.add_argument("--tiles", type=int, default=3, help="with --hardware: how many fields the tile scan takes")
    parser.add_argument(
        "--allow-objective", action="store_true", help="with --hardware: also turn to another objective and back"
    )
    parser.add_argument(
        "--controller",
        action="store_true",
        help="require the ZMART Controller layer: fail, rather than skip it, when the controller is not installed",
    )
    args = parser.parse_args(argv)
    if args.mock and args.hardware:
        parser.error("--mock and --hardware are mutually exclusive")
    if (args.out or args.allow_objective) and not args.hardware:
        parser.error("--out and --allow-objective go with --hardware")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_mock = not args.hardware
    overall_start = time.perf_counter()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    env = build_env()

    print(_rule())
    print("PLAIN NAVIGATOR DRIVER CI")
    print(_rule())
    subprocess.run([sys.executable, str(DRIVER_ROOT / "testing" / "diagnostics.py")], env=env)

    steps: list[dict] = []

    # --- lint (non-fatal: report style debt without masking test results) ----
    if run_mock:
        if importlib.util.find_spec("ruff") is not None:
            steps.append(run_step("lint: ruff check", [sys.executable, "-m", "ruff", "check", str(DRIVER_ROOT)], env, fatal=False))
            steps.append(
                run_step(
                    "lint: ruff format --check",
                    [sys.executable, "-m", "ruff", "format", "--check", str(DRIVER_ROOT)],
                    env,
                    fatal=False,
                )
            )
        else:
            print("\n  (ruff not installed -- skipping lint; `pip install ruff` to enable)")

    cov_available = importlib.util.find_spec("pytest_cov") is not None

    # --- the offline suite + coverage (fatal): the driver, then the driver in the controller ---
    if run_mock:

        def pytest_cmd(marks: str, junit: str, *, append: bool) -> list[str]:
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                *[str(p) for p in TEST_PATHS],
                "-m",
                marks,
                f"--junit-xml={REPORT_DIR / junit}",
            ]
            if cov_available:
                cmd += [
                    "--cov=zmart_drivers.leica.stellaris.navigator",
                    f"--cov-config={DRIVER_ROOT / 'testing' / '.coveragerc'}",
                    *(["--cov-append"] if append else []),
                    "--cov-report=term-missing:skip-covered",
                    f"--cov-report=xml:{REPORT_DIR / 'coverage.xml'}",
                    f"--cov-report=html:{REPORT_DIR / 'htmlcov'}",
                ]
            return cmd

        if not cov_available:
            print("\n  (pytest-cov not installed -- running without coverage; `pip install pytest-cov` to enable)")
        coverage = " + coverage" if cov_available else ""
        steps.append(
            run_step(
                f"tests: the driver, offline{coverage}",
                pytest_cmd("not hardware and not controller", "junit.xml", append=False),
                env,
                fatal=True,
            )
        )
        name = f"tests: the driver in the ZMART Controller, offline{coverage}"
        if controller_installed():
            steps.append(
                run_step(name, pytest_cmd("controller and not hardware", "junit_controller.xml", append=True), env, fatal=True)
            )
        else:
            steps.append(controller_missing(name, required=args.controller))

    # --- the check at the microscope (fatal) ----------------------------------
    if args.hardware:
        # The limits gate is proven against the stand-in before anything is
        # sent to LAS X: a broken gate must never reach the physical stage.
        selftest = run_step(
            "limits: self-check (the gate proven before any hardware)",
            [sys.executable, "-m", "pytest", "-q", str(DRIVER_ROOT / "testing" / "unit" / "test_limits.py")],
            env,
            fatal=True,
        )
        steps.append(selftest)
        if selftest["returncode"] != 0:
            print("\n  ABORT: the limits self-check FAILED. Refusing to run the hardware check; the stage is not touched.", flush=True)
        else:
            out = args.out or str(REPORT_DIR / "navigator-ci")
            cmd = [
                sys.executable,
                str(DRIVER_ROOT / "testing" / "hardware" / "navigator_ci.py"),
                "--out",
                out,
                "--tiles",
                str(args.tiles),
                *(["--allow-objective"] if args.allow_objective else []),
            ]
            # LAS X's Data Container client writes DCLogFile.log into the folder it
            # is started in; run in the reports folder, so it lands there.
            steps.append(run_step("hardware: the plain Navigator route on the live instrument", cmd, env, fatal=True, cwd=REPORT_DIR))

    # --- summary -------------------------------------------------------------
    total_elapsed = time.perf_counter() - overall_start
    fatal_failures = [s for s in steps if not s["ok"] and s["fatal"]]
    warn_failures = [s for s in steps if not s["ok"] and not s["fatal"]]

    print(f"\n{_rule()}")
    print("CI SUMMARY")
    print(_rule())
    for step in steps:
        flag = "SKIP" if step.get("skipped") else "OK  " if step["ok"] else ("FAIL" if step["fatal"] else "WARN")
        print(f"  [{flag}]  {step['name']:<60}  {step['seconds']:>6.1f}s")
    print(f"\n  total: {total_elapsed:.1f}s")
    print(f"  reports written to: {REPORT_DIR}")

    summary = {
        "total_seconds": round(total_elapsed, 2),
        "passed": not fatal_failures,
        "fatal_failures": [s["name"] for s in fatal_failures],
        "warnings": [s["name"] for s in warn_failures],
        "steps": steps,
    }
    (REPORT_DIR / "ci_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if warn_failures:
        print(f"\n  WARN: {len(warn_failures)} non-fatal step(s) reported issues -- see above.")
    if fatal_failures:
        print(f"\n  RESULT: FAILED -- {len(fatal_failures)} fatal step(s): {', '.join(s['name'] for s in fatal_failures)}.")
        return 1
    print("\n  RESULT: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
