#!/usr/bin/env python3
"""The ZEISS driver's CI: one command for the offline suite, or the check at the microscope.

    python run_ci.py                 # offline: lint (reported), then the tests, in two layers
    python run_ci.py --controller    # the same, and the controller layer must run
    python run_ci.py --hardware      # at the microscope: testing/hardware/check_on_zen.py, read-only
    python run_ci.py --hardware --allow-move --allow-acquire   # with small moves and one snap

The offline suite has two layers. The first checks the driver by itself,
which is how many people use it. The second checks it inside the ZMART
Controller; it runs when the controller is installed and shows as SKIP when
it is not, unless ``--controller`` asks for it. The tests need no
microscope; the real-wire tests also need the ``zeiss`` extra, and skip
themselves without it.

Run it from anywhere: paths are found from this file's place. Reports go to
``testing/report/``.

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

DRIVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DRIVER_ROOT.parents[3]
REPORT_DIR = DRIVER_ROOT / "testing" / "report"
PACKAGE = "zmart_drivers.zeiss.light_microscope.zen_api_grpc"


def child_environment() -> dict:
    """The repository root on the import path, the working directory off it, coverage beside the reports."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPO_ROOT), env.get("PYTHONPATH", "")])
    )
    env["PYTHONSAFEPATH"] = "1"
    env["COVERAGE_FILE"] = str(REPORT_DIR / ".coverage")
    return env


def run_step(name: str, command: list[str], env: dict, *, fatal: bool) -> dict:
    """Run one step, show its command and how it ended, and answer a record of it."""
    print(
        f"\n{'-' * 72}\nSTEP: {name}{'' if fatal else '   (reported, not fatal)'}\n  $ {' '.join(command)}\n{'-' * 72}",
        flush=True,
    )
    started = time.perf_counter()
    try:
        code = subprocess.run(command, env=env, cwd=REPORT_DIR).returncode
    except OSError as why:
        print(f"  ! could not start: {why}")
        code = 127
    seconds = time.perf_counter() - started
    print(
        f"\n  -> {'OK' if code == 0 else 'FAIL'}  (exit {code})  in {seconds:.1f}s",
        flush=True,
    )
    return {
        "name": name,
        "ok": code == 0,
        "fatal": fatal,
        "skipped": False,
        "returncode": code,
        "seconds": round(seconds, 2),
    }


def skipped(name: str, reason: str, *, required: bool) -> dict:
    print(
        f"\n{'-' * 72}\n{'STEP' if required else 'SKIP'}: {name}\n  {reason}",
        flush=True,
    )
    return {
        "name": name,
        "ok": not required,
        "fatal": required,
        "skipped": not required,
        "returncode": None,
        "seconds": 0.0,
    }


def pytest_command(marks: str, junit: str, *, append: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(DRIVER_ROOT / "testing"),
        "-m",
        marks,
        f"--junit-xml={REPORT_DIR / junit}",
    ]
    if importlib.util.find_spec("pytest_cov") is not None:
        command += [
            f"--cov={PACKAGE}",
            f"--cov-config={DRIVER_ROOT / 'testing' / '.coveragerc'}",
            *(["--cov-append"] if append else []),
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=xml:{REPORT_DIR / 'coverage.xml'}",
        ]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The ZEISS driver's CI.")
    parser.add_argument(
        "--controller",
        action="store_true",
        help="fail, rather than skip, when the ZMART Controller is not installed",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="run the check at the microscope instead of the offline suite",
    )
    parser.add_argument(
        "--allow-move",
        action="store_true",
        help="with --hardware: small stage and focus moves, and back",
    )
    parser.add_argument(
        "--allow-acquire",
        action="store_true",
        help="with --hardware: one snap with the experiment named by --experiment",
    )
    parser.add_argument(
        "--experiment", default=None, help="with --hardware: the ZEN experiment to load"
    )
    args = parser.parse_args(argv)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    env = child_environment()
    steps = []

    if args.hardware:
        command = [
            sys.executable,
            str(DRIVER_ROOT / "testing" / "hardware" / "check_on_zen.py"),
            f"--report-dir={REPORT_DIR}",
        ]
        command += ["--allow-move"] if args.allow_move else []
        command += ["--allow-acquire"] if args.allow_acquire else []
        command += [f"--experiment={args.experiment}"] if args.experiment else []
        steps.append(
            run_step("hardware: the driver against ZEN", command, env, fatal=True)
        )
    else:
        if importlib.util.find_spec("ruff") is not None:
            steps.append(
                run_step(
                    "lint: ruff check",
                    [sys.executable, "-m", "ruff", "check", str(DRIVER_ROOT)],
                    env,
                    fatal=False,
                )
            )
            steps.append(
                run_step(
                    "lint: ruff format --check",
                    [
                        sys.executable,
                        "-m",
                        "ruff",
                        "format",
                        "--check",
                        str(DRIVER_ROOT),
                        # The dispatcher is the Leica driver's, copied byte for byte.
                        "--exclude",
                        "dispatcher",
                    ],
                    env,
                    fatal=False,
                )
            )
        steps.append(
            run_step(
                "tests: the driver, offline",
                pytest_command(
                    "not hardware and not controller", "junit.xml", append=False
                ),
                env,
                fatal=True,
            )
        )
        name = "tests: the driver in the ZMART Controller, offline"
        if importlib.util.find_spec("zmart_controller") is not None:
            steps.append(
                run_step(
                    name,
                    pytest_command(
                        "controller and not hardware",
                        "junit_controller.xml",
                        append=True,
                    ),
                    env,
                    fatal=True,
                )
            )
        else:
            reason = "the ZMART Controller is not installed" + (
                ' (--controller asks for it: pip install -e ".[zeiss,controller]")'
                if args.controller
                else ""
            )
            steps.append(skipped(name, reason, required=args.controller))

    failed = [step for step in steps if not step["ok"] and step["fatal"]]
    print(f"\n{'=' * 72}\nCI SUMMARY\n{'=' * 72}")
    for step in steps:
        flag = (
            "SKIP"
            if step["skipped"]
            else "OK  "
            if step["ok"]
            else ("FAIL" if step["fatal"] else "WARN")
        )
        print(f"  [{flag}]  {step['name']:<52}  {step['seconds']:>6.1f}s")
    (REPORT_DIR / "ci_summary.json").write_text(
        json.dumps({"passed": not failed, "steps": steps}, indent=2), encoding="utf-8"
    )
    print(f"\n  reports: {REPORT_DIR}\n\n  RESULT: {'FAILED' if failed else 'PASSED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
