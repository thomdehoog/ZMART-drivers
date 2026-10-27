#!/usr/bin/env python3
"""The driver against a live FLUOVIEW: reads first, a move and a capture only when asked.

Each check prints PASS or FAIL with what was read, and everything goes into
``<report-dir>/hardware_check.json``. The move is 20 µm along X and back, through
the limits; the capture is whatever is set up in FLUOVIEW, saved under the
report folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MOVE_UM = 20.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the FV4000 driver against a live FLUOVIEW."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--allow-move", action="store_true")
    parser.add_argument("--allow-acquire", action="store_true")
    parser.add_argument("--report-dir", default=str(HERE.parents[1] / "report"))
    args = parser.parse_args(argv)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, fn):
        started = time.perf_counter()
        try:
            value = fn()
            checks.append(
                {
                    "check": name,
                    "ok": True,
                    "value": value,
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
            print(f"PASS: {name}: {value}")
        except Exception as exc:  # noqa: BLE001 -- every failure is reported, the run goes on
            checks.append(
                {
                    "check": name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
            print(f"FAIL: {name}: {type(exc).__name__}: {exc}")

    import zmart_drivers.evident.fv4000.rdk as fv4000

    scope = None

    def connect():
        nonlocal scope
        scope = fv4000.connect(
            host=args.host, port=args.port, output_root=str(report_dir / "captures")
        )
        return scope.handle.fluoview_version

    check("connect, FLUOVIEW's version", connect)
    if scope is None:
        _write(report_dir, checks)
        return 1
    try:
        check(
            "the stage and the focus",
            lambda: {
                axis: scope.position()[axis]["position"] for axis in ("x", "y", "z")
            },
        )
        check("the objectives", scope.objectives)
        check(
            "the state",
            lambda: {
                key: value
                for key, value in scope.state()["changeable"].items()
                if key not in ("channels", "lasers")
            },
        )
        check("the channels", lambda: list(scope.state()["changeable"]["channels"]))
        check("the lasers", lambda: list(scope.state()["changeable"]["lasers"]))
        if args.allow_move:

            def move_and_back():
                home = scope.position()
                x, y, z = (home[a]["position"] for a in ("x", "y", "z"))
                there = scope.move_to(x + MOVE_UM, y, z)
                back = scope.move_to(x, y, z)
                return {
                    "there_x": there["x"]["position"],
                    "back_x": back["x"]["position"],
                    "unconfirmed": scope.handle.unconfirmed[-2:],
                }

            check(f"a {MOVE_UM:g} um move along X and back", move_and_back)
        if args.allow_acquire:

            def capture():
                taken = scope.acquire(
                    "hardware_check",
                    acquisition_type="check",
                    output_folder=str(report_dir / "captures"),
                )
                (saved,) = scope.save(taken["position_label"])
                return {
                    "fluoview_file": taken["fluoview_file"],
                    "store": saved["files"][0],
                    "planes": len(saved["planes"]),
                }

            check("one capture, saved as OME-Zarr", capture)
    finally:
        check("close (FLUOVIEW back in LOCAL mode)", scope.close)
    _write(report_dir, checks)
    return 0 if all(c["ok"] for c in checks) else 1


def _write(report_dir: Path, checks: list) -> None:
    (report_dir / "hardware_check.json").write_text(
        json.dumps(checks, indent=2, default=str), encoding="utf-8"
    )
    print(f"report: {report_dir / 'hardware_check.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
