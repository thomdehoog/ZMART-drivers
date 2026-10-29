#!/usr/bin/env python3
"""The check at the microscope: the driver against a real ZEN (or ZEISS's simulator), step by step.

It reads by default and changes nothing: it connects, reads the stage, the
focus, the objectives, the experiments and ZEN's image folder, and checks
that the limits are installed and refuse a move far outside them. With
``--allow-move`` it moves the stage 50 µm along x and y and the focus 5 µm,
each within the limits, reads every move back and returns. With
``--allow-acquire`` it loads ``--experiment`` and takes one snap into the
report folder, and checks that the file and its planes are there.

Each step prints PASS or FAIL with what it saw, and the run writes
``check_on_zen.json`` into ``--report-dir``. Run it through ``run_ci.py
--hardware``, or directly. The connection is the driver's default, with the
values given here::

    python check_on_zen.py --host 127.0.0.1 --port 5002 --password <token> --allow-move

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: How far the small moves go, in micrometres.
XY_STEP_UM = 50.0
Z_STEP_UM = 5.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the ZEISS driver against ZEN.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--password", help="the gateway's control token")
    parser.add_argument("--cert-file")
    parser.add_argument("--zen-config-ini")
    parser.add_argument("--zen-images")
    parser.add_argument("--experiment")
    parser.add_argument("--allow-move", action="store_true")
    parser.add_argument("--allow-acquire", action="store_true")
    parser.add_argument("--report-dir", default=".")
    args = parser.parse_args(argv)

    import zmart_drivers.zeiss.light_microscope.zen_api_grpc as zen
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher import (
        limits as gate,
    )

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def step(name, fn):
        started = time.perf_counter()
        try:
            seen = fn()
            results.append(
                {
                    "step": name,
                    "ok": True,
                    "seen": seen,
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
            print(f"PASS  {name}: {seen}")
            return seen
        except Exception as why:  # noqa: BLE001 -- every failure is reported, the run goes on where it can
            results.append(
                {
                    "step": name,
                    "ok": False,
                    "error": f"{type(why).__name__}: {why}",
                    "trace": traceback.format_exc(),
                }
            )
            print(f"FAIL  {name}: {type(why).__name__}: {why}")
            return None

    connection = {
        "host": args.host,
        "port": args.port,
        "password": args.password,
        "cert_file": args.cert_file,
        "zen_config_ini": args.zen_config_ini,
        "zen_images": args.zen_images,
        "experiment": args.experiment if args.allow_acquire else None,
        "output_root": str(report_dir / "captures"),
    }
    opened = {}

    def connect():
        opened["scope"] = zen.connect(
            **{k: v for k, v in connection.items() if v is not None}
        )
        return opened["scope"].handle.client.runtime

    step("connect", connect)
    if "scope" not in opened:
        return finish(report_dir, results)
    scope = opened["scope"]
    try:
        start = step(
            "read the stage and the focus",
            lambda: {a: scope.position()[a]["actuator_readings"] for a in "xyz"},
        )
        step(
            "read the objectives",
            lambda: [(o["position"], o["name"]) for o in scope.objectives()],
        )
        step("read the experiments", lambda: scope.experiments())
        step("ZEN's image folder", lambda: scope.handle.image_output_path)
        step(
            "the limits are installed",
            lambda: (
                gate.describe(scope.client) or _fail("no limits govern the session")
            ),
        )
        step("a move far outside the limits is refused", lambda: _refused(scope))
        if args.allow_move and start:
            here = scope.position()
            x, y, z = (here[a]["position"] for a in "xyz")
            step(
                "a small move along x and y, read back",
                lambda: _moved(
                    scope.move_to(x + XY_STEP_UM, y + XY_STEP_UM, z),
                    x + XY_STEP_UM,
                    y + XY_STEP_UM,
                    z,
                ),
            )
            step(
                "a small focus move, read back",
                lambda: _moved(
                    scope.move_to(x + XY_STEP_UM, y + XY_STEP_UM, z + Z_STEP_UM),
                    x + XY_STEP_UM,
                    y + XY_STEP_UM,
                    z + Z_STEP_UM,
                ),
            )
            step(
                "back where it started", lambda: _moved(scope.move_to(x, y, z), x, y, z)
            )
        if args.allow_acquire:
            step("one snap, saved with its planes", lambda: _snapped(scope))
        step("no change was left unconfirmed", lambda: _none_unconfirmed(scope))
    finally:
        scope.close()
    return finish(report_dir, results)


def _fail(what):
    raise RuntimeError(str(what))


def _none_unconfirmed(scope) -> str:
    if scope.handle.unconfirmed:
        raise RuntimeError(
            f"changes were sent but never seen: {scope.handle.unconfirmed}"
        )
    return "none"


def _refused(scope) -> str:
    try:
        scope.move_to(1e9, 0.0, 0.0)
    except RuntimeError as why:
        if "refused" in str(why):
            return "refused, nothing sent"
        raise
    raise RuntimeError("a move to 1e9 um was not refused")


def _moved(answer: dict, x: float, y: float, z: float) -> dict:
    seen = {a: answer[a]["position"] for a in "xyz"}
    for axis, wanted, tolerance in (("x", x, 1.0), ("y", y, 1.0), ("z", z, 0.5)):
        if abs(seen[axis] - wanted) > tolerance:
            raise RuntimeError(f"{axis} read back {seen[axis]:.2f}, asked {wanted:.2f}")
    return seen


def _snapped(scope) -> dict:
    scope.acquire(
        "check",
        acquisition_type="hardware_check",
        acquisition_settings={"trigger": "snap"},
    )
    (saved,) = scope.save()
    if not Path(saved["czi"]).is_file() or not saved["planes"]:
        raise RuntimeError(f"the snap was not saved as expected: {saved}")
    return {"czi": saved["czi"], "planes": len(saved["planes"])}


def finish(report_dir: Path, results: list[dict]) -> int:
    (report_dir / "check_on_zen.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    failed = [r for r in results if not r["ok"]]
    print(
        f"\n{len(results) - len(failed)} passed, {len(failed)} failed; the record is {report_dir / 'check_on_zen.json'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
