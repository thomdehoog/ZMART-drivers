"""Hand a message to LAS X and wait until it says "received".

``UpdateAwaitReceipt`` both hands the message over and waits, inside the
call, until LAS X answers "received" or its timeout passes. "Received" only
means LAS X got the message; whether the setting, move or job switch then
happened is the confirmation's business.

Delivery follows the one rule in ``dispatcher.tuning``: ``WINDOWS`` tries of
``WINDOW_S`` each. There is no pause between tries, because each try already
waits a full window for the answer.

Acquisition never comes through here: a second acquire is a second
acquisition, so it is sent once with ``UpdateAsync``.
"""

from __future__ import annotations

import logging

from ..dispatcher import tuning

log = logging.getLogger(__name__)


def deliver(api_obj, *, label="message"):
    """Deliver *api_obj*'s message; True once LAS X says "received", else False."""
    for attempt in range(1, tuning.WINDOWS + 1):
        if api_obj.UpdateAwaitReceipt(tuning.WINDOW_S):
            return True
        log.warning("%s not received (try %d/%d)", label, attempt, tuning.WINDOWS)
    return False
