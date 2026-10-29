"""Clearing LAS X's cache: only pictures whose capture was saved, and only by their name.

The plain Navigator has no canvas to clear, so letting go of a picture is one
step: LAS X's Data Container deletes the element, after its name is checked.
A stand-in Data Container is used here; LAS X's own is reached only on the
microscope computer.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest

from zmart_drivers.leica.stellaris.navigator.methods import Session
from zmart_drivers.leica.stellaris.navigator.procedures.clear_cache import (
    clear as release,
)


class FakeElement:
    """A Data Container element: its name, and whether it was deleted."""

    def __init__(self, name):
        self.name, self.deleted = name, False

    def GetName(self):
        return self.name

    def Delete(self):
        self.deleted = True


class FakeFactory:
    def __init__(self, elements):
        self.elements = elements

    def GetElement(self, number):
        return self.elements[number]


class TestDeleting:
    def test_the_element_is_deleted_when_it_is_still_the_picture(self, monkeypatch):
        element = FakeElement("Image002")
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory({746: element}))

        release.delete(746, "Image002")

        assert element.deleted

    def test_an_element_that_is_no_longer_the_picture_is_not_deleted(self, monkeypatch):
        element = FakeElement("Image003")
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory({746: element}))

        with pytest.raises(RuntimeError, match="is 'Image003', not 'Image002'"):
            release.delete(746, "Image002")
        assert not element.deleted

    def test_no_canvas_is_touched(self):
        """The plain Navigator has no Clear button; nothing of a canvas is in the procedure."""
        assert not hasattr(release, "navigator")
        assert not hasattr(release, "prepare")


def _session(*in_cache):
    return Session(client=object(), connection={}, hash6="abc123", in_cache=list(in_cache))


class TestClearing:
    """The procedure lets go of what was saved, one by one, and leaves behind what it could not."""

    def test_every_saved_picture_is_let_go_of_and_the_list_empties(self, monkeypatch):
        elements = {1: FakeElement("Image001"), 2: FakeElement("Mosaic001")}
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory(elements))
        handle = _session((1, "Image001"), (2, "Mosaic001"))

        answer = release.clear(handle)

        assert answer == {"cleared": ["Image001", "Mosaic001"], "failed": {}}
        assert handle.in_cache == []
        assert all(e.deleted for e in elements.values())

    def test_a_picture_that_cannot_be_let_go_of_stays_listed_and_the_rest_are_cleared(self, monkeypatch):
        elements = {1: FakeElement("Renamed"), 2: FakeElement("Image002")}
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory(elements))
        handle = _session((1, "Image001"), (2, "Image002"))

        answer = release.clear(handle)

        assert answer["cleared"] == ["Image002"]
        assert list(answer["failed"]) == ["Image001"]
        assert handle.in_cache == [(1, "Image001")]
        assert not elements[1].deleted

    def test_nothing_saved_means_nothing_touched(self, monkeypatch):
        def never():
            raise AssertionError("the Data Container is not asked for when nothing is listed")

        monkeypatch.setattr(release, "connected_data_container", never)
        assert release.clear(_session()) == {"cleared": [], "failed": {}}

    def test_a_closed_session_refuses(self):
        handle = _session((1, "Image001"))
        handle.closed = True
        with pytest.raises(RuntimeError, match="disconnected"):
            release.clear(handle)
