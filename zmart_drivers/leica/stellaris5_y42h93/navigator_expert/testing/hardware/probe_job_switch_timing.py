"""Time every reading a job switch makes, to find one that waits.

The full run on the STELLARIS on 2026-10-10 (``SCOPE_RUN_2026-10-10.md``,
section 3.3) saw one job switch wait exactly 12 s, one full waiting budget,
before the command went out. This probe switches jobs through the driver's
controller-facing class, the way ``set_state`` does in a workflow, and prints
every reading the switch makes: which reading, from which source it was
answered, and how long it took. A reading that got no answer shows ``NONE``
and about 12 s.

It switches from the selected job to every other normal job and back, the
given number of rounds, and ends on the job it started with. A job switch can
change the objective, and then the driver moves the stage to keep the sample
point, so the stage must be clear. Nothing is acquired.

Usage (from the repository root, LAS X running):
  python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/hardware/probe_job_switch_timing.py --yes
  python ... --yes --rounds 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_DRIVER_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (  # noqa: E402
    read as readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_driver import (  # noqa: E402
    ZmartDriver,
)


def _timed_readings(t0: float):
    """Wrap the routed readers' one entry point so every reading prints its timing."""
    original = readers.wait_for

    def wait_for(datum, client, **kwargs):
        started = time.monotonic()
        reading = original(datum, client, **kwargs)
        if kwargs.get("deadline") is None:
            answered = reading is not None and reading.error is None
            source = reading.source if answered else "NONE"
            print(
                f"  [{started - t0:7.2f}s] {datum:13s} job={kwargs.get('job_name')!s:10s} "
                f"mode={kwargs.get('mode')!s:6s} -> {source:5s} "
                f"in {time.monotonic() - started:5.2f}s"
            )
        return reading

    readers.wait_for = wait_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="the stage is clear; switch jobs")
    parser.add_argument("--rounds", type=int, default=2, help="round trips per job (default 2)")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("pass --yes once the stage is clear: a job switch can move the stage")

    t0 = time.monotonic()
    _timed_readings(t0)
    connection = json.loads((_DRIVER_ROOT / "zmart_driver.json").read_text(encoding="utf-8"))
    driver = ZmartDriver(connection["connection"])
    try:
        _, (changeable, _) = driver.get_state()
        start = changeable["job"]
        _, settings = driver.get_acquisition_settings()
        others = [name for name in settings["job"]["options"] if name != start]
        print(f"start job: {start!r}; switching to {others} and back, {args.rounds} round(s)")
        for _round in range(args.rounds):
            for target in others:
                for job in (target, start):
                    print(f"--- switch to {job!r}")
                    began = time.monotonic()
                    try:
                        answer = driver.set_state({"job": job})
                        print(f"    {answer} in {time.monotonic() - began:.2f}s")
                    except Exception as exc:
                        print(f"    raised {type(exc).__name__}: {exc}")
                        print(f"    after {time.monotonic() - began:.2f}s")
    finally:
        driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
