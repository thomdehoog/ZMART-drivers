"""Clearing LAS X's cache: the ``clear_cache`` procedure.

Every picture LAS X takes stays in its cache until it is told to let go, so a
long run slowly fills the disk. Letting go is a deliberate step, taken when the
operator or the workflow decides it, and only for pictures whose capture has
been saved (``save_data`` records those). Acquiring and saving never do it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from ...session.handle import Session, require_open
from . import release

log = logging.getLogger(__name__)


def clear(handle: Session) -> dict:
    """Have LAS X let go of the pictures of the captures saved so far; say what happened.

    Each picture is checked by name just before it is let go of, so nothing
    else is ever deleted. A picture that cannot be let go of stays on the list
    and is reported, so a later call can try again; the others are still
    cleared. Returns ``{"cleared": [names], "failed": {name: reason}}``.
    """
    require_open(handle)
    cleared: list[str] = []
    failed: dict[str, str] = {}
    if not handle.in_cache:
        return {"cleared": cleared, "failed": failed}

    # The canvas must be showing for LAS X to clear its hidden copies.
    release.prepare()
    for number, name in list(handle.in_cache):
        try:
            release.delete(number, name)
        except Exception as why:  # noqa: BLE001 -- one stubborn picture must not stop the rest
            log.warning("could not clear %s from LAS X's cache: %s", name, why)
            failed[name] = str(why)
        else:
            handle.in_cache.remove((number, name))
            cleared.append(name)
    return {"cleared": cleared, "failed": failed}
