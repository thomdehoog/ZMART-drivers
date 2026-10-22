"""At the microscope: the three readers, side by side, for speed, failures and agreement. Reads only.

The readers are LAS X's state tree (``direct``), the CAM API (``api``) and LAS
X's log (``log``). Each runs in a process of its own, reading nine values as
fast as it can for ``--seconds`` and writing every reading, timed, to a file.
Nothing is written to LAS X and nothing is scanned. ``benchmark_report.py``
makes the tables (how long a reading takes, how often it fails or comes back
empty, and how far the three agree).

Usage, in the driver's environment on the microscope PC::

    python benchmark_readers.py --out D:/ZMART-check/readers
    python benchmark_report.py D:/ZMART-check/readers > readers.md

With ``--check`` it writes the tables itself (``readers.md`` in ``--out``)
and fails when a reader failed or the CAM API disagrees with the direct
reader (``benchmark_report.problems``); the CI runs it so.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

JOB = None  # the job whose settings are read: --job, or the one selected in LAS X
READERS = ("direct", "api", "log")


# --- the reader processes ------------------------------------------------------


def readings_direct(_client):
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import reads as d

    # The job's settings are read for JOB, as the other two readers read them,
    # so that an unselected job is compared on its own settings. (Read from
    # the selected job's values instead, the direct column showed the selected
    # job's zoom and rotation for HiRes, 15 October 2026.) The z drive is read
    # where it truly is, as only the direct reader can.
    values = from_settings(
        d.get_job_settings(JOB), d.get_xy(), d.get_selected_job(), d.get_scan_status()
    )
    values["z-galvo"] = d.read_z_um("z-galvo")
    return values


def from_settings(settings, xy, selected, status):
    """The readings out of the reader's answers; a reading it gave no answer for is None.

    The log reader answers None for what the log has no fresh, unambiguous
    entry of (by design); that leaves only that reading empty.
    """
    first = settings["activeSettings"][0] if settings else None
    return {
        "zoom": settings["zoom"]["current"] if settings else None,
        "scan speed": settings["scanSpeed"]["value"] if settings else None,
        "pinhole": first["pinholeAiry"]["value"] if first else None,
        "line average": first["lineAverage"] if first else None,
        "rotation": settings["scanFieldRotation"]["value"] if settings else None,
        "xy": None if xy is None else [xy["x_um"], xy["y_um"]],
        "z-galvo": float(settings["zPosition"]["z-galvo"]["position"])
        if settings and settings.get("zPosition")
        else None,
        "selected job": None if selected is None else selected["Name"],
        "scan status": status,
    }


def readings_api(client):
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import reads as a

    return from_settings(
        a.get_job_settings(client, JOB),
        a.get_xy(client),
        a.get_selected_job(client),
        a.get_scan_status(client),
    )


def readings_log(_client):
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.log import reads as lr

    snap = lr.parse_log()
    return from_settings(
        lr.get_job_settings(JOB, snap),
        lr.get_xy(snap),
        lr.get_selected_job(snap),
        lr.get_scan_status(snap),
    )


def poll(reader, out: Path, until: Path):
    """Read as fast as possible until *until* exists; one JSON line per reading."""
    client = None
    if reader == "api":
        from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api.client import connect_python_client

        client = connect_python_client()
    read = {"direct": readings_direct, "api": readings_api, "log": readings_log}[reader]
    with out.open("w", encoding="utf-8") as f:
        while not until.exists():
            started = time.time()
            t = time.perf_counter()
            try:
                values, error = read(client), None
            except Exception as why:  # noqa: BLE001 -- a failed reading is a result
                values, error = None, f"{type(why).__name__}: {why}"
            f.write(
                json.dumps(
                    {
                        "t": started,
                        "s": time.perf_counter() - t,
                        "values": values,
                        "error": error,
                    }
                )
                + "\n"
            )
            f.flush()


def main() -> int:
    global JOB
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", required=True, help="the folder the readings are written to"
    )
    parser.add_argument(
        "--job",
        default=None,
        help="the job whose settings are read (default: the one selected)",
    )
    parser.add_argument(
        "--seconds", type=float, default=30, help="how long the readers read"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write readers.md and fail when a reader is wrong",
    )
    parser.add_argument("--poll", choices=READERS, help=argparse.SUPPRESS)
    asked = parser.parse_args()
    JOB = asked.job
    if JOB is None:
        from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import reads as direct_reader

        JOB = direct_reader.get_selected_job()["Name"]
    out = Path(asked.out)
    until = out / "stop"
    if asked.poll:
        poll(asked.poll, out / f"readings_{asked.poll}.jsonl", until)
        return 0
    out.mkdir(parents=True, exist_ok=True)
    until.unlink(missing_ok=True)
    pollers = [
        subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--out",
                str(out),
                "--poll",
                reader,
                "--job",
                JOB,
            ]
        )
        for reader in READERS
    ]
    marks = {}
    try:
        time.sleep(8)  # the readers start
        marks["rest"] = time.time()
        time.sleep(asked.seconds)
        marks["end"] = time.time()
    finally:
        until.touch()
        for p in pollers:
            p.wait(timeout=30)
    (out / "benchmark.json").write_text(
        json.dumps({"job": JOB, "marks": marks}, indent=1), encoding="utf-8"
    )
    if not asked.check:
        print(
            f"readings for {asked.seconds:.0f} s; tables: python benchmark_report.py {out}"
        )
        return 0
    # The CI starts scripts with PYTHONSAFEPATH, which keeps this folder off the search path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import benchmark_report

    (out / "readers.md").write_text(benchmark_report.report(out), encoding="utf-8")
    found = benchmark_report.problems(out)
    for problem in found:
        print(f"  problem: {problem}")
    print(
        f"\n{'ALL CHECKS PASSED' if not found else f'FAILED: {len(found)} problem(s)'}  (tables: {out / 'readers.md'})"
    )
    return 0 if not found else 1


if __name__ == "__main__":
    sys.exit(main())
