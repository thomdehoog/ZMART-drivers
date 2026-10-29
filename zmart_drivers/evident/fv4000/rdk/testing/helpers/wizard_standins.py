"""The microscope as the setup wizard's tests see it: the pretend FLUOVIEW, and what the operator does to it.

The Leica driver's wizard tests need three stand-ins (an offline LAS X, pictures
that shift with the stage, and a physical simulator). Here the pretend FLUOVIEW
is all three: its stage moves the field of view, its scanner can be mounted
turned or mirrored, and its objectives look and focus at offsets a test sets.
So every step of the wizard runs the driver's real code against it, through
the driver's own ``connect()``.

What the operator does at the microscope is a plain function here: drive the
stage, turn the nosepiece, set up or switch off a z-stack, match the zoom.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from mock_fluoview import MockFluoview, MockRdkClient

from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client

#: How far each objective looks and focuses from slot 0, in micrometres (x, y, z): what the calibration must find.
TRUE_OFFSETS = {1: (-1.5, 2.5, -0.5), 2: (3.0, -2.0, 1.0)}


def install_fluoview(mp, mock: MockFluoview | None = None) -> MockFluoview:
    """A pretend FLUOVIEW answering every connection the driver makes; the settings go to the test's own folder."""
    mock = mock or MockFluoview(scan_s=0.0, travel_s=0.0)
    mp.setattr(
        rdk_client,
        "connect_client",
        lambda host="127.0.0.1", port=8080, **_: MockRdkClient(mock),
    )
    return mock


def drive_stage_to(
    mock: MockFluoview, x_um: float, y_um: float, z_um: float | None = None
) -> None:
    """What the operator does with the joystick: the stage now stands here, in FLUOVIEW's micrometres."""
    mock._move(
        x=round(x_um * 1000),
        y=round(y_um * 1000),
        z=None if z_um is None else round(z_um * 1000),
    )


def give_objectives_offsets(mock: MockFluoview) -> None:
    """Objectives that are not parcentric or parfocal, and a zoom matched so every objective gives the same pixel size."""
    mock.pixel_um_by_slot = {slot: 0.5 for slot in mock.pixel_um_by_slot}
    mock.objective_offsets_um.update(TRUE_OFFSETS)


def turn_nosepiece(mock: MockFluoview, slot: int) -> None:
    """The operator puts in the objective of *slot*; the stage stays where it is."""
    mock.objective_slot = slot


def set_up_z_stack(
    mock: MockFluoview,
    *,
    start_um: float = -3.0,
    end_um: float = 3.0,
    step_um: float = 0.5,
) -> None:
    """A z-stack in FLUOVIEW, around its Z origin, that brackets each objective's sharp plane."""
    mock.z_stack.update(
        enabled=True,
        start_nm=round(start_um * 1000),
        end_nm=round(end_um * 1000),
        step_nm=round(step_um * 1000),
        slices=int(round((end_um - start_um) / step_um)) + 1,
    )


def switch_z_stack_off(mock: MockFluoview) -> None:
    mock.z_stack["enabled"] = False
