"""Generate canonical ZMART OME metadata.

Vendor OME is parsed as input/provenance. The files written by ``save``
use this module as the output metadata contract.

Contract with ``ome.py``: that module owns TIFF tag-270 access
(:func:`ome.read_tiff_tag_270`) and the validate/patch side; this module
GENERATES metadata and only ever reads existing OME through that public
function. The live settings read follows the one rule in
``vendor_specific/connection/waiting.py``, like every other reading.
"""

from __future__ import annotations

import json
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...vendor_specific.primitives.capture import tiff_tag as _ome
from ...vendor_specific.primitives.capture.product import (
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

# Namespace + annotation IDs for the embedded machine/software state block.
STATE_NS = "https://zmart-microscopy/state"
STATE_MAP_ID = "Annotation:zmart-state-map"
STATE_JSON_ID = "Annotation:zmart-state-json"

# The vendor's facts that OME has no field for, kept by name.
VENDOR_NS = "https://zmart-microscopy/vendor"
VENDOR_MAP_ID = "Annotation:vendor-facts"

INSTRUMENT_ID = "Instrument:0"
OBJECTIVE_ID = "Objective:0"
#: OME's Medium enumeration (ObjectiveSettings); an Immersion outside it is not a medium.
MEDIA = ("Air", "Oil", "Water", "Glycerol", "Other")

ET.register_namespace("", OME_NS)
ET.register_namespace("xsi", XSI_NS)


def image_xml(
    metadata: AcquisitionMetadata, *, image_name: str, state: dict | None = None
) -> bytes:
    """OME XML for a whole capture whose pixels live elsewhere (an OME-Zarr store).

    Every channel and every plane, with the pixels declared absent from the
    XML itself (``MetadataOnly``): the store beside it holds them.
    """
    return _ome_xml(
        metadata,
        image_name=image_name,
        channels=metadata.channels,
        tiff_entries=[],
        planes=sorted(metadata.planes.items()),
        state=state,
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
    state: dict | None = None,
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
    drive_maps = [
        (_append_plane(pixels, index, plane), plane) for index, plane in planes
    ]
    drive_maps = [(ref, plane) for ref, plane in drive_maps if ref is not None]

    if metadata.annotations or drive_maps or state is not None:
        # AnnotationRefs come AFTER Pixels in the Image element, and the one
        # StructuredAnnotations block AFTER Image in the OME element.
        annotations = ET.Element(_tag("StructuredAnnotations"))
        if metadata.annotations:
            ET.SubElement(image, _tag("AnnotationRef"), {"ID": VENDOR_MAP_ID})
            vendor = ET.SubElement(
                annotations,
                _tag("MapAnnotation"),
                {"ID": VENDOR_MAP_ID, "Namespace": VENDOR_NS},
            )
            value = ET.SubElement(vendor, _tag("Value"))
            for key, text in metadata.annotations:
                ET.SubElement(value, _tag("M"), {"K": key}).text = text
        for ref, plane in drive_maps:
            drives = ET.SubElement(
                annotations, _tag("MapAnnotation"), {"ID": ref, "Namespace": VENDOR_NS}
            )
            value = ET.SubElement(drives, _tag("Value"))
            for name, micrometres in plane.actuators:
                ET.SubElement(value, _tag("M"), {"K": name}).text = _num(micrometres)
        if state is not None:
            _append_state_annotations(annotations, image, state)
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
    ref = f"Annotation:drives-t{index.t}-z{index.z}-c{index.c}"
    ET.SubElement(element, _tag("AnnotationRef"), {"ID": ref})
    return ref


def _append_state_annotations(
    annotations: ET.Element, image: ET.Element, state: dict
) -> None:
    """Embed the machine/software state as schema-valid StructuredAnnotations.

    Two annotations in the ``StructuredAnnotations`` block: a
    ``MapAnnotation`` of flat human-readable highlights and a
    ``CommentAnnotation`` carrying the full JSON dump. The ``<Image>`` gets
    an ``AnnotationRef`` (after ``<Pixels>``) to each.
    """
    ET.SubElement(image, _tag("AnnotationRef"), {"ID": STATE_MAP_ID})
    ET.SubElement(image, _tag("AnnotationRef"), {"ID": STATE_JSON_ID})

    map_ann = ET.SubElement(
        annotations, _tag("MapAnnotation"), {"ID": STATE_MAP_ID, "Namespace": STATE_NS}
    )
    value = ET.SubElement(map_ann, _tag("Value"))
    for key, text in _state_map_entries(state):
        m = ET.SubElement(value, _tag("M"), {"K": key})
        m.text = _ascii(text)

    comment = ET.SubElement(
        annotations,
        _tag("CommentAnnotation"),
        {"ID": STATE_JSON_ID, "Namespace": STATE_NS},
    )
    comment_value = ET.SubElement(comment, _tag("Value"))
    comment_value.text = json.dumps(
        state, ensure_ascii=True, default=str, sort_keys=True
    )


def _state_map_entries(state: dict) -> list[tuple[str, str]]:
    """Flat human-readable highlights pulled defensively from *state*."""
    entries: list[tuple[str, str]] = []
    if not isinstance(state, dict):
        return entries

    software = state.get("software")
    if isinstance(software, dict):
        for key, val in software.items():
            if val is not None:
                entries.append((f"software.{key}", str(val)))

    hardware = state.get("hardware")
    if isinstance(hardware, dict):
        microscope = hardware.get("Microscope")
        if isinstance(microscope, dict) and microscope.get("name"):
            entries.append(("microscope", str(microscope["name"])))

    objective = _state_objective_name(state)
    if objective:
        entries.append(("objective", objective))

    provenance = state.get("provenance")
    if isinstance(provenance, dict):
        for key in (
            "folder",
            "position_label",
            "acquisition_hash",
            "session_hash6",
            "exported_at",
        ):
            val = provenance.get(key)
            if val is not None:
                entries.append((key, str(val)))
    return entries


def _state_objective_name(state: dict) -> str | None:
    """Best-effort objective name from a few known state shapes."""
    for candidate in (
        state.get("position"),
        state.get("job_settings"),
        state.get("hardware"),
    ):
        if not isinstance(candidate, dict):
            continue
        objective = candidate.get("objective")
        if isinstance(objective, dict) and objective.get("name"):
            return str(objective["name"])
        hardware = candidate.get("hardware")
        if isinstance(hardware, dict):
            objective = hardware.get("objective")
            if isinstance(objective, dict) and objective.get("name"):
                return str(objective["name"])
    return None


def _ascii(text: str) -> str:
    """Force 7-bit ASCII (TIFF tag 270 requirement); escape the rest."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


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
