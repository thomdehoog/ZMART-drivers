"""Writing one capture as an OME-Zarr 0.5 store, by the ZMART metadata convention.

A store is a folder: ``zarr.json`` says what the image is (its axes, pixel
size and position), ``0/`` holds the pixels, and ``OME/METADATA.ome.xml``
holds the OME data model (the instrument, the channel, where the plane was
taken) with ZMART's annotations and the vendor's facts in it, as
``zmart_drivers/metadata_convention/README.md`` describes. Everything a
reader in ZMART needs is in those three places.

A capture of this driver is one plane: one channel, one depth, one time
point. The pixels are already turned to the stage's axes
(``driver_setup/orientation``) when they arrive here.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import shutil
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers import metadata_convention as convention

OME_NS = convention.OME_NS
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = f"{OME_NS} {OME_NS}/ome.xsd"
MICROMETRE = "µm"


@dataclass(frozen=True)
class Plane:
    """What one plane is and where it was taken: the frame position of its centre, and the drives' own readings."""

    x_um: float
    y_um: float
    z_um: float
    actuator_readings: dict = field(
        default_factory=dict
    )  # "<axis>/<actuator>" -> micrometres
    exposure_ms: float | None = None


@dataclass(frozen=True)
class Capture:
    """Everything a store says about one capture besides the ZMART notes."""

    name: str
    pixels: object  # a 2-D numpy array, rows then columns, turned to the stage's axes
    pixel_size_um: float | None  # on the sample; None when NIS has no calibration
    plane: Plane
    channel: str | None
    objective: dict | None  # {"slot", "name"}
    camera: dict | None
    nis_version: str | None
    acquired_at: datetime
    position_label: str
    description: str = ""


def write_store(path: Path, capture: Capture, notes: convention.Notes) -> Path:
    """Write *capture* as the OME-Zarr store at *path*; answer the path.

    The store is written to a sibling folder first and renamed into place,
    so a reader never sees a half-written one. A store that already exists
    is never overwritten: the caller chooses a free name.
    """
    import numpy as np
    import zarr

    path = Path(path)
    if path.exists():
        raise FileExistsError(
            f"a store already exists at {path}; a capture never overwrites another"
        )
    pixels = np.asarray(capture.pixels)
    if pixels.ndim != 2:
        raise ValueError(
            f"a capture of this driver is one plane, got pixels of shape {pixels.shape}"
        )
    scale_xy = float(capture.pixel_size_um) if capture.pixel_size_um else 1.0
    height, width = pixels.shape
    writing = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.writing")
    try:
        group = zarr.open_group(str(writing), mode="w", zarr_format=3)
        group.attrs.update(
            {
                "ome": {
                    "version": "0.5",
                    "multiscales": [
                        {
                            "name": capture.name,
                            "axes": [
                                {"name": "t", "type": "time", "unit": "second"},
                                {"name": "c", "type": "channel"},
                                {"name": "z", "type": "space", "unit": "micrometer"},
                                {"name": "y", "type": "space", "unit": "micrometer"},
                                {"name": "x", "type": "space", "unit": "micrometer"},
                            ],
                            "datasets": [
                                {
                                    "path": "0",
                                    "coordinateTransformations": [
                                        {
                                            "type": "scale",
                                            "scale": [
                                                1.0,
                                                1.0,
                                                1.0,
                                                scale_xy,
                                                scale_xy,
                                            ],
                                        },
                                        {
                                            "type": "translation",
                                            # The centre of the first voxel, as a position.
                                            "translation": [
                                                0.0,
                                                0.0,
                                                capture.plane.z_um,
                                                capture.plane.y_um
                                                - (height - 1) / 2 * scale_xy,
                                                capture.plane.x_um
                                                - (width - 1) / 2 * scale_xy,
                                            ],
                                        },
                                    ],
                                }
                            ],
                            "metadata": {
                                "levels": "one level, the pixels as captured",
                                "z": "a single plane has no measured z size; its z scale is 1",
                                "pixel_size": "NIS-Elements' calibration for the objective in use"
                                if capture.pixel_size_um
                                else "unknown: NIS-Elements has no calibration for the objective in use, so the scale is 1",
                            },
                        }
                    ],
                    "omero": {
                        "channels": [
                            {
                                "label": capture.channel or "channel 0",
                                "color": "FFFFFF",
                                "active": True,
                                "coefficient": 1,
                                "family": "linear",
                                "inverted": False,
                                "window": _window(pixels),
                            }
                        ],
                        "rdefs": {"defaultT": 0, "defaultZ": 0, "model": "greyscale"},
                    },
                },
                "zmart": convention.zarr_attributes(),
            }
        )
        array = group.create_array(
            "0",
            shape=(1, 1, 1, height, width),
            chunks=(1, 1, 1, height, width),
            dtype=pixels.dtype,
            # OME-Zarr 0.5 asks the array to name its dimensions as the axes do.
            dimension_names=("t", "c", "z", "y", "x"),
        )
        array[0, 0, 0] = pixels
        ome_dir = writing / "OME"
        ome_dir.mkdir()
        (ome_dir / "METADATA.ome.xml").write_text(
            image_xml(capture, notes, pixels.dtype), encoding="utf-8"
        )
        os.replace(writing, path)
    except BaseException:
        shutil.rmtree(writing, ignore_errors=True)
        raise
    return path


def image_xml(capture: Capture, notes: convention.Notes, dtype) -> str:
    """The OME-XML of one capture: the instrument, the image, its plane, and ZMART's annotations."""
    import numpy as np

    ET.register_namespace("", OME_NS)
    ET.register_namespace("xsi", XSI_NS)
    root = ET.Element(
        _tag("OME"),
        {
            f"{{{XSI_NS}}}schemaLocation": SCHEMA_LOCATION,
            "UUID": f"urn:uuid:{uuid.uuid4()}",
        },
    )

    instrument = ET.SubElement(root, _tag("Instrument"), {"ID": "Instrument:0"})
    ET.SubElement(
        instrument, _tag("Microscope"), {"Manufacturer": "Nikon", "Type": "Inverted"}
    )
    if capture.camera:
        ET.SubElement(
            instrument,
            _tag("Detector"),
            {
                "ID": "Detector:0",
                "Model": capture.camera.get("name", ""),
                "Type": "CCD",
            },
        )
    objective_id = None
    if capture.objective:
        objective_id = f"Objective:{capture.objective['slot']}"
        attributes = {"ID": objective_id, "Model": capture.objective.get("name", "")}
        magnification = _magnification(capture.objective.get("name"))
        if magnification:
            attributes["NominalMagnification"] = _num(magnification)
        ET.SubElement(instrument, _tag("Objective"), attributes)

    image = ET.SubElement(root, _tag("Image"), {"ID": "Image:0", "Name": capture.name})
    ET.SubElement(image, _tag("AcquisitionDate")).text = capture.acquired_at.astimezone(
        UTC
    ).strftime("%Y-%m-%dT%H:%M:%S")
    if capture.description:
        ET.SubElement(image, _tag("Description")).text = capture.description
    ET.SubElement(image, _tag("InstrumentRef"), {"ID": "Instrument:0"})
    if objective_id:
        ET.SubElement(image, _tag("ObjectiveSettings"), {"ID": objective_id})
    ET.SubElement(
        image,
        _tag("StageLabel"),
        {
            "Name": capture.position_label,
            "X": _num(capture.plane.x_um),
            "XUnit": MICROMETRE,
            "Y": _num(capture.plane.y_um),
            "YUnit": MICROMETRE,
            "Z": _num(capture.plane.z_um),
            "ZUnit": MICROMETRE,
        },
    )
    height, width = np.asarray(capture.pixels).shape
    pixels_attributes = {
        "ID": "Pixels:0",
        "DimensionOrder": "XYCZT",
        "Type": _ome_type(dtype),
        "SizeX": str(width),
        "SizeY": str(height),
        "SizeZ": "1",
        "SizeC": "1",
        "SizeT": "1",
    }
    if capture.pixel_size_um:
        pixels_attributes.update(
            PhysicalSizeX=_num(capture.pixel_size_um),
            PhysicalSizeXUnit=MICROMETRE,
            PhysicalSizeY=_num(capture.pixel_size_um),
            PhysicalSizeYUnit=MICROMETRE,
        )
    pixels = ET.SubElement(image, _tag("Pixels"), pixels_attributes)
    channel = ET.SubElement(
        pixels,
        _tag("Channel"),
        {
            "ID": "Channel:0:0",
            "Name": capture.channel or "channel 0",
            "SamplesPerPixel": "1",
        },
    )
    if capture.camera:
        ET.SubElement(channel, _tag("DetectorSettings"), {"ID": "Detector:0"})
    ET.SubElement(pixels, _tag("MetadataOnly"))
    plane_attributes = {
        "TheZ": "0",
        "TheC": "0",
        "TheT": "0",
        "DeltaT": "0",
        "DeltaTUnit": "s",
        "PositionX": _num(capture.plane.x_um),
        "PositionXUnit": MICROMETRE,
        "PositionY": _num(capture.plane.y_um),
        "PositionYUnit": MICROMETRE,
        "PositionZ": _num(capture.plane.z_um),
        "PositionZUnit": MICROMETRE,
    }
    if capture.plane.exposure_ms is not None:
        plane_attributes.update(
            ExposureTime=_num(capture.plane.exposure_ms), ExposureTimeUnit="ms"
        )
    plane = ET.SubElement(pixels, _tag("Plane"), plane_attributes)
    readings_id = convention.actuator_readings_id(0, 0)
    convention.reference(plane, readings_id)

    annotations = ET.SubElement(root, _tag("StructuredAnnotations"))
    convention.append_notes(annotations, image, notes)
    convention.append_map(
        annotations,
        readings_id,
        convention.ACTUATOR_READINGS,
        sorted(capture.plane.actuator_readings.items()),
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _window(pixels) -> dict:
    import numpy as np

    deepest = (
        int(np.iinfo(pixels.dtype).max)
        if np.issubdtype(pixels.dtype, np.integer)
        else 1
    )
    return {
        "min": 0,
        "max": deepest,
        "start": int(pixels.min()),
        "end": int(pixels.max()),
    }


def _ome_type(dtype) -> str:
    import numpy as np

    return {
        np.dtype("uint8"): "uint8",
        np.dtype("uint16"): "uint16",
        np.dtype("uint32"): "uint32",
        np.dtype("int8"): "int8",
        np.dtype("int16"): "int16",
        np.dtype("int32"): "int32",
        np.dtype("float32"): "float",
        np.dtype("float64"): "double",
    }[np.dtype(dtype)]


def _magnification(name: str | None) -> float | None:
    import re

    found = re.search(r"(\d+(?:\.\d+)?)\s*[xX]\b", name or "")
    return float(found.group(1)) if found else None


def _tag(name: str) -> str:
    return f"{{{OME_NS}}}{name}"


def _num(value: float) -> str:
    return format(float(value), ".12g")
