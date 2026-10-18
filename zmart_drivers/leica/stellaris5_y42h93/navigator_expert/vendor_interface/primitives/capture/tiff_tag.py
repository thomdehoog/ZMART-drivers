"""Read the description LAS X embeds in a TIFF file.

LAS X writes a picture's description (OME-XML) into the TIFF
"ImageDescription" tag (number 270) of the picture's file. This file finds
that tag in the first image of a TIFF and hands the text back, so the driver
can read LAS X's own description of a picture. The driver no longer writes
OME-TIFF files itself; what it saves is OME-Zarr.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import struct

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
