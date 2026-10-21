"""Clearing LAS X's cache: only pictures whose capture was saved, and only by their name."""

from __future__ import annotations

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.clear_cache import clear as clearing
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.clear_cache import release as release
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.session import ZmartHandle


class TestPreparing:
    """Before pictures are released, the canvas is shown, so that the release can clear it."""

    def test_the_canvas_is_shown(self, monkeypatch):
        shown = []
        monkeypatch.setattr(
            release.lasx_navigator, "show_canvas", lambda: shown.append(1) or True
        )
        release.prepare()
        assert shown == [1]

    def test_a_canvas_that_cannot_be_shown_is_logged_and_the_release_goes_ahead(
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


def _session(*in_cache):
    return ZmartHandle(client=object(), connection={}, hash6="abc123", in_cache=list(in_cache))


class TestClearing:
    """The procedure lets go of what was saved, one by one, and leaves behind what it could not."""

    @pytest.fixture(autouse=True)
    def canvas(self, monkeypatch):
        self.shown = []
        monkeypatch.setattr(release, "prepare", lambda: self.shown.append(1))
        monkeypatch.setattr(release.lasx_navigator, "clear_canvas", lambda: True)

    def test_every_saved_picture_is_let_go_of_and_the_list_empties(self, monkeypatch):
        elements = {1: FakeElement("HiRes001"), 2: FakeElement("HiRes002")}
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory(elements))
        handle = _session((1, "HiRes001"), (2, "HiRes002"))

        answer = clearing.clear(handle)

        assert answer == {"cleared": ["HiRes001", "HiRes002"], "failed": {}}
        assert handle.in_cache == []
        assert all(e.deleted for e in elements.values())
        assert self.shown == [1]

    def test_a_picture_that_cannot_be_let_go_of_stays_listed_and_the_rest_are_cleared(self, monkeypatch):
        elements = {1: FakeElement("Renamed"), 2: FakeElement("HiRes002")}
        monkeypatch.setattr(release, "connected_data_container", lambda: FakeFactory(elements))
        handle = _session((1, "HiRes001"), (2, "HiRes002"))

        answer = clearing.clear(handle)

        assert answer["cleared"] == ["HiRes002"]
        assert list(answer["failed"]) == ["HiRes001"]
        assert handle.in_cache == [(1, "HiRes001")]
        assert not elements[1].deleted

    def test_nothing_saved_means_nothing_touched(self):
        handle = _session()
        assert clearing.clear(handle) == {"cleared": [], "failed": {}}
        assert self.shown == []


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
