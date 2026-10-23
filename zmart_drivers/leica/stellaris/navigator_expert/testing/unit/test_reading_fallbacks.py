"""Which source answers when the first one cannot: it is set in the reading profile, in one place.

A reading asks its first source (the direct reader) and, when that cannot
answer, a fallback takes its place. These tests check that the fallbacks are
the profile's, so that changing the profile changes what the driver does, and
that no code picks a fallback of its own.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes

from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as read
from zmart_drivers.leica.stellaris.navigator_expert.procedures import readings as readers
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as reading_settings


@pytest.fixture
def use_profile(monkeypatch):
    """Install a reading profile with the given settings for one test."""

    def install(**settings):
        monkeypatch.setattr(
            reading_settings, "STATE_READERS", reading_settings.StateReaderProfile(**settings)
        )

    return install


def test_the_shipped_fallbacks():
    shipped = reading_settings.StateReaderProfile()
    assert shipped.reading_fallback == "api"
    assert shipped.waiting_fallback == "api"
    assert shipped.selected_job_confirm_fallback == "hybrid"


@pytest.mark.parametrize(
    "field", ["reading_fallback", "waiting_fallback", "selected_job_confirm_fallback"]
)
def test_a_fallback_that_names_no_family_is_refused(field):
    with pytest.raises(ValueError, match=field):
        reading_settings.StateReaderProfile(**{field: "direct"})


def test_a_plain_reading_falls_back_to_the_profiles_family(use_profile, monkeypatch):
    use_profile(reading_fallback="log")
    asked = {}
    monkeypatch.setattr(
        readers, "wait_for", lambda datum, client, **kw: asked.update(kw)
    )

    readers.get_xy(object())

    assert asked["fallback"] == "log"


def test_a_caller_can_still_name_its_own_fallback(use_profile, monkeypatch):
    use_profile(reading_fallback="log")
    asked = {}
    monkeypatch.setattr(
        readers, "wait_for", lambda datum, client, **kw: asked.update(kw)
    )

    readers.get_xy(object(), fallback="api")

    assert asked["fallback"] == "api"


def test_the_idle_check_falls_back_to_the_waiting_family(use_profile):
    use_profile(waiting_fallback="hybrid")

    assert writes.idle().fallback == "hybrid"


def test_the_first_stage_names_the_profiles_family_when_none_is_given(use_profile):
    use_profile(reading_fallback="log")
    spec = read.spec("xy")

    stage, rest = read.first_stage(spec, "direct", None)

    assert stage is not None
    assert rest == "log"


def test_job_confirmation_falls_back_as_the_profile_says(use_profile):
    use_profile(
        selected_job_mode="direct",
        selected_job_confirm_fallback="api",
    )

    confirm = writes.select_job("Overview").confirm

    # The direct reader first (the reading's own order), the api alone in its place.
    assert (confirm.reading, confirm.mode, confirm.fallback) == ("selected_job", None, "api")
    assert read.mode_for("selected_job") == "direct"
