"""Generate canonical ZMART OME metadata.

Vendor OME is parsed as input/provenance. The files written by ``save``
use this module as the output metadata contract: the store's description
follows the ZMART metadata convention (``zmart_drivers.metadata_convention``).
This module writes the OME data model from what LAS X described; the
convention writes ZMART's annotations, and LAS X's own goes in its vendor
layer.

Contract with ``ome.py``: that module owns TIFF tag-270 access
(:func:`ome.read_tiff_tag_270`) and the validate/patch side; this module
GENERATES metadata and only ever reads existing OME through that public
function. The live settings read follows the one rule in
``connection/waiting.py``, like every other reading.
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any

from zmart_drivers import metadata_convention as convention
from ...primitives.capture import tiff_tag as _ome
from ...primitives.capture.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    Instrument,
    PlaneIndex,
    PlaneMetadata,
)

log = logging.getLogger(__name__)

OME_NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
MICROMETER = "\u00b5m"

#: The name LAS X's own description and facts go under (the convention's vendor layer).
VENDOR = "leica-lasx"

INSTRUMENT_ID = "Instrument:0"
OBJECTIVE_ID = "Objective:0"
#: OME's Medium enumeration (ObjectiveSettings); an Immersion outside it is not a medium.
MEDIA = ("Air", "Oil", "Water", "Glycerol", "Other")

ET.register_namespace("", OME_NS)
ET.register_namespace("xsi", XSI_NS)


def image_xml(
    metadata: AcquisitionMetadata,
    *,
    image_name: str,
    notes: convention.Notes | None = None,
) -> bytes:
    """OME XML for a whole capture whose pixels live elsewhere (an OME-Zarr store).

    Every channel and every plane, with the pixels declared absent from the
    XML itself (``MetadataOnly``): the store beside it holds them. With
    *notes*, the convention's annotations as well.
    """
    return _ome_xml(
        metadata,
        image_name=image_name,
        channels=metadata.channels,
        tiff_entries=[],
        planes=sorted(metadata.planes.items()),
        notes=notes,
    )


def extract_embedded_ome_xml(tiff_src: Path) -> bytes:
    """Return raw OME-XML from TIFF ImageDescription tag 270."""
    try:
        data = tiff_src.read_bytes()
    except OSError as e:
        raise RuntimeError(f"Could not read embedded OME source {tiff_src}: {e}") from e

    xml_raw, _offset, _count, _entry_pos, endian_or_err = _ome.read_tiff_tag_270(data)
    if xml_raw is not None:
        return xml_raw

    try:
        import tifffile

        with tifffile.TiffFile(str(tiff_src)) as tif:
            description = tif.pages[0].description
    except Exception as e:
        raise RuntimeError(
            f"Could not extract embedded OME-XML from {tiff_src}: "
            f"{endian_or_err}; tifffile fallback failed: {e}"
        ) from e
    if not description or "<OME" not in description:
        raise RuntimeError(
            f"Could not extract embedded OME-XML from {tiff_src}: "
            f"{endian_or_err}; tifffile found no OME ImageDescription"
        )
    return description.encode("utf-8")


def _ome_xml(
    metadata: AcquisitionMetadata,
    *,
    image_name: str,
    channels: tuple[ChannelMetadata, ...],
    tiff_entries: list[tuple[int, int, int, str]],
    planes: list[tuple[PlaneIndex, PlaneMetadata]] = (),
    notes: convention.Notes | None = None,
) -> bytes:
    """OME 2016-06 XML for the planes named, in the order the schema requires.

    OME holds Instrument, Image, StructuredAnnotations; Image holds
    AcquisitionDate, InstrumentRef, ObjectiveSettings, Pixels, AnnotationRef;
    Pixels holds Channel, TiffData, Plane. The text is plain ASCII, as TIFF
    tag 270 must be: any other character is written as an XML character
    reference (``&#181;m``), which every OME reader decodes.
    """
    root = ET.Element(
        _tag("OME"),
        {
            _tag("schemaLocation", XSI_NS): f"{OME_NS} {OME_NS}/ome.xsd",
        },
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
        objective = instrument.objective
        if objective is not None:
            settings = {"ID": OBJECTIVE_ID}
            if objective.refractive_index is not None:
                settings["RefractiveIndex"] = _num(objective.refractive_index)
            if objective.immersion in MEDIA:
                settings["Medium"] = objective.immersion
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
    _add_physical(pixels_attrs, "X", metadata.physical_size_x_um)
    _add_physical(pixels_attrs, "Y", metadata.physical_size_y_um)
    _add_physical(pixels_attrs, "Z", metadata.physical_size_z_um)
    pixels = ET.SubElement(image, _tag("Pixels"), pixels_attrs)

    for channel in channels:
        _append_channel(pixels, channel)
    if not tiff_entries:
        ET.SubElement(pixels, _tag("MetadataOnly"))
    for first_t, first_z, first_c, filename in tiff_entries:
        tiff_data = ET.SubElement(
            pixels,
            _tag("TiffData"),
            {
                "FirstT": str(first_t),
                "FirstZ": str(first_z),
                "FirstC": str(first_c),
                "IFD": "0",
                "PlaneCount": "1",
            },
        )
        uuid_el = ET.SubElement(tiff_data, _tag("UUID"), {"FileName": filename})
        uuid_el.text = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, filename))
    # The actuator readings of a plane position, shared by its channels: one map per t and z.
    readings: dict[str, PlaneMetadata] = {}
    for index, plane in planes:
        ref = _append_plane(pixels, index, plane)
        if ref is not None:
            readings.setdefault(ref, plane)

    # AnnotationRefs come AFTER Pixels in the Image element, and the one
    # StructuredAnnotations block AFTER Image in the OME element.
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
    """The microscope, its lasers, detectors, objective and filters, in schema order."""
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
        if instrument.manufacturer:
            attrs["Manufacturer"] = instrument.manufacturer
        if laser.wavelength_nm:
            attrs["Wavelength"] = _num(laser.wavelength_nm)
            attrs["WavelengthUnit"] = "nm"
        if laser.tuneable:
            attrs["Tuneable"] = "true"
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
        if instrument.manufacturer:
            attrs["Manufacturer"] = instrument.manufacturer
        if objective.magnification is not None:
            attrs["NominalMagnification"] = _num(objective.magnification)
        if objective.lens_na is not None:
            attrs["LensNA"] = _num(objective.lens_na)
        if objective.immersion:
            attrs["Immersion"] = objective.immersion
        if objective.correction:
            attrs["Correction"] = objective.correction
        ET.SubElement(element, _tag("Objective"), attrs)
    for item in instrument.filters:
        attrs = {"ID": item.id, "Model": item.model}
        if item.type:
            attrs["Type"] = item.type
        made = ET.SubElement(element, _tag("Filter"), attrs)
        passes = {}
        if item.cut_in_nm is not None:
            passes.update(CutIn=_num(item.cut_in_nm), CutInUnit="nm")
        if item.cut_out_nm is not None:
            passes.update(CutOut=_num(item.cut_out_nm), CutOutUnit="nm")
        if passes:
            ET.SubElement(made, _tag("TransmittanceRange"), passes)


def _append_channel(pixels: ET.Element, channel: ChannelMetadata) -> None:
    """One Channel with its light source, detector and light path, in schema order."""
    element = ET.SubElement(pixels, _tag("Channel"), _channel_attrs(channel))
    if channel.laser_id:
        attrs = {"ID": channel.laser_id}
        if channel.laser_attenuation is not None:
            attrs["Attenuation"] = _num(min(1.0, max(0.0, channel.laser_attenuation)))
        if channel.excitation_wavelength_nm:
            attrs["Wavelength"] = _num(channel.excitation_wavelength_nm)
            attrs["WavelengthUnit"] = "nm"
        ET.SubElement(element, _tag("LightSourceSettings"), attrs)
    if channel.detector_id:
        attrs = {"ID": channel.detector_id}
        if channel.detector_offset is not None:
            attrs["Offset"] = _num(channel.detector_offset)
        if channel.detector_gain is not None:
            attrs["Gain"] = _num(channel.detector_gain)
        ET.SubElement(element, _tag("DetectorSettings"), attrs)
    if channel.emission_filter_ids:
        path = ET.SubElement(element, _tag("LightPath"))
        for filter_id in channel.emission_filter_ids:
            ET.SubElement(path, _tag("EmissionFilterRef"), {"ID": filter_id})


def _append_plane(
    pixels: ET.Element, index: PlaneIndex, plane: PlaneMetadata
) -> str | None:
    """One Plane; when it names where each drive stood, the ID of the annotation that will say so."""
    attrs = {"TheZ": str(index.z), "TheT": str(index.t), "TheC": str(index.c)}
    if plane.delta_t_s is not None:
        attrs.update(DeltaT=_num(plane.delta_t_s), DeltaTUnit="s")
    for axis, value in (
        ("X", plane.position_x_um),
        ("Y", plane.position_y_um),
        ("Z", plane.position_z_um),
    ):
        if value is not None:
            # A position has no unit by default in OME 2016-06 ("reference
            # frame"), so it is always said.
            attrs[f"Position{axis}"] = _num(value)
            attrs[f"Position{axis}Unit"] = MICROMETER
    element = ET.SubElement(pixels, _tag("Plane"), attrs)
    if not plane.actuators:
        return None
    ref = convention.actuator_readings_id(index.t, index.z)
    convention.reference(element, ref)
    return ref


def _channel_attrs(channel: ChannelMetadata) -> dict[str, str]:
    attrs = {
        "ID": f"Channel:0:{channel.index}",
        "SamplesPerPixel": "1",
    }
    if channel.name:
        attrs["Name"] = channel.name
    if channel.color is not None:
        attrs["Color"] = str(channel.color)
    if channel.wavelength_nm is not None and channel.wavelength_nm > 0:
        attrs["EmissionWavelength"] = _num(channel.wavelength_nm)
        attrs["EmissionWavelengthUnit"] = "nm"
    if channel.excitation_wavelength_nm:
        attrs["ExcitationWavelength"] = _num(channel.excitation_wavelength_nm)
        attrs["ExcitationWavelengthUnit"] = "nm"
    if channel.pinhole_um is not None:
        attrs["PinholeSize"] = _num(channel.pinhole_um)
        attrs["PinholeSizeUnit"] = MICROMETER
    if channel.fluor:
        attrs["Fluor"] = channel.fluor
    if channel.acquisition_mode:
        attrs["AcquisitionMode"] = channel.acquisition_mode
    return attrs


def _ensure_channels(metadata: AcquisitionMetadata) -> AcquisitionMetadata:
    channels = {c.index: c for c in metadata.channels}
    return replace(
        metadata,
        channels=tuple(
            channels.get(i, ChannelMetadata(index=i)) for i in range(metadata.size_c)
        ),
    )


_UNKNOWN = object()


def _add_physical(attrs: dict[str, str], axis: str, value_um: float | None) -> None:
    if value_um is None:
        return
    attrs[f"PhysicalSize{axis}"] = _num(value_um)
    # No explicit Unit attribute: the OME schema default is exactly µm, and
    # the embedded per-plane XML lives in TIFF tag 270, which tifffile
    # requires to be 7-bit ASCII — a literal 'µm' would fail the write.


def _tag(name: str, ns: str = OME_NS) -> str:
    return f"{{{ns}}}{name}"


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_float_or_unknown(value: Any) -> float | object:
    parsed = _float_or_none(value)
    if parsed is None or parsed <= 0:
        return _UNKNOWN
    return parsed


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: float) -> str:
    return f"{value:.12g}"
