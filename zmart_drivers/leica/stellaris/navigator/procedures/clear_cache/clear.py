r"""Clearing LAS X's cache: the ``clear_cache`` procedure, and letting go of one saved picture.

Every picture LAS X takes stays in its Data Container cache (the folder the
registry names; ``D:\Temp2`` on the STELLARIS Y42H93) until LAS X is told to
let go, so a long run slowly fills the disk. Letting go is a deliberate step,
taken when the operator or the workflow decides it, and only for pictures
whose capture has been saved (``save_data`` records those). Acquiring and
saving never do it.

The cache cannot be emptied from outside: LAS X holds its files open, and
LAS X's project still lists the picture, so deleting them would leave the
project pointing at data that is gone. LAS X can let go of a picture itself,
as when it is deleted in LAS X's window: its files leave the cache, and the
project no longer lists it. :func:`delete` asks for that delete through the
Data Container's .NET client from the LAS X install (``data_container.py``).
Nothing of LAS X is copied or changed. The picture's name is checked before
deleting, so that nothing else is ever deleted.

The plain Navigator's canvas has no Clear button, unlike Navigator Expert's,
so this driver does not clear a canvas; the picture is let go of through
the Data Container alone (STELLARIS, 2026-10-15: a capture and a tile scan
let go of this way).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging

from .data_container import connected_data_container

log = logging.getLogger(__name__)


def clear(handle) -> dict:
    """Have LAS X let go of the pictures of the captures saved so far; say what happened.

    Each picture is checked by name just before it is let go of, so nothing
    else is ever deleted. A picture that cannot be let go of stays on the
    list and is reported, so a later call can try again; the others are
    still cleared. Returns ``{"cleared": [names], "failed": {name: reason}}``.
    """
    handle.require_open()
    cleared: list[str] = []
    failed: dict[str, str] = {}
    for number, name in list(handle.in_cache):
        try:
            delete(number, name)
        except Exception as why:  # noqa: BLE001 -- one stubborn picture must not stop the rest
            log.warning("could not clear %s from LAS X's cache: %s", name, why)
            failed[name] = str(why)
        else:
            handle.in_cache.remove((number, name))
            cleared.append(name)
    return {"cleared": cleared, "failed": failed}


def delete(number: int, name: str) -> None:
    """Have LAS X delete the picture *name*, its Data Container element *number*.

    It is the delete in LAS X's own window. Refused, with nothing deleted,
    when the element is no longer that picture.
    """
    element = connected_data_container().GetElement(number)
    if element.GetName() != name:
        raise RuntimeError(f"LAS X's element {number} is {element.GetName()!r}, not {name!r}; nothing deleted")
    element.Delete()
