"""Which source answers when the first one cannot: it is set in the reading profile, in one place.

A reading asks its first source (the direct reader) and, when that cannot
answer, a fallback takes its place. These tests check that the fallbacks are
the profile's, so that changing the profile changes what the driver does, and
that no code picks a fallback of its own.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.confirmations import confirm_select_job
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import prechecks, profiles
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings import sources
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings import state as readers


@pytest.fixture
def use_profile(monkeypatch):
    """Install a reading profile with the given settings for one test."""

    def install(**settings):
        monkeypatch.setattr(profiles, "STATE_READERS", profiles.StateReaderProfile(**settings))

    return install


def test_the_shipped_fallbacks():
    shipped = profiles.StateReaderProfile()
    assert shipped.reading_fallback == "api"
    assert shipped.waiting_fallback == "api"
    assert shipped.selected_job_confirm_fallback == "hybrid"


@pytest.mark.parametrize("field", ["reading_fallback", "waiting_fallback", "selected_job_confirm_fallback"])
def test_a_fallback_that_names_no_family_is_refused(field):
    with pytest.raises(ValueError, match=field):
        profiles.StateReaderProfile(**{field: "direct"})


def test_a_plain_reading_falls_back_to_the_profiles_family(use_profile, monkeypatch):
    use_profile(reading_fallback="log")
    asked = {}
    monkeypatch.setattr(readers, "wait_for", lambda datum, client, **kw: asked.update(kw))

    readers.get_xy(object())

    assert asked["fallback"] == "log"


def test_a_caller_can_still_name_its_own_fallback(use_profile, monkeypatch):
    use_profile(reading_fallback="log")
    asked = {}
    monkeypatch.setattr(readers, "wait_for", lambda datum, client, **kw: asked.update(kw))

    readers.get_xy(object(), fallback="api")

    assert asked["fallback"] == "api"


def test_the_idle_check_falls_back_to_the_waiting_family(use_profile, monkeypatch):
    use_profile(waiting_fallback="hybrid")
    asked = {}

    def fake_scan_status(client, **kw):
        asked.update(kw)

    monkeypatch.setattr(prechecks._readers, "get_scan_status", fake_scan_status)

    prechecks.check_idle(object())

    assert asked["fallback"] == "hybrid"


def test_the_first_stage_names_the_profiles_family_when_none_is_given(use_profile):
    use_profile(reading_fallback="log")
    spec = sources.spec("xy")

    stage, rest = sources.first_stage(spec, "direct", None)

    assert stage is not None
    assert rest == "log"


def test_job_confirmation_falls_back_as_the_profile_says(use_profile):
    use_profile(selected_job_mode="direct", selected_job_confirm_source="direct", selected_job_confirm_fallback="api")

    api_confirm, log_leg, budget_s = confirm_select_job.select_job_confirm_legs(
        "Overview", command_started_at=0.0
    )

    assert api_confirm is not None
    assert log_leg is None  # the api alone takes the direct reader's place, with no log race
    assert budget_s is None
