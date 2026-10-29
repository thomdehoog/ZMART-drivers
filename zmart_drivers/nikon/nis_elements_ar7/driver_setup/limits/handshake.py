"""At connect: find this microscope's limits, check them, and hand them to the limits gate.

The gate (``dispatcher/limits.py``) only keeps each connection's limits and
refuses what they do not allow. This file does the part that needs this
microscope's saved settings and NIS's own travel:

1. It reads the travel NIS reports, and where z stands now.
2. It reads the operator's ``limits.json``. When there is none, or it does
   not check out, it uses the one that ships with the driver, cut to NIS's
   travel, with z kept near where it stands now; and it says so loudly.
3. It installs the result for the connection.

When even that fails, the gate is given the reason, and every change is
refused until the limits are fixed. It never raises: a connection always
ends up with limits, or with every change refused.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``driver_setup/limits/handshake.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from ...dispatcher import dispatch
from ...dispatcher import limits as gate
from ...primitives.nis_python import profile
from .. import store
from . import config
from .limit_rules import UNPUBLISHED_Z_WINDOW_UM, NikonLimits

log = logging.getLogger(__name__)


def nis_travel_and_z(client: Any) -> tuple[dict | None, float | None]:
    """NIS's own travel per axis, and where z stands; None for whichever cannot be read."""
    travel = dispatch.run(profile, client, "stage_limits")
    z = dispatch.run(profile, client, "z")
    travel_value = travel.value if travel is not None and travel.error is None else None
    z_value = z.value["z_um"] if z is not None and z.error is None else None
    return travel_value, z_value


def _defaults(
    machine: store.MachineProfile, travel: dict | None, z_now: float | None, reason: str
) -> NikonLimits:
    if z_now is None:
        raise gate.LimitsError(
            "z cannot be read, so the cautious default limits cannot be placed around it"
        )
    path = machine.bundled_default("limits")
    limits = NikonLimits(
        config.load(path),
        travel=travel,
        z_window=[z_now - UNPUBLISHED_Z_WINDOW_UM, z_now + UNPUBLISHED_Z_WINDOW_UM],
        source="defaults",
        path=path,
        is_fallback=True,
    )
    log.warning(
        "limits: %s. This session uses the driver's cautious defaults: x and y within NIS's own travel, and z only "
        "%g um either way from where it stands now (%s um). Publish this microscope's limits with "
        "driver_setup/limits/notebooks/set_limits.ipynb.",
        reason,
        UNPUBLISHED_Z_WINDOW_UM,
        z_now,
    )
    return limits


def connect_handshake(
    client: Any, *, machine: store.MachineProfile | None = None, load: bool = True
) -> gate.GateStatus:
    """Find, check and install the limits for *client*; what was installed. Never raises."""
    machine = machine or store.machine()
    travel, z_now = nis_travel_and_z(client)
    if travel is None:
        log.warning(
            "NIS's own stage travel could not be read; the limits are not cut to it (the bridge still refuses moves beyond it)"
        )
    try:
        saved = machine.saved_path("limits") if load else None
        if saved is None:
            reason = (
                "no limits are published for this microscope"
                if load
                else "loading the limits was switched off"
            )
            limits = _defaults(machine, travel, z_now, reason)
        else:
            try:
                limits = NikonLimits(
                    config.load(saved),
                    travel=travel,
                    z_window=None,
                    source="machine",
                    path=saved,
                    is_fallback=False,
                )
            except Exception as why:  # noqa: BLE001 -- any problem with the file falls back, loudly
                limits = _defaults(
                    machine,
                    travel,
                    z_now,
                    f"the published limits.json could not be used ({why})",
                )
        state = gate.GateState(
            limits=limits,
            stage_cfg=gate.freeze({"stage_um": limits.stage_um()}),
            error=None,
        )
        log.info("limits in force: %s", limits.describe())
    except Exception as why:  # noqa: BLE001 -- last resort: every change is refused, with the reason
        error = f"no limits could be installed ({why}); every change is refused until they can (see driver_setup/limits)"
        log.error("%s", error)
        state = gate.GateState(limits=None, stage_cfg=None, error=error)
    gate.install(client, state)
    if state.limits is not None and state.limits.devices:
        warn_about_unknown_devices(client, state.limits.devices)
    return gate.status(state)


def warn_about_unknown_devices(client: Any, entries: dict) -> None:
    """Say, at connect, when a limits entry names a device NIS does not report: a misspelling would limit nothing.

    ``"lamp_voltage/dia"`` against NIS's ``DIA`` would never match, so the
    lamp would be left unlimited without a word. Never raises.
    """
    from ...procedures import readings

    try:
        present = readings.device_names(readings.devices(client))
    except Exception as why:  # noqa: BLE001 -- only a warning; the limits are already installed
        log.warning(
            "the devices named in the limits could not be checked against NIS: %s", why
        )
        return
    for name in entries:
        if "/" in name and name not in present:
            log.warning(
                "the limits name %r, but NIS reports no such device, so that entry limits nothing; "
                "the devices NIS reports are %s",
                name,
                sorted(n for n in present if n.startswith(name.split("/")[0] + "/")),
            )
