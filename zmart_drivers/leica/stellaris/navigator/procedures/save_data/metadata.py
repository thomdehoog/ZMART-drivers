r"""Read LAS X's description of a picture into ZMART's OME metadata, and LAS X's TIFF tags.

LAS X keeps one description beside every picture: in its Data Container
cache while the picture is taken (``collect.py``), and in the
``Metadata\<name>.xlif`` its AutoSave writes, entry for entry the same. It
is the full record of the capture: size and bit depth, the time of every
plane, the stage and focus positions, and the whole confocal setting
(objective, pinhole, detectors, laser lines, detection bands, filter
wheels, scan speed, averaging...). LAS X's own OME-TIFF keeps only the
first few of these; this module reads all of them.

What OME 2016-06 has a field for becomes that field (see ``product``);
the rest is kept by name in ``AcquisitionMetadata.annotations``. Values are
taken as the description gives them and never filled in: a fact it does
not state is left out, and a picture it does not describe in a way this
module knows is refused.

Two readings rest on the simulator only so far (11 October 2026) and are
named where they are made: the order of the time stamps of a picture with
several channels *and* several planes, and the per-setting values of a job
with more than one sequential setting.

Read the description LAS X embeds in a TIFF file.

LAS X writes a picture's description (OME-XML) into the TIFF
"ImageDescription" tag (number 270) of the picture's file. This file finds
that tag in the first image of a TIFF and hands the text back, so the driver
can read LAS X's own description of a picture. The driver no longer writes
OME-TIFF files itself; what it saves is OME-Zarr.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import datetime
import struct
import xml.etree.ElementTree as ET

from .product import (
    AcquisitionMetadata,
    ChannelMetadata,
    Detector,
    Filter,
    Instrument,
    Laser,
    Objective,
    PlaneIndex,
    PlaneMetadata,
)

__all__ = ["axes", "fields", "image_of", "metadata_from_description", "tile_positions"]

#: LAS X's dimension numbers for x, y and z. A time series (4) is taken one
#: picture at a time by the workflow, so a picture with any other dimension
#: is refused rather than guessed at.
X_AXIS, Y_AXIS, Z_AXIS = 1, 2, 3

#: The Navigator's tile scan (its Preview, over a list of positions) keeps its
#: fields one after the other along LAS X dimension 10, each field's position
#: in the ``TileScanInfo`` attachment; it comes with dimension 4 holding one
#: time point (simulator, 2026-10-15). A single time point is read as none; a
#: time series is still refused.
TIME_AXIS, TILE_AXIS = 4, 10

#: LAS X's look-up-table names as OME colours (RGBA, as a signed 32-bit int).
COLOURS = {
    "Red": 0xFF0000FF,
    "Green": 0x00FF00FF,
    "Blue": 0x0000FFFF,
    "Cyan": 0x00FFFFFF,
    "Magenta": 0xFF00FFFF,
    "Yellow": 0xFFFF00FF,
    "Gray": 0xFFFFFFFF,
    "Grey": 0xFFFFFFFF,
}

#: LAS X's immersion words as OME's Immersion.
IMMERSIONS = {
    "DRY": "Air",
    "OIL": "Oil",
    "WATER": "Water",
    "W": "Water",
    "GLYC": "Glycerol",
}

#: Lens corrections that the objective's name states, in OME's words.
CORRECTIONS = (
    ("PL APO", "PlanApo"),
    ("PL FLUOTAR", "PlanFluor"),
    ("FLUOTAR", "Fluotar"),
)

#: LAS X's detector types that OME names; every other one (HyD, SiPM) is "Other".
DETECTOR_TYPES = {"PMT": "PMT"}

#: Windows file time: 100-nanosecond ticks since 1 January 1601, UTC.
FILE_TIME_ZERO = datetime.datetime(1601, 1, 1, tzinfo=datetime.UTC)


def image_of(xml: str) -> ET.Element:
    """The ``Image`` element of a description, from the cache or from an ``.xlif``."""
    root = ET.fromstring(xml)
    image = root if root.tag == "Image" else root.find(".//Image")
    if image is None or image.find("ImageDescription") is None:
        raise RuntimeError(
            "LAS X's description of the picture has no Image with an ImageDescription"
        )
    return image


def axes(image: ET.Element) -> dict[int, dict]:
    """The picture's x, y and z axes (count, length in metres, origin, byte step), and its tile fields.

    The fields (:data:`TILE_AXIS`) give their count and byte step; a single
    time point is passed over. Refuses any other dimension, a time series,
    and any x, y or z axis not in metres.
    """
    found = {}
    for dimension in image.findall(
        "./ImageDescription/Dimensions/DimensionDescription"
    ):
        dim_id = int(dimension.get("DimID", "0"))
        if dim_id == TIME_AXIS and int(dimension.get("NumberOfElements")) == 1:
            continue
        if dim_id == TILE_AXIS:
            found[TILE_AXIS] = {
                "count": int(dimension.get("NumberOfElements")),
                "step": int(dimension.get("BytesInc")),
            }
            continue
        if dim_id not in (X_AXIS, Y_AXIS, Z_AXIS):
            raise RuntimeError(
                f"the picture has LAS X dimension {dim_id}; only x, y and z (1, 2, 3) are "
                "read, a time series or tile scan is taken picture by picture"
            )
        if dimension.get("Unit") != "m":
            raise RuntimeError(
                f"LAS X gives an axis in {dimension.get('Unit')!r}, not metres"
            )
        found[dim_id] = {
            "count": int(dimension.get("NumberOfElements")),
            "length_m": float(dimension.get("Length")),
            "origin_m": float(dimension.get("Origin")),
            "step": int(dimension.get("BytesInc")),
        }
    if X_AXIS not in found or Y_AXIS not in found:
        raise RuntimeError("LAS X's description of the picture gives no x or no y axis")
    return found


def fields(image: ET.Element) -> int:
    """How many fields the picture holds: a tile scan's, or one."""
    dims = axes(image)
    return dims[TILE_AXIS]["count"] if TILE_AXIS in dims else 1


def tile_positions(image: ET.Element) -> list[tuple[float, float, float]]:
    """Each field's stage position, in metres (x, y, z), as the ``TileScanInfo`` attachment lists them."""
    info = image.find("./Attachment[@Name='TileScanInfo']")
    if info is None:
        return []
    return [
        tuple(float(tile.get(axis)) for axis in ("PosX", "PosY", "PosZ"))
        for tile in info.findall("Tile")
    ]


def metadata_from_description(xml: str, field: int = 0) -> AcquisitionMetadata:
    """Everything the description says about the picture, or about one field of a tile scan, as ZMART's OME metadata.

    A field is placed where its tile was taken, and its time is counted from
    its own first plane.
    """
    image = image_of(xml)
    dims = axes(image)
    count = dims[TILE_AXIS]["count"] if TILE_AXIS in dims else 1
    if not 0 <= field < count:
        raise RuntimeError(f"the picture holds {count} field(s); field {field} is not there")
    position = None
    if TILE_AXIS in dims:
        listed = tile_positions(image)
        if len(listed) != count:
            raise RuntimeError(f"LAS X lists {len(listed)} positions for {count} fields")
        position = listed[field]
    channels = image.findall("./ImageDescription/Channels/ChannelDescription")
    if not channels:
        raise RuntimeError("LAS X's description of the picture lists no channels")
    bits = {int(channel.get("Resolution")) for channel in channels}
    if len(bits) != 1:
        raise RuntimeError(
            f"the picture's channels have different bit depths: {sorted(bits)}"
        )
    (significant_bits,) = bits
    if significant_bits > 16:
        raise RuntimeError(
            f"a {significant_bits}-bit picture is not read (8 to 16 bit are)"
        )

    hardware = image.find("./Attachment[@Name='HardwareSetting']")
    setting = (
        hardware.find("ATLConfocalSettingDefinition") if hardware is not None else None
    )
    if setting is None:
        raise RuntimeError("LAS X's description of the picture has no confocal setting")
    x, y = dims[X_AXIS], dims[Y_AXIS]
    z = dims.get(Z_AXIS)
    size_z = z["count"] if z else 1

    reading = Reading(
        image, hardware, setting, position=position, first_stamp=field * size_z * len(channels)
    )
    described = [reading.channel(c, element) for c, element in enumerate(channels)]
    return AcquisitionMetadata(
        size_x=x["count"],
        size_y=y["count"],
        size_t=1,
        size_z=size_z,
        size_c=len(channels),
        pixel_type="uint8" if significant_bits <= 8 else "uint16",
        # Length over pixels, as LAS X's own OME-TIFF gives the pixel size.
        physical_size_x_um=x["length_m"] / x["count"] * 1e6,
        physical_size_y_um=y["length_m"] / y["count"] * 1e6,
        # The z length spans the steps between the planes: 17 planes over
        # 38.55 um are 2.41 um apart, as LAS X's own plane heights say. A
        # stack taken top-down has a negative length (STELLARIS, 2026-10-15);
        # the distance between planes is the same, so its size is taken.
        physical_size_z_um=abs(z["length_m"]) / (size_z - 1) * 1e6
        if z and size_z > 1
        else None,
        significant_bits=significant_bits,
        acquisition_date=reading.acquisition_date(),
        channels=tuple(channel for channel, _ in described),
        instrument=reading.instrument(),
        planes=reading.planes(len(channels), z),
        annotations=reading.annotations(described),
    )


class Reading:
    """One description being read: the parts of the instrument are collected as channels name them."""

    def __init__(
        self,
        image: ET.Element,
        hardware: ET.Element,
        setting: ET.Element,
        *,
        position: tuple[float, float, float] | None = None,
        first_stamp: int = 0,
    ) -> None:
        self.image = image
        self.hardware = hardware
        self.setting = setting
        #: A tile scan's field: where it was taken (metres) and its first time stamp.
        self.position = position
        self.first_stamp = first_stamp
        self.sequence = hardware.find("LDM_Block_Sequential")
        self.detectors: dict[str, Detector] = {}
        self.lasers: dict[str, Laser] = {}
        self.filters: list[Filter] = []

    def setting_for(self, index: int) -> ET.Element:
        """The setting a channel was taken with.

        A job with one setting has it in full at the top. A job with several
        keeps each in its sequential list (STELLARIS, 2026-10-15: two
        settings, each channel with its own laser and detector, as LAS X
        showed them).
        """
        listed = (
            self.sequence.findall(
                "./LDM_Block_Sequential_List/ATLConfocalSettingDefinition"
            )
            if self.sequence is not None
            else []
        )
        return (
            listed[index] if len(listed) > 1 and index < len(listed) else self.setting
        )

    def channel(
        self, c: int, element: ET.Element
    ) -> tuple[ChannelMetadata, dict[str, str]]:
        """One channel, and the facts about it OME has no field for."""
        properties = {
            (p.findtext("Key") or ""): (p.findtext("Value") or "")
            for p in element.findall("ChannelProperty")
        }
        facts = {}
        dye = properties.get("DyeName") or None
        sequential = int(properties.get("SequentialSettingIndex") or 0)
        setting = self.setting_for(sequential)
        detector_element = next(
            (
                d
                for d in setting.findall("./DetectorList/Detector")
                if d.get("Name") == properties.get("DetectorName")
                and d.get("IsActive") == "1"
            ),
            None,
        )
        channel = {
            "index": c,
            "name": dye.split("/", 1)[-1] if dye else properties.get("DetectorName"),
            "fluor": dye.split("/", 1)[-1] if dye else None,
            "color": signed(COLOURS[element.get("LUTName")])
            if element.get("LUTName") in COLOURS
            else None,
            "pinhole_um": number(self.setting.get("Pinhole"), 1e6),
            "acquisition_mode": "LaserScanningConfocalMicroscopy",
        }
        if dye:
            facts["dye (LAS X)"] = dye
        facts["sequential setting"] = str(sequential)
        if detector_element is not None:
            detector = self.detector(detector_element)
            channel.update(
                detector_id=detector.id,
                detector_gain=number(detector_element.get("Gain")),
                detector_offset=number(detector_element.get("Offset")),
            )
            if detector_element.get("AcquisitionModeName"):
                facts["detector mode"] = detector_element.get("AcquisitionModeName")
            band = self.band(detector_element)
            if band is not None:
                channel["emission_filter_ids"] = (band.id,)
            reference = detector_element.find("DetectionReferenceLine")
            if reference is not None and reference.get("LaserName"):
                wavelength = number(reference.get("LaserWavelength"))
                laser = self.laser(reference.get("LaserName"))
                channel["excitation_wavelength_nm"] = wavelength
                if laser is not None:
                    channel["laser_id"] = laser[0].id
                    intensity = self.intensity(setting, laser[1], wavelength)
                    if intensity is not None:
                        channel["laser_attenuation"] = 1 - float(intensity) / 100
                        facts["laser intensity (%)"] = intensity
        return ChannelMetadata(**channel), facts

    def detector(self, element: ET.Element) -> Detector:
        name = element.get("Name")
        if name not in self.detectors:
            self.detectors[name] = Detector(
                id=f"Detector:{len(self.detectors)}",
                model=name,
                type=DETECTOR_TYPES.get(element.get("Type"), "Other"),
            )
        return self.detectors[name]

    def laser(self, name: str) -> tuple[Laser, ET.Element] | None:
        """The laser of this name in the setting, and its element."""
        element = next(
            (
                e
                for e in self.setting.findall("./LaserArray/Laser")
                if e.get("LaserName") == name
            ),
            None,
        )
        if element is None:
            return None
        if name not in self.lasers:
            wavelength = number(element.get("Wavelength"))
            self.lasers[name] = Laser(
                id=f"LightSource:{len(self.lasers)}",
                model=name,
                wavelength_nm=wavelength if wavelength else None,
                tuneable=not wavelength,
            )
        return self.lasers[name], element

    def intensity(
        self, setting: ET.Element, laser: ET.Element, wavelength: float | None
    ) -> str | None:
        """The intensity (in %) the laser's line was set to, as LAS X wrote it."""
        for aotf in setting.findall("./AotfList/Aotf"):
            if aotf.get("LightSourceType") != laser.get("LightSourceType"):
                continue
            for line in aotf.findall("LaserLineSetting"):
                if (
                    wavelength is not None
                    and number(line.get("LaserLine")) == wavelength
                ):
                    return line.get("IntensityDev")
        return None

    def band(self, detector: ET.Element) -> Filter | None:
        """The spectral band the detector caught, as a filter that passes it."""
        band = next(
            (
                b
                for b in self.setting.findall("./Spectro/MultiBand")
                if b.get("Channel") == detector.get("Channel")
            ),
            None,
        )
        if band is None:
            return None
        made = Filter(
            id=f"Filter:{len(self.filters)}",
            model=f"{detector.get('Name')} detection band",
            cut_in_nm=number(band.get("TargetWaveLengthBegin")),
            cut_out_nm=number(band.get("TargetWaveLengthEnd")),
        )
        self.filters.append(made)
        return made

    def instrument(self) -> Instrument:
        name = " ".join((self.setting.get("ObjectiveName") or "").split())
        objective = None
        if name:
            objective = Objective(
                model=name,
                magnification=number(self.setting.get("Magnification")),
                lens_na=number(self.setting.get("NumericalAperture")),
                immersion=IMMERSIONS.get(
                    (self.setting.get("Immersion") or "").upper(), "Other"
                ),
                correction=next(
                    (ome for words, ome in CORRECTIONS if words in name), None
                ),
                refractive_index=number(self.setting.get("RefractionIndex")),
            )
        return Instrument(
            manufacturer="Leica Microsystems",
            model=self.setting.get("MicroscopeModel")
            or self.hardware.get("SystemTypeName")
            or None,
            serial_number=self.setting.get("SystemSerialNumber") or None,
            type="Inverted"
            if self.setting.get("IsInverseMicroscopeModel") == "1"
            else "Upright",
            objective=objective,
            detectors=tuple(self.detectors.values()),
            lasers=tuple(self.lasers.values()),
            filters=tuple(self.filters),
        )

    def time_stamps(self) -> list[int]:
        """The picture's time stamps from its first plane on: a tile scan field's own."""
        stamps = self.image.find("TimeStampList")
        every = (
            [int(s, 16) for s in (stamps.text or "").split()]
            if stamps is not None
            else []
        )
        return every[self.first_stamp :]

    def acquisition_date(self) -> str | None:
        stamps = self.time_stamps()
        if not stamps:
            return None
        when = FILE_TIME_ZERO + datetime.timedelta(microseconds=stamps[0] // 10)
        return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def planes(self, size_c: int, z: dict | None) -> dict[PlaneIndex, PlaneMetadata]:
        """When every plane was taken, and where each drive stood for it.

        The stage gives x and y. For z, the description names the drive that
        made the stack (``ZUseModeName``): that one moves from the axis's
        origin by one step a plane, and every other z drive stands where the
        description says it stood (``AdditionalZPosition``; the drive that
        moved is recorded there at its last position, so it is not read for
        the planes). The plane's height is all z drives together. A stack
        made by z-wide is read the same way (STELLARIS, 2026-10-15: z-galvo
        and z-wide stacks, each with the other drive at its working height,
        matched LAS X's own plane heights). A stack taken top-down runs its
        planes downwards from the axis's origin, as LAS X took them.

        LAS X stamps every plane of every channel once, in the order the
        pixels are kept: channel inside plane (STELLARIS, 2026-10-15: two
        channels in a six-plane stack).
        """
        stamps = self.time_stamps()
        size_z = z["count"] if z else 1
        step = z["length_m"] / (size_z - 1) if z and size_z > 1 else 0.0
        origin = z["origin_m"] if z else number(self.setting.get("ZPosition"))
        moving = self.setting.get("ZUseModeName")
        standing = {
            drive.get("ZUseModeName"): number(drive.get("ZPosition"))
            for drive in self.setting.findall(
                "./AdditionalZPositionList/AdditionalZPosition"
            )
            if drive.get("ZUseModeName") and drive.get("ZUseModeName") != moving
        }
        if self.position is not None:
            x_um, y_um = self.position[0] * 1e6, self.position[1] * 1e6
        else:
            x_um = number(self.setting.get("StagePosX"), 1e6)
            y_um = number(self.setting.get("StagePosY"), 1e6)
        planes = {}
        for plane in range(size_z):
            drives = dict(standing)
            if moving and origin is not None:
                drives[moving] = origin + plane * step
            heights = [metres for metres in drives.values() if metres is not None]
            # Named as get_xyz names them in actuator_readings, <axis>/<actuator>:
            # the stage's one motor per axis, and LAS X's z drives by their names.
            actuators = [("x/motoric", x_um), ("y/motoric", y_um)]
            actuators += [
                (f"z/{name}", metres * 1e6 if metres is not None else None)
                for name, metres in sorted(drives.items())
            ]
            for c in range(size_c):
                stamp = plane * size_c + c
                planes[PlaneIndex(t=0, z=plane, c=c)] = PlaneMetadata(
                    delta_t_s=(stamps[stamp] - stamps[0]) / 1e7
                    if stamp < len(stamps)
                    else None,
                    position_x_um=x_um,
                    position_y_um=y_um,
                    position_z_um=sum(heights) * 1e6 if heights else None,
                    actuators=tuple(
                        (name, value) for name, value in actuators if value is not None
                    ),
                )
        return planes

    def annotations(
        self, described: list[tuple[ChannelMetadata, dict[str, str]]]
    ) -> tuple[tuple[str, str], ...]:
        """The facts OME has no field for, by name, as LAS X wrote them."""
        s = self.setting
        named = [
            (
                "job",
                self.sequence.get("BlockName") if self.sequence is not None else None,
            ),
            ("software", self.hardware.get("Software")),
            ("system type", self.hardware.get("SystemTypeName")),
            ("zoom", s.get("Zoom")),
            ("scan speed (Hz)", s.get("ScanSpeed")),
            ("pixel dwell time (s)", s.get("PixelDwellTime")),
            ("line average", s.get("LineAverage")),
            ("frame average", s.get("FrameAverage")),
            ("line accumulation", s.get("Line_Accumulation")),
            ("frame accumulation", s.get("FrameAccumulation")),
            ("scan direction", s.get("ScanDirectionXName")),
            ("scan field rotation (degrees)", s.get("RotatorAngle")),
            ("pinhole (Airy units)", s.get("PinholeAiry")),
            ("objective order number", s.get("ObjectiveNumber")),
            ("z drive of the stack", s.get("ZUseModeName")),
        ]
        for wheel in s.findall("./FilterWheel/Wheel"):
            if wheel.get("FilterWheelName"):
                named.append(
                    (
                        f"filter wheel {wheel.get('FilterWheelName').strip()}",
                        (wheel.get("FilterName") or "").strip(),
                    )
                )
        carrier = self.image.find("./Attachment[@Name='CarrierInfo']/Carrier")
        if carrier is not None:
            named.append(
                ("carrier", " ".join(f"{k}={v}" for k, v in carrier.attrib.items()))
            )
        for channel, facts in described:
            named.extend(
                (f"channel {channel.index} {key}", value)
                for key, value in facts.items()
            )
        return tuple((key, value) for key, value in named if value not in (None, ""))


def number(text: str | None, scale: float = 1.0) -> float | None:
    """A number the description gives, scaled; None when it gives none."""
    if text in (None, ""):
        return None
    try:
        return float(text) * scale
    except ValueError:
        return None


def signed(rgba: int) -> int:
    """An RGBA colour as OME's signed 32-bit integer."""
    return rgba - (1 << 32) if rgba >= 1 << 31 else rgba


_TIFF_TAG_IMAGE_DESCRIPTION = 270
_TIFF_LITTLE_ENDIAN = b"II"
_TIFF_BIG_ENDIAN = b"MM"
_TIFF_MAGIC = 42


def read_tiff_tag_270(data):
    """Locate ImageDescription (tag 270) in the first IFD of a TIFF.

    Args:
        data: Raw TIFF file content (bytes or bytearray).

    Returns:
        ``(xml_bytes, desc_offset, desc_count, desc_entry_pos, endian)``
        on success, or ``(None, None, None, None, error_string)`` on
        failure.
    """
    if len(data) < 8:
        return None, None, None, None, "File too small to be a TIFF"

    # Byte order
    if data[:2] == _TIFF_LITTLE_ENDIAN:
        endian = "<"
    elif data[:2] == _TIFF_BIG_ENDIAN:
        endian = ">"
    else:
        return None, None, None, None, "Not a valid TIFF file"

    # Magic number
    magic = struct.unpack_from(endian + "H", data, 2)[0]
    if magic != _TIFF_MAGIC:
        return None, None, None, None, f"Not a standard TIFF (magic={magic})"

    # First IFD
    ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
    if ifd_offset + 2 > len(data):
        return None, None, None, None, "IFD offset beyond file end"

    num_entries = struct.unpack_from(endian + "H", data, ifd_offset)[0]

    pos = ifd_offset + 2
    for _ in range(num_entries):
        if pos + 12 > len(data):
            break
        tag = struct.unpack_from(endian + "H", data, pos)[0]
        count = struct.unpack_from(endian + "I", data, pos + 4)[0]

        if tag == _TIFF_TAG_IMAGE_DESCRIPTION:
            if count <= 4:
                desc_offset = pos + 8
            else:
                desc_offset = struct.unpack_from(endian + "I", data, pos + 8)[0]

            xml_raw = bytes(data[desc_offset : desc_offset + count])
            xml_raw = xml_raw.rstrip(b"\x00")
            return xml_raw, desc_offset, count, pos, endian

        pos += 12

    return None, None, None, None, "No ImageDescription tag found"
