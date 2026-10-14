"""At the microscope: many captures, each released from LAS X's cache; does the cache stay flat?

Captures one LAS X job again and again through the ZMART Controller, each
saved from LAS X's cache with ``cleanup_source`` on, so that the driver has
LAS X let go of the capture and clears the Navigator canvas, which holds the
capture's hidden copy (``TileScan <n>``). It measures LAS X's cache (the
``V2LMSDC_*`` files in LAS X's cache folder) before, every ten captures and
after: in total, how much of it is hidden copies, and the rest. It counts the
captures that failed and the releases whose canvas could not be cleared.

On the LAS X simulator (2026-10-14) a hidden copy took 1.3 to 2 times its
capture's pixels, and all of them were let go of while the Navigator was shown. Keep
the Navigator shown in LAS X for the whole run: with another view shown, the
canvas cannot be cleared and the hidden copies stay.

It never edits a LAS X file: it selects the job, moves the stage to where it
already stands (so every capture gets its canvas position), and captures.
A report is written where the script is run, as ``check_cache_release_<label>.json``.

Usage, in the driver's environment on the microscope PC::

    python check_cache_release.py --job HiRes --captures 50 --label hires-50
    python check_cache_release.py --job HiRes --captures 50 --label hires-50 --save-to D:/ZMART-check
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
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
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import lasx_data_container as container

        if number not in self.names:
            description = next(self.root.glob(f"V2LMSDC_OBJ{number}_*.tmp"), None)
            try:
                records = container.read_records(description.read_bytes()) if description else []
                self.names[number] = (container.first_text(records[2]) or "") if len(records) > 2 else ""
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
        return {"total": round(total / 2**20, 2), "hidden": round(hidden / 2**20, 2), "rest": round((total - hidden) / 2**20, 2)}


def grown(now: dict, start: dict) -> dict:
    return {part: round(now[part] - start[part], 2) for part in start}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="the LAS X job to capture")
    parser.add_argument("--captures", type=int, default=50, help="how many captures")
    parser.add_argument("--label", required=True, help="a name for this check, e.g. hires-50")
    parser.add_argument("--save-to", default=None, help="a folder instead of the set-up data location")
    asked = parser.parse_args()

    from zmart_controller import ZmartController

    import zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_controller_plugin as driver
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import lasx_cache_release
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import lasx_data_container as container

    releases = Releases()
    logging.getLogger(lasx_cache_release.__name__).addHandler(releases)
    cache = Cache(container.data_container_root())
    settings = {"job": asked.job, "source": "cache", "format": "ome-zarr", "folder": "cache-release-check",
                "cleanup_source": True, "projection": "none"}
    if asked.save_to:
        settings["save_to"] = asked.save_to
    report: dict = {"job": asked.job, "label": asked.label, "captures": asked.captures,
                    "cache folder": str(cache.root), "progress": [], "errors": []}

    session = ZmartController(driver, {})
    try:
        here = session.get_xyz()["content"]
        session.set_xyz(*(float(here[axis]["position"]) for axis in "xyz"))
        start = report["cache before, MB"] = cache.measure()
        print(f"before: {start['total']:.1f} MB, of which hidden copies {start['hidden']:.1f} MB", flush=True)
        began, failed = time.perf_counter(), 0
        for i in range(1, asked.captures + 1):
            answer = session.acquire(position_label=f"t{i:04d}", acquisition_settings=settings)
            if not answer["success"]:
                failed += 1
                report["errors"].append(f"capture {i}: {answer['content']}"[:300])
            if i % 10 == 0 or i == asked.captures:
                step = {"captures": i, "s": round(time.perf_counter() - began, 1),
                        "grown, MB": grown(cache.measure(), start), "failed": failed,
                        "canvas not cleared": releases.not_cleared}
                report["progress"].append(step)
                g = step["grown, MB"]
                print(f"{i:4d} captures, {step['s']:6.0f} s: cache +{g['total']:.1f} MB (hidden copies "
                      f"+{g['hidden']:.1f}, rest +{g['rest']:.1f}); failed {failed}; "
                      f"canvas not cleared {releases.not_cleared}", flush=True)
    finally:
        session.disconnect()

    end = report["cache after, MB"] = cache.measure()
    done = report["progress"][-1]["captures"] if report["progress"] else 0
    g = grown(end, start)
    report["per capture, MB"] = {part: round(value / done, 3) for part, value in g.items()} if done else None
    report["canvas not cleared"] = releases.not_cleared
    report["why not cleared"] = releases.reason
    report["checks"] = {
        "every capture saved": done == asked.captures and not report["errors"],
        "the canvas cleared at every release": releases.not_cleared == 0,
    }
    out = Path(f"check_cache_release_{re.sub(r'[^A-Za-z0-9_-]', '-', asked.label)}.json")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    if report["per capture, MB"]:
        per = report["per capture, MB"]
        print(f"per capture: cache +{per['total']:.2f} MB, of which hidden copies +{per['hidden']:.2f} MB, "
              f"rest +{per['rest']:.2f} MB")
    if releases.reason:
        print(releases.reason)
    failing = [name for name, ok in report["checks"].items() if not ok]
    print(f"report: {out}")
    print("ALL CHECKS PASSED" if not failing else "FAILED: " + "; ".join(failing))
    return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())
