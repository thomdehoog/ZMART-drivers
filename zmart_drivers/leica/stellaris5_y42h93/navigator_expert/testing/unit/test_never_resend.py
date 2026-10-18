"""An acquisition is sent once and never again.

Sending an acquisition a second time would start a second scan and save two
data sets, so the profile forbids it for good: no caller, and no change to the
other timing settings, can switch the resend back on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import replace

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.send_loop import (
    resolve_profile as _resolve_profile,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.primitive_handling.profiles import (
    ACQUIRE,
    COMMAND_DEFAULT,
    SELECT_JOB,
)


def _is_sent_once(profile):
    return (
        profile.max_retries == 0
        and profile.max_confirm_attempts == 1
        and not profile.refire_on_unconfirmed
    )


def test_the_acquisition_is_sent_once():
    assert ACQUIRE.never_resend
    assert _is_sent_once(ACQUIRE)


def test_asking_for_retries_does_not_make_the_acquisition_resend():
    asked = _resolve_profile(ACQUIRE, max_retries=5)
    assert _is_sent_once(asked)
    assert _is_sent_once(
        replace(ACQUIRE, max_confirm_attempts=4, refire_on_unconfirmed=True)
    )


def test_other_commands_still_send_again():
    assert not COMMAND_DEFAULT.never_resend
    assert SELECT_JOB.max_retries > 0
