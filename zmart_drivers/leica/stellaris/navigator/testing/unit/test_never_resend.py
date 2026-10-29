"""A capture and a tile scan are sent once and never again.

Sending one a second time would start a second scan and save two
data sets, so its write says ``never_resend`` and the dispatcher holds it to
one send for good: no caller, and no change to the other timing settings, can
switch the resend back on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import replace

from zmart_drivers.leica.stellaris.navigator.dispatcher.write import (
    resolve_profile as _resolve_profile,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    profile as direct_profile,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import writes


def _is_sent_once(profile):
    return (
        profile.max_retries == 0
        and profile.max_confirm_attempts == 1
        and not profile.refire_on_unconfirmed
    )


def test_the_capture_and_the_tile_scan_are_sent_once():
    assert writes.capture(2518).never_resend
    assert writes.tile_scan([(63500.0, 41500.0, 0.0)], 2518).never_resend
    assert _is_sent_once(_resolve_profile(direct_profile.WRITING, never_resend=writes.capture(2518).never_resend))


def test_asking_for_retries_does_not_make_the_acquisition_resend():
    assert _is_sent_once(_resolve_profile(direct_profile.WRITING, never_resend=True, max_retries=5))
    assert _is_sent_once(
        _resolve_profile(
            replace(direct_profile.WRITING, max_confirm_attempts=4, refire_on_unconfirmed=True),
            never_resend=True,
        )
    )


def test_other_commands_still_send_again():
    assert not direct_profile.WRITING.never_resend
    assert not writes.setting("zoom", 2.0).never_resend
    assert not writes.move_xy(63500.0, 41500.0).never_resend
    assert direct_profile.WRITING.max_retries > 0
