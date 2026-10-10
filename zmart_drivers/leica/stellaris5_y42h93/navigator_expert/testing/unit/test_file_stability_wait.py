"""The wait for an exported file gives up on no progress, never on a long write.

LAS X writes its OME-TIFF while the driver watches it. A large tile scan or a
deep stack can take longer to write than any fixed timeout, so the timeout is
a no-progress budget: a file that keeps growing is waited for as long as it
grows, and only a file that has stopped changing yet never becomes readable
is given up on.
"""

from __future__ import annotations

import threading
import time

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import (
    files as _files,
)


def test_a_file_that_keeps_growing_longer_than_the_budget_is_still_waited_for(tmp_path):
    path = tmp_path / "export.ome.tif"
    budget_s = 0.3  # much shorter than the write below

    def write_slowly():
        with path.open("wb") as handle:
            for _ in range(12):  # grows for about 0.6 s, twice the budget
                handle.write(b"x" * 1024)
                handle.flush()
                time.sleep(0.05)

    writer = threading.Thread(target=write_slowly)
    writer.start()
    try:
        stable = _files._wait_file_stable(path, budget_s, poll_interval=0.02, stable_readings=3)
    finally:
        writer.join()

    assert stable is True
    assert path.stat().st_size == 12 * 1024


def test_a_file_that_never_grows_is_given_up_on_after_the_budget(tmp_path):
    path = tmp_path / "export.ome.tif"
    path.write_bytes(b"")  # exists, but empty: never readable

    started = time.perf_counter()
    stable = _files._wait_file_stable(path, 0.2, poll_interval=0.02, stable_readings=3)

    assert stable is False
    assert 0.2 <= time.perf_counter() - started < 1.5


def test_wait_all_stable_gives_every_file_its_own_budget(tmp_path):
    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    first.write_bytes(b"done")
    second.write_bytes(b"done")

    outcome = _files.wait_all_stable([first, second], timeout=0.5, poll_interval=0.02)

    assert outcome["success"] is True
