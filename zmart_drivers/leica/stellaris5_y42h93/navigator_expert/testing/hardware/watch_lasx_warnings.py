"""At the microscope: does the driver see LAS X's warnings, and answer the OK-only ones?

The driver reads each dialog LAS X opens from ``LAS X.log`` and finds its
window through Windows UI Automation (``primitives/interface/lasx_dialogs.py``).
Both were seen only on the LAS X simulator. This script shows them on the
microscope, in two steps:

- ``--look``: presses nothing. Each time LAS X opens or closes a dialog it
  prints what the log says (its caption and text) and the dialog windows
  found on screen with their buttons. A warning the driver would answer
  shows caption ``Warning`` and the one button ``ButtonMessageOk``. Close
  the dialog yourself.
- without ``--look``: the driver's own watcher, as a connected driver runs
  it. Every warning whose only button is OK is answered and its text
  printed; any other dialog is left open for you, and printed.

Needs no CAM API connection. Usage, in the driver's environment::

    python watch_lasx_warnings.py --look --minutes 5
    python watch_lasx_warnings.py --minutes 5
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

POLL_S = 0.5


def look(minutes: float) -> None:
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.primitives.interface import (
        lasx_dialogs,
    )

    lasx_dialogs.pending()  # what the log said before the look starts
    print(f"looking for {minutes:g} min; open a LAS X dialog now", flush=True)
    shown, until = None, time.monotonic() + minutes * 60
    while time.monotonic() < until:
        dialog = lasx_dialogs.pending()
        if dialog != shown:
            shown = dialog
            if dialog is None:
                print("the log says: the dialog was closed", flush=True)
            else:
                print(
                    f"the log says: {dialog.caption}: {dialog.text!r} (shown {dialog.at})",
                    flush=True,
                )
                try:
                    windows = lasx_dialogs.lasx_windows().dialogs()
                    print(
                        f"  on screen: {len(windows)} dialog window(s), buttons {[w.buttons for w in windows]}",
                        flush=True,
                    )
                except lasx_dialogs.WindowsUnavailable as why:
                    print(f"  on screen: not found: {why}", flush=True)
        time.sleep(POLL_S)


def answer(minutes: float) -> None:
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.primitives.interface import (
        lasx_dialogs,
    )

    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
    )
    lasx_dialogs.keep_watching()
    print(
        f"the driver's watcher answers OK-only warnings for {minutes:g} min", flush=True
    )
    try:
        time.sleep(minutes * 60)
    finally:
        lasx_dialogs.stop_watching()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--look", action="store_true", help="press nothing; print what is seen"
    )
    parser.add_argument("--minutes", type=float, default=5, help="how long to watch")
    asked = parser.parse_args()
    (look if asked.look else answer)(asked.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
