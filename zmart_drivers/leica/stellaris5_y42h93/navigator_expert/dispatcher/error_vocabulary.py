"""The kinds of problem a microscope can run into, in words every driver shares.

Each microscope reports trouble in its own way: LAS X writes a message into an
echo, ZEN raises an exception, mesoSPIM answers with a status. So that the
rest of the driver does not have to understand each of these, every driver
sorts what its vendor reported into one of the kinds below. What to do about
each kind is decided once, for every microscope, in ``error_rules.py``.

The first four kinds are how the vendor software can fail. The others are not
errors the vendor raises; they are outcomes the driver reaches on its own.
They are listed here so that every case has one name.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from enum import Enum


class Kind(str, Enum):
    """One kind of problem."""

    #: The request itself is wrong: an unknown option, a value the vendor calls invalid.
    BAD_REQUEST = "bad_request"
    #: The software is busy for now: a short timeout, a scan in progress. Trying again can work.
    TEMPORARY = "temporary"
    #: The vendor reports a failure that trying again will not fix, such as a hardware fault.
    PERMANENT = "permanent"
    #: The vendor software was closed or can no longer be reached.
    CONNECTION_LOST = "connection_lost"
    #: A request was sent but its reply never came, so it may or may not have happened.
    OUTCOME_UNKNOWN = "outcome_unknown"
    #: The target lies outside the allowed range, so nothing was sent.
    REFUSED_BY_LIMITS = "refused_by_limits"
    #: A reading could not be had (a stale log entry, a read that timed out).
    UNKNOWN_READING = "unknown_reading"
    #: The command was accepted, but the readback never matched.
    UNCONFIRMED = "unconfirmed"
    #: The user asked to stop.
    STOPPED_BY_USER = "stopped_by_user"
