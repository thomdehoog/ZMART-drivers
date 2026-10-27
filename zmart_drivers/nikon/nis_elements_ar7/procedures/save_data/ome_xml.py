"""Write a capture's OME-XML: the OME data model, ZMART's annotations, and NIS's own words.

A saved store keeps its pixels in Zarr, and everything that says how they
were made in ``OME/METADATA.ome.xml``, by the ZMART metadata convention
(``zmart_drivers/metadata_convention``). This file writes that XML: the
microscope, the image and its pixels, every channel and every plane with
where it was taken, and the annotations the convention adds. The XML follows
the OME 2016-06 schema's order, and holds plain ASCII only (any other letter
is written as a character reference, such as ``&#181;m``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/save_data/ome_canonical.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from zmart_drivers import metadata_convention as convention

from .product import AcquisitionMetadata, PlaneIndex, PlaneMetadata

OME_NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
MICROMETER = "µm"
#: The name NIS's own description and facts go under (the convention's vendor layer).
VENDOR = "nikon-nis-elements"
INSTRUMENT_ID = "Instrument:0"
OBJECTIVE_ID = "Objective:0"

ET.register_namespace("", OME_NS)
ET.register_namespace("xsi", XSI_NS)


def _tag(name: str) -> str:
    return f"{{{OME_NS}}}{name}"


def _num(value: float) -> str:
    return format(float(value), ".12g")


def image_xml(
    metadata: AcquisitionMetadata,
    *,
    image_name: str,
    notes: convention.Notes | None = None,
) -> bytes:
    """The OME-XML of one capture whose pixels are kept in its store (``MetadataOnly``)."""
    root = ET.Element(
        _tag("OME"), {f"{{{XSI_NS}}}schemaLocation": f"{OME_NS} {OME_NS}/ome.xsd"}
    )
    instrument = metadata.instrument
    if instrument is not None:
        element = ET.SubElement(root, _tag("Instrument"), {"ID": INSTRUMENT_ID})
        microscope = {"Manufacturer": instrument.manufacturer}
        if instrument.model:
            microscope["Model"] = instrument.model
        ET.SubElement(element, _tag("Microscope"), microscope)
        if instrument.objective:
            ET.SubElement(
                element,
                _tag("Objective"),
                {
                    "ID": OBJECTIVE_ID,
                    "Manufacturer": instrument.manufacturer,
                    "Model": instrument.objective,
                },
            )

    image = ET.SubElement(root, _tag("Image"), {"ID": "Image:0", "Name": image_name})
    if metadata.acquisition_date:
        ET.SubElement(image, _tag("AcquisitionDate")).text = metadata.acquisition_date
    if notes is not None and notes.image_description:
        ET.SubElement(image, _tag("Description")).text = notes.image_description
    if instrument is not None:
        ET.SubElement(image, _tag("InstrumentRef"), {"ID": INSTRUMENT_ID})
        if instrument.objective:
            ET.SubElement(image, _tag("ObjectiveSettings"), {"ID": OBJECTIVE_ID})
    label = (notes.capture_record or {}).get("position_label") if notes else None
    if label:
        ET.SubElement(image, _tag("StageLabel"), {"Name": str(label)})

    pixels_attrs = {
        "ID": "Pixels:0",
        "DimensionOrder": "XYZCT",
        "Type": metadata.pixel_type,
        "SizeX": str(metadata.size_x),
        "SizeY": str(metadata.size_y),
        "SizeZ": str(metadata.size_z),
        "SizeC": str(metadata.size_c),
        "SizeT": str(metadata.size_t),
        "PhysicalSizeX": _num(metadata.physical_size_x_um),
        "PhysicalSizeY": _num(metadata.physical_size_y_um),
    }
    if metadata.significant_bits is not None:
        pixels_attrs["SignificantBits"] = str(metadata.significant_bits)
    pixels = ET.SubElement(image, _tag("Pixels"), pixels_attrs)
    for channel in metadata.channels:
        attrs = {"ID": f"Channel:0:{channel.index}", "SamplesPerPixel": "1"}
        if channel.name:
            attrs["Name"] = channel.name
        if channel.color is not None:
            attrs["Color"] = str(channel.color)
        ET.SubElement(pixels, _tag("Channel"), attrs)
    ET.SubElement(pixels, _tag("MetadataOnly"))

    readings: dict[str, PlaneMetadata] = {}
    for index, plane in sorted(metadata.planes.items()):
        ref = _append_plane(pixels, index, plane)
        if ref is not None:
            readings.setdefault(ref, plane)

    annotations = ET.Element(_tag("StructuredAnnotations"))
    for ref, plane in readings.items():
        convention.append_map(
            annotations, ref, convention.ACTUATOR_READINGS, plane.actuators
        )
    if notes is not None:
        convention.append_notes(annotations, image, notes)
    if metadata.annotations:
        ref = f"Annotation:{VENDOR}-highlights"
        convention.reference(image, ref)
        convention.append_map(
            annotations, ref, convention.vendor_namespace(VENDOR), metadata.annotations
        )
    if len(annotations):
        root.append(annotations)

    text = ET.tostring(root, encoding="unicode")
    return b"<?xml version='1.0' encoding='utf-8'?>\n" + text.encode(
        "ascii", "xmlcharrefreplace"
    )


def _append_plane(
    pixels: ET.Element, index: PlaneIndex, plane: PlaneMetadata
) -> str | None:
    """One Plane with where it was taken; the ID of its actuator-readings annotation, when it has readings."""
    attrs = {"TheZ": str(index.z), "TheT": str(index.t), "TheC": str(index.c)}
    for axis, value in (
        ("X", plane.position_x_um),
        ("Y", plane.position_y_um),
        ("Z", plane.position_z_um),
    ):
        if value is not None:
            attrs[f"Position{axis}"] = _num(value)
            attrs[f"Position{axis}Unit"] = MICROMETER
    element = ET.SubElement(pixels, _tag("Plane"), attrs)
    if not plane.actuators:
        return None
    ref = convention.actuator_readings_id(index.t, index.z)
    convention.reference(element, ref)
    return ref
