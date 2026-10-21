"""Hand a message to LAS X and wait until it says "received".

``UpdateAwaitReceipt`` both hands the message over and waits, inside the
call, until LAS X answers "received" or its timeout passes. "Received" only
means LAS X got the message; whether the setting, move or job switch then
happened is the confirmation's business.

Delivery follows the one rule in ``connection/waiting.py``: ``WINDOWS`` tries of
``WINDOW_S`` each. There is no pause between tries, because each try already
waits a full window for the answer.

Acquisition never comes through here: a second acquire is a second
acquisition, so it is sent once with ``UpdateAsync``.

Before a message goes out, a LAS X warning that asks only for OK is answered,
and a message is not sent while another dialog is open
(``lasx_dialogs.DialogOpen``): LAS X's hardware server waits for every open
dialog, and a message sent into it waits too. While the message is out, a
watcher answers an OK-only warning it causes, which a message sent straight to
the hardware server would otherwise wait for without end.
"""

from __future__ import annotations

import logging

from ..interface import lasx_dialogs
from ...connection import waiting

log = logging.getLogger(__name__)


def deliver(api_obj, *, label="message"):
    """Deliver *api_obj*'s message; True once LAS X says "received", else False.

    Raises ``lasx_dialogs.DialogOpen``, with nothing sent, while a LAS X dialog
    the driver does not answer is open.
    """
    lasx_dialogs.answer_warnings()
    with lasx_dialogs.watching():
        for attempt in range(1, waiting.WINDOWS + 1):
            if api_obj.UpdateAwaitReceipt(waiting.WINDOW_S):
                return True
            log.warning("%s not received (try %d/%d)", label, attempt, waiting.WINDOWS)
    return False
