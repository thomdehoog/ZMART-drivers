"""Pytest gate for the cache-route check's reading of a store's OME metadata.

A check that meets broken metadata has to say so in its report, not stop:
on the STELLARIS (2026-10-15) a top-down stack was saved with a negative z
pixel size, and the check crashed reading it, so the run left no report.
"""

import check_cache_route
import pytest

pytest.importorskip("ome_types")

OME = (
    '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
    '<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint8" '
    'SizeX="2" SizeY="2" SizeZ="2" SizeC="1" SizeT="1" '
    'PhysicalSizeX="1.0" PhysicalSizeY="1.0" PhysicalSizeZ="{z}">'
    '<Channel ID="Channel:0:0" SamplesPerPixel="1"/>'
    "<MetadataOnly/></Pixels></Image></OME>"
)


def test_valid_metadata_is_read():
    checks = {}
    ome = check_cache_route._read_ome(OME.format(z="2.409"), checks)
    assert ome.images[0].pixels.physical_size_z == pytest.approx(2.409)
    assert checks["valid OME 2016-06"] in (True, "not checked (lxml missing)")


def test_a_negative_z_pixel_size_is_reported_not_raised():
    checks = {}
    ome = check_cache_route._read_ome(OME.format(z="-2.409"), checks)
    assert ome is None
    assert str(checks["valid OME 2016-06"]).startswith("NO")
