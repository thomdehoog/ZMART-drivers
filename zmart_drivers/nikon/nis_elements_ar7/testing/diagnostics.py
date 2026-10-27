"""The computer's context, printed at the top of every test run and saved with the reports.

When a test fails on another computer (a GitHub runner, or the PC beside a
microscope), the first question is what was installed there. This module
answers it in two places: ``conftest.py`` prints :func:`header_lines` at the
top of every pytest run, and ``run_ci.py`` runs this file, which prints the
same lines and saves them as ``testing/report/env.json``.

Nothing here may break a run: a missing package or a missing git is reported,
never raised.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``testing/diagnostics.py``.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]

#: The packages whose versions change how the driver behaves. An absent one is
#: reported as absent, because that is itself worth knowing.
TRACKED_PACKAGES = (
    "pytest",
    "pytest-cov",
    "ruff",
    "numpy",
    "tifffile",
    "zarr",
    "ome-zarr-models",
    "zmart-controller",
)


def package_version(name: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(name)
        except PackageNotFoundError:
            return "(absent)"
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - importlib is always there
        return f"(error: {exc})"


def git(*args: str) -> str:
    """One git answer about this repository, or what went wrong."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return done.stdout.strip() or f"(git: {done.stderr.strip() or 'no answer'})"
    except Exception as exc:  # noqa: BLE001 - git may be missing
        return f"(git unavailable: {exc.__class__.__name__})"


def driver_version() -> str:
    try:
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        from zmart_drivers.nikon.nis_elements_ar7 import __version__

        return __version__
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return f"(cannot import: {exc!r})"


def context() -> dict:
    """Everything worth knowing about this computer for reading a test report."""
    return {
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "driver": driver_version(),
        "git": {
            "commit": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        },
        "packages": {name: package_version(name) for name in TRACKED_PACKAGES},
    }


def header_lines() -> list[str]:
    found = context()
    packages = ", ".join(
        f"{name} {version}" for name, version in found["packages"].items()
    )
    return [
        f"nis_elements_ar7 {found['driver']} on Python {found['python']}, {found['platform']}",
        f"git {found['git']['branch']} @ {found['git']['commit']}",
        f"packages: {packages}",
    ]


def write_env_json(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "env.json"
    path.write_text(json.dumps(context(), indent=2), encoding="utf-8")
    return path


def main() -> int:
    for line in header_lines():
        print(line)
    print(f"saved: {write_env_json(_HERE / 'report')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
