"""At the microscope: one capture saved from LAS X's cache, checked, and its metadata shown.

Selects one LAS X job and captures it through the driver's ``Microscope``,
the way a workflow does: ``acquire()``, then ``save()``. It needs only the
driver, not the ZMART Controller. For this run only, the driver's profile (``IMAGE_SAVE``)
reads the pictures from LAS X's cache and saves one OME-Zarr store; the
capture stays in LAS X's cache. Then it checks and reports:

- the store: one OME-Zarr image, valid OME-Zarr 0.5 (when ome-zarr-models
  is installed), and its OME-XML valid OME 2016-06 (when ome-types and lxml
  are);
- the metadata, for a person to read: size and bit depth; each channel's
  dye, excitation, detector, gain, offset and detection band, laser and
  attenuation; the objective; the microscope's model and serial; and, per
  plane, its canvas height, the z-wide and z-galvo drives and its time;
- LAS X's own laser power fields, straight from its description;
- the projection beside a stack, and the capture's log.

It never edits a LAS X file: it selects the job, moves the stage to where
it already stands (so every plane gets its canvas position), and captures.
Whether LAS X's AutoSave is on or off makes no difference to it: the cache
route does not use AutoSave, and the check never asks about it. A report is written where the script is run (or to ``--report-dir``), as
``check_cache_route_<label>.json``.

Usage, in the driver's environment on the microscope PC::

    python check_cache_route.py --label selected-job
    python check_cache_route.py --job HiRes --label hires-16bit
    python check_cache_route.py --label stack --save-to D:/ZMART-check

The data location must be set up (driver_setup/data_location), or given
with ``--save-to``. See ``docs/leica/SCOPE_CHECK_CACHE_ROUTE.md`` for the tests to run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--job",
        default=None,
        help="the LAS X job to capture (default: the one selected in LAS X)",
    )
    parser.add_argument(
        "--label", required=True, help="a name for this check, e.g. hires-16bit"
    )
    parser.add_argument(
        "--save-to", default=None, help="a folder instead of the set-up data location"
    )
    parser.add_argument(
        "--report-dir", default=".", help="where the report is written (default: here)"
    )
    asked = parser.parse_args()

    import zarr

    from zmart_drivers.leica.stellaris import navigator_expert

    report: dict = {
        "job": asked.job,
        "label": asked.label,
        "checks": {},
        "metadata": {},
    }
    checks = report["checks"]
    scope = navigator_expert.connect(
        **({"output_root": asked.save_to} if asked.save_to else {})
    )
    try:
        if asked.job:
            try:
                scope.select_job(asked.job)
            except (RuntimeError, ValueError) as why:
                report["error"] = f"the job was not selected: {why}"
                return _finish(report, Path(asked.report_dir))
        state = scope.state()
        report["job"] = state["changeable"].get("job")
        report["system"] = {
            k: v
            for k, v in (state.get("observed") or {}).items()
            if k in ("serial_number", "system_type", "stand")
        }
        here = scope.position()
        scope.move_to(*(float(here[axis]["position"]) for axis in "xyz"))

        started = time.perf_counter()
        try:
            taken = scope.acquire(re.sub(r"[^A-Za-z0-9_-]", "-", asked.label))
            (record,) = scope.save(taken["position_label"])
        except Exception as why:  # noqa: BLE001 -- reported, not raised
            checks["captured and saved"] = False
            report["error"] = str(why)
            return _finish(report, Path(asked.report_dir))
        report["acquire_s"] = round(time.perf_counter() - started, 2)
        checks["captured and saved"] = True
        stores = sorted({plane["path"] for plane in record["planes"]})
        checks["one store for the capture"] = len(stores) == 1 and stores[0].endswith(
            ".ome.zarr"
        )
        store = Path(stores[0])
        level = zarr.open_group(str(store), mode="r")["0"][:]
        report["store"] = {
            "path": str(store),
            "shape (t, c, z, y, x)": list(level.shape),
            "dtype": str(level.dtype),
        }

        try:
            from ome_zarr_models.v05.image import Image

            Image.from_zarr(zarr.open_group(str(store), mode="r"))
            checks["valid OME-Zarr 0.5"] = True
        except ImportError:
            checks["valid OME-Zarr 0.5"] = "not checked (ome-zarr-models missing)"
        except Exception as why:  # noqa: BLE001 -- reported, not raised
            checks["valid OME-Zarr 0.5"] = f"NO: {why}"

        ome_text = (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8")
        ome = _read_ome(ome_text, checks)
        if ome is not None:
            report["metadata"] = _described(ome)

        report["laser power, as LAS X writes it"] = _laser_power(record)

        # The saved record lists the files and the planes. What the driver did, and what it projected, is in the files it lists: the
        # capture's log and the projections folder beside the data.
        logs = [Path(p) for p in record["files"] if p.endswith(".log.json")]
        projections = [
            p for p in record["files"] if Path(p).parent.name == "projections"
        ]
        stack = level.shape[2] > 1
        checks[
            "projection beside the stack"
            if stack
            else "no projection for a single plane"
        ] = bool(projections) if stack else not projections
        checks["log beside the data"] = len(logs) == 1 and logs[0].is_file()
        report["planes, as the driver answered"] = [
            {k: plane.get(k) for k in ("c", "z", "x_um", "y_um", "z_um")}
            for plane in record["planes"]
        ]
        if checks["log beside the data"]:
            entry = json.loads(logs[0].read_text(encoding="utf-8"))
            report["unconfirmed"] = entry.get("unconfirmed")
        return _finish(report, Path(asked.report_dir))
    finally:
        scope.close()


def _read_ome(text: str, checks: dict):
    try:
        import ome_types
    except ImportError:
        checks["valid OME 2016-06"] = "not checked (ome-types missing)"
        return None
    try:
        ome = ome_types.from_xml(text, validate=True)
        checks["valid OME 2016-06"] = True
    except ImportError:
        ome = ome_types.from_xml(text, validate=False)
        checks["valid OME 2016-06"] = "not checked (lxml missing)"
    except Exception as why:  # noqa: BLE001 -- reported, not raised
        checks["valid OME 2016-06"] = f"NO: {why}"
        try:
            # Read without the schema, to still show the metadata a person can check.
            ome = ome_types.from_xml(text, validate=False)
        except Exception:  # noqa: BLE001 -- a value even this reading refuses: the report says why above
            ome = None
    return ome


def _described(ome) -> dict:
    """The OME metadata in plain fields a person can check against LAS X."""
    image, instrument = ome.images[0], (ome.instruments[0] if ome.instruments else None)
    pixels = image.pixels
    detectors = {d.id: d for d in (instrument.detectors if instrument else [])}
    lasers = {laser.id: laser for laser in (instrument.lasers if instrument else [])}
    filters = {f.id: f for f in (instrument.filters if instrument else [])}
    drives = {
        a.id: {m.k: m.value for m in a.value.ms}
        for a in (
            ome.structured_annotations.map_annotations
            if ome.structured_annotations
            else []
        )
    }
    channels = []
    for channel in pixels.channels:
        detector = channel.detector_settings
        light = channel.light_source_settings
        band = [
            filters[ref.id].transmittance_range
            for ref in (
                channel.light_path.emission_filters if channel.light_path else []
            )
            if ref.id in filters
        ]
        channels.append(
            {
                "name": channel.name,
                "dye": channel.fluor,
                "excitation nm": channel.excitation_wavelength,
                "pinhole um": channel.pinhole_size,
                "detector": detectors[detector.id].model
                if detector and detector.id in detectors
                else None,
                "gain": detector.gain if detector else None,
                "offset": detector.offset if detector else None,
                "laser": lasers[light.id].model
                if light and light.id in lasers
                else None,
                "attenuation": light.attenuation if light else None,
                "band nm": [(r.cut_in, r.cut_out) for r in band if r is not None],
            }
        )
    objective = (
        instrument.objectives[0] if instrument and instrument.objectives else None
    )
    return {
        "acquired": str(image.acquisition_date),
        "size (x, y, z, c)": [
            pixels.size_x,
            pixels.size_y,
            pixels.size_z,
            pixels.size_c,
        ],
        "type, significant bits": [str(pixels.type.value), pixels.significant_bits],
        "pixel size um (x, y, z)": [
            pixels.physical_size_x,
            pixels.physical_size_y,
            pixels.physical_size_z,
        ],
        "microscope": instrument.microscope.model_dump(exclude_none=True)
        if instrument and instrument.microscope
        else None,
        "objective": objective.model_dump(
            include={
                "model",
                "nominal_magnification",
                "lens_na",
                "immersion",
                "correction",
            }
        )
        if objective
        else None,
        "channels": channels,
        "planes": [
            {
                "z": p.the_z,
                "c": p.the_c,
                "dt s": p.delta_t,
                "canvas x, y, z um": [p.position_x, p.position_y, p.position_z],
                "drives": drives.get(p.annotation_refs[0].id)
                if p.annotation_refs
                else None,
            }
            for p in pixels.planes
        ],
        "facts": drives.get("Annotation:vendor-facts"),
    }


def _laser_power(record: dict) -> list[dict]:
    """Each laser's power fields, from the capture's own setting in LAS X's description."""
    from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.metadata import (
        image_of,
    )

    description = next(
        (Path(p) for p in record["files"] if p.endswith(".lasx-description.xml")),
        None,
    )
    if description is None or not description.is_file():
        return []
    image = image_of(description.read_text(encoding="utf-8"))
    setting = image.find(
        "./Attachment[@Name='HardwareSetting']/ATLConfocalSettingDefinition"
    )
    fields = (
        "LaserName",
        "Wavelength",
        "PowerState",
        "OutputPowerWatt",
        "OutputPowerPercentage",
    )
    return [
        {key: laser.get(key) for key in fields if laser.get(key) is not None}
        for laser in (
            setting.findall("./LaserArray/Laser") if setting is not None else []
        )
    ]


def _finish(report: dict, folder: Path) -> int:
    failed = [
        name
        for name, result in report["checks"].items()
        if result is False or str(result).startswith("NO")
    ]
    report["verdict"] = "ALL CHECKS PASSED" if not failed else f"FAILED: {failed}"
    out = folder / f"check_cache_route_{report['label']}.json"
    try:
        out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError:
        out = None
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f"\n{report['verdict']}" + (f"  (report: {out})" if out else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
