"""Reproduce the slow job switch: a burst of small stage moves, then a switch.

On the STELLARIS on 2026-10-10 the adapter validator's job switch waited 10 to
12 s before the ``SelectJob`` command went out, while the plain switch probe
(``probe_job_switch_timing.py``) saw every switch finish in under a second.
The difference is what comes just before: the validator switches right after
a burst of stage moves. This probe does the same, ten small XY moves and back,
then a switch, and prints three things:

- every reading the driver makes, with its source and duration (as the
  switch probe does);
- every call that took longer than 0.3 s, by name, among the log parser, the
  limits gate and the pre-fire checks of a job switch;
- how many threads are alive at each step.

On the microscope this showed about 60 threads alive after the burst, 65 log
parses of 10 s each where one alone takes 0.1 s, and the switch's own log
read stuck behind them for 7 to 10 s (see ``SCOPE_CHECK_JOB_SWITCH.md``).

The moves are 25 um around the current position, with the z-galvo, and end
where they started. Nothing is acquired. The stage must be clear.

Usage (from the repository root, LAS X running):
  python zmart_drivers/leica/stellaris5_y42h93/navigator_expert/testing/hardware/probe_switch_after_moves.py --yes
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

_DRIVER_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (  # noqa: E402
    confirm_select_job as _confirm_select_job,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (  # noqa: E402
    objective_shift as _objective_shift,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (  # noqa: E402
    gate as _gate,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (  # noqa: E402
    read as _readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (  # noqa: E402
    log_reader as _log_reader,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_driver import (  # noqa: E402
    ZmartDriver,
)

#: A call slower than this is printed by name.
SLOW_S = 0.3

_T0 = time.monotonic()


def _say(text: str) -> None:
    print(f"[{time.monotonic() - _T0:7.2f}s] {text}   [threads alive: {threading.active_count()}]", flush=True)


def _time_readings() -> None:
    """Print every reading the driver makes, as probe_job_switch_timing does."""
    original = _readers.wait_for

    def wait_for(datum, client, **kwargs):
        started = time.monotonic()
        reading = original(datum, client, **kwargs)
        if kwargs.get("deadline") is None:
            answered = reading is not None and reading.error is None
            source = reading.source if answered else "NONE"
            print(
                f"  [{started - _T0:7.2f}s] {datum:13s} job={kwargs.get('job_name')!s:10s} "
                f"mode={kwargs.get('mode')!s:6s} -> {source:5s} "
                f"in {time.monotonic() - started:5.2f}s",
                flush=True,
            )
        return reading

    _readers.wait_for = wait_for


def _time_slow_calls() -> None:
    """Print, by name, every watched call that takes longer than SLOW_S."""

    def watch(module, name):
        function = getattr(module, name)

        def timed(*args, **kwargs):
            started = time.monotonic()
            try:
                return function(*args, **kwargs)
            finally:
                took = time.monotonic() - started
                if took > SLOW_S:
                    short = module.__name__.rsplit(".", 1)[-1]
                    print(f"  [{started - _T0:7.2f}s] SLOW {short}.{name} took {took:5.2f}s", flush=True)

        setattr(module, name, timed)

    watch(_log_reader, "parse_log")
    watch(_gate, "check_refusal")
    watch(_confirm_select_job, "prepare_select_job")
    watch(_confirm_select_job, "_selected_job_name_from_log")
    watch(_objective_shift, "record_before_change")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="the stage is clear; move and switch")
    parser.add_argument("--moves", type=int, default=10, help="moves in the burst (default 10)")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("pass --yes once the stage is clear: this probe moves the stage and switches jobs")

    _time_readings()
    _time_slow_calls()
    connection = json.loads((_DRIVER_ROOT / "zmart_driver.json").read_text(encoding="utf-8"))
    driver = ZmartDriver(connection["connection"])
    try:
        _, (changeable, _) = driver.get_state()
        start = changeable["job"]
        _, settings = driver.get_acquisition_settings()
        other = next(name for name in settings["job"]["options"] if name != start)
        _, (x, y, z, _actuators, _canvas) = driver.get_xyz()
        _say(f"start job={start!r} at x={x:.1f} y={y:.1f} z={z:.1f}")

        _say(f"--- burst: {args.moves} XY moves of 25 um, then back to the start")
        for index in range(args.moves):
            sign = -1 if index % 2 == 0 else 1
            driver.set_xyz(x + sign * 25, y + sign * 25, z, with_actuators={"z": "z-galvo"})
        driver.set_xyz(x, y, z, with_actuators={"z": "z-galvo"})
        _say("--- moves done; get_state, as the validator does before it switches")
        driver.get_state()

        for job in (other, start):
            _say(f"--- switch to {job!r}")
            began = time.monotonic()
            answer = driver.set_state({"job": job})
            _say(f"    {answer} in {time.monotonic() - began:.2f}s")
    finally:
        driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
