#!/usr/bin/env python3
"""The driver at NIS-Elements: a short check on the Ti2 simulator or a microscope, with the bridge running.

    python check_microscope.py --yes --save-to D:\\ZMART-data\\check

It connects as a user does, reads everything, moves the stage 20 micrometres
along x and back, selects the first optical configuration, sets the exposure,
takes one picture and saves it, and prints what it found. Every change is
read back; each line says PASS or FAIL. Nothing else is touched, and the stage
ends where it started. Keep the objective clear of the sample on a first run.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zmart_drivers.nikon.ti2 import nis_elements

STEP_UM = 20.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the driver at NIS-Elements.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm that the stage may move 20 um and a picture may be taken",
    )
    parser.add_argument(
        "--save-to", required=True, help="the folder the picture is saved in"
    )
    parser.add_argument(
        "--report", default=None, help="write the findings as JSON here"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="the bridge's port, when not the default"
    )
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error(
            "this check moves the stage by 20 um and takes a picture; add --yes to confirm"
        )
    findings = []

    def check(name, fn):
        started = time.perf_counter()
        try:
            value = fn()
            findings.append(
                {
                    "check": name,
                    "pass": True,
                    "value": value,
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
            print(f"PASS  {name}: {json.dumps(value, default=str)[:200]}")
        except Exception as why:  # noqa: BLE001 -- every failure is a finding
            findings.append(
                {
                    "check": name,
                    "pass": False,
                    "error": f"{type(why).__name__}: {why}",
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
            print(f"FAIL  {name}: {type(why).__name__}: {why}")

    save_to = Path(args.save_to)
    save_to.mkdir(parents=True, exist_ok=True)
    scope = nis_elements.connect(
        output_root=str(save_to), **({"port": args.port} if args.port else {})
    )
    try:
        check("the bridge answers", lambda: scope.client.info)
        state = scope.state()
        check(
            "the state is read",
            lambda: {
                "changeable": state["changeable"],
                "setup": state["observed"]["setup"],
            },
        )
        start = scope.position()
        check(
            "the position is read",
            lambda: {axis: start[axis]["position"] for axis in ("x", "y", "z")},
        )
        x, y, z = (start[axis]["position"] for axis in ("x", "y", "z"))
        check(
            f"a move of {STEP_UM:g} um along x is confirmed",
            lambda: scope.move_to(x + STEP_UM, y, z)["x"]["position"],
        )
        check("and the move back", lambda: scope.move_to(x, y, z)["x"]["position"])
        channels = scope.channels()["channels"]
        if channels:
            check(
                f"the optical configuration {channels[0]!r} is selected",
                lambda: scope.select_channel(channels[0]),
            )
        else:
            findings.append(
                {
                    "check": "an optical configuration is selected",
                    "pass": False,
                    "error": "NIS-Elements has none; make one first",
                }
            )
        exposure = scope.exposure()
        check(
            "the exposure is set and read back",
            lambda: scope.set_exposure(exposure or 20.0),
        )
        check(
            "one picture is taken and saved as OME-Zarr",
            lambda: (scope.acquire("check", acquisition_type="check"), scope.save())[1][
                0
            ]["files"],
        )
        check(
            "the unconfirmed changes", lambda: scope.state()["observed"]["unconfirmed"]
        )
    finally:
        scope.close()
    failed = [f for f in findings if not f["pass"]]
    if args.report:
        Path(args.report).write_text(
            json.dumps(findings, indent=2, default=str), encoding="utf-8"
        )
    print(f"\n{len(findings) - len(failed)} of {len(findings)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
