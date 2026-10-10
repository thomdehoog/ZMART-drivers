"""One parse of the LAS X log at a time, shared by readers asking together.

On the STELLARIS (2026-10-10, SCOPE_CHECK_JOB_SWITCH.md "What was found") a
burst of moves left about sixty log parses running at once: every reading's
race started its own 0.1 s CPU-bound parse of the log's last 4 MB, a stopped
race did not stop a parse in flight, and a job switch's own log read queued
behind them for 8 to 12 s. The live log is now parsed by one parse at a time,
and a reader only gets a parse begun after it asked: readers asking at the
same time share one.
"""

import threading
import time
from unittest.mock import patch

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    read as readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import tuning
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    log_reader,
)

PARSE_S = 0.05


@pytest.fixture(autouse=True)
def fresh_shared_parse(monkeypatch):
    monkeypatch.setattr(log_reader, "LIVE_LOG", log_reader.SharedParse())
    monkeypatch.setattr(tuning, "POLL_S", 0.02)


class SlowParse:
    """A stand-in for one parse of the files: takes PARSE_S, counts overlap."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = 0
        self.most_at_once = 0
        self.calls = 0

    def __call__(self, *args, **kwargs):
        with self.lock:
            self.calls += 1
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(PARSE_S)
        with self.lock:
            self.running -= 1
        return log_reader.Snapshot(now=time.time())


def test_sixty_readers_at_once_run_one_parse_at_a_time():
    slow = SlowParse()
    with patch.object(log_reader, "parse_files", side_effect=slow):
        threads = [threading.Thread(target=log_reader.parse_log) for _ in range(60)]
        t0 = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        elapsed = time.monotonic() - t0
    assert slow.most_at_once == 1
    assert slow.calls <= 3
    assert elapsed < 10 * PARSE_S


def test_a_snapshot_parsed_before_a_reader_asked_is_never_handed_to_it():
    """The simulator, 2026-10-11: right after a move, a cached snapshot from just
    before the move's confirmation still held the old position, fresh by its
    line's age, and won the race. A reader only gets a parse begun after it asked.
    """
    slow = SlowParse()
    with patch.object(log_reader, "parse_files", side_effect=slow):
        first = log_reader.parse_log()
        second = log_reader.parse_log()
    assert second is not first
    assert slow.calls == 2


def test_readers_asking_during_a_parse_share_the_next_one():
    slow = SlowParse()
    results = []
    with patch.object(log_reader, "parse_files", side_effect=slow):
        first = threading.Thread(target=lambda: results.append(log_reader.parse_log()))
        first.start()
        time.sleep(PARSE_S / 3)
        late = [threading.Thread(target=lambda: results.append(log_reader.parse_log())) for _ in range(5)]
        for thread in late:
            thread.start()
        for thread in [first, *late]:
            thread.join(5.0)
    assert slow.calls == 2
    assert slow.most_at_once == 1
    assert len({id(snapshot) for snapshot in results[1:]}) == 1  # the five late ones share one


def test_explicit_files_or_lines_are_parsed_directly():
    """Tests and offline tools that name their input never share the live snapshot."""
    slow = SlowParse()
    with patch.object(log_reader, "parse_files", side_effect=slow):
        log_reader.parse_log()
    direct = log_reader.parse_log(lines=[], now=100.0)
    assert direct.now == 100.0
    assert slow.calls == 1


def test_a_failed_parse_is_not_shared():
    calls = []

    def fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("log locked")
        return log_reader.Snapshot(now=time.time())

    with patch.object(log_reader, "parse_files", side_effect=fails_once):
        with pytest.raises(OSError):
            log_reader.parse_log()
        assert log_reader.parse_log() is not None
    assert len(calls) == 2


def test_a_log_worker_whose_race_is_won_starts_no_parse():
    stop = threading.Event()
    stop.set()
    answers = []
    spec = readers.capabilities.spec("xy")
    with patch.object(log_reader, "parse_log") as parse:
        readers._log_worker(
            spec, None, 1.0, deadline=time.monotonic() + 1.0, stop=stop, answers=answers
        )
    parse.assert_not_called()
    assert answers == []


def test_sixty_racing_readings_never_overlap_two_parses():
    """The scope's burst: many readings racing at once, the API answering fast."""
    slow = SlowParse()
    with (
        patch.object(log_reader, "parse_files", side_effect=slow),
        patch.object(readers.api_reader, "get_xy", return_value={"x_um": 1.0, "y_um": 2.0}),
    ):
        threads = [
            threading.Thread(target=lambda: readers.get_xy(object())) for _ in range(60)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        time.sleep(3 * PARSE_S)
    assert slow.most_at_once == 1


def test_a_snapshot_counts_only_for_the_log_files_it_was_parsed_from(monkeypatch, tmp_path):
    """Pointing the reader at other files (the --mock validators do) never serves the old ones."""
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import profiles

    seen = []

    def parse(*args, **kwargs):
        seen.append(profiles.LOG_READER.lcs_log_path)
        return log_reader.Snapshot(now=time.time())

    with patch.object(log_reader, "parse_files", side_effect=parse):
        real = log_reader.parse_log()
        monkeypatch.setattr(
            profiles,
            "LOG_READER",
            profiles.LogReaderProfile(
                lcs_log_path=str(tmp_path / "none.log"), msgbox_log_path=str(tmp_path / "none2.log")
            ),
        )
        other = log_reader.parse_log()
    assert other is not real
    assert seen[1] == str(tmp_path / "none.log")


def test_a_question_sent_to_las_x_is_seen_through_when_the_log_wins():
    """Abandoning a CAM question mid-flight made LAS X drop the next one, under a burst.

    The log now answers at once from the shared snapshot, so it wins most
    races; the API question already sent is still waited for to its answer,
    and no new question is sent after the race is won.
    """
    asked, answered = [], []
    log_value = {"x_um": 5.0, "y_um": 6.0}

    def slow_api(client, *, deadline, should_stop=None, **kwargs):
        asked.append(1)
        time.sleep(0.05)
        stopped_early = should_stop is not None and should_stop()
        answered.append(not stopped_early)
        return {"x_um": 1.0, "y_um": 2.0}

    snapshot = log_reader.Snapshot(now=time.time())
    with (
        patch.object(log_reader, "parse_files", return_value=snapshot),
        patch.object(log_reader, "get_xy", return_value=log_value),
        patch.object(log_reader, "ages", return_value={"xy": 0.1}),
        patch.object(readers.api_reader, "get_xy", side_effect=slow_api),
    ):
        reading = readers.get_xy(object(), diagnostics=True)
        time.sleep(0.2)
    assert reading.source == "log"
    assert asked == [1]
    assert answered == [True]
