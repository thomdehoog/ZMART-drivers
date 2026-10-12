"""At the microscope: the CAM route and the direct route, side by side, every write put back.

The direct route (``direct/``) sends the driver's commands to LAS X's hardware
server instead of the CAM API, and reads job settings, hardware info and stage
XY from it. On the LAS X simulator both routes gave identical results for every
translated command (2026-10-12). This script repeats that comparison on a real
instrument:

1. **Reads:** the job's settings, the hardware info and the stage XY, once from
   the CAM API and once from the hardware server; they should be identical.
2. **Settings:** each of the job's settings is moved one small step from its
   current value through the CAM route, read back, put back; then the same
   through the direct route. Zoom, scan speed, sequential mode, scan-field
   rotation, image format, frame and line accumulation and average, pinhole.
3. **A refusal:** zoom 100, which LAS X refuses; both routes should report it.
4. **Opt-in, each behind its own flag:** ``--allow-moves`` (stage XY +25 µm and
   back, z-galvo +2 µm and back), ``--allow-objective`` (the next objective
   slot and back), ``--allow-lasers`` (the first laser line's intensity +0.01
   and back, its shutter closed and opened), ``--allow-stack`` (the stack's
   step size +0.5 µm and back; LAS X may re-centre the stack by a grid step).

Every write is put back, in the same order, even when the run stops early.
Nothing selects a job, acquires, or touches a file. A report is written where
the script is run, as ``compare_routes_<label>.json``.

Usage, in the driver's environment on the microscope PC::

    python compare_routes.py --job HiRes --label stellaris-settings
    python compare_routes.py --job HiRes --label stellaris-all --allow-moves --allow-objective
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--job", required=True, help="the LAS X job whose settings are written"
    )
    parser.add_argument(
        "--label", required=True, help="a name for this run, e.g. stellaris-settings"
    )
    parser.add_argument(
        "--allow-moves",
        action="store_true",
        help="stage XY +25 µm and z-galvo +2 µm, and back",
    )
    parser.add_argument(
        "--allow-objective",
        action="store_true",
        help="the next objective slot, and back",
    )
    parser.add_argument(
        "--allow-lasers",
        action="store_true",
        help="laser intensity +0.01 and the shutter, and back",
    )
    parser.add_argument(
        "--allow-stack",
        action="store_true",
        help="the stack's step size +0.5 µm, and back",
    )
    asked = parser.parse_args()

    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import direct
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
        set as cam,
    )
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.connect import (
        connect_microscope,
    )
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
        read as readers,
    )

    client = connect_microscope()
    route = direct.route(client)
    job = asked.job
    report = {
        "job": job,
        "label": asked.label,
        "system": readers.get_hardware_info(client, mode="api").get("SystemType"),
    }
    print(f"LAS X: {report['system']}; job {job!r}")

    def settings():
        return readers.get_job_settings(client, job, mode="api")

    report["reads"] = {
        "job settings identical": readers.get_job_settings(route, job, mode="api")
        == settings(),
        "hardware info identical": readers.get_hardware_info(route, mode="api")
        == readers.get_hardware_info(client, mode="api"),
        "stage xy": {
            "cam": readers.get_xy(client, mode="api"),
            "direct": readers.get_xy(route, mode="api"),
        },
    }
    for name, value in report["reads"].items():
        print(f"read  {name:24s} {value}")

    cases = setting_cases(cam, job, settings())
    if asked.allow_moves:
        cases += move_cases(cam, readers, client, job)
    if asked.allow_objective:
        cases += objective_cases(cam, readers, client, job, settings())
    if asked.allow_lasers:
        cases += laser_cases(cam, job, settings())
    if asked.allow_stack:
        cases += stack_cases(cam, job, settings())
    cases.append(
        (
            "zoom 100 (refused)",
            lambda k, v: cam.set_zoom(k, job, v),
            lambda s: s["zoom"]["current"],
            100.0,
            settings()["zoom"]["current"],
        )
    )

    report["writes"] = []
    for name, write, read, alternate, original in cases:
        for label, chosen in (("CAM", client), ("direct", route)):
            started = time.perf_counter()
            try:
                result = write(chosen, alternate)
                seen = read(settings())
            finally:
                put_back = write(client, original)
            row = {
                "command": name,
                "route": label,
                "success": result["success"],
                "confirmed": result["confirmed"],
                "attempts": result["timing"].get("attempts"),
                "read_back": seen,
                "seconds": round(time.perf_counter() - started, 3),
                "message": None
                if result["success"] and result["confirmed"]
                else str(result.get("message")),
                "put_back": bool(put_back["success"]),
            }
            report["writes"].append(row)
            print(
                f"write {name:24s} {label:6s} success={row['success']!s:5} confirmed={row['confirmed']!s:5} "
                f"attempts={row['attempts']} read={seen!r} put_back={row['put_back']}"
                + (f" | {row['message'][:100]}" if row["message"] else "")
            )
    report["same outcome on both routes"] = same_outcome(report["writes"])
    print("same outcome on both routes:", report["same outcome on both routes"])
    with open(f"compare_routes_{asked.label}.json", "w", encoding="utf-8") as out:
        json.dump(report, out, indent=1, default=str)
    return (
        0
        if report["same outcome on both routes"]
        and all(
            report["reads"][k]
            for k in ("job settings identical", "hardware info identical")
        )
        else 1
    )


def setting_cases(cam, job, now):
    """The job's settings, each one small step from where it is."""
    first = now["activeSettings"][0]
    zoom = now["zoom"]["current"]
    pinhole = first["pinholeAiry"]["value"]
    return [
        (
            "zoom",
            lambda k, v: cam.set_zoom(k, job, v),
            lambda s: s["zoom"]["current"],
            zoom + 1 if zoom + 1 <= now["zoom"]["max"] else zoom - 0.5,
            zoom,
        ),
        (
            "scan speed",
            lambda k, v: cam.set_scan_speed(k, job, v),
            lambda s: s["scanSpeed"]["value"],
            600 if now["scanSpeed"]["value"] != 600 else 400,
            now["scanSpeed"]["value"],
        ),
        (
            "sequential mode",
            lambda k, v: cam.set_sequential_mode(k, job, v),
            lambda s: s["sequentialMode"],
            "Frame" if now["sequentialMode"] != "Frame" else "Line",
            now["sequentialMode"],
        ),
        (
            "scan-field rotation",
            lambda k, v: cam.set_scan_field_rotation(k, job, v),
            lambda s: s["scanFieldRotation"]["value"],
            now["scanFieldRotation"]["value"] + 5,
            now["scanFieldRotation"]["value"],
        ),
        (
            "image format",
            lambda k, v: cam.set_image_format(k, job, v),
            lambda s: s["format"],
            "1024 x 1024" if now["format"] != "1024 x 1024" else "512 x 512",
            now["format"],
        ),
        (
            "frame accumulation",
            lambda k, v: cam.set_frame_accumulation(k, job, 0, v),
            lambda s: s["activeSettings"][0]["frameAccumulation"],
            first["frameAccumulation"] + 1,
            first["frameAccumulation"],
        ),
        (
            "frame average",
            lambda k, v: cam.set_frame_average(k, job, 0, v),
            lambda s: s["activeSettings"][0]["frameAverage"],
            first["frameAverage"] + 1,
            first["frameAverage"],
        ),
        (
            "line accumulation",
            lambda k, v: cam.set_line_accumulation(k, job, 0, v),
            lambda s: s["activeSettings"][0]["lineAccumulation"],
            first["lineAccumulation"] + 1,
            first["lineAccumulation"],
        ),
        (
            "line average",
            lambda k, v: cam.set_line_average(k, job, 0, v),
            lambda s: s["activeSettings"][0]["lineAverage"],
            first["lineAverage"] + 1,
            first["lineAverage"],
        ),
        (
            "pinhole",
            lambda k, v: cam.set_pinhole_airy(k, job, 0, v),
            lambda s: s["activeSettings"][0]["pinholeAiry"]["value"],
            pinhole + 0.2
            if pinhole + 0.2 <= first["pinholeAiry"]["max"]
            else pinhole - 0.2,
            pinhole,
        ),
    ]


def move_cases(cam, readers, client, job):
    here = readers.get_xy(client, mode="api")
    galvo = float(
        readers.get_job_settings(client, job, mode="api")["zPosition"]["z-galvo"][
            "position"
        ]
    )
    return [
        (
            "stage xy +25 µm",
            lambda k, v: cam.move_xy(k, *v),
            lambda s: (
                readers.get_xy(client, mode="api")["x_um"],
                readers.get_xy(client, mode="api")["y_um"],
            ),
            (here["x_um"] + 25, here["y_um"]),
            (here["x_um"], here["y_um"]),
        ),
        (
            "z-galvo +2 µm",
            lambda k, v: cam.move_z(k, job, v, z_mode="galvo"),
            lambda s: s["zPosition"]["z-galvo"]["position"],
            galvo + 2,
            galvo,
        ),
    ]


def objective_cases(cam, readers, client, job, now):
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions.objectives import (
        objective_by_slot,
    )

    hardware = readers.get_hardware_info(client, mode="api")
    slots = sorted(objective_by_slot(hardware))
    current = now["objective"]["slotIndex"]
    others = [s for s in slots if s != current]
    if not others:
        print("objective: only one slot found; skipped")
        return []
    return [
        (
            "objective",
            lambda k, v: cam.set_objective(
                k, job, hardware, slot_index=v, compensate=False
            ),
            lambda s: s["objective"]["slotIndex"],
            others[0],
            current,
        )
    ]


def laser_cases(cam, job, now):
    line = now["activeSettings"][0]["activeLaserLines"][0]
    route, index, level = (
        line["beamRoute"],
        line["lineIndex"],
        line["intensity"]["value"],
    )
    return [
        (
            "laser intensity +0.01",
            lambda k, v: cam.set_laser_intensity(k, job, 0, route, index, v),
            lambda s: s["activeSettings"][0]["activeLaserLines"][0]["intensity"][
                "value"
            ],
            min(level + 0.01, line["intensity"]["max"]),
            level,
        ),
        (
            "laser shutter",
            lambda k, v: cam.set_laser_shutter(k, job, 0, route, v),
            lambda s: s["activeSettings"][0]["activeLaserLines"][0]["shutterOpen"],
            not line["shutterOpen"],
            line["shutterOpen"],
        ),
    ]


def stack_cases(cam, job, now):
    if not now.get("stack"):
        print("stack: the job has no z-stack; skipped")
        return []
    step = float(now["stack"]["stepSize"])
    return [
        (
            "stack step +0.5 µm",
            lambda k, v: cam.set_z_stack_step_size(k, job, v),
            lambda s: s["stack"]["stepSize"],
            step + 0.5,
            step,
        )
    ]


def same_outcome(rows) -> bool:
    """Whether CAM and direct rows agree on success, confirmed and read-back, command by command."""
    by = {}
    for row in rows:
        by.setdefault(row["command"], {})[row["route"]] = (
            row["success"],
            row["confirmed"],
            row["read_back"],
        )
    return all(len(set(pair.values())) == 1 for pair in by.values())


if __name__ == "__main__":
    sys.exit(main())
