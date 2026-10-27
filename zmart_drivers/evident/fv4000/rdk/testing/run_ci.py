#!/usr/bin/env python3
"""The FV4000 RDK driver's CI: one command for the offline suite, and one for the checks at the microscope.

    python run_ci.py                 # offline (default): lint + the offline suite + coverage
    python run_ci.py --hardware      # at the microscope: the checks against a live FLUOVIEW
    python run_ci.py --controller    # also require the ZMART Controller layer (with either)

Two layers, as in every ZMART driver. The driver layer needs only the driver,
because the driver is also used by itself. The controller layer checks the
driver inside the ZMART Controller; it runs when the controller is installed
and is shown as SKIP when it is not. ``--controller`` turns that SKIP into a
failure, for a computer that is meant to have the controller.

Every step prints its command, its result and its time; the run opens with
an environment header and writes machine-readable reports to
``testing/report/`` (env.json, junit.xml, coverage.xml, ci_summary.json), so
a failure carries its own context off-machine. Lint is reported but not
fatal; a test failure fails the run.

This file is the CI definition; the repository's GitHub workflow only calls it.
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

DRIVER_ROOT = Path(__file__).resolve().parents[1]  # .../rdk
REPO_ROOT = DRIVER_ROOT.parents[3]
REPORT_DIR = DRIVER_ROOT / "testing" / "report"
TEST_PATHS = [DRIVER_ROOT / "testing"]


def build_env() -> dict:
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


def controller_missing(name: str, *, required: bool) -> dict:
    reason = "the ZMART Controller is not installed"
    if required:
        reason += ' (--controller asks for it: pip install -e ".[evident,controller]")'
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


def _rule(char: str = "=") -> str:
    return char * 72


def run_step(
    name: str, cmd: list[str], env: dict, *, fatal: bool, cwd: Path | None = None
) -> dict:
    print(
        f"\n{_rule('-')}\nSTEP: {name}{'' if fatal else '   (non-fatal)'}\n  $ {' '.join(cmd)}\n{_rule('-')}",
        flush=True,
    )
    start = time.perf_counter()
    try:
        returncode, error = subprocess.run(cmd, env=env, cwd=cwd).returncode, None
    except OSError as exc:
        returncode, error = 126, f"command failed to launch: {exc}"
        print(f"  ! {error}")
    elapsed = time.perf_counter() - start
    ok = returncode == 0
    print(
        f"\n  -> {'OK' if ok else 'FAIL'}  (exit {returncode})  in {elapsed:.1f}s",
        flush=True,
    )
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
    parser = argparse.ArgumentParser(
        description="FV4000 RDK driver CI (offline suite + coverage + reports)."
    )
    parser.add_argument(
        "--mock", action="store_true", help="run the offline suite (default)"
    )
    parser.add_argument(
        "--hardware", action="store_true", help="the checks against a live FLUOVIEW"
    )
    parser.add_argument(
        "--host",
        default=None,
        help="with --hardware: the computer FLUOVIEW runs on (default 127.0.0.1)",
    )
    parser.add_argument(
        "--allow-move",
        action="store_true",
        help="with --hardware: also make a small move and back",
    )
    parser.add_argument(
        "--allow-acquire",
        action="store_true",
        help="with --hardware: also take one picture and save it",
    )
    parser.add_argument(
        "--controller",
        action="store_true",
        help="require the ZMART Controller layer: fail, rather than skip it, when the controller is not installed",
    )
    args = parser.parse_args(argv)
    if args.mock and args.hardware:
        parser.error("--mock and --hardware are mutually exclusive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_mock = not args.hardware
    overall_start = time.perf_counter()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    env = build_env()

    print(_rule() + "\nFV4000 RDK DRIVER CI\n" + _rule())
    subprocess.run(
        [sys.executable, str(DRIVER_ROOT / "testing" / "diagnostics.py")], env=env
    )
    steps: list[dict] = []

    if run_mock:
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
                    ],
                    env,
                    fatal=False,
                )
            )
        else:
            print(
                "\n  (ruff not installed -- skipping lint; `pip install ruff` to enable)"
            )
        cov_available = importlib.util.find_spec("pytest_cov") is not None

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
                    "--cov=zmart_drivers.evident.fv4000.rdk",
                    f"--cov-config={DRIVER_ROOT / 'testing' / '.coveragerc'}",
                    *(["--cov-append"] if append else []),
                    "--cov-report=term-missing:skip-covered",
                    f"--cov-report=xml:{REPORT_DIR / 'coverage.xml'}",
                    f"--cov-report=html:{REPORT_DIR / 'htmlcov'}",
                ]
            return cmd

        coverage = " + coverage" if cov_available else ""
        steps.append(
            run_step(
                f"tests: the driver, offline{coverage}",
                pytest_cmd(
                    "not hardware and not controller", "junit.xml", append=False
                ),
                env,
                fatal=True,
            )
        )
        name = f"tests: the driver in the ZMART Controller, offline{coverage}"
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
            steps.append(controller_missing(name, required=args.controller))
    else:
        hw = DRIVER_ROOT / "testing" / "hardware"
        # The limits gate is proven against the mock before FLUOVIEW is touched.
        gate = run_step(
            "limits: offline self-check before any hardware",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(DRIVER_ROOT / "testing" / "unit" / "test_limits.py"),
            ],
            env,
            fatal=True,
        )
        steps.append(gate)
        if gate["returncode"] == 0:
            cmd = [
                sys.executable,
                str(hw / "validate_hardware.py"),
                f"--report-dir={REPORT_DIR}",
            ]
            if args.host:
                cmd.append(f"--host={args.host}")
            if args.allow_move:
                cmd.append("--allow-move")
            if args.allow_acquire:
                cmd.append("--allow-acquire")
            steps.append(
                run_step("hardware: the driver against FLUOVIEW", cmd, env, fatal=True)
            )
        else:
            print(
                "\n  ABORT: the limits self-check FAILED; FLUOVIEW is not touched.",
                flush=True,
            )

    total = time.perf_counter() - overall_start
    fatal_failures = [s for s in steps if not s["ok"] and s["fatal"]]
    warn_failures = [s for s in steps if not s["ok"] and not s["fatal"]]
    print(f"\n{_rule()}\nCI SUMMARY\n{_rule()}")
    for step in steps:
        flag = (
            "SKIP"
            if step.get("skipped")
            else "OK  "
            if step["ok"]
            else ("FAIL" if step["fatal"] else "WARN")
        )
        print(f"  [{flag}]  {step['name']:<50}  {step['seconds']:>6.1f}s")
    print(f"\n  total: {total:.1f}s\n  reports written to: {REPORT_DIR}")
    (REPORT_DIR / "ci_summary.json").write_text(
        json.dumps(
            {
                "total_seconds": round(total, 2),
                "passed": not fatal_failures,
                "fatal_failures": [s["name"] for s in fatal_failures],
                "warnings": [s["name"] for s in warn_failures],
                "steps": steps,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if warn_failures:
        print(
            f"\n  WARN: {len(warn_failures)} non-fatal step(s) reported issues -- see above."
        )
    if fatal_failures:
        print(
            f"\n  RESULT: FAILED -- {len(fatal_failures)} fatal step(s): {', '.join(s['name'] for s in fatal_failures)}."
        )
        return 1
    print("\n  RESULT: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
