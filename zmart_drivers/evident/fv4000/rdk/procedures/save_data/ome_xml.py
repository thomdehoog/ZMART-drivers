"""The OME-XML of a capture: the OME data model, with ZMART's annotations and FLUOVIEW's own facts.

OME (the Open Microscopy Environment) is the community's standard way of
describing a microscope picture: its sizes, its channels, every plane's time
and position, and the instrument. The store written by ``ome_zarr.py`` keeps
this description in ``OME/METADATA.ome.xml``, as the ZMART metadata
convention (``zmart_drivers.metadata_convention``) asks. This file writes
that XML from an ``AcquisitionMetadata``; the convention writes ZMART's
annotations, and FLUOVIEW's own facts go in its vendor layer.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
After the Leica driver's OME writer.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from zmart_drivers import metadata_convention as convention

from .product import (
    AcquisitionMetadata,
    ChannelMetadata,
    Instrument,
    PlaneIndex,
    PlaneMetadata,
)

OME_NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
MICROMETER = "µm"

#: The name FLUOVIEW's own facts go under (the convention's vendor layer).
VENDOR = "evident-fluoview"

INSTRUMENT_ID = "Instrument:0"
OBJECTIVE_ID = "Objective:0"
MEDIA = ("Air", "Oil", "Water", "Glycerol", "Other")

ET.register_namespace("", OME_NS)
ET.register_namespace("xsi", XSI_NS)


def image_xml(
    metadata: AcquisitionMetadata,
    *,
    image_name: str,
    notes: convention.Notes | None = None,
) -> bytes:
    """OME 2016-06 XML for a whole capture whose pixels live in the store beside it (``MetadataOnly``)."""
    root = ET.Element(
        _tag("OME"), {_tag("schemaLocation", XSI_NS): f"{OME_NS} {OME_NS}/ome.xsd"}
    )
    instrument = metadata.instrument
    if instrument is not None:
        _append_instrument(root, instrument)
    image = ET.SubElement(root, _tag("Image"), {"ID": "Image:0", "Name": image_name})
    if metadata.acquisition_date:
        ET.SubElement(image, _tag("AcquisitionDate")).text = metadata.acquisition_date
    if notes is not None and notes.image_description:
        ET.SubElement(image, _tag("Description")).text = notes.image_description
    if instrument is not None:
        ET.SubElement(image, _tag("InstrumentRef"), {"ID": INSTRUMENT_ID})
        if instrument.objective is not None:
            settings = {"ID": OBJECTIVE_ID}
            if instrument.objective.immersion in MEDIA:
                settings["Medium"] = instrument.objective.immersion
            ET.SubElement(image, _tag("ObjectiveSettings"), settings)
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
    }
    if metadata.significant_bits is not None:
        pixels_attrs["SignificantBits"] = str(metadata.significant_bits)
    for axis, value in (
        ("X", metadata.physical_size_x_um),
        ("Y", metadata.physical_size_y_um),
        ("Z", metadata.physical_size_z_um),
    ):
        if value is not None:
            pixels_attrs[f"PhysicalSize{axis}"] = _num(value)
    pixels = ET.SubElement(image, _tag("Pixels"), pixels_attrs)
    for channel in metadata.channels:
        _append_channel(pixels, channel)
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


def _append_instrument(root: ET.Element, instrument: Instrument) -> None:
    element = ET.SubElement(root, _tag("Instrument"), {"ID": INSTRUMENT_ID})
    microscope = {
        key: value
        for key, value in (
            ("Manufacturer", instrument.manufacturer),
            ("Model", instrument.model),
            ("SerialNumber", instrument.serial_number),
            ("Type", instrument.type),
        )
        if value
    }
    if microscope:
        ET.SubElement(element, _tag("Microscope"), microscope)
    for laser in instrument.lasers:
        attrs = {"ID": laser.id, "Model": laser.model}
        if laser.wavelength_nm:
            attrs.update(Wavelength=_num(laser.wavelength_nm), WavelengthUnit="nm")
        ET.SubElement(element, _tag("Laser"), attrs)
    for detector in instrument.detectors:
        ET.SubElement(
            element,
            _tag("Detector"),
            {"ID": detector.id, "Model": detector.model, "Type": detector.type},
        )
    objective = instrument.objective
    if objective is not None:
        attrs = {"ID": OBJECTIVE_ID, "Model": objective.model}
        if objective.magnification is not None:
            attrs["NominalMagnification"] = _num(objective.magnification)
        if objective.lens_na is not None:
            attrs["LensNA"] = _num(objective.lens_na)
        if objective.immersion:
            attrs["Immersion"] = objective.immersion
        if objective.correction:
            attrs["Correction"] = objective.correction
        ET.SubElement(element, _tag("Objective"), attrs)


def _append_channel(pixels: ET.Element, channel: ChannelMetadata) -> None:
    attrs = {"ID": f"Channel:0:{channel.index}", "SamplesPerPixel": "1"}
    if channel.name:
        attrs["Name"] = channel.name
    if channel.color is not None:
        attrs["Color"] = str(channel.color)
    if channel.excitation_wavelength_nm:
        attrs.update(
            ExcitationWavelength=_num(channel.excitation_wavelength_nm),
            ExcitationWavelengthUnit="nm",
        )
    element = ET.SubElement(pixels, _tag("Channel"), attrs)
    if channel.laser_id:
        light = {"ID": channel.laser_id}
        if channel.laser_attenuation is not None:
            light["Attenuation"] = _num(min(1.0, max(0.0, channel.laser_attenuation)))
        ET.SubElement(element, _tag("LightSourceSettings"), light)
    if channel.detector_id:
        detector = {"ID": channel.detector_id}
        if channel.detector_offset is not None:
            detector["Offset"] = _num(channel.detector_offset)
        if channel.detector_gain is not None:
            detector["Gain"] = _num(channel.detector_gain)
        if channel.detector_voltage_v is not None:
            detector.update(Voltage=_num(channel.detector_voltage_v), VoltageUnit="V")
        ET.SubElement(element, _tag("DetectorSettings"), detector)


def _append_plane(
    pixels: ET.Element, index: PlaneIndex, plane: PlaneMetadata
) -> str | None:
    attrs = {"TheZ": str(index.z), "TheT": str(index.t), "TheC": str(index.c)}
    if plane.delta_t_s is not None:
        attrs.update(DeltaT=_num(plane.delta_t_s), DeltaTUnit="s")
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


def _tag(name: str, ns: str = OME_NS) -> str:
    return f"{{{ns}}}{name}"


def _num(value: float) -> str:
    return f"{value:.12g}"
