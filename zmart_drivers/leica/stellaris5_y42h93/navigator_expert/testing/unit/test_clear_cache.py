"""Letting go of a picture in LAS X's cache: only the picture read, and only by its name."""

from __future__ import annotations

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.saving import (
    clear_cache as release,
)


class TestPreparing:
    """Before a capture that will be released, the canvas is shown, so that the release can clear it."""

    def test_the_canvas_is_shown(self, monkeypatch):
        shown = []
        monkeypatch.setattr(
            release.lasx_navigator, "show_canvas", lambda: shown.append(1) or True
        )
        release.prepare()
        assert shown == [1]

    def test_a_canvas_that_cannot_be_shown_is_logged_and_the_capture_goes_ahead(
        self, monkeypatch, caplog
    ):
        def unavailable():
            raise release.lasx_navigator.NavigatorUnavailable(
                "Navigator Expert's Experiment button was not found"
            )

        monkeypatch.setattr(release.lasx_navigator, "show_canvas", unavailable)
        release.prepare()
        assert "Experiment button was not found" in caplog.text


class TestDeleting:
    @pytest.fixture(autouse=True)
    def canvas(self, monkeypatch):
        cleared = []
        monkeypatch.setattr(
            release.lasx_navigator, "clear_canvas", lambda: cleared.append(1) or True
        )
        return cleared

    def test_the_element_is_deleted_when_it_is_still_the_picture(
        self, monkeypatch, canvas
    ):
        element = FakeElement("HiRes002")
        monkeypatch.setattr(
            release, "connected_data_container", lambda: FakeFactory({746: element})
        )

        release.delete(746, "HiRes002")

        assert element.deleted

    def test_the_navigator_canvas_is_cleared_too_so_its_hidden_copy_goes(
        self, monkeypatch, canvas
    ):
        monkeypatch.setattr(
            release,
            "connected_data_container",
            lambda: FakeFactory({746: FakeElement("HiRes002")}),
        )
        release.delete(746, "HiRes002")
        assert canvas == [1]

    def test_a_canvas_that_cannot_be_cleared_is_logged_and_the_picture_still_released(
        self, monkeypatch, caplog
    ):
        element = FakeElement("HiRes002")
        monkeypatch.setattr(
            release, "connected_data_container", lambda: FakeFactory({746: element})
        )

        def unavailable():
            raise release.lasx_navigator.NavigatorUnavailable(
                "the Navigator is not shown"
            )

        monkeypatch.setattr(release.lasx_navigator, "clear_canvas", unavailable)
        release.delete(746, "HiRes002")
        assert element.deleted
        assert "the Navigator is not shown" in caplog.text

    def test_an_element_that_is_no_longer_the_picture_is_not_deleted(
        self, monkeypatch, canvas
    ):
        element = FakeElement("HiRes003")
        monkeypatch.setattr(
            release, "connected_data_container", lambda: FakeFactory({746: element})
        )

        with pytest.raises(RuntimeError, match="is 'HiRes003', not 'HiRes002'"):
            release.delete(746, "HiRes002")
        assert not element.deleted
        assert canvas == []


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
