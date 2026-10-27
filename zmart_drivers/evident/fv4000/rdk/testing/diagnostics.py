"""Environment context for triaging a failure on another computer: printed at the top of every run and saved as env.json.

Everything here is defensive: a missing package or an unimportable driver is
reported, never raised. Diagnostics must not be able to break a run.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent

_TRACKED_PACKAGES = (
    "pytest",
    "pytest-cov",
    "coverage",
    "ruff",
    "numpy",
    "zarr",
    "ome-zarr-models",
    "ome-types",
    "zmart-controller",
)


def _package_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "(absent)"
    except Exception as exc:  # pragma: no cover
        return f"(error: {exc})"


def _git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=str(_HERE), timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def context() -> dict:
    """The environment as a JSON-serialisable dictionary."""
    ctx = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "node": platform.node(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
        "pythonpath": os.environ.get("PYTHONPATH", ""),
        "packages": {name: _package_version(name) for name in _TRACKED_PACKAGES},
        "git_branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_sha": _git(["rev-parse", "--short", "HEAD"]),
        "git_dirty": bool(_git(["status", "--porcelain"])),
    }
    try:
        import zmart_drivers.evident.fv4000.rdk as driver

        ctx["driver_version"] = getattr(driver, "__version__", "(unknown)")
        ctx["driver_path"] = str(Path(driver.__file__).resolve().parent)
    except Exception as exc:
        ctx["driver_version"] = f"IMPORT FAILED: {type(exc).__name__}: {exc}"
        ctx["driver_path"] = ""
    return ctx


def header_lines() -> list[str]:
    c = context()
    packages = "  ".join(f"{name}={ver}" for name, ver in c["packages"].items())
    git = f"{c['git_branch'] or '?'}@{c['git_sha'] or '?'}{' (dirty)' if c['git_dirty'] else ''}"
    return [
        f"fv4000 rdk test context  ({c['timestamp']})",
        f"  platform : {c['platform']}  [{c['machine']}]  node={c['node']}",
        f"  python   : {c['python_version']}  [{c['python_executable']}]",
        f"  driver   : fv4000 rdk {c['driver_version']}",
        f"  location : {c['driver_path'] or '(not importable)'}",
        f"  git      : {git}",
        f"  packages : {packages}",
    ]


def write_env_json(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / "env.json"
    target.write_text(json.dumps(context(), indent=2), encoding="utf-8")
    return target


def main() -> int:
    repo_root = _HERE.parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    for line in header_lines():
        print(line)
    print(f"  (context written to {write_env_json(_HERE / 'report')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
