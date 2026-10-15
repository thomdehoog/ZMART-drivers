"""The tables of a ``benchmark_readers.py`` run: speed, failures and agreement.

Usage::

    python benchmark_report.py D:/ZMART-check/readers > readers.md
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

READERS = ("direct", "api", "log")

#: How close two readings have to be to count as agreeing.
TOLERANCE = {
    "zoom": 0.01,
    "scan speed": 0,
    "pinhole": 0.02,
    "line average": 0,
    "rotation": 0.05,
    "xy": 10.0,  # the simulator's stage moves in 20 µm steps: +25 µm ends at +20 µm
    "z-galvo": 0.05,
    "selected job": None,
    "scan status": None,
}


def shows(datum, value, target) -> bool:
    if value is None:
        return False
    tolerance = TOLERANCE.get(datum)
    if tolerance is None:
        return value == target
    if datum == "xy":
        return all(abs(a - b) <= tolerance for a, b in zip(value, target))
    return abs(float(value) - float(target)) <= tolerance


def quantiles(values):
    if not values:
        return "–"
    values = sorted(values)
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return f"{statistics.median(values) * 1e3:.0f} / {p95 * 1e3:.0f} / {values[-1] * 1e3:.0f}"


def load(folder: Path):
    run = json.loads((folder / "benchmark.json").read_text(encoding="utf-8"))
    readings = {}
    for reader in READERS:
        path = folder / f"readings_{reader}.jsonl"
        readings[reader] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return run, readings


def between(rows, begin, end):
    return [r for r in rows if begin <= r["t"] < end]


def report(folder: Path) -> str:
    run, readings = load(folder)
    marks = run["marks"]
    lines = [f"# Readers: benchmark ({Path(folder).name})", ""]

    def section(title):
        lines.extend(["", f"## {title}", ""])

    # --- at rest
    section("Readers at rest")
    lines.append("| Reader | Rounds | Failed rounds | Values without an answer | One round, ms (median / p95 / max) |")
    lines.append("|---|---|---|---|---|")
    rest = {r: between(readings[r], marks["rest"], marks["end"]) for r in READERS}
    for r in READERS:
        rows = rest[r]
        failed = [x for x in rows if x["error"]]
        empty = sum(1 for x in rows if x["values"] for v in x["values"].values() if v is None)
        values = sum(len(x["values"]) for x in rows if x["values"])
        lines.append(f"| {r} | {len(rows)} | {len(failed)} | {empty} of {values} | {quantiles([x['s'] for x in rows if not x['error']])} |")
    errors = {r: sorted({x["error"][:90] for x in rest[r] if x["error"]}) for r in READERS}
    for r, found in errors.items():
        for e in found:
            lines.append(f"\n- {r} failed: `{e}`")

    section("Agreement at rest: each reader's last reading against the direct reader's")
    last = {r: next((x["values"] for x in reversed(rest[r]) if x["values"]), None) for r in READERS}
    lines.append("| Reading | direct | api | log |")
    lines.append("|---|---|---|---|")
    for datum in TOLERANCE:
        values = [None if last[r] is None else last[r].get(datum) for r in READERS]
        marked = [
            f"{v}" if i == 0 or values[0] is None or shows(datum, v, values[0]) else f"**{v}**"
            for i, v in enumerate(values)
        ]
        lines.append(f"| {datum} | " + " | ".join(marked) + " |")
    lines.append("\nA value in bold differs from the direct reader's.")
    found = problems(folder)
    section("Check")
    lines.extend([f"- {p}" for p in found] or ["No problems: every reader answered, and the CAM API agrees with the direct reader."])
    return "\n".join(lines) + "\n"


#: What the CAM API is not compared on: its z-galvo is the height stored in the
#: job's settings, which stays at a z-galvo stack's middle while the drive is
#: elsewhere (STELLARIS, 2026-10-15); only the direct reader reads the drive.
NOT_COMPARED = ("z-galvo",)


def problems(folder: Path) -> list[str]:
    """What is wrong with a run, in plain sentences; an empty list when nothing is.

    Wrong is a reader with no reading at rest, a reader that failed at rest,
    or the CAM API answering a value differently from the direct reader. The
    log is not compared: it knows only what LAS X wrote lately, so it may have
    no answer, or an older one, by design.
    """
    run, readings = load(folder)
    marks = run["marks"]
    rest = {r: between(readings[r], marks["rest"], marks["end"]) for r in READERS}
    found = []
    for r in READERS:
        if not rest[r]:
            found.append(f"{r}: no reading while LAS X was at rest")
        failed = [x for x in rest[r] if x["error"]]
        if failed:
            found.append(f"{r}: {len(failed)} of {len(rest[r])} readings failed, e.g. {failed[0]['error'][:120]}")
    direct = next((x["values"] for x in reversed(rest["direct"]) if x["values"]), None)
    api = next((x["values"] for x in reversed(rest["api"]) if x["values"]), None)
    if direct and api:
        for datum in TOLERANCE:
            if datum in NOT_COMPARED:
                continue
            if direct.get(datum) is None or not shows(datum, api.get(datum), direct[datum]):
                found.append(f"{datum}: the CAM API says {api.get(datum)!r}, the direct reader {direct.get(datum)!r}")
    return found


if __name__ == "__main__":
    print(report(Path(sys.argv[1])))
