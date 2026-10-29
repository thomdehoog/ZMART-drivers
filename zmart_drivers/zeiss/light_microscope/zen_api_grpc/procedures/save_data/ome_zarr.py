"""Write one ZEN capture as one OME-Zarr 0.5 image, following the ZMART metadata convention.

ZEN writes every acquisition as a CZI file. ``save`` turns that file into one
OME-Zarr store, ``data/<name>.ome.zarr``, the format every ZMART driver saves
in, so the ZMART Viewer and ZMART Analysis open a ZEISS capture exactly as
they open a Leica one. The store is made with OME's own models
(``ome-zarr-models``: OME-Zarr 0.5, checked as it is built) and written by
zarr-python (Zarr v3), the same way the Leica driver writes its stores. It
holds:

- the full-resolution pixels, axes t, c, z, y, x, with the lowest plane first;
- the pixels turned so that the image's columns run along the stage's x and
  its rows along the stage's y. A camera is often mounted a quarter turn
  away from the stage, and the orientation setup measured by how much; the
  turn only rearranges pixels, so nothing is lost;
- the pixel size, and where the image lies on the sample: the centre of its
  first pixel, half a pixel in from the corner of the picture, at its lowest
  plane, in the frame the workflow drives in (micrometres from the origin);
- each channel's name and colour, as ZEN described them, and a display
  window from its pixels (OME-Zarr's ``omero`` block);
- coarser levels below the full resolution, for looking at the image zoomed
  out: each halves y and x by the mean of 2x2 pixels, the ZMART Viewer's own
  rule, until a level fits one chunk;
- the capture's OME-XML at ``OME/METADATA.ome.xml``: the OME data model (the
  objective, the channels, where every plane was taken), ZMART's own
  annotations (the controller's state and the capture record), and ZEN's
  own description of the image, the CZI's metadata, byte for byte.

The store is written under a temporary name beside its place and renamed
into it whole, so a reader watching the folder sees either nothing or the
finished store.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
The store's layout, its pyramid and its renaming follow the Leica driver's
``ome_zarr.py``, so the two drivers write the same kind of store.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from zmart_drivers import metadata_convention as convention

log = logging.getLogger(__name__)

#: The edge of a chunk in y and x, as in the Leica driver's stores.
PIECE = 128

#: How long the finished store may wait to be renamed into place while
#: Windows refuses ("Access is denied") because another program, such as
#: the search indexer, has one of its fresh files open for a moment.
RENAME_WAIT_S = 10.0

AXES = "tczyx"

#: The name of ZEN, as the convention's vendor namespace names it.
VENDOR = "zeiss-zen"

OME_NS = convention.OME_NS
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("", OME_NS)
ET.register_namespace("xsi", XSI_NS)

#: The pixel types of a CZI that a store can hold, and their OME names.
_GREY_TYPES = {"Gray8": "uint8", "Gray16": "uint16", "Gray32Float": "float"}
#: Colour cameras write blue, green and red in one pixel; each becomes a channel.
_COLOUR_TYPES = {"Bgr24": "uint8", "Bgr48": "uint16", "Bgr96Float": "float"}
_COLOUR_PARTS = (("red", 0xFF0000FF), ("green", 0x00FF00FF), ("blue", 0x0000FFFF))


@dataclass(frozen=True)
class Channel:
    """One channel of the store: its name, and its colour as OME writes it (RGBA, or None)."""

    name: str
    color: int | None = None


@dataclass(frozen=True)
class Plane:
    """Where one plane of the store was taken: its place in the store and on the sample.

    ``t``, ``c`` and ``z`` are its indices in the store (``z`` 0 is the
    lowest); ``x_um``, ``y_um`` and ``z_um`` the centre of the field of view
    and the plane's height, in the frame; ``actuators`` what each drive read
    when it was taken, as ``(axis/actuator, micrometres)`` pairs.
    """

    t: int
    c: int
    z: int
    x_um: float | None
    y_um: float | None
    z_um: float | None
    actuators: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class Capture:
    """One capture gathered for writing: its pixels and what is said about them.

    ``volume`` is t, c, z, y, x, lowest plane first, already turned onto the
    stage's axes. ``pixel_um`` is the pixel size along the stored x and y;
    ``z_step_um`` the distance between two planes (None for one plane).
    ``translation`` is where the centre of the first voxel lies, in t, c, z,
    y, x. ``store_position`` finds a plane of the CZI, as (t, c, z) in ZEN's
    order, in the store.
    """

    volume: object
    pixel_um: tuple[float, float]
    z_step_um: float | None
    translation: list[float]
    channels: tuple[Channel, ...]
    planes: tuple[Plane, ...]
    pixel_type: str
    vendor_description: str | None = None
    acquisition_date: str | None = None
    objective: Mapping | None = None
    annotations: tuple[tuple[str, str], ...] = ()
    store_position: Mapping[tuple[int, int, int], tuple[int, int, int]] = field(
        default_factory=dict
    )


# =============================================================================
# Reading the CZI
# =============================================================================


def read_czi(path: Path | str) -> dict:
    """Every plane of a CZI as one t, c, z, y, x array, in ZEN's own order, and what ZEN said about it.

    Answers ``{"volume", "channels", "pixel_type", "pixel_um", "z_step_um",
    "description", "acquisition_date"}``. A colour camera's blue, green and
    red become three channels. Raises ``RuntimeError`` when ZEISS's reader
    ``pylibCZIrw`` is missing or the file holds a kind of pixel a store
    cannot take.
    """
    import numpy as np

    try:
        from pylibCZIrw import czi as pyczi
    except ImportError as why:
        raise RuntimeError(
            "saving a ZEN capture as OME-Zarr reads its CZI with ZEISS's pylibCZIrw; "
            'install the driver with its zeiss extra (pip install -e ".[zeiss]")'
        ) from why
    from .planes import _channel_names, _scaling_um

    with pyczi.open_czi(str(path)) as document:
        box = document.total_bounding_box
        sizes = {axis: max(1, span[1] - span[0]) for axis, span in box.items()}
        kinds = set(document.pixel_types.values())
        if len(kinds) != 1 or not kinds <= {*_GREY_TYPES, *_COLOUR_TYPES}:
            raise RuntimeError(
                f"{Path(path).name} holds pixels of the kind {sorted(kinds)}; an OME-Zarr store "
                f"can take one of {sorted({*_GREY_TYPES, *_COLOUR_TYPES})} per capture"
            )
        kind = kinds.pop()
        description = document.raw_metadata
        metadata = document.metadata
        size_t, size_c, size_z = sizes.get("T", 1), sizes.get("C", 1), sizes.get("Z", 1)
        first = {axis: span[0] for axis, span in box.items()}
        planes = {}
        for t in range(size_t):
            for c in range(size_c):
                for z in range(size_z):
                    index = {
                        axis: first.get(axis, 0) + offset
                        for axis, offset in (("T", t), ("C", c), ("Z", z))
                        if axis in box
                    }
                    planes[t, c, z] = np.asarray(document.read(plane=index))
    image = (metadata.get("ImageDocument") or {}).get("Metadata") or {}
    names = _channel_names(image)
    names = [*names[:size_c], *(f"channel {i}" for i in range(len(names), size_c))]
    colours = _channel_colours(image)
    sample = next(iter(planes.values()))
    if kind in _COLOUR_TYPES:
        # (y, x, 3) in blue, green, red: three channels per ZEN channel, red first.
        volume = np.stack(
            [
                np.stack(
                    [
                        np.stack(
                            [planes[t, c, z][..., 2 - part] for z in range(size_z)]
                        )
                        for c in range(size_c)
                        for part in range(3)
                    ]
                )
                for t in range(size_t)
            ]
        )
        channels = tuple(
            Channel(f"{names[c]} {colour}", rgba)
            for c in range(size_c)
            for colour, rgba in _COLOUR_PARTS
        )
        pixel_type = _COLOUR_TYPES[kind]
    else:
        volume = np.stack(
            [
                np.stack(
                    [
                        np.stack(
                            [
                                planes[t, c, z].reshape(sample.shape[:2])
                                for z in range(size_z)
                            ]
                        )
                        for c in range(size_c)
                    ]
                )
                for t in range(size_t)
            ]
        )
        channels = tuple(
            Channel(names[c], colours[c] if c < len(colours) else None)
            for c in range(size_c)
        )
        pixel_type = _GREY_TYPES[kind]
    scaling = _scaling_um(image)
    return {
        "volume": volume,
        "channels": channels,
        "pixel_type": pixel_type,
        "pixel_um": (scaling["X"], scaling["Y"])
        if "X" in scaling and "Y" in scaling
        else None,
        "z_step_um": scaling.get("Z"),
        "description": description,
        "acquisition_date": _acquisition_date(image),
    }


def _channel_colours(image: dict) -> list[int | None]:
    """Each channel's colour as OME writes it (signed 32-bit RGBA), from ZEN's ``#AARRGGBB``."""
    try:
        found = image["DisplaySetting"]["Channels"]["Channel"]
    except (KeyError, TypeError):
        try:
            found = image["Information"]["Image"]["Dimensions"]["Channels"]["Channel"]
        except (KeyError, TypeError):
            return []
    if isinstance(found, dict):
        found = [found]
    colours = []
    for one in found:
        text = str((one or {}).get("Color") or "").lstrip("#")
        try:
            argb = int(text, 16)
        except ValueError:
            colours.append(None)
            continue
        if len(text) == 6:
            argb |= 0xFF000000
        rgba = ((argb & 0x00FFFFFF) << 8) | ((argb >> 24) & 0xFF)
        colours.append(rgba - (1 << 32) if rgba >= 1 << 31 else rgba)
    return colours


def _acquisition_date(image: dict) -> str | None:
    """When ZEN says the image was taken, as OME writes a date (ISO 8601 without the zone)."""
    try:
        text = str(image["Information"]["Image"]["AcquisitionDateAndTime"])
    except (KeyError, TypeError):
        return None
    # OME 2016-06 takes a plain ISO date and time: the zone and the digits
    # past the microsecond are dropped.
    text = text.split("+")[0].rstrip("Z")
    date, _, rest = text.partition("T")
    if not rest:
        return None
    whole, _, fraction = rest.partition(".")
    return f"{date}T{whole}" + (f".{fraction[:6]}" if fraction else "")


# =============================================================================
# Turning the pixels onto the stage's axes, and placing them
# =============================================================================


def to_stage_axes(array, image_to_stage):
    """*array* (its last two axes rows and columns) turned so that columns run along +x and rows along +y.

    *image_to_stage* is the orientation's 2x2 matrix: its first column is
    the stage direction of one step along the camera's columns, its second
    that of one step along its rows. A quarter turn swaps rows and columns;
    a minus sign reverses one of them. Only the order of the pixels changes.
    """
    import numpy as np

    (a, b), (c, d) = image_to_stage
    if a != 0:
        # No swap: x follows the columns, y the rows.
        turned = array[..., :: (1 if d > 0 else -1), :: (1 if a > 0 else -1)]
    else:
        # A quarter turn: x follows the rows and y the columns.
        swapped = np.swapaxes(array, -1, -2)
        turned = swapped[..., :: (1 if c > 0 else -1), :: (1 if b > 0 else -1)]
    return np.ascontiguousarray(turned)


def gather(
    czi: dict,
    *,
    image_to_stage,
    heights_um: list[float],
    centre_um: tuple[float, float],
    readings: Mapping[str, float] | None = None,
    raw_z_um: list[float] | None = None,
    objective: Mapping | None = None,
) -> Capture:
    """The CZI's planes as one store's capture: turned, the lowest plane first, and placed on the sample.

    *heights_um* are the slice heights in the frame, in ZEN's slice order;
    *centre_um* the frame's x and y at the middle of the picture. *readings*
    are the stage drives' own readings at the capture (``x/motoric`` and
    ``y/motoric``) and *raw_z_um* the focus drive's reading of each slice, in
    ZEN's order, kept as each plane's actuator readings.
    """
    import numpy as np

    raw = czi["volume"]
    size_t, size_c, size_z = raw.shape[:3]
    if len(heights_um) != size_z:
        heights_um = [heights_um[0] if heights_um else 0.0] * size_z
    ascending = sorted(range(size_z), key=lambda z: heights_um[z])
    volume = to_stage_axes(raw[:, :, ascending], image_to_stage)

    pixel_x, pixel_y = czi["pixel_um"] or (None, None)
    if not pixel_x or not pixel_y:
        raise RuntimeError(
            "ZEN did not write the pixel size into the CZI, so the image cannot be placed on the sample"
        )
    if image_to_stage[0][0] == 0:
        pixel_x, pixel_y = pixel_y, pixel_x
    ny, nx = volume.shape[-2:]
    lowest = heights_um[ascending[0]]
    translation = [
        0.0,
        0.0,
        float(lowest),
        float(centre_um[1]) - (ny - 1) * pixel_y / 2,
        float(centre_um[0]) - (nx - 1) * pixel_x / 2,
    ]
    steps = np.diff([heights_um[z] for z in ascending])
    z_step = (
        float(np.mean(steps)) if size_z > 1 and np.all(steps > 0) else czi["z_step_um"]
    )
    planes = []
    store_position = {}
    for t in range(size_t):
        for c in range(size_c):
            for depth, z in enumerate(ascending):
                store_position[t, c, z] = (t, c, depth)
                pairs = dict(readings or {})
                if raw_z_um is not None and z < len(raw_z_um):
                    pairs["z/focus"] = raw_z_um[z]
                planes.append(
                    Plane(
                        t=t,
                        c=c,
                        z=depth,
                        x_um=float(centre_um[0]),
                        y_um=float(centre_um[1]),
                        z_um=float(heights_um[z]),
                        actuators=tuple(sorted(pairs.items())),
                    )
                )
    return Capture(
        volume=volume,
        pixel_um=(float(pixel_x), float(pixel_y)),
        z_step_um=z_step,
        translation=translation,
        channels=czi["channels"],
        planes=tuple(planes),
        pixel_type=czi["pixel_type"],
        vendor_description=czi["description"],
        acquisition_date=czi["acquisition_date"],
        objective=dict(objective) if objective else None,
        store_position=store_position,
    )


def max_projection(capture: Capture) -> Capture:
    """The brightest of every pixel over z: one plane, lying where the stack starts.

    What it was made from is said beside it: the method, how many planes,
    and the lowest and highest of them. A projected plane has no single
    focus reading, so the actuator readings are left out.
    """
    heights = sorted(p.z_um for p in capture.planes if p.z_um is not None)
    size_z = capture.volume.shape[2]
    facts = [("projection", "max over z"), ("projected planes", str(size_z))]
    if heights:
        facts += [
            ("lowest plane z (µm)", f"{heights[0]:.12g}"),
            ("highest plane z (µm)", f"{heights[-1]:.12g}"),
        ]
    return replace(
        capture,
        volume=capture.volume.max(axis=2, keepdims=True),
        z_step_um=None,
        planes=tuple(
            replace(plane, actuators=()) for plane in capture.planes if plane.z == 0
        ),
        annotations=(*capture.annotations, *facts),
        store_position={},
    )


def read_plane(store: Path, position: tuple[int, int, int]):
    """One 2-D plane of the saved *store*, at (t, c, z) in the order the store holds them."""
    import numpy as np
    import zarr

    t, c, z = position
    group = zarr.open_group(zarr.storage.LocalStore(str(store)), mode="r")
    return np.asarray(group["0"][t, c, z])


# =============================================================================
# Writing the store
# =============================================================================


def write_store(
    capture: Capture, store: Path, *, notes: convention.Notes | None = None
) -> Path:
    """Write one gathered capture as the OME-Zarr image *store*, whole, and return it.

    A store that already exists is refused: a capture is never written over
    another. The pixels are read back from disk and compared with what was
    written before the store is renamed into place.
    """
    import numpy as np
    import zarr
    from ome_zarr_models.v05.axes import Axis
    from ome_zarr_models.v05.image import Image
    from pydantic_zarr.v3 import ArraySpec

    store = Path(store)
    if store.exists():
        raise RuntimeError(
            f"{store} already exists; a capture is never written over another"
        )
    volume = capture.volume
    # Short, and hidden by its dot: the chunks lie several folders deeper,
    # and Windows refuses a path longer than 260 characters where long paths
    # are not enabled.
    writing = store.with_name(f".{uuid.uuid4().hex[:8]}.writing")
    store.parent.mkdir(parents=True, exist_ok=True)
    levels = mean_pyramid(volume)
    specs = [
        ArraySpec.from_array(
            level,
            chunk_grid={
                "name": "regular",
                "configuration": {
                    "chunk_shape": [
                        1,
                        1,
                        1,
                        min(PIECE, level.shape[-2]),
                        min(PIECE, level.shape[-1]),
                    ]
                },
            },
            dimension_names=list(AXES),
            # Said outright: newer zarr refuses a multi-byte array whose byte
            # order is left to a default.
            codecs=[{"name": "bytes", "configuration": {"endian": "little"}}],
        )
        for level in levels
    ]
    # Each coarser pixel is the mean of a 2x2 block, so its centre lies half
    # a finer pixel in from the block's first: x and y step on by that.
    scales, translations = [], []
    step_x, step_y = capture.pixel_um
    t, c, z, y, x = capture.translation
    for _ in levels:
        scales.append([1.0, 1.0, capture.z_step_um or 1.0, step_y, step_x])
        translations.append([t, c, z, y, x])
        y, x = y + step_y / 2, x + step_x / 2
        step_y, step_x = step_y * 2, step_x * 2
    image = Image.new(
        array_specs=specs,
        paths=[str(index) for index in range(len(levels))],
        axes=[
            Axis(name="t", type="time", unit="second"),
            Axis(name="c", type="channel"),
            Axis(name="z", type="space", unit="micrometer"),
            Axis(name="y", type="space", unit="micrometer"),
            Axis(name="x", type="space", unit="micrometer"),
        ],
        scales=scales,
        translations=translations,
        name=store.name,
    )
    group = image.to_zarr(zarr.storage.LocalStore(str(writing)), path="")
    group["0"][...] = volume
    if not np.array_equal(group["0"][...], volume):
        _remove_when_free(writing)
        raise RuntimeError(
            f"{store.name}: the pixels read back from disk are not those written; not saved"
        )
    for index, level in enumerate(levels[1:], start=1):
        group[str(index)][...] = level
    # The models write an absent value as null; a browser viewer refuses
    # "unit": null, so what is absent is left out, as the specification has it.
    ome = _without_empty(dict(group.attrs["ome"]))
    # The viewer bakes only stores that say their pyramid is a mean one.
    ome["multiscales"][0]["type"] = "mean"
    ome["multiscales"][0]["metadata"] = {
        "description": _levels_described(capture, len(levels))
    }
    deepest = (
        float(np.iinfo(volume.dtype).max)
        if volume.dtype.kind in "iu"
        else float(np.max(volume))
    )
    ome["omero"] = {
        "channels": [
            {
                "label": channel.name,
                "color": _hex(channel.color) if channel.color is not None else "FFFFFF",
                "window": _window(volume[:, index], deepest),
                "active": True,
                "coefficient": 1,
                "family": "linear",
                "inverted": False,
            }
            for index, channel in enumerate(capture.channels)
        ],
        "rdefs": {
            "defaultT": 0,
            "defaultZ": volume.shape[2] // 2,
            "model": "color",
        },
    }
    group.attrs["ome"] = ome
    group.attrs["zmart"] = convention.zarr_attributes()
    (writing / "OME").mkdir()
    (writing / "OME" / "METADATA.ome.xml").write_bytes(
        image_xml(capture, image_name=store.name, notes=notes)
    )
    _rename_when_free(writing, store)
    return store


def image_xml(
    capture: Capture, *, image_name: str, notes: convention.Notes | None = None
) -> bytes:
    """The store's OME 2016-06 XML: the objective, the image, its channels and planes, and the annotations.

    The pixels themselves are in the store (``MetadataOnly``). With *notes*,
    the convention's annotations; ZEN's own description of the image goes in
    as the vendor's description, byte for byte.
    """
    root = ET.Element(
        _tag("OME"), {f"{{{XSI_NS}}}schemaLocation": f"{OME_NS} {OME_NS}/ome.xsd"}
    )
    objective = capture.objective or {}
    instrument = ET.SubElement(root, _tag("Instrument"), {"ID": "Instrument:0"})
    ET.SubElement(instrument, _tag("Microscope"), {"Manufacturer": "ZEISS"})
    if objective:
        attrs = {"ID": "Objective:0"}
        if objective.get("name"):
            attrs["Model"] = str(objective["name"])
        if objective.get("magnification"):
            attrs["NominalMagnification"] = _num(float(objective["magnification"]))
        if objective.get("na"):
            attrs["LensNA"] = _num(float(objective["na"]))
        ET.SubElement(instrument, _tag("Objective"), attrs)
    image = ET.SubElement(root, _tag("Image"), {"ID": "Image:0", "Name": image_name})
    if capture.acquisition_date:
        ET.SubElement(image, _tag("AcquisitionDate")).text = capture.acquisition_date
    if notes is not None and notes.image_description:
        ET.SubElement(image, _tag("Description")).text = notes.image_description
    ET.SubElement(image, _tag("InstrumentRef"), {"ID": "Instrument:0"})
    if objective:
        ET.SubElement(image, _tag("ObjectiveSettings"), {"ID": "Objective:0"})
    label = (notes.capture_record or {}).get("position_label") if notes else None
    if label:
        ET.SubElement(image, _tag("StageLabel"), {"Name": str(label)})
    size_t, size_c, size_z, size_y, size_x = capture.volume.shape
    pixels_attrs = {
        "ID": "Pixels:0",
        "DimensionOrder": "XYZCT",
        "Type": capture.pixel_type,
        "SizeX": str(size_x),
        "SizeY": str(size_y),
        "SizeZ": str(size_z),
        "SizeC": str(size_c),
        "SizeT": str(size_t),
        "PhysicalSizeX": _num(capture.pixel_um[0]),
        "PhysicalSizeY": _num(capture.pixel_um[1]),
    }
    if capture.z_step_um:
        pixels_attrs["PhysicalSizeZ"] = _num(capture.z_step_um)
    pixels = ET.SubElement(image, _tag("Pixels"), pixels_attrs)
    for index, channel in enumerate(capture.channels):
        attrs = {
            "ID": f"Channel:0:{index}",
            "Name": channel.name,
            "SamplesPerPixel": "1",
        }
        if channel.color is not None:
            attrs["Color"] = str(channel.color)
        ET.SubElement(pixels, _tag("Channel"), attrs)
    ET.SubElement(pixels, _tag("MetadataOnly"))
    readings: dict[str, Plane] = {}
    for plane in capture.planes:
        attrs = {"TheZ": str(plane.z), "TheT": str(plane.t), "TheC": str(plane.c)}
        for axis, value in (("X", plane.x_um), ("Y", plane.y_um), ("Z", plane.z_um)):
            if value is not None:
                # A position has no unit by default in OME 2016-06, so it is said.
                attrs[f"Position{axis}"] = _num(value)
                attrs[f"Position{axis}Unit"] = "µm"
        element = ET.SubElement(pixels, _tag("Plane"), attrs)
        if plane.actuators:
            ref = convention.actuator_readings_id(plane.t, plane.z)
            convention.reference(element, ref)
            readings.setdefault(ref, plane)

    # The AnnotationRefs come after Pixels in the Image, and the one
    # StructuredAnnotations block after the Image, as the schema orders them.
    annotations = ET.Element(_tag("StructuredAnnotations"))
    for ref, plane in readings.items():
        convention.append_map(
            annotations, ref, convention.ACTUATOR_READINGS, plane.actuators
        )
    if notes is not None:
        convention.append_notes(annotations, image, notes)
    if capture.annotations:
        ref = f"Annotation:{VENDOR}-highlights"
        convention.reference(image, ref)
        convention.append_map(
            annotations, ref, convention.vendor_namespace(VENDOR), capture.annotations
        )
    if len(annotations):
        root.append(annotations)
    text = ET.tostring(root, encoding="unicode")
    return b"<?xml version='1.0' encoding='utf-8'?>\n" + text.encode(
        "ascii", "xmlcharrefreplace"
    )


def mean_pyramid(volume) -> list:
    """*volume* (t, c, z, y, x) and its coarser levels, each halving y and x by a 2x2 mean.

    Down until a level fits one chunk (:data:`PIECE`), and always at least
    one coarser level. z, c and t are never reduced.
    """
    levels = [volume]
    while len(levels) < 2 or max(levels[-1].shape[-2:]) > PIECE:
        if min(levels[-1].shape[-2:]) <= 1:
            break
        levels.append(halve_xy(levels[-1]))
    return levels


def halve_xy(values):
    """One level coarser: the mean of each 2x2 block in y and x.

    The ZMART Viewer's own reducer, so the viewer can use these levels as
    its own: an odd last row or column is repeated, the mean is taken in
    float64, and whole numbers are rounded half to even into their own type.
    """
    import numpy as np

    h, w = values.shape[-2:]
    even = np.pad(
        values, [(0, 0)] * (values.ndim - 2) + [(0, h % 2), (0, w % 2)], mode="edge"
    )
    blocks = even.reshape(*values.shape[:-2], (h + 1) // 2, 2, (w + 1) // 2, 2)
    mean = blocks.mean(axis=(-3, -1), dtype="float64")
    return (np.rint(mean) if values.dtype.kind in "iu" else mean).astype(values.dtype)


def _rename_when_free(
    writing: Path, store: Path, *, wait_s: float = RENAME_WAIT_S
) -> None:
    """Rename the finished *writing* folder to *store*, waiting out a passing lock on it.

    Windows refuses to rename a folder while any program holds one of its
    files open, with "Access is denied". That hold is usually brief, so the
    rename is tried again every tenth of a second for up to *wait_s*.
    """
    deadline = time.perf_counter() + wait_s
    started = time.perf_counter()
    while True:
        try:
            os.replace(writing, store)
        except PermissionError:
            if time.perf_counter() >= deadline:
                raise
            time.sleep(0.1)
            continue
        waited = time.perf_counter() - started
        if waited >= 0.1:
            log.warning(
                "%s: renamed into place after %.1f s; something held a file in it open",
                store.name,
                waited,
            )
        return


def _remove_when_free(writing: Path, *, wait_s: float = RENAME_WAIT_S) -> None:
    """Remove a half-written folder, waiting out a passing lock; one that stays is logged and left."""
    deadline = time.perf_counter() + wait_s
    while writing.exists():
        try:
            shutil.rmtree(writing)
        except OSError as why:
            if time.perf_counter() >= deadline:
                log.warning("%s could not be removed: %s", writing, why)
                return
            time.sleep(0.1)


def _levels_described(capture: Capture, count: int) -> str:
    """What the store's levels are, as OME-Zarr's multiscale ``metadata`` says it."""
    said = (
        f"{count} levels: full resolution, then each halving y and x by the mean of "
        "2x2 voxels (an odd edge repeated, integers rounded half to even); "
        "z, c and t are not reduced"
    )
    if capture.volume.shape[2] == 1 and not capture.z_step_um:
        said += "; a single plane, so its z scale of 1 is a placeholder, not a measured size"
    return said


def _without_empty(value):
    """*value* with every null entry left out of its dictionaries, at any depth."""
    if isinstance(value, Mapping):
        return {
            key: _without_empty(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_without_empty(item) for item in value]
    return value


def _window(pixels, deepest: float) -> dict:
    """Where a channel's display starts and ends: its 0.1st to 99.9th percentile."""
    import numpy as np

    start, end = (float(v) for v in np.percentile(pixels, (0.1, 99.9)))
    return {"min": 0.0, "max": deepest, "start": start, "end": end}


def _hex(rgba: int) -> str:
    """An OME colour (signed 32-bit RGBA) as the six hex digits OME-Zarr's omero block takes."""
    return f"{(rgba & 0xFFFFFFFF) >> 8:06X}"


def _tag(name: str) -> str:
    return f"{{{OME_NS}}}{name}"


def _num(value: float) -> str:
    return f"{value:.12g}"
