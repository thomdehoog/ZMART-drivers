"""A microscope set up as an operator would set it up, for the tests: limits, orientation, calibration, data folder.

``provision`` publishes, into the test's own settings folder, the same files
the setup notebooks publish: a limits file around the stand-in's stage, a
measured orientation with its frame, and a calibration of the stand-in's
objectives. ``connected`` then opens a ``Microscope`` on the stand-in ZEN.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mock_zen_api import FakeScope, build_fake_client

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher import limits as gate
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher.limits import (
    LimitsStatus,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.limits import (
    config as limits_config,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.store import MACHINE

MOMENT = datetime(2026, 1, 1, tzinfo=UTC)

#: The stand-in's stage travel, in ZEN's coordinates: wide around where it starts.
STAGE_UM = {"x": [0.0, 100000.0], "y": [0.0, 75000.0], "z": [0.0, 10000.0]}

#: The stand-in's objectives, measured against the 10x.
TRANSLATIONS = {"1": [0.0, 0.0, 0.0], "2": [4.0, -2.0, 3.0], "3": [10.0, 6.0, -5.0]}


def provision(
    *, limits: dict | None = None, orientation: bool = True, calibration: bool = True
) -> None:
    """Publish limits, a measured orientation and a calibration, as the notebooks would."""
    payload = (
        limits if limits is not None else limits_config.build_limits_payload(STAGE_UM)
    )
    MACHINE.publish_snapshot(MOMENT, limits=payload)
    if orientation:
        MACHINE.publish_snapshot(
            MOMENT,
            orientation={
                "schema_version": 1,
                "measured": True,
                "rotation_deg": 0,
                "reflection": False,
                "frame": {
                    "image_size_px": [128, 96],
                    "pixel_size_um": [0.65, 0.65],
                    "magnification": 10.0,
                    "objective": {"position": 1, "name": "Plan-Apochromat 10x/0.45"},
                },
            },
        )
    if calibration:
        MACHINE.publish_snapshot(
            MOMENT,
            calibration={
                "schema_version": 1,
                "objectives": {
                    key: {"name": f"position {key}", "translation_um": value}
                    for key, value in TRANSLATIONS.items()
                },
            },
        )


def connected(
    monkeypatch, tmp_path: Path, scope: FakeScope | None = None, **connection
):
    """A ``Microscope`` on the stand-in ZEN, saving under *tmp_path*; ``(microscope, scope)``."""
    scope = scope if scope is not None else FakeScope()
    scope.image_output_folder = scope.image_output_folder or str(
        tmp_path / "zen_images"
    )
    Path(scope.image_output_folder).mkdir(parents=True, exist_ok=True)
    made = {}

    def stand_in(_connection):
        client, _ = build_fake_client(scope)
        made["client"] = client
        return client

    monkeypatch.setattr(methods, "open_connection", stand_in)
    defaults = {
        "password": "pretend",
        "output_root": str(tmp_path / "runs"),
        "experiment": "ZMART_Snap",
    }
    microscope = methods.connect(**{**defaults, **connection})
    return microscope, scope


class PermissiveLimits:
    """Limits that allow everything, for tests about how a change is sent, not whether it may be."""

    def check_command(self, command, values):
        return None

    def status(self):
        return LimitsStatus(source="test", path=None, is_fallback=False)

    def describe(self):
        return self.status().describe()


def allow_everything(client) -> None:
    """Install permissive limits for *client*."""
    gate.install(client, gate.GateState(limits=PermissiveLimits()))
