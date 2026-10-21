"""At the microscope: many captures, each released from LAS X's cache; does the cache stay flat?

Selects one LAS X job and captures it again and again through the ZMART
Controller, as a workflow does. For this run only, the driver's profile
(``IMAGE_SAVE``) reads each capture from LAS X's cache with
``cleanup_cache`` on, so that LAS X lets go of the capture once it is
saved, and the Navigator canvas, which holds the capture's hidden copy
(``TileScan <n>``), is cleared. It measures LAS X's cache (the
``V2LMSDC_*`` files in LAS X's cache folder) before, every ten captures and
after: in total, how much of it is hidden copies, and the rest. It counts the
captures that failed and the releases whose canvas could not be cleared, and
it fails when the hidden copies grew over the run: a Clear press that goes
through but frees nothing is caught there.

On the LAS X simulator (2026-10-14) a hidden copy took 1.3 to 2 times its
capture's pixels, and all of them were let go of at every release. The canvas
can be cleared only while LAS X shows it, so before each capture the driver
shows Navigator Expert's Experiment tab, when another one is shown.

It never edits a LAS X file: it selects the job, moves the stage to where it
already stands (so every capture gets its canvas position), and captures.
Whether LAS X's AutoSave is on or off makes no difference to it: the cache
route does not use AutoSave, and the check never asks about it. A report is written where the script is run (or to ``--report-dir``), as
``check_cache_release_<label>.json``.

Usage, in the driver's environment on the microscope PC::

    python check_cache_release.py --captures 50 --label fifty
    python check_cache_release.py --job HiRes --captures 50 --label hires-50 --save-to D:/ZMART-check

Without ``--job`` the job selected in LAS X is captured.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

NOT_CLEARED = "the Navigator canvas was not cleared"


class Releases(logging.Handler):
    """Counts the releases whose Navigator canvas was not cleared, and keeps the first reason."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.not_cleared, self.reason = 0, None

    def emit(self, record):
        message = record.getMessage()
        if message.startswith(NOT_CLEARED):
            self.not_cleared += 1
            self.reason = self.reason or message


class Cache:
    """LAS X's cache folder, measured: all of it, the hidden copies, and the rest, in MB."""

    def __init__(self, root: Path):
        self.root, self.names = root, {}

    def name_of(self, number: str) -> str:
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture import lasx_data_container as container

        if number not in self.names:
            description = next(self.root.glob(f"V2LMSDC_OBJ{number}_*.tmp"), None)
            try:
                records = (
                    container.read_records(description.read_bytes())
                    if description
                    else []
                )
                self.names[number] = (
                    (container.first_text(records[2]) or "") if len(records) > 2 else ""
                )
            except OSError:
                self.names[number] = ""
        return self.names[number]

    def measure(self) -> dict[str, float]:
        total = hidden = 0
        for path in self.root.glob("V2LMSDC_*.tmp"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            number = re.match(r"V2LMSDC_(?:OBJ|MMF)(\d+)_", path.name)
            if number and self.name_of(number.group(1)).startswith("TileScan"):
                hidden += size
        return {
            "total": round(total / 2**20, 2),
            "hidden": round(hidden / 2**20, 2),
            "rest": round((total - hidden) / 2**20, 2),
        }


def grown(now: dict, start: dict) -> dict:
    return {part: round(now[part] - start[part], 2) for part in start}


#: How much the hidden copies may grow over a whole run and still count as let
#: go of. A capture's hidden copy is at least the capture's own pixels (about
#: 1 MB for 512 x 512 in three channels), and it stays unless the canvas is
#: cleared; so 1 MB in total is well below what one capture that was not let
#: go of adds, and well above measuring noise.
HIDDEN_MARGIN_MB = 1.0


def verdict(
    *, captures: int, done: int, errors: list, not_cleared: int, hidden_grown_mb: float
) -> dict:
    """The run's checks, by name, True when passed.

    A Clear press that goes through but frees nothing would still count as
    cleared, so the hidden copies are measured too: they must not grow.
    """
    return {
        "every capture saved": done == captures and not errors,
        "the canvas cleared at every release": not_cleared == 0,
        "the hidden copies did not grow": hidden_grown_mb <= HIDDEN_MARGIN_MB,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--job",
        default=None,
        help="the LAS X job to capture (default: the one selected in LAS X)",
    )
    parser.add_argument("--captures", type=int, default=50, help="how many captures")
    parser.add_argument(
        "--label", required=True, help="a name for this check, e.g. hires-50"
    )
    parser.add_argument(
        "--save-to", default=None, help="a folder instead of the set-up data location"
    )
    parser.add_argument(
        "--report-dir", default=".", help="where the report is written (default: here)"
    )
    asked = parser.parse_args()

    from zmart_controller import ZmartController

    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.for_zmart_controller.zmart_driver import (
        ZmartDriver as driver,
    )
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.clear_cache import release as lasx_cache_release
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture import lasx_data_container as container
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.acquisition import acquisition as _acquisition

    releases = Releases()
    logging.getLogger(lasx_cache_release.__name__).addHandler(releases)
    cache = Cache(container.data_container_root())
    report: dict = {
        "job": asked.job,
        "label": asked.label,
        "captures": asked.captures,
        "cache folder": str(cache.root),
        "progress": [],
        "errors": [],
    }

    _acquisition.IMAGE_SAVE = replace(
        _acquisition.IMAGE_SAVE,
        format="ome-zarr",
        projection="none",
        cleanup_cache=True,
    )
    session = ZmartController(
        driver, {"output_root": asked.save_to} if asked.save_to else {}
    )
    try:
        chosen = (
            session.set_state({"changeable": {"job": asked.job}})
            if asked.job
            else {"success": True}
        )
        report["job"] = session.get_state()["content"]["changeable"].get("job")
        if not chosen["success"]:
            raise SystemExit(f"the job was not selected: {chosen['content']}")
        here = session.get_xyz()["content"]
        session.set_xyz(*(float(here[axis]["position"]) for axis in "xyz"))
        start = report["cache before, MB"] = cache.measure()
        print(
            f"before: {start['total']:.1f} MB, of which hidden copies {start['hidden']:.1f} MB",
            flush=True,
        )
        began, failed = time.perf_counter(), 0
        for i in range(1, asked.captures + 1):
            answer = session.acquire(position_label=f"t{i:04d}")
            if not answer["success"]:
                failed += 1
                report["errors"].append(f"capture {i}: {answer['content']}"[:300])
            if i % 10 == 0 or i == asked.captures:
                step = {
                    "captures": i,
                    "s": round(time.perf_counter() - began, 1),
                    "grown, MB": grown(cache.measure(), start),
                    "failed": failed,
                    "canvas not cleared": releases.not_cleared,
                }
                report["progress"].append(step)
                g = step["grown, MB"]
                print(
                    f"{i:4d} captures, {step['s']:6.0f} s: cache +{g['total']:.1f} MB (hidden copies "
                    f"+{g['hidden']:.1f}, rest +{g['rest']:.1f}); failed {failed}; "
                    f"canvas not cleared {releases.not_cleared}",
                    flush=True,
                )
    finally:
        session.disconnect()

    end = report["cache after, MB"] = cache.measure()
    done = report["progress"][-1]["captures"] if report["progress"] else 0
    g = grown(end, start)
    report["per capture, MB"] = (
        {part: round(value / done, 3) for part, value in g.items()} if done else None
    )
    report["canvas not cleared"] = releases.not_cleared
    report["why not cleared"] = releases.reason
    report["checks"] = verdict(
        captures=asked.captures,
        done=done,
        errors=report["errors"],
        not_cleared=releases.not_cleared,
        hidden_grown_mb=g["hidden"],
    )
    out = (
        Path(asked.report_dir)
        / f"check_cache_release_{re.sub(r'[^A-Za-z0-9_-]', '-', asked.label)}.json"
    )
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    if report["per capture, MB"]:
        per = report["per capture, MB"]
        print(
            f"per capture: cache +{per['total']:.2f} MB, of which hidden copies +{per['hidden']:.2f} MB, "
            f"rest +{per['rest']:.2f} MB"
        )
    if releases.reason:
        print(releases.reason)
    failing = [name for name, ok in report["checks"].items() if not ok]
    print(f"report: {out}")
    print("ALL CHECKS PASSED" if not failing else "FAILED: " + "; ".join(failing))
    return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())
