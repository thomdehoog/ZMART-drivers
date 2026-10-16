"""Pytest gate for the reader benchmark's check: when do the three readers count as working?

``benchmark_readers.py --check`` reads LAS X with the direct reader, the CAM
API and the log side by side, then asks :func:`benchmark_report.problems`
whether anything is wrong. Wrong is: a reader that failed while LAS X was at
rest, or the CAM API answering differently from the direct reader. The log
may have no answer for a value (it only knows what LAS X wrote lately), and
the CAM API's z-galvo is not compared: it keeps a job's stored height, while
only the direct reader reads where the drive is.
"""

import json

import benchmark_report


def reading(t, values, error=None):
    return {"t": t, "s": 0.001, "values": values, "error": error}


VALUES = {
    "zoom": 1.0,
    "scan speed": 400,
    "pinhole": 1.0,
    "line average": 1,
    "rotation": 0.0,
    "xy": [63500.0, 41500.0],
    "z-galvo": 0.0,
    "selected job": "HiRes",
    "scan status": "eScanIdle",
}


def write_run(folder, *, direct=None, api=None, log=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "benchmark.json").write_text(json.dumps({"job": "HiRes", "marks": {"rest": 10, "end": 20}}))
    for name, rows in (("direct", direct), ("api", api), ("log", log)):
        rows = rows if rows is not None else [reading(15, dict(VALUES))]
        (folder / f"readings_{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return folder


def test_three_readers_that_agree_pass(tmp_path):
    assert benchmark_report.problems(write_run(tmp_path)) == []


def test_a_failed_reading_at_rest_is_a_problem(tmp_path):
    folder = write_run(tmp_path, api=[reading(15, None, "TimeoutError: no answer")])
    assert any("api" in p and "failed" in p for p in benchmark_report.problems(folder))


def test_a_reading_before_the_rest_does_not_count(tmp_path):
    folder = write_run(tmp_path, direct=[reading(5, None, "starting"), reading(15, dict(VALUES))])
    assert benchmark_report.problems(folder) == []


def test_the_cam_api_disagreeing_with_the_direct_reader_is_a_problem(tmp_path):
    folder = write_run(tmp_path, api=[reading(15, {**VALUES, "zoom": 2.0})])
    assert any("zoom" in p for p in benchmark_report.problems(folder))


def test_the_cam_apis_z_galvo_is_not_compared(tmp_path):
    folder = write_run(tmp_path, api=[reading(15, {**VALUES, "z-galvo": 8.64})])
    assert benchmark_report.problems(folder) == []


def test_the_log_may_have_no_answer_for_a_value(tmp_path):
    folder = write_run(tmp_path, log=[reading(15, {**VALUES, "zoom": None, "xy": None})])
    assert benchmark_report.problems(folder) == []


def test_a_reader_with_no_reading_at_rest_is_a_problem(tmp_path):
    folder = write_run(tmp_path, log=[])
    assert any("log" in p for p in benchmark_report.problems(folder))
