"""An acquisition is sent once and never again.

Sending an acquisition a second time would start a second scan and save two
data sets, so its profile says ``never_resend`` and the dispatcher holds it to
one send for good: no caller, and no change to the other timing settings, can
switch the resend back on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import replace

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.send_loop import (
    resolve_profile as _resolve_profile,
)

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.sending.command_profile import CommandProfile
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import acquire as acquire_change
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.operations.changes import select_job as select_job_change



def _is_sent_once(profile):
    return (
        profile.max_retries == 0
        and profile.max_confirm_attempts == 1
        and not profile.refire_on_unconfirmed
    )


def test_the_acquisition_is_sent_once():
    assert acquire_change.ACQUIRE.never_resend
    assert _is_sent_once(_resolve_profile(acquire_change.ACQUIRE))


def test_asking_for_retries_does_not_make_the_acquisition_resend():
    assert _is_sent_once(_resolve_profile(acquire_change.ACQUIRE, max_retries=5))
    assert _is_sent_once(
        _resolve_profile(
            replace(acquire_change.ACQUIRE, max_confirm_attempts=4, refire_on_unconfirmed=True)
        )
    )


def test_other_commands_still_send_again():
    assert not CommandProfile().never_resend
    assert select_job_change.SELECT_JOB.max_retries > 0
