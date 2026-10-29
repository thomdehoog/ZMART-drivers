#!/usr/bin/env python3
"""The Nikon driver's tests in one command, the same on a laptop, a GitHub runner and the microscope's PC.

::

    python run_ci.py               the offline suite: lint, the driver layer, the controller layer, coverage
    python run_ci.py --controller  as above, and a missing ZMART Controller is a failure
    python run_ci.py --hardware    how to run the hardware check, which runs inside NIS-Elements

There are two layers of tests. The driver layer needs only the driver,
because the driver is also used by itself. The controller layer checks the
driver inside the ZMART Controller; it runs when the controller is installed
and is shown as SKIP when it is not. ``--controller`` makes that SKIP a
failure, for a computer that is meant to have the controller.

Each step prints its command, its result and how long it took. Lint is
reported but does not fail the run; a failing test does. The reports go to
``testing/report/``: env.json, junit.xml, coverage.xml and ci_summary.json.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``testing/run_ci.py``.
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

DRIVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DRIVER_ROOT.parents[2]
TESTING = DRIVER_ROOT / "testing"
REPORT_DIR = TESTING / "report"


HARDWARE_HOW_TO = """
The hardware check runs inside NIS-Elements, in NIS's own Python, so it is not started from here.
At the microscope:
  1. Copy the ZMART-drivers folder to C:\\ZMART-drivers (or onto the Desktop).
  2. Open testing\\hardware\\check_hardware_nis.py in Notepad and switch on, at the top,
     what may change (one group at a time, with someone at the microscope).
  3. In NIS-Elements: Macro > Run Macro From File > testing\\hardware\\check_hardware.mac.
  4. The results folder appears beside check_hardware_nis.py.
"""


def build_env() -> dict:
    """The environment for each step: the repository on the path, and coverage's data kept with the reports."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        os.pathsep.join([str(REPO_ROOT), existing]) if existing else str(REPO_ROOT)
    )
    env["PYTHONSAFEPATH"] = "1"
    env["COVERAGE_FILE"] = str(REPORT_DIR / ".coverage")
    return env


def installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def run_step(name: str, command: list[str], *, fatal: bool) -> dict:
    """Run one step, print what it did, and answer its record."""
    print(f"\n=== {name} ===\n$ {' '.join(command)}", flush=True)
    started = time.monotonic()
    code = subprocess.run(command, cwd=TESTING, env=build_env(), check=False).returncode
    seconds = round(time.monotonic() - started, 1)
    # pytest answers 5 when it collected nothing, which for a layer means "nothing to run".
    outcome = "PASS" if code == 0 else "EMPTY" if code == 5 else "FAIL"
    print(f"--- {name}: {outcome} ({seconds} s)", flush=True)
    return {
        "step": name,
        "outcome": outcome,
        "code": code,
        "seconds": seconds,
        "fatal": fatal,
    }


def skipped(name: str, why: str, *, fatal: bool) -> dict:
    print(
        f"\n=== {name} ===\n--- {name}: {'FAIL' if fatal else 'SKIP'} ({why})",
        flush=True,
    )
    return {
        "step": name,
        "outcome": "FAIL" if fatal else "SKIP",
        "why": why,
        "fatal": fatal,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--controller",
        action="store_true",
        help="a missing ZMART Controller fails the run",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="say how to run the hardware check inside NIS-Elements",
    )
    parser.add_argument(
        "--no-coverage", action="store_true", help="run without measuring coverage"
    )
    args = parser.parse_args(argv)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    steps = [
        run_step("environment", [python, str(TESTING / "diagnostics.py")], fatal=False)
    ]

    if args.hardware:
        print(HARDWARE_HOW_TO, flush=True)
        steps.append(
            skipped(
                "hardware",
                "runs inside NIS-Elements, started by check_hardware.mac",
                fatal=False,
            )
        )
    else:
        if installed("ruff"):
            steps.append(
                run_step(
                    "lint",
                    [
                        python,
                        "-m",
                        "ruff",
                        "check",
                        str(DRIVER_ROOT),
                        # The dispatcher is the same file in every driver, kept as the Leica driver has it.
                        "--extend-exclude",
                        str(DRIVER_ROOT / "dispatcher"),
                    ],
                    fatal=False,
                )
            )
        else:
            steps.append(skipped("lint", "ruff is not installed", fatal=False))

        coverage = []
        if not args.no_coverage and installed("pytest_cov"):
            coverage = [
                f"--cov={DRIVER_ROOT}",
                f"--cov-config={TESTING / '.coveragerc'}",
                f"--cov-report=xml:{REPORT_DIR / 'coverage.xml'}",
                "--cov-report=term",
            ]
        pytest = [
            python,
            "-m",
            "pytest",
            "-c",
            str(TESTING / "pytest.ini"),
            "--rootdir",
            str(TESTING),
        ]
        steps.append(
            run_step(
                "driver layer",
                [
                    *pytest,
                    "-m",
                    "not controller",
                    f"--junit-xml={REPORT_DIR / 'junit.xml'}",
                    *coverage,
                ],
                fatal=True,
            )
        )
        if installed("zmart_controller"):
            steps.append(
                run_step(
                    "controller layer",
                    [
                        *pytest,
                        "-m",
                        "controller",
                        f"--junit-xml={REPORT_DIR / 'junit-controller.xml'}",
                    ],
                    fatal=True,
                )
            )
        else:
            steps.append(
                skipped(
                    "controller layer",
                    "the ZMART Controller is not installed",
                    fatal=args.controller,
                )
            )

    failed = [s["step"] for s in steps if s["fatal"] and s["outcome"] == "FAIL"]
    (REPORT_DIR / "ci_summary.json").write_text(
        json.dumps({"steps": steps, "failed": failed}, indent=2), encoding="utf-8"
    )
    print("\n=== summary ===")
    for step in steps:
        print(f"{step['outcome']:>5}  {step['step']}")
    print("FAILED: " + ", ".join(failed) if failed else "all fatal steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
