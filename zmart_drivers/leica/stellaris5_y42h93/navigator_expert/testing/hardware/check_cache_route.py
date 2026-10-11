"""At the microscope: one capture saved from LAS X's cache, checked, and its metadata shown.

Captures one LAS X job through the ZMART Controller, as a workflow does, with
``source: "cache"`` and ``format: "ome-zarr"``, then checks and reports:

- the store: one OME-Zarr image, valid OME-Zarr 0.5 (when ome-zarr-models
  is installed), and its OME-XML valid OME 2016-06 (when ome-types and lxml
  are);
- when LAS X's AutoSave is on, that the store holds exactly the pixels of
  the OME-TIFF AutoSave wrote for the same capture; with AutoSave off, that
  the capture was saved at all;
- the metadata, for a person to read: size and bit depth; each channel's
  dye, excitation, detector, gain, offset and detection band, laser and
  attenuation; the objective; the microscope's model and serial; and, per
  plane, its canvas height, the z-wide and z-galvo drives and its time;
- LAS X's own laser power fields, straight from its description;
- the projection beside a stack, and the capture's log.

It never edits a LAS X file: it selects the job, moves the stage to where
it already stands (so every plane gets its canvas position), and captures.
A report is written where the script is run, as ``check_cache_route_<label>.json``.

Usage, in the driver's environment on the microscope PC::

    python check_cache_route.py --job HiRes --label hires-16bit
    python check_cache_route.py --job "AF Job" --label stack --save-to D:\\ZMART-check

The data location must be set up (configuration/data_location), or given
with ``--save-to``. See ``SCOPE_CHECK_CACHE_ROUTE.md`` for the tests to run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="the LAS X job to capture")
    parser.add_argument("--label", required=True, help="a name for this check, e.g. hires-16bit")
    parser.add_argument("--save-to", default=None, help="a folder instead of the set-up data location")
    asked = parser.parse_args()

    import tifffile
    import zarr
    from zmart_controller import ZmartController

    import zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_controller_plugin as driver
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import lasx_native_autosave as autosave

    report: dict = {"job": asked.job, "label": asked.label, "checks": {}, "metadata": {}}
    checks = report["checks"]
    session = ZmartController(driver, {})
    try:
        state = session.get_state()["content"]
        report["system"] = {k: v for k, v in (state.get("observed") or {}).items() if k in ("serial_number", "system_type", "stand")}
        here = session.get_xyz()["content"]
        session.set_xyz(*(float(here[axis]["position"]) for axis in "xyz"))

        autosave_on = _try(autosave.native_autosave_enabled)
        autosave_root = _try(autosave.native_autosave_base_folder) if autosave_on else None
        before = {str(p) for p in Path(autosave_root).rglob("*.ome.tif")} if autosave_root else set()

        settings = {"job": asked.job, "source": "cache", "format": "ome-zarr", "folder": "cache-check"}
        if asked.save_to:
            settings["save_to"] = asked.save_to
        started = time.perf_counter()
        answer = session.acquire(position_label=re.sub(r"[^A-Za-z0-9_-]", "-", asked.label), acquisition_settings=settings)
        report["acquire_s"] = round(time.perf_counter() - started, 2)
        checks["captured and saved"] = bool(answer["success"])
        if not answer["success"]:
            report["error"] = str(answer["content"])
            return _finish(report)
        record = answer["content"]
        stores = sorted({plane["path"] for plane in record["planes"]})
        checks["one store for the capture"] = len(stores) == 1 and stores[0].endswith(".ome.zarr")
        store = Path(stores[0])
        level = zarr.open_group(str(store), mode="r")["0"][:]
        report["store"] = {"path": str(store), "shape (t, c, z, y, x)": list(level.shape), "dtype": str(level.dtype)}

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

        if autosave_on:
            new = _wait_for_new(Path(autosave_root), before)
            if len(new) != 1:
                checks["identical to AutoSave's file"] = f"NO: AutoSave wrote {len(new)} new files"
            else:
                time.sleep(2.0)
                with tifffile.TiffFile(new[0]) as tif:
                    reference = tif.asarray().reshape(-1, *tif.pages[0].shape)
                size_c, size_z = level.shape[1], level.shape[2]
                # The store is ordered lowest plane first; AutoSave keeps LAS X's order.
                order = _store_to_vendor_z(record)
                same = all(
                    np.array_equal(level[0, c, zi], reference[order[zi] * size_c + c])
                    for c in range(size_c) for zi in range(size_z)
                )
                checks["identical to AutoSave's file"] = same
                report["autosave_file"] = str(new[0])
        else:
            checks["identical to AutoSave's file"] = "not compared: AutoSave is off"

        report["laser power, as LAS X writes it"] = _laser_power(record)
        stack = level.shape[2] > 1
        checks["projection beside the stack" if stack else "no projection for a single plane"] = (
            bool(record.get("projections")) if stack else not record.get("projections")
        )
        checks["log beside the data"] = all(Path(p).is_file() for p in record.get("log", [])) and bool(record.get("log"))
        report["planes, as the driver answered"] = [
            {k: plane.get(k) for k in ("c", "z", "x_um", "y_um", "z_um")} for plane in record["planes"]
        ]
        report["unconfirmed"] = record.get("unconfirmed")
        return _finish(report)
    finally:
        session.disconnect()


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
        ome = ome_types.from_xml(text, validate=False)
    return ome


def _described(ome) -> dict:
    """The OME metadata in plain fields a person can check against LAS X."""
    image, instrument = ome.images[0], (ome.instruments[0] if ome.instruments else None)
    pixels = image.pixels
    detectors = {d.id: d for d in (instrument.detectors if instrument else [])}
    lasers = {laser.id: laser for laser in (instrument.lasers if instrument else [])}
    filters = {f.id: f for f in (instrument.filters if instrument else [])}
    drives = {a.id: {m.k: m.value for m in a.value.ms} for a in (ome.structured_annotations.map_annotations if ome.structured_annotations else [])}
    channels = []
    for channel in pixels.channels:
        detector = channel.detector_settings
        light = channel.light_source_settings
        band = [filters[ref.id].transmittance_range for ref in (channel.light_path.emission_filters if channel.light_path else []) if ref.id in filters]
        channels.append({
            "name": channel.name, "dye": channel.fluor, "excitation nm": channel.excitation_wavelength,
            "pinhole um": channel.pinhole_size,
            "detector": detectors[detector.id].model if detector and detector.id in detectors else None,
            "gain": detector.gain if detector else None, "offset": detector.offset if detector else None,
            "laser": lasers[light.id].model if light and light.id in lasers else None,
            "attenuation": light.attenuation if light else None,
            "band nm": [(r.cut_in, r.cut_out) for r in band if r is not None],
        })
    objective = instrument.objectives[0] if instrument and instrument.objectives else None
    return {
        "acquired": str(image.acquisition_date),
        "size (x, y, z, c)": [pixels.size_x, pixels.size_y, pixels.size_z, pixels.size_c],
        "type, significant bits": [str(pixels.type.value), pixels.significant_bits],
        "pixel size um (x, y, z)": [pixels.physical_size_x, pixels.physical_size_y, pixels.physical_size_z],
        "microscope": instrument.microscope.model_dump(exclude_none=True) if instrument and instrument.microscope else None,
        "objective": objective.model_dump(include={"model", "nominal_magnification", "lens_na", "immersion", "correction"}) if objective else None,
        "channels": channels,
        "planes": [
            {"z": p.the_z, "c": p.the_c, "dt s": p.delta_t, "canvas x, y, z um": [p.position_x, p.position_y, p.position_z],
             "drives": drives.get(p.annotation_refs[0].id) if p.annotation_refs else None}
            for p in pixels.planes
        ],
        "facts": drives.get("Annotation:vendor-facts"),
    }


def _laser_power(record: dict) -> list[dict]:
    """Each laser's power fields, from the capture's own setting in LAS X's description."""
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output.lasx_description import image_of

    description = next((Path(p) for p in record.get("vendor_metadata", []) if p.endswith(".lasx-description.xml")), None)
    if description is None or not description.is_file():
        return []
    image = image_of(description.read_text(encoding="utf-8"))
    setting = image.find("./Attachment[@Name='HardwareSetting']/ATLConfocalSettingDefinition")
    fields = ("LaserName", "Wavelength", "PowerState", "OutputPowerWatt", "OutputPowerPercentage")
    return [
        {key: laser.get(key) for key in fields if laser.get(key) is not None}
        for laser in (setting.findall("./LaserArray/Laser") if setting is not None else [])
    ]


def _store_to_vendor_z(record: dict) -> list[int]:
    """The vendor's z number of each store plane: the store runs from the lowest plane up."""
    heights = {plane["z"]: plane["z_um"] for plane in record["planes"]}
    return sorted(heights, key=lambda z: (heights[z] is None, heights[z] if heights[z] is not None else z))


def _wait_for_new(root: Path, before: set[str], seconds: float = 60.0) -> list[Path]:
    deadline = time.time() + seconds
    while True:
        new = [p for p in root.rglob("*.ome.tif") if str(p) not in before]
        if new or time.time() > deadline:
            return new
        time.sleep(0.5)


def _try(call):
    try:
        return call()
    except Exception:  # noqa: BLE001 -- a check that cannot run says so in the report
        return None


def _finish(report: dict) -> int:
    failed = [name for name, result in report["checks"].items() if result is False or str(result).startswith("NO")]
    report["verdict"] = "ALL CHECKS PASSED" if not failed else f"FAILED: {failed}"
    out = Path.cwd() / f"check_cache_route_{report['label']}.json"
    try:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        out = None
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f"\n{report['verdict']}" + (f"  (report: {out})" if out else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
