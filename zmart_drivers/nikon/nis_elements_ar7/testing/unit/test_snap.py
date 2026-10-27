"""The snap: one picture into a new NIS document, saved as a TIFF, and only that document closed.

The operator may have their own images open in NIS. The snap must never save
or close one of them. So it captures, finds exactly one new document, saves
that one, and closes that one. When NIS captured into an existing document,
or took no picture, nothing is saved and nothing is closed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest
import tifffile
from mock_nis import HEIGHT, WIDTH, open_operator_image

from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import handshake
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import NisRefused
from zmart_drivers.nikon.nis_elements_ar7.procedures import changes


@pytest.fixture
def governed(client):
    handshake.connect_handshake(client)
    return client


def test_a_snap_saves_the_new_picture_and_keeps_the_operators_image(
    governed, stand_in, tmp_path
):
    mine = open_operator_image(stand_in.scope)
    path = tmp_path / "staging" / "a.tif"
    answer = changes.take_picture(governed, str(path))
    assert path.is_file()
    assert tifffile.imread(path).shape == (HEIGHT, WIDTH)
    assert answer["image"] == {
        "width": WIDTH,
        "height": HEIGHT,
        "bits": 12,
        "components": 1,
    }
    assert answer["calibration"]["pixel"] == pytest.approx(0.65)
    assert answer["document"] != mine.name
    assert [d.name for d in stand_in.scope.documents] == [mine.name]
    assert stand_in.scope.current is mine


def test_a_capture_into_an_existing_document_saves_and_closes_nothing(
    governed, stand_in, tmp_path
):
    mine = open_operator_image(stand_in.scope)
    stand_in.scope.capture_into_current = True
    path = tmp_path / "a.tif"
    with pytest.raises(RuntimeError, match="new document"):
        changes.take_picture(governed, str(path))
    assert not path.exists()
    assert (
        "ImageSaveAs" not in stand_in.scope.calls
        and "CloseCurrentDocument" not in stand_in.scope.calls
    )
    assert stand_in.scope.documents == [mine]


def test_no_picture_with_the_camera_off_saves_nothing(governed, stand_in, tmp_path):
    stand_in.scope.hardware_on = False
    with pytest.raises(RuntimeError, match="-9"):
        changes.take_picture(governed, str(tmp_path / "a.tif"))
    assert "ImageSaveAs" not in stand_in.scope.calls


def test_a_capture_is_sent_once_whatever_happens(governed, stand_in, tmp_path):
    stand_in.scope.hardware_on = False
    with pytest.raises(RuntimeError):
        changes.take_picture(governed, str(tmp_path / "a.tif"))
    assert stand_in.scope.calls.count("Capture") == 1


def test_a_snap_never_saves_over_a_file(governed, stand_in, tmp_path):
    path = tmp_path / "a.tif"
    path.write_bytes(b"earlier")
    with pytest.raises(RuntimeError, match="exists"):
        changes.take_picture(governed, str(path))
    assert path.read_bytes() == b"earlier"
    assert "Capture" not in stand_in.scope.calls


def test_a_snap_takes_only_a_full_tiff_path(client):
    for path in ("a.tif", "/tmp/a.nd2"):
        with pytest.raises(NisRefused, match="tif"):
            client.send_change("snap", "snap", [path])


def test_a_snap_needs_the_limits_like_any_change(client, stand_in, tmp_path):
    with pytest.raises(RuntimeError, match="limits"):
        changes.take_picture(client, str(tmp_path / "a.tif"))
    assert "Capture" not in stand_in.scope.calls
