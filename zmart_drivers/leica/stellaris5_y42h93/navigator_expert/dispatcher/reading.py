"""One answer to a question about the microscope, with where it came from.

Every reading the driver takes comes back as a :class:`Reading`: the value, the
source that gave it (the CAM API, the LAS X log or the direct reader), when it
was seen and how old it was. Callers that only want the value ask for it
plainly; callers that want to know how far to trust it ask for the whole
reading.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """A source-tagged state reading used by routed callers."""

    value: object
    source: str
    observed_at: float | None
    age_s: float | None
    error: Exception | None = None

    def _replace_value_none(self):
        """This reading with the value stripped (kept: source/timing/error)."""
        if self.value is None:
            return self
        return Reading(
            value=None,
            source=self.source,
            observed_at=self.observed_at,
            age_s=self.age_s,
            error=self.error,
        )


def plain_or_diagnostic(reading, diagnostics):
    """The whole reading when *diagnostics* is asked for, otherwise just its value."""
    if diagnostics:
        return reading
    return None if reading is None else reading.value


def derive(reading, value):
    """A reading with a new value, keeping the source, timing and error of *reading*."""
    if reading is None:
        return None
    return Reading(
        value=value,
        source=reading.source,
        observed_at=reading.observed_at,
        age_s=reading.age_s,
        error=reading.error,
    )
