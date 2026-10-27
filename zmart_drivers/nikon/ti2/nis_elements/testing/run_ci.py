#!/usr/bin/env python3
"""The Nikon NIS-Elements driver's CI: one command runs the offline suite, or the check at the microscope.

    python run_ci.py                # offline: lint + the suite against the mock + coverage
    python run_ci.py --controller   # also require the ZMART Controller layer
    python run_ci.py --hardware     # at NIS-Elements (the Ti2 simulator or a microscope) with the bridge running

Two layers offline. The driver layer needs only the driver, because the
driver is also used by itself. The controller layer checks the driver inside
the ZMART Controller; it runs when the controller is installed, and is shown
as SKIP when it is not. ``--controller`` turns that SKIP into a failure.

Every step prints its command, its result and its time, and the run writes
machine-readable reports to ``testing/report/`` (env.json, junit.xml,
coverage.xml, ci_summary.json), so a failure carries its own context. Lint is
reported but not fatal; a test failure fails the run.

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

DRIVER_ROOT = Path(__file__).resolve().parents[1]  # .../nis_elements
REPO_ROOT = DRIVER_ROOT.parents[3]  # the repository root (import root of zmart_drivers)
REPORT_DIR = DRIVER_ROOT / "testing" / "report"
PACKAGE = "zmart_drivers.nikon.ti2.nis_elements"


def build_env() -> dict:
    """The child-process environment: the repository root first on PYTHONPATH, coverage with the reports."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        os.pathsep.join([str(REPO_ROOT), existing]) if existing else str(REPO_ROOT)
    )
    env["PYTHONSAFEPATH"] = "1"
    env["COVERAGE_FILE"] = str(REPORT_DIR / ".coverage")
    return env


def controller_installed() -> bool:
    return importlib.util.find_spec("zmart_controller") is not None


def _rule(char: str = "=") -> str:
    return char * 72


def run_step(
    name: str, cmd: list[str], env: dict, *, fatal: bool, cwd: Path | None = None
) -> dict:
    """Run one step, streaming its output, and answer a record of it."""
    print(
        f"\n{_rule('-')}\nSTEP: {name}{'' if fatal else '   (non-fatal)'}\n  $ {' '.join(cmd)}\n{_rule('-')}",
        flush=True,
    )
    start = time.perf_counter()
    try:
        returncode = subprocess.run(cmd, env=env, cwd=cwd, check=False).returncode
        error = None
    except OSError as why:
        returncode, error = 126, f"the command could not be started: {why}"
        print(f"  ! {error}")
    elapsed = time.perf_counter() - start
    print(
        f"\n  -> {'OK' if returncode == 0 else 'FAIL'}  (exit {returncode})  in {elapsed:.1f}s",
        flush=True,
    )
    return {
        "name": name,
        "ok": returncode == 0,
        "fatal": fatal,
        "returncode": returncode,
        "seconds": round(elapsed, 2),
        "error": error,
        "command": cmd,
    }


def skipped(name: str, reason: str, *, required: bool) -> dict:
    print(
        f"\n{_rule('-')}\n{'STEP' if required else 'SKIP'}: {name}\n  {reason}",
        flush=True,
    )
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


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The Nikon NIS-Elements driver's CI.")
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="the check at NIS-Elements, with the bridge running",
    )
    parser.add_argument(
        "--controller", action="store_true", help="require the ZMART Controller layer"
    )
    parser.add_argument(
        "--save-to",
        default=None,
        help="with --hardware: where the check saves its capture",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    env = build_env()
    print(f"{_rule()}\nNIKON NIS-ELEMENTS DRIVER CI\n{_rule()}")
    subprocess.run(
        [sys.executable, str(DRIVER_ROOT / "testing" / "diagnostics.py")],
        env=env,
        check=False,
    )
    steps: list[dict] = []

    if not args.hardware:
        if importlib.util.find_spec("ruff") is not None:
            steps.append(
                run_step(
                    "lint: ruff check",
                    # The dispatcher is a verbatim copy of the Leica driver's and
                    # stays byte for byte the same, so it is not linted here.
                    [
                        sys.executable,
                        "-m",
                        "ruff",
                        "check",
                        "--exclude",
                        "dispatcher",
                        str(DRIVER_ROOT),
                    ],
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
                        "--exclude",
                        "dispatcher",
                        str(DRIVER_ROOT),
                    ],
                    env,
                    fatal=False,
                )
            )
        else:
            print("\n  (ruff is not installed; skipping lint)")
        cov = importlib.util.find_spec("pytest_cov") is not None

        def pytest_cmd(marks: str, junit: str, *, append: bool) -> list[str]:
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                str(DRIVER_ROOT / "testing"),
                "-m",
                marks,
                f"--junit-xml={REPORT_DIR / junit}",
            ]
            if cov:
                cmd += [
                    f"--cov={PACKAGE}",
                    f"--cov-config={DRIVER_ROOT / 'testing' / '.coveragerc'}",
                    *(["--cov-append"] if append else []),
                    "--cov-report=term-missing:skip-covered",
                    f"--cov-report=xml:{REPORT_DIR / 'coverage.xml'}",
                    f"--cov-report=html:{REPORT_DIR / 'htmlcov'}",
                ]
            return cmd

        steps.append(
            run_step(
                "tests: the driver, offline",
                pytest_cmd(
                    "not hardware and not controller", "junit.xml", append=False
                ),
                env,
                fatal=True,
            )
        )
        name = "tests: the driver in the ZMART Controller, offline"
        if controller_installed():
            steps.append(
                run_step(
                    name,
                    pytest_cmd(
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
                ' (--controller asks for it: pip install -e ".[nikon,controller]")'
                if args.controller
                else ""
            )
            steps.append(skipped(name, reason, required=args.controller))
    else:
        save_to = args.save_to or str(REPORT_DIR / "captures")
        steps.append(
            run_step(
                "hardware: the driver at NIS-Elements (read, a small move and back, a channel, the exposure, one capture saved)",
                [
                    sys.executable,
                    str(DRIVER_ROOT / "testing" / "hardware" / "check_microscope.py"),
                    "--yes",
                    f"--save-to={save_to}",
                    f"--report={REPORT_DIR / 'check_microscope.json'}",
                ],
                env,
                fatal=True,
            )
        )

    fatal_failures = [s for s in steps if not s["ok"] and s["fatal"]]
    warnings = [s for s in steps if not s["ok"] and not s["fatal"]]
    print(f"\n{_rule()}\nCI SUMMARY\n{_rule()}")
    for step in steps:
        flag = (
            "SKIP"
            if step.get("skipped")
            else "OK  "
            if step["ok"]
            else ("FAIL" if step["fatal"] else "WARN")
        )
        print(f"  [{flag}]  {step['name']:<60}  {step['seconds']:>6.1f}s")
    total = time.perf_counter() - started
    print(f"\n  total: {total:.1f}s\n  reports written to: {REPORT_DIR}")
    (REPORT_DIR / "ci_summary.json").write_text(
        json.dumps(
            {
                "total_seconds": round(total, 2),
                "passed": not fatal_failures,
                "fatal_failures": [s["name"] for s in fatal_failures],
                "warnings": [s["name"] for s in warnings],
                "steps": steps,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if warnings:
        print(
            f"\n  WARN: {len(warnings)} non-fatal step(s) reported issues; see above."
        )
    if fatal_failures:
        print(f"\n  RESULT: FAILED -- {', '.join(s['name'] for s in fatal_failures)}")
        return 1
    print("\n  RESULT: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
