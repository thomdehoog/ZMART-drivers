"""The store's description follows ZMART-controller's agreement, docs/the_store.

Layer 2 is the OME data model, schema-valid; layer 3 holds only what the
controller's contract defines, under the agreement's namespaces; layer 4 holds
LAS X's description byte for byte, and the driver's own facts.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris.navigator_expert.primitives.capture import (
    metadata_map as described,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data import (
    ome_canonical as canonical,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "lasx_description"
HIRES = "hires004-two-channels-12-bit"
STACK = "af-job002-17-planes-16-bit"
STORE = "https://github.com/ZMB-UZH/ZMART-controller/the_store/1/"

CONTROLLER_STATE = {
    "changeable": {"job": "Overview"},
    "observed": {"system type": "SIMULATOR"},
}
XYZ = {
    axis: {
        "position": 1.0,
        "unit": "micrometer",
        "used_actuator": "motoric",
        "actuator_readings": {"motoric": 2.0},
        "canvas": [0.0, 10.0],
    }
    for axis in "xyz"
}
CAPTURE_RECORD = {
    "position_label": "A1",
    "acquisition_type": "overview",
    "acquisition_settings": {"asked": {}, "used": {"trigger": "full"}},
    "started": "2026-10-22T18:59:05+00:00",
    "finished": "2026-10-22T18:59:07+00:00",
    "xyz": XYZ,
    "driver": {
        "name": "zmart_drivers.leica.stellaris.navigator_expert",
        "version": "0.7.0b4",
    },
    "controller": {"version": "0.4.0rc4"},
}


def notes(**overrides) -> canonical.StoreNotes:
    given = {
        "controller_state": CONTROLLER_STATE,
        "capture_record": CAPTURE_RECORD,
        "pixels_to_position": ((0, -1), (1, 0)),
        "vendor": {"origin": {"x": 5.0}},
        "vendor_descriptions": ('<Data><Image Name="Overview007"/></Data>',),
    }
    return canonical.StoreNotes(**{**given, **overrides})


def the_xml(name: str = HIRES, **overrides):
    """The OME description of a fixture capture, parsed and checked against the schema."""
    ome_types = pytest.importorskip("ome_types")
    metadata = described.metadata_from_description(
        (DATA / f"{name}.xml").read_text(encoding="utf-8")
    )
    text = canonical.image_xml(metadata, image_name="p", notes=notes(**overrides))
    return ome_types.from_xml(text.decode("ascii"), validate=True)


def annotations_in(ome, namespace: str) -> list:
    found = ome.structured_annotations
    every = [*found.comment_annotations, *found.map_annotations, *found.xml_annotations]
    return [one for one in every if one.namespace == namespace]


def the_one(ome, namespace: str):
    [annotation] = annotations_in(ome, namespace)
    return annotation


class TestLayerThree:
    """What the controller's contract defines, under the agreement's namespaces."""

    def test_the_controller_state_is_get_state_whole(self):
        ome = the_xml()

        held = json.loads(the_one(ome, STORE + "controller-state").value)

        assert held == CONTROLLER_STATE

    def test_the_capture_record_is_the_capture_in_the_controllers_words(self):
        ome = the_xml()

        held = json.loads(the_one(ome, STORE + "capture-record").value)

        assert held == CAPTURE_RECORD

    def test_the_image_frame_is_the_turn_applied_to_the_pixels(self):
        ome = the_xml()

        frame = {m.k: m.value for m in the_one(ome, STORE + "image-frame").value.ms}

        assert json.loads(frame["pixels"]) == [[0, -1], [1, 0]]

    def test_the_actuator_readings_are_per_plane_position_in_get_xyz_names(self):
        """One map per t and z, shared by the channels, keyed <axis>/<actuator>."""
        ome = the_xml(STACK)
        planes = ome.images[0].pixels.planes
        readings = annotations_in(ome, STORE + "actuator-readings")

        assert len(readings) == len({(plane.the_t, plane.the_z) for plane in planes})
        first = next(one for one in readings)
        keys = {m.k for m in first.value.ms}
        assert {"x/motoric", "y/motoric"} <= keys
        assert all(key.split("/")[0] in ("x", "y", "z") for key in keys)
        assert not any("(" in key for key in keys)
        by_id = {one.id: one for one in readings}
        for plane in planes:
            [ref] = plane.annotation_refs
            assert ref.id in by_id

    def test_nothing_of_the_vendors_is_in_layer_three(self):
        ome = the_xml()

        zmart = [
            one
            for one in [
                *ome.structured_annotations.comment_annotations,
                *ome.structured_annotations.map_annotations,
            ]
            if one.namespace.startswith(STORE) and "/vendor/" not in one.namespace
        ]

        assert zmart
        assert all(
            "Leica" not in str(one.value)
            for one in zmart
            if one.namespace != STORE + "controller-state"
        )

    def test_the_position_label_is_the_images_stage_label(self):
        ome = the_xml()

        assert ome.images[0].stage_label.name == "A1"


class TestLayerFour:
    """LAS X's own, byte for byte, and the driver's own facts."""

    def test_the_lasx_description_is_kept_byte_for_byte(self):
        ome = the_xml()

        [description] = [
            one
            for one in annotations_in(ome, STORE + "vendor/leica-lasx")
            if type(one).__name__ == "CommentAnnotation" and one.value.startswith("<")
        ]

        assert description.value == '<Data><Image Name="Overview007"/></Data>'

    def test_the_drivers_own_facts_and_the_lasx_highlights_are_layer_four(self):
        ome = the_xml()

        held = annotations_in(ome, STORE + "vendor/leica-lasx")
        texts = [one.value for one in held if type(one).__name__ == "CommentAnnotation"]
        maps = [one for one in held if type(one).__name__ == "MapAnnotation"]

        assert {"origin": {"x": 5.0}} in [
            json.loads(t) for t in texts if t.startswith("{")
        ]
        assert maps, "the readable LAS X key-value list is layer 4"


def test_without_notes_only_what_the_vendor_description_gives_is_written():
    """The actuator readings come from LAS X's description; the rest needs the notes."""
    ome_types = pytest.importorskip("ome_types")
    metadata = described.metadata_from_description(
        (DATA / f"{HIRES}.xml").read_text(encoding="utf-8")
    )

    ome = ome_types.from_xml(
        canonical.image_xml(metadata, image_name="p").decode("ascii"), validate=True
    )

    namespaces = {
        one.namespace
        for one in [
            *ome.structured_annotations.comment_annotations,
            *ome.structured_annotations.map_annotations,
        ]
    }
    assert namespaces == {STORE + "actuator-readings", STORE + "vendor/leica-lasx"}
