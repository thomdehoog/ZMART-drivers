"""The ZMART metadata convention: what every ZMART driver writes into a capture's store.

A capture is one OME-Zarr 0.5 store. Its OME-XML (``OME/METADATA.ome.xml``)
holds the OME data model, ZMART's annotations in the controller's own words,
and the vendor's own description. README.md beside this file is the
convention; this module is the part of writing it that is the same for every
driver, and ``check_store`` says whether a store follows it.

It imports the standard library only, so it installs with ZMART Drivers
alone; ``check_store`` reads a store with zarr, ome-zarr-models and ome-types,
when they are installed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: The version of the convention this module writes.
VERSION = "1"

#: Where ZMART's annotations live; the version is part of each name, so it travels with the XML.
NAMESPACE = f"https://github.com/ZMB-UZH/ZMART-drivers/metadata_convention/{VERSION}/"
CONTROLLER_STATE = NAMESPACE + "controller-state"
CAPTURE_RECORD = NAMESPACE + "capture-record"
IMAGE_FRAME = NAMESPACE + "image-frame"
ACTUATOR_READINGS = NAMESPACE + "actuator-readings"

#: The keys a capture record holds (``derived_from`` only for a store made from another).
CAPTURE_RECORD_KEYS = (
    "position_label",
    "acquisition_type",
    "acquisition_settings",
    "started",
    "finished",
    "xyz",
    "driver",
    "controller",
)

OME_NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"


def vendor_namespace(name: str) -> str:
    """Where a vendor's own description and facts live, named after the vendor's software."""
    return f"{NAMESPACE}vendor/{name}"


def zarr_attributes() -> dict:
    """What a store's ``zarr.json`` carries beside ``ome``: the convention it follows."""
    return {"metadata_convention": VERSION}


@dataclass(frozen=True)
class Notes:
    """What a store says besides its pixels and the OME fields.

    ``controller_state`` is ``get_state()`` whole and ``capture_record`` the
    capture in the controller's words (``CAPTURE_RECORD_KEYS``).
    ``pixels_to_position`` is the 2x2 turn or mirror already applied to the
    pixels. ``vendor`` is named after the vendor's software; ``vendor_facts``
    are the driver's and the vendor's own facts and ``vendor_descriptions``
    the vendor's descriptions of the image, each as its text, byte for byte.
    ``image_description`` is what the vendor calls the image.
    """

    controller_state: dict | None = None
    capture_record: dict | None = None
    pixels_to_position: tuple = ((1, 0), (0, 1))
    vendor: str | None = None
    vendor_facts: dict | None = None
    vendor_descriptions: tuple[str, ...] = ()
    image_description: str | None = None

    def as_json(self) -> dict:
        """Everything but the vendor's descriptions, as one JSON object, for a file beside the store."""
        return {
            "controller_state": self.controller_state,
            "capture_record": self.capture_record,
            "pixels_to_position": [list(row) for row in self.pixels_to_position],
            "vendor": self.vendor,
            "vendor_facts": self.vendor_facts,
        }


def append_notes(annotations: ET.Element, image: ET.Element, notes: Notes) -> None:
    """ZMART's annotations and the vendor's, each in its namespace, referenced from the Image.

    *annotations* is the OME ``StructuredAnnotations`` element and *image* the
    ``Image``; its ``AnnotationRef``s are appended, so call this after the
    ``Pixels`` are written, as the schema orders them.
    """
    texts = [
        (CONTROLLER_STATE, "controller-state", notes.controller_state),
        (CAPTURE_RECORD, "capture-record", notes.capture_record),
    ]
    if notes.vendor:
        texts.append(
            (
                vendor_namespace(notes.vendor),
                f"{notes.vendor}-facts",
                notes.vendor_facts,
            )
        )
    for namespace, name, value in texts:
        if value is not None:
            append_comment(
                annotations, image, f"Annotation:{name}", namespace, _json(value)
            )
    frame = [("pixels", json.dumps([list(row) for row in notes.pixels_to_position]))]
    reference(image, "Annotation:image-frame")
    append_map(annotations, "Annotation:image-frame", IMAGE_FRAME, frame)
    for number, description in enumerate(notes.vendor_descriptions):
        append_comment(
            annotations,
            image,
            f"Annotation:{notes.vendor}-description-{number}",
            vendor_namespace(notes.vendor or "unknown"),
            description,
        )


def actuator_readings_id(t: int, z: int) -> str:
    """The annotation a plane position's readings are kept in; its channels share it."""
    return f"Annotation:actuator-readings-t{t}-z{z}"


def append_comment(
    annotations: ET.Element, image: ET.Element, ref: str, namespace: str, text: str
) -> None:
    reference(image, ref)
    comment = ET.SubElement(
        annotations, _tag("CommentAnnotation"), {"ID": ref, "Namespace": namespace}
    )
    ET.SubElement(comment, _tag("Value")).text = text


def append_map(annotations: ET.Element, ref: str, namespace: str, pairs) -> None:
    made = ET.SubElement(
        annotations, _tag("MapAnnotation"), {"ID": ref, "Namespace": namespace}
    )
    value = ET.SubElement(made, _tag("Value"))
    for key, text in pairs:
        ET.SubElement(value, _tag("M"), {"K": key}).text = (
            _number(text) if isinstance(text, float) else str(text)
        )


def reference(element: ET.Element, ref: str) -> None:
    ET.SubElement(element, _tag("AnnotationRef"), {"ID": ref})


def check_store(store: str | Path) -> list[str]:
    """What keeps *store* from following the convention; empty when it does.

    Reads its ``zarr.json`` and ``OME/METADATA.ome.xml``: layer 1 is a valid
    OME-Zarr 0.5 image naming the convention; the XML is valid against the OME
    2016-06 schema; ZMART's annotations are there, in their namespaces, with
    the capture record's keys.
    """
    import zarr
    from ome_types import from_xml
    from ome_zarr_models.v05.image import Image

    store = Path(store)
    problems = []
    attributes = json.loads((store / "zarr.json").read_text(encoding="utf-8"))[
        "attributes"
    ]
    if (attributes.get("zmart") or {}).get("metadata_convention") != VERSION:
        problems.append(
            f"zarr.json does not say it follows metadata convention {VERSION}"
        )
    try:
        Image.from_zarr(zarr.open_group(str(store), mode="r"))
    except Exception as why:  # noqa: BLE001 -- every reason is a problem to report
        problems.append(f"not a valid OME-Zarr 0.5 image: {why}")
    try:
        ome = from_xml(
            (store / "OME" / "METADATA.ome.xml").read_text(encoding="utf-8"),
            validate=True,
        )
    except Exception as why:  # noqa: BLE001
        return [*problems, f"OME/METADATA.ome.xml is not valid OME 2016-06: {why}"]
    found = ome.structured_annotations
    every = [*found.comment_annotations, *found.map_annotations]
    by_namespace = {}
    for one in every:
        by_namespace.setdefault(one.namespace, []).append(one)
    for namespace in (CONTROLLER_STATE, CAPTURE_RECORD, IMAGE_FRAME):
        if len(by_namespace.get(namespace, [])) != 1:
            problems.append(f"there is not exactly one annotation in {namespace}")
    if len(by_namespace.get(CAPTURE_RECORD, [])) == 1:
        record = json.loads(by_namespace[CAPTURE_RECORD][0].value)
        missing = [key for key in CAPTURE_RECORD_KEYS if key not in record]
        if missing:
            problems.append(f"the capture record has no {', '.join(missing)}")
    return problems


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str, sort_keys=True)


def _tag(name: str) -> str:
    return f"{{{OME_NS}}}{name}"


def _number(value: float) -> str:
    return format(value, ".12g")
