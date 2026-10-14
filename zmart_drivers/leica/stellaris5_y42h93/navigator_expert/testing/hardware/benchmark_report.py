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
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report(Path(sys.argv[1])))
