"""Limits fixtures for the offline suite.

Runtime machine limits resolve through the configuration folder only after
publication; bundled defaults remain package-local. Tests call
``provision_machine_limits`` when they need a machine-published envelope, or
``install_permissive_limits`` to let a stand-in client through the gate
without touching disk.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as _gate
from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import (
    config as _limits_config,
)
from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules
from zmart_drivers.leica.stellaris.navigator.driver_setup.store import MachineProfile

# The bundled conservative default envelope.
DEFAULT_STAGE_UM = {
    "x": [1000.0, 130000.0],
    "y": [1000.0, 100000.0],
    "z_galvo": [-250.0, 250.0],
    "z_wide": [0.0, 8000.0],
}

_SEED_MOMENT = datetime(2026, 1, 1, tzinfo=UTC)


def merged_limits_payload(stage_um: dict, *, functions: dict | None = None) -> dict:
    """The single flat limits.json payload."""
    payload = _limits_config.build_limits_payload(stage_um)
    if functions is not None:
        payload.update(functions)
    return payload


def provision_machine_limits(
    root: str | Path, *, stage_um: dict | None = None, function_limits: dict | None = None, moment=None
) -> MachineProfile:
    """Publish a machine-local snapshot carrying the single merged limits.json under *root*."""
    profile = MachineProfile(programdata_root=Path(root))
    profile.publish_snapshot(
        moment or _SEED_MOMENT, limits=merged_limits_payload(dict(stage_um or DEFAULT_STAGE_UM), functions=function_limits)
    )
    return profile


def hermetic_mock_machine_root() -> Path:
    """A throwaway configuration folder with published limits and a two-objective calibration, made active."""
    root = Path(tempfile.mkdtemp(prefix="zmart_microscopy_mock_root_"))
    os.environ["ZMART_MICROSCOPY_ROOT"] = str(root)
    profile = provision_machine_limits(root)
    profile.publish_snapshot(
        _SEED_MOMENT,
        calibration={
            "schema_version": 13,
            "objectives": {
                "0": {"name": "HC PL APO 10x/0.40 DRY", "translation_um": [0.0, 0.0, 0.0]},
                "1": {"name": "HC PL APO 20x/0.70 UV", "translation_um": [10.0, -6.0, -3.0]},
            },
        },
    )
    return root


def permissive_function_limits(**set_xyz_constraints) -> object:
    """An in-memory policy for the tests of how a change travels; not about limits."""
    payload = _limits_config.build_limits_payload(
        {"x": [-1e12, 1e12], "y": [-1e12, 1e12], "z_galvo": [-1e12, 1e12], "z_wide": [-1e12, 1e12]}
    )
    for param, bounds in set_xyz_constraints.items():
        payload[param] = {"range": [bounds["min"], bounds["max"]]}
    return limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False)


def install_permissive_limits(client, *, wide_stage=False, **set_xyz_constraints):
    """Install a permissive gate state for *client*; with ``wide_stage`` the stage envelope is opened wide too."""
    _gate.install(
        client,
        _gate.GateState(limits=permissive_function_limits(**set_xyz_constraints), stage_cfg=None, error=None),
    )
    if wide_stage:
        limit_rules.set_stage_limits(
            x_min=0.0, x_max=1_000_000.0, y_min=0.0, y_max=1_000_000.0,
            z_galvo_min=-250.0, z_galvo_max=250.0, z_wide_min=-100_000.0, z_wide_max=100_000.0,
        )
    return client
