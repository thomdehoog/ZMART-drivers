"""At the microscope: the plain Navigator driver, end to end, with LAS X showing its Navigator (not Navigator Expert).

Every step goes through the driver as a workflow would: the readings
(``procedures/readings.py``), the changes through the dispatcher with this
microscope's limits installed (``procedures/changes.py``), saving from
LAS X's cache (``procedures/save_data``) and letting go of it
(``procedures/clear_cache``). Everything is read back and put back:

0. preconditions: LAS X answers and is idle; it shows its plain Navigator
   (no Navigator Expert jobs); the limits installed (this machine's
   ``limits.json``, else the driver's defaults, said which) hold where the
   stage and z stand; LAS X's OK-only boxes are answered from here on.
   Nothing moves when one of these fails;
1. every direct reading;
2. zoom, scan speed, scan field rotation, line average and pinhole, each to
   a value inside its own range and back;
3. z-galvo and z-wide +2 um and back, the stage +25 um in x and back;
4. with ``--allow-objective`` only: another objective of the same immersion,
   and back; z-wide, which LAS X moves by itself on an objective change, is
   reported and left as LAS X put it;
5. one capture (the window's Capture Image) into the LAS X project chosen
   as the Navigator Expert driver chooses it, saved as one OME-Zarr store,
   then let go of in LAS X's cache;
6. a tile scan (the window's Preview) into that project, over three
   positions in a row, a field and a tenth apart, saved as one store per
   field, let go of, and everything the Preview changed put back; timed.

This is the check that passed its 50 steps on the STELLARIS on 2026-10-15,
as the route it was built from stood then (the private branch
``release-candidate-direct-writers-navigator-original``); here the same
steps run through the driver's parts instead of raw sends. Stage clear:
step 3 moves the stage 25 um and z by 2 um; step 6 moves it two tiles of a
field and a tenth each (about 2.6 mm with the 10x objective at zoom 1; the
other way near the edge of the limits).

Usage, in the driver's environment::

    python navigator_ci.py --out D:/ZMART-check/navigator-ci
    python navigator_ci.py --out D:/ZMART-check/navigator-ci --allow-objective

The report is ``navigator_ci.json`` in ``--out``; the last line says
``ALL CHECKS PASSED`` or what failed, and the exit code is 0 only then.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

DRIVER = "zmart_drivers.leica.stellaris.navigator"


class Run:
    """The checks so far, one line each, printed as they come."""

    def __init__(self):
        self.records = []

    def note(self, step, name, ok, detail=""):
        status = "PASS" if ok is True else "SKIP" if ok is None else "FAIL"
        self.records.append({"step": step, "check": name, "status": status, "detail": str(detail)})
        print(f"{time.strftime('%H:%M:%S')} | {status} | {step}: {name}" + (f" -- {detail}" if detail else ""), flush=True)
        return ok is True

    @property
    def failed(self):
        return [r for r in self.records if r["status"] == "FAIL"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="the folder for the stores and the report")
    parser.add_argument("--tiles", type=int, default=3, help="positions in the tile scan")
    parser.add_argument("--allow-objective", action="store_true", help="also switch to another objective of the same immersion")
    asked = parser.parse_args()

    import importlib

    hardware_server = importlib.import_module(f"{DRIVER}.primitives.direct.client")
    d = importlib.import_module(f"{DRIVER}.primitives.direct.reads")
    readings = importlib.import_module(f"{DRIVER}.procedures.readings")
    changes = importlib.import_module(f"{DRIVER}.procedures.changes")
    dialogs = importlib.import_module(f"{DRIVER}.procedures.answer_dialogs")
    handshake = importlib.import_module(f"{DRIVER}.driver_setup.limits.handshake")
    gate = importlib.import_module(f"{DRIVER}.dispatcher.limits")
    save = importlib.import_module(f"{DRIVER}.procedures.save_data.save")
    clear = importlib.import_module(f"{DRIVER}.procedures.clear_cache.clear")
    Naming = importlib.import_module(f"{DRIVER}.procedures.save_data.naming").Naming
    PlaneIndex = importlib.import_module(f"{DRIVER}.procedures.save_data.product").PlaneIndex
    TILE_SPACING = importlib.import_module(f"{DRIVER}.procedures.acquire").TILE_SPACING

    out = Path(asked.out)
    out.mkdir(parents=True, exist_ok=True)
    run = Run()

    def finish():
        (out / "navigator_ci.json").write_text(
            json.dumps({"records": run.records, "answered_boxes": [a.message for a in dialogs.answered_since(0)]}, indent=1),
            encoding="utf-8",
        )
        print(f"\nreport: {out / 'navigator_ci.json'}")
        bad = run.failed
        print("ALL CHECKS PASSED" if not bad else "FAILED: " + "; ".join(f"{r['step']}: {r['check']}" for r in bad))
        return 0 if not bad else 1

    def outcome(step, name, result, detail=""):
        """A change's result as one check: passed when sent and confirmed."""
        ok = bool(result.get("success")) and bool(result.get("confirmed"))
        return run.note(step, name, ok, detail or result.get("message"))

    # --- 0. preconditions
    step = "0 preconditions"
    try:
        client = hardware_server.connect("ZMART navigator_ci")
        status = d.get_scan_status()
    except Exception as why:  # noqa: BLE001 -- reported, the run stops
        run.note(step, "LAS X's hardware server answers", False, why)
        return finish()
    run.note(step, "LAS X's hardware server answers", True, status)
    if not run.note(step, "LAS X is idle", status == "eScanIdle", status):
        return finish()
    jobs = d.get_jobs()
    if not run.note(step, "LAS X shows its plain Navigator (no Navigator Expert jobs)", not jobs, [j.get("Name") for j in jobs] if jobs else ""):
        return finish()
    limits = handshake.connect_handshake(client)
    envelope = limits.stage_cfg["stage_um"] if limits.ok else None
    if not run.note(step, "the limits are installed", limits.ok, f"{limits.limits.describe() if limits.ok else limits.error}"):
        return finish()
    xy = d.get_xy()
    x0, y0 = xy["x_um"], xy["y_um"]
    galvo0, wide0 = d.read_z_um("z-galvo"), d.read_z_um("z-wide")
    problems = [
        f"{axis} {value:g} um is outside {low:g}..{high:g}"
        for axis, value in (("x", x0), ("y", y0), ("z_galvo", galvo0), ("z_wide", wide0))
        for low, high in [envelope[axis]]
        if not low <= value <= high
    ]
    if not run.note(step, "the stage and z stand inside the limits", not problems,
                    "; ".join(problems) or f"x {x0:.0f}, y {y0:.0f}, z-galvo {galvo0:.2f}, z-wide {wide0:.2f} um"):
        return finish()
    dialogs.pending()
    dialogs.keep_watching()
    try:
        # --- 1. readers
        step = "1 readers"
        for name in ("get_xy", "get_scan_status", "get_scan_mode", "get_zoom", "get_scan_speed", "get_format",
                     "get_averaging", "get_pinhole", "get_objective", "get_fov", "get_scan_field_rotation",
                     "get_pixel_size", "get_bit_depth", "get_serial_number", "get_objectives", "get_hardware_info"):
            try:
                answer = getattr(readings, name)(client)
                run.note(step, name, answer is not None, json.dumps(answer, default=str)[:100])
            except Exception as why:  # noqa: BLE001 -- a reading that fails is a result
                run.note(step, name, False, f"{type(why).__name__}: {why}")

        # --- 2. settings
        step = "2 settings"
        zoom = d.get_zoom()
        speed, speed_range = d.get_scan_speed()["value"], d.get_scan_speed_range()
        rotation = d.get_scan_field_rotation()
        pinhole = d.get_pinhole()["airy"]
        line_average = d.get_averaging()["lineAverage"]

        def inside(current, low, high, delta):
            return current + delta if current + delta <= high else current - delta

        cases = [
            ("zoom", zoom["current"], round(inside(zoom["current"], zoom["min"], zoom["max"], 0.5), 2)),
            ("scan_speed", speed, 600 if speed != 600 and speed_range["Min"] <= 600 <= speed_range["Max"] else 400),
            ("scan_field_rotation", rotation["value"], inside(rotation["value"], rotation["min"], rotation["max"], 5)),
            ("line_average", line_average, 2 if line_average == 1 else 1),
            ("pinhole_airy", pinhole["value"], round(inside(pinhole["value"], pinhole["min"], pinhole["max"], 0.5), 2)),
        ]
        for name, current, target in cases:
            try:
                outcome(step, f"{name} to {target:g}", changes.change(client, name, target))
            except Exception as why:  # noqa: BLE001
                run.note(step, f"{name} to {target:g}", False, f"{type(why).__name__}: {why}")
            finally:
                outcome(step, f"{name} back to {current:g}", changes.change(client, name, current))

        # --- 3. moves
        step = "3 moves"
        for drive, start in (("z-galvo", galvo0), ("z-wide", wide0)):
            try:
                outcome(step, f"{drive} +2 um", changes.move_z(client, drive, start + 2, tolerance=0.1),
                        f"read {d.read_z_um(drive):.2f}")
            finally:
                outcome(step, f"{drive} back", changes.move_z(client, drive, start, tolerance=0.1),
                        f"read {d.read_z_um(drive):.2f}")
        try:
            outcome(step, "stage x +25 um", changes.move_xy(client, x0 + 25, y0), f"read {d.get_xy()['x_um']:.0f}")
        finally:
            outcome(step, "stage back", changes.move_xy(client, x0, y0, tolerance=1),
                    f"read {d.get_xy()['x_um']:.0f}, {d.get_xy()['y_um']:.0f}")

        # --- 4. objective
        step = "4 objective"
        if not asked.allow_objective:
            run.note(step, "another objective of the same immersion", None, "not asked for (--allow-objective)")
        else:
            current = d.get_objective()
            objectives = d.get_objectives()
            others = [o for o in objectives if o.get("slotIndex") != current["slotIndex"] and o.get("immersion") == current["immersion"]]
            if not others:
                run.note(step, "another objective of the same immersion", None, f"none besides slot {current['slotIndex']}")
            else:
                target = others[0]["slotIndex"]
                try:
                    outcome(step, f"objective to slot {target}", changes.set_objective(client, objectives, slot_index=target),
                            f"read slot {d.get_objective()['slotIndex']}")
                finally:
                    outcome(step, f"objective back to slot {current['slotIndex']}",
                            changes.set_objective(client, objectives, slot_index=current["slotIndex"]),
                            f"read slot {d.get_objective()['slotIndex']}")
                    # LAS X moves z-wide by itself on an objective change (its parfocal
                    # compensation; 0 -> 6.11 um after 10x -> 20x -> 10x, STELLARIS
                    # 2026-10-15). Reported, and left as LAS X put it.
                    time.sleep(2.0)
                    wide = d.read_z_um("z-wide")
                    run.note(step, "z-wide after the objective round trip (left as LAS X put it)", True, f"{wide0:.2f} -> {wide:.2f} um")

        def kept(saved_list):
            pixels = [Path(s.image_paths[PlaneIndex(0, 0, 0)]) for s in saved_list]
            return all((p / "zarr.json").is_file() for p in pixels), pixels

        def let_go(saved_list):
            """Have LAS X let go of the one picture the stores came from; what was cleared."""
            targets = {s.release_target for s in saved_list if s.release_target}
            for number, name in targets:
                clear.delete(number, name)
            return sorted(name for _, name in targets)

        # --- 5. capture
        step = "5 capture"
        t0 = time.perf_counter()
        try:
            acq = changes.take_picture(client)
            run.note(step, "the capture ran (scan seen running, then idle)", acq.command_result.get("confirmed") is True,
                     acq.command_result.get("message"))
            saved = save.save(acq, out / "capture", [Naming(stem="capture_navchk_one")],
                              place=save.placed_at({"x_um": x0, "y_um": y0, "z_um": galvo0 + wide0}))
            ok, stores = kept(saved)
            cleared = let_go(saved)
            run.note(step, "captured, saved as one store, let go of", ok and bool(cleared),
                     f"{stores[0].name}, {cleared} in {time.perf_counter() - t0:.1f} s")
        except Exception as why:  # noqa: BLE001
            run.note(step, "captured, saved as one store, let go of", False, f"{type(why).__name__}: {why}")

        # --- 6. tile scan
        step = "6 tile scan"
        field_um = d.get_fov()[0] * 1e6
        step_um = field_um * TILE_SPACING
        low, high = envelope["x"]
        forward = [(x0 + i * step_um, y0, galvo0) for i in range(asked.tiles)]
        positions = forward if all(low <= p[0] <= high for p in forward) else [(x0 - i * step_um, y0, galvo0) for i in range(asked.tiles)]
        run.note(step, f"{asked.tiles} positions inside the limits", all(low <= p[0] <= high for p in positions),
                 ", ".join(f"{p[0]:.0f}" for p in positions))
        t0 = time.perf_counter()
        try:
            acq = changes.take_tile_scan(client, positions)
            run.note(step, "the tile scan ran (scan seen running, then idle)", acq.command_result.get("confirmed") is True,
                     acq.command_result.get("message"))
            put_back = acq.command_result.get("put_back") or {}
            run.note(step, "the stage back, scan mode xyz", bool(put_back.get("confirmed")) and d.get_scan_mode().startswith("xyz"),
                     f"x {d.get_xy()['x_um']:.0f}, scan mode {d.get_scan_mode()}")

            def place(field, stage_um, depths):
                x, y, z = stage_um or positions[field]
                return lambda depth: {"x_um": x, "y_um": y, "z_um": z + wide0}

            saved = save.save(acq, out / "tile-scan", [Naming(stem=f"tile-scan_navchk_field{f}") for f in range(len(positions))], place=place)
            took = time.perf_counter() - t0
            ok, stores = kept(saved)
            cleared = let_go(saved)
            run.note(step, f"{len(positions)} fields saved as {len(stores)} stores, let go of",
                     ok and len(stores) == len(positions) and bool(cleared), f"{cleared}, {took:.1f} s from start to saved")
        except Exception as why:  # noqa: BLE001
            run.note(step, "tile scan saved", False, f"{type(why).__name__}: {why}")
    except Exception as why:  # noqa: BLE001 -- anything unforeseen is reported, never hidden
        run.note("unexpected", type(why).__name__, False, traceback.format_exc()[-400:])
    finally:
        dialogs.stop_watching()
        answered = dialogs.answered_since(0)
        run.note("dialogs", "LAS X boxes answered during the run", True if not answered else None, "; ".join(a.message for a in answered))
        gate.uninstall(client)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
