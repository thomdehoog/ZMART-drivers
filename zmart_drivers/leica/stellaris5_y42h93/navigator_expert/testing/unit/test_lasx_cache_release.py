"""Letting go of a picture in LAS X's cache: only the picture read, and only by its name."""

from __future__ import annotations

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.output import lasx_cache_release as release


class TestDeleting:
    def test_the_element_is_deleted_when_it_is_still_the_picture(self, monkeypatch):
        element = FakeElement("HiRes002")
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory({746: element}))

        release.delete(746, "HiRes002")

        assert element.deleted

    def test_an_element_that_is_no_longer_the_picture_is_not_deleted(self, monkeypatch):
        element = FakeElement("HiRes003")
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory({746: element}))

        with pytest.raises(RuntimeError, match="is 'HiRes003', not 'HiRes002'"):
            release.delete(746, "HiRes002")
        assert not element.deleted


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
