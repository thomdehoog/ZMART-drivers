"""The environment a test run happened in, printed at the top of every run and saved beside the reports.

When a test fails on another computer, the first question is what the
environment was. This answers it: the system, Python, the packages that
matter, the git revision, and whether the bridge package is there. Everything
is reported, never raised: diagnostics must not be able to break a run.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TRACKED = (
    "pytest",
    "pytest-cov",
    "ruff",
    "numpy",
    "tifffile",
    "zarr",
    "nis-bridge",
    "zmart-controller",
)


def _version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "(absent)"
    except Exception as why:  # noqa: BLE001 -- reported, never raised
        return f"(error: {why})"


def _git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(_HERE),
            timeout=10,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def context() -> dict:
    return {
        "when": datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "packages": {name: _version(name) for name in _TRACKED},
        "git": {
            "revision": _git(["rev-parse", "--short", "HEAD"]),
            "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        },
    }


def header_lines() -> list[str]:
    found = context()
    packages = ", ".join(
        f"{name} {version}" for name, version in found["packages"].items()
    )
    return [
        f"nis_elements driver: {found['system']}, Python {found['python']}, git {found['git']['branch']}@{found['git']['revision']}",
        f"  packages: {packages}",
    ]


def main() -> int:
    found = context()
    print("\n".join(header_lines()))
    report = _HERE / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "env.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
