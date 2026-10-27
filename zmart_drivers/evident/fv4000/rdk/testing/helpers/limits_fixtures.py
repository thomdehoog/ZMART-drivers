"""Limits fixtures for the offline suite: a published limits file in a throwaway root, or permissive limits in memory.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers.evident.fv4000.rdk.dispatcher import limits as _gate
from zmart_drivers.evident.fv4000.rdk.driver_setup.limits import (
    config as _limits_config,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.limits import limit_rules
from zmart_drivers.evident.fv4000.rdk.driver_setup.store import MachineProfile

#: A measured-looking travel around the mock's home position (x 10 mm, y 8 mm, z 2.5 mm), in micrometres.
DEFAULT_STAGE_UM = {
    "x": [0.0, 60000.0],
    "y": [0.0, 40000.0],
    "z": [0.0, 8000.0],
    "z_piezo": [-100.0, 100.0],
}
_SEED_MOMENT = datetime(2026, 1, 1, tzinfo=UTC)


def merged_limits_payload(stage_um: dict, *, functions: dict | None = None) -> dict:
    payload = _limits_config.build_limits_payload(stage_um)
    if functions is not None:
        payload.update(functions)
    return payload


def provision_machine_limits(
    root: str | Path,
    *,
    stage_um: dict | None = None,
    function_limits: dict | None = None,
    moment: datetime | None = None,
) -> MachineProfile:
    """Publish one ``limits/<datetime>/limits.json`` under *root*, and answer the profile that reads it."""
    profile = MachineProfile(programdata_root=Path(root))
    profile.publish_snapshot(
        moment or _SEED_MOMENT,
        limits=merged_limits_payload(
            dict(stage_um or DEFAULT_STAGE_UM), functions=function_limits
        ),
    )
    return profile


def permissive_limits(**constraints) -> limit_rules.EvidentLimits:
    """Limits in memory that let everything through, with *constraints* (``set_laser_intensity={"range": [0, 10]}``) on top."""
    payload = _limits_config.build_limits_payload(
        {"x": [-1e5, 1e5], "y": [-1e5, 1e5], "z": [-1e4, 1e4], "z_piezo": [-1e3, 1e3]}
    )
    payload.update(constraints)
    return limit_rules.EvidentLimits(
        payload, source="test", path=None, is_fallback=False
    )


def install_permissive_limits(client, **constraints):
    """Install permissive limits for *client* and apply a wide travel, so a test about mechanics passes the gate."""
    _gate.install(
        client,
        _gate.GateState(
            limits=permissive_limits(**constraints), stage_cfg=None, error=None
        ),
    )
    limit_rules.set_stage_limits(
        x=(-1e5, 1e5), y=(-1e5, 1e5), z=(-1e4, 1e4), z_piezo=(-1e3, 1e3)
    )
    return client
